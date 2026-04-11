from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Patient
from core.text_utils import normalize_text


class Command(BaseCommand):
    help = "Rellena `full_name_norm` y `dni_norm` para pacientes existentes en batches."

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=500, help='Tamaño del batch')

    def handle(self, *args, **options):
        batch = options.get('batch_size', 500)
        qs = Patient.objects.all().order_by('id')
        total = qs.count()
        self.stdout.write(self.style.NOTICE(f"Procesando {total} pacientes en batches de {batch}..."))

        processed = 0
        while True:
            objs = list(qs[processed:processed + batch])
            if not objs:
                break
            with transaction.atomic():
                for p in objs:
                    try:
                        # Calcular valores normalizados localmente y actualizar solo campos necesarios
                        full_norm = normalize_text(p.full_name)
                        dni_norm = normalize_text(p.dni)
                        # Evitar guardar si ya está correcto
                        if p.full_name_norm != full_norm or p.dni_norm != dni_norm:
                            p.full_name_norm = full_norm
                            p.dni_norm = dni_norm
                            p.save(update_fields=['full_name_norm', 'dni_norm'])
                    except Exception as e:
                        self.stderr.write(f"Error procesando paciente {p.pk}: {e}")
            processed += len(objs)
            self.stdout.write(self.style.SUCCESS(f"Procesados {processed}/{total}"))

        self.stdout.write(self.style.SUCCESS('Completado.'))
