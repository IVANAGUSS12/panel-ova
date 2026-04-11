from .models import AuditLog
from django.utils.deprecation import MiddlewareMixin
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from . import signals
import logging
import os
from django.db import connection

class AuditMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if (
            request.path.startswith('/static/')
            or request.path.startswith('/media/')
            or request.path == '/favicon.ico'
        ):
            return None
        ip = request.META.get('REMOTE_ADDR')
        user = request.user if request.user.is_authenticated else None
        AuditLog.objects.create(
            user=user,
            path=request.path,
            method=request.method,
            ip_address=ip,
        )
        return None


class CurrentUserMiddleware(MiddlewareMixin):
    """Middleware para capturar el usuario actual en los signals"""
    def process_request(self, request):
        if request.user.is_authenticated:
            signals.set_current_user(request.user)
        else:
            signals.set_current_user(None)
    
    def process_response(self, request, response):
        signals.set_current_user(None)
        return response


class SlowQueryLoggingMiddleware(MiddlewareMixin):
    """Registra consultas SQL lentas para ayudar a identificar cuellos de botella.

    - Usa `django.db.connection.queries`, por lo que requiere `DEBUG = True` o
      un wrapper que capture consultas.
    - Umbral configurable mediante la variable de entorno `SLOW_QUERY_THRESHOLD` (segundos).
    """
    def process_request(self, request):
        # Guardamos cuántas consultas había al inicio de la petición
        try:
            request._sql_start_index = len(connection.queries)
        except Exception:
            request._sql_start_index = 0

    def process_response(self, request, response):
        try:
            start = getattr(request, '_sql_start_index', 0)
            queries = connection.queries[start:]
        except Exception:
            queries = []

        try:
            threshold = float(os.getenv('SLOW_QUERY_THRESHOLD', '0.05'))
        except Exception:
            threshold = 0.05

        if queries:
            logger = logging.getLogger('slow_queries')
            for q in queries:
                # 'time' suele ser string en segundos cuando Django registra las queries
                try:
                    t = float(q.get('time', 0))
                except Exception:
                    t = 0
                if t >= threshold:
                    logger.warning(
                        'Slow query: %.6fs path=%s sql=%s',
                        t,
                        getattr(request, 'path', 'unknown'),
                        q.get('sql')[:2000],
                    )

        return response
