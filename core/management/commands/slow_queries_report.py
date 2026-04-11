import re
import os
from collections import Counter
from django.core.management.base import BaseCommand

LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '..', '..', '..', 'slow_queries.log')
# The above path is relative; if it doesn't resolve, we fallback to BASE_DIR/slow_queries.log

class Command(BaseCommand):
    help = 'Analiza slow_queries.log y muestra un resumen de las consultas más frecuentes y lentas.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--path', '-p',
            dest='path',
            help='Ruta al archivo slow_queries.log (por defecto proyecto/slow_queries.log)'
        )
        parser.add_argument(
            '--top', '-t',
            dest='top',
            type=int,
            default=20,
            help='Cantidad de consultas únicas a mostrar (por defecto 20)'
        )

    def handle(self, *args, **options):
        path = options.get('path')
        if not path:
            # compute project root based on this file
            here = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            path = os.path.abspath(os.path.join(here, '..', 'slow_queries.log'))

        if not os.path.exists(path):
            self.stdout.write(self.style.ERROR(f"No existe el archivo: {path}"))
            return

        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            text = fh.read()

        # Buscamos líneas con el patrón generado por el middleware:
        # Slow query: <seconds> path=<path> sql=<SQL>
        pattern = re.compile(r"Slow query:\s*([0-9\.]+)s\s*path=([^\s]+)\s*sql=(.*)", re.DOTALL)
        matches = pattern.findall(text)

        if not matches:
            self.stdout.write(self.style.WARNING('No se encontraron entradas con formato esperado en slow_queries.log'))
            return

        # Normalizar SQL (quitar valores literales para agrupar)
        def normalize_sql(sql):
            # Quitar literales entre comillas y números largos
            sql = re.sub(r"'[^']*'", "'?'", sql)
            sql = re.sub(r'\b\d+\b', '?', sql)
            sql = ' '.join(sql.split())
            return sql.strip()[:1000]

        aggregated = {}
        for sec_str, path, sql in matches:
            try:
                sec = float(sec_str)
            except Exception:
                sec = 0.0
            key = normalize_sql(sql)
            arr = aggregated.setdefault(key, {'count': 0, 'total_time': 0.0, 'max_time': 0.0, 'examples': []})
            arr['count'] += 1
            arr['total_time'] += sec
            arr['max_time'] = max(arr['max_time'], sec)
            if len(arr['examples']) < 5:
                arr['examples'].append({'time': sec, 'path': path, 'sql': sql[:2000]})

        # Ordenar por tiempo total
        items = sorted(aggregated.items(), key=lambda kv: kv[1]['total_time'], reverse=True)

        top = options.get('top', 20)
        self.stdout.write(self.style.SUCCESS(f"Top {top} consultas agrupadas por patrón (por tiempo total):\n"))
        for i, (pattern_sql, meta) in enumerate(items[:top], start=1):
            avg = meta['total_time'] / meta['count'] if meta['count'] else 0
            self.stdout.write(self.style.NOTICE(f"{i}. count={meta['count']} total={meta['total_time']:.4f}s avg={avg:.4f}s max={meta['max_time']:.4f}s"))
            self.stdout.write(f"   SQL pattern: {pattern_sql}\n")
            for ex in meta['examples']:
                self.stdout.write(f"     - example: time={ex['time']:.4f}s path={ex['path']}\n")
            self.stdout.write("\n")

        self.stdout.write(self.style.SUCCESS('Análisis completado.'))
