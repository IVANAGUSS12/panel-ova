from .models import AuditLog
from django.utils.deprecation import MiddlewareMixin
from django.http import HttpResponseForbidden
from django.shortcuts import redirect

class AuditMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path.startswith('/static/'):
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
class ExternalQRLockdownMiddleware:
    """
    Si el host viene de trycloudflare.com, solo dejamos usar /qr/carga/
    (y static, favicon).
    Cualquier otra ruta redirige al QR o se bloquea.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.allowed_paths = [
            "/qr/carga/",
            "/static/",
            "/favicon.ico",
        ]

    def __call__(self, request):
        host = request.get_host().split(":")[0]

        # Solo nos importa el acceso externo
        if "trycloudflare.com" in host:
            # Si no está yendo al QR, lo mandamos al QR sí o sí
            if not any(request.path.startswith(p) for p in self.allowed_paths):
                return redirect("/qr/carga/")

        return self.get_response(request)