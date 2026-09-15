import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings

from core import saavedra_portable_backend as backend


_scheduler_started = False
_scheduler_lock = threading.Lock()


def _should_start_scheduler() -> bool:
    if not getattr(settings, "AUTO_REFRESH_AGENDAS", True):
        return False

    blocked_commands = {
        "makemigrations",
        "migrate",
        "showmigrations",
        "diffsettings",
        "collectstatic",
        "shell",
        "dbshell",
        "createsuperuser",
        "changepassword",
        "test",
        "refresh_agendas",
        "import_portable_statuses",
        "slow_queries_report",
        "generate_thumbnails",
        "populate_normalized_fields",
        "import_ova_log",
        "check",
    }
    if any(arg in blocked_commands for arg in sys.argv[1:]):
        return False

    if "runserver" in sys.argv and os.environ.get("RUN_MAIN") != "true":
        return False

    return True


def _scheduler_loop() -> None:
    if not backend.acquire_refresh_leader_lock():
        print("[agenda-scheduler] otro proceso ya quedó como actualizador principal")
        return

    interval_seconds = max(int(getattr(settings, "AUTO_REFRESH_AGENDAS_INTERVAL_MINUTES", 20) * 60), 60)
    agendas = [agenda["key"] for agenda in backend.get_available_agendas()]

    while True:
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[agenda-scheduler] {started_at} iniciando actualización: {', '.join(agendas)}")

        for agenda_key in agendas:
            agenda = backend.get_agenda_config(agenda_key)
            try:
                result = backend.refresh_report(agenda_key)
                print(
                    f"[agenda-scheduler] {agenda['title']}: {result['count']} cirugías actualizadas a las {result['downloaded_at']}"
                )
            except Exception as exc:
                print(f"[agenda-scheduler] {agenda['title']}: error al actualizar: {exc}")

        time.sleep(interval_seconds)


def start_agenda_scheduler() -> None:
    global _scheduler_started

    with _scheduler_lock:
        if _scheduler_started or not _should_start_scheduler():
            return

        thread = threading.Thread(target=_scheduler_loop, name="agenda-auto-refresh", daemon=True)
        thread.start()
        _scheduler_started = True
