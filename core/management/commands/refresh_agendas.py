import time

from django.core.management.base import BaseCommand

from core import saavedra_portable_backend as backend


class Command(BaseCommand):
    help = "Actualiza los reportes de agendas quirófano por sede una vez o en loop."

    def add_arguments(self, parser):
        parser.add_argument(
            "--agenda",
            action="append",
            dest="agendas",
            help="Clave de agenda a actualizar. Puede repetirse. Por defecto actualiza todas.",
        )
        parser.add_argument(
            "--interval-minutes",
            type=int,
            default=20,
            help="Minutos entre ejecuciones cuando se usa --loop. Por defecto 20.",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Ejecuta el refresco continuamente respetando el intervalo configurado.",
        )

    def handle(self, *args, **options):
        agendas = options.get("agendas") or [agenda["key"] for agenda in backend.get_available_agendas()]
        interval_minutes = max(int(options.get("interval_minutes") or 20), 1)
        run_forever = bool(options.get("loop"))

        if run_forever and not backend.acquire_refresh_leader_lock():
            self.stdout.write(self.style.WARNING("Otro proceso ya está actualizando agendas; este comando no tomará el control."))
            return

        while True:
            had_errors = False
            started_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self.stdout.write(self.style.NOTICE(f"[{started_at}] Iniciando actualización de agendas: {', '.join(agendas)}"))

            for agenda_key in agendas:
                agenda = backend.get_agenda_config(agenda_key)
                try:
                    result = backend.refresh_report(agenda_key)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"{agenda['title']}: {result['count']} cirugías actualizadas a las {result['downloaded_at']}"
                        )
                    )
                except Exception as exc:
                    had_errors = True
                    self.stderr.write(self.style.ERROR(f"{agenda['title']}: {exc}"))

            if not run_forever:
                if had_errors:
                    raise SystemExit(1)
                return

            self.stdout.write(f"Esperando {interval_minutes} minuto(s) para la próxima actualización.")
            time.sleep(interval_minutes * 60)