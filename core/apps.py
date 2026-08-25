from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        import core.signals  # Importar signals al iniciar la app
        from core.agenda_scheduler import start_agenda_scheduler

        start_agenda_scheduler()
