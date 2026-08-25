import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core import saavedra_portable_backend as backend


class Command(BaseCommand):
    help = "Importa estados manuales desde una app portable vieja al store actual de agendas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            required=True,
            help="Ruta a la carpeta data de la app portable vieja.",
        )
        parser.add_argument(
            "--agenda",
            default="saavedra",
            help="Agenda destino. Por defecto saavedra.",
        )

    def handle(self, *args, **options):
        source_dir = Path(options["source"]).expanduser().resolve()
        agenda_key = backend.get_agenda_config(options.get("agenda"))["key"]

        status_path = source_dir / "status.json"
        if not status_path.exists():
            raise CommandError(f"No existe el archivo de estados: {status_path}")

        try:
            raw_store = json.loads(status_path.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"No se pudo leer {status_path}: {exc}") from exc

        if not isinstance(raw_store, dict):
            raise CommandError("El status.json origen no tiene formato válido.")

        manual_store = {
            key: value
            for key, value in raw_store.items()
            if backend._status_has_manual_data(value)
        }

        if not manual_store:
            self.stdout.write(self.style.WARNING("No se encontraron estados manuales para importar."))
            return

        current_surgeries = backend.list_surgeries(agenda_key)
        target_status = backend.read_status_store(agenda_key)

        exact_matches = 0
        fuzzy_matches = 0
        inserted_raw = 0
        inserted_current = 0

        merged = dict(target_status)

        for key, value in manual_store.items():
            if key not in merged:
                merged[key] = value
                inserted_raw += 1

        for entry in current_surgeries:
            current_key = backend.surgery_key(entry)
            if current_key in manual_store:
                if current_key not in merged:
                    inserted_current += 1
                merged[current_key] = manual_store[current_key]
                exact_matches += 1
                continue

            matched_legacy_key = backend._find_status_store_match(entry, manual_store)
            if matched_legacy_key:
                if current_key not in merged:
                    inserted_current += 1
                merged[current_key] = manual_store[matched_legacy_key]
                fuzzy_matches += 1

        if merged != target_status:
            backend.save_json(backend._agenda_paths(agenda_key)["status"], merged)

        self.stdout.write(self.style.SUCCESS(f"Agenda destino: {backend.get_agenda_config(agenda_key)['title']}"))
        self.stdout.write(f"Estados manuales origen: {len(manual_store)}")
        self.stdout.write(f"Claves legacy agregadas al store: {inserted_raw}")
        self.stdout.write(f"Coincidencias exactas con reporte actual: {exact_matches}")
        self.stdout.write(f"Coincidencias por remapeo: {fuzzy_matches}")
        self.stdout.write(f"Claves actuales agregadas/actualizadas: {inserted_current}")