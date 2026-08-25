"""
Comando: autorizar_desde_excel

Lee la hoja QUIROFANO del Excel y marca como AUTORIZADO en la app
a todos los pacientes que constan como operados (Suspendido = 'No').

Uso:
    python manage.py autorizar_desde_excel --excel ruta/al/archivo.xlsx
    python manage.py autorizar_desde_excel --excel ruta/al/archivo.xlsx --dry-run
    python manage.py autorizar_desde_excel --excel ruta/al/archivo.xlsx --estado PENDIENTE_PRESTADOR
"""

import os
import openpyxl
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from core.models import Patient, PatientHistory


class Command(BaseCommand):
    help = 'Marca como AUTORIZADO a pacientes del Excel de quirófano (cruce por DNI)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--excel',
            required=True,
            help='Ruta al archivo .xlsx (hoja QUIROFANO)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Solo muestra qué se haría, sin guardar cambios'
        )
        parser.add_argument(
            '--estado',
            default='',
            help='Filtrar solo pacientes con este estado actual (ej: PENDIENTE_PRESTADOR). '
                 'Por defecto aplica a cualquier estado activo (no AUTORIZADO, no REALIZADO).'
        )
        parser.add_argument(
            '--yes',
            action='store_true',
            default=False,
            help='Confirmar automáticamente sin pedir input'
        )

    def handle(self, *args, **options):
        ruta = options['excel']
        dry_run = options['dry_run']
        auto_yes = options['yes']
        estado_filtro = options['estado'].strip().upper()

        if not os.path.exists(ruta):
            raise CommandError(f'No se encontró el archivo: {ruta}')

        self.stdout.write(f'Leyendo {ruta}...')

        try:
            wb = openpyxl.load_workbook(ruta, data_only=True)
        except Exception as e:
            raise CommandError(f'Error al abrir el Excel: {e}')

        if 'QUIROFANO' not in wb.sheetnames:
            raise CommandError(f'No existe la hoja "QUIROFANO". Hojas disponibles: {wb.sheetnames}')

        ws = wb['QUIROFANO']

        # Columnas: Quirofano(1) FechaCir(2) Paciente(3) ObraSocial(4) DNI(5)
        #           Intervencion(6) Cirujano(7) Especialidad(8) Origen(9)
        #           Suspendido(10) FechaSuspension(11) Reprogramado(12) FechaAnterior(13)

        # Recolectar DNIs operados (Suspendido = 'No')
        dnis_operados = set()
        filas_ignoradas = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            dni_raw = row[4]    # Col 5 → índice 4
            suspendido = str(row[9] or '').strip().lower()  # Col 10

            if not dni_raw:
                continue

            # Normalizar DNI: solo dígitos
            dni_str = str(int(dni_raw)) if isinstance(dni_raw, float) else str(dni_raw).strip()

            if suspendido == 'no':
                dnis_operados.add(dni_str)
            else:
                filas_ignoradas += 1

        self.stdout.write(
            f'DNIs operados (Suspendido=No): {len(dnis_operados)} | '
            f'Suspendidos/ignorados: {filas_ignoradas}'
        )

        if not dnis_operados:
            self.stdout.write(self.style.WARNING('No se encontraron DNIs válidos. Nada que hacer.'))
            return

        # Buscar pacientes en la DB (excluye PENDIENTE, AUTORIZADO y REALIZADO)
        estados_excluidos = [Patient.STATUS_PENDIENTE, Patient.STATUS_AUTORIZADO, Patient.STATUS_REALIZADO]
        qs = Patient.objects.filter(dni__in=dnis_operados).exclude(status__in=estados_excluidos)

        if estado_filtro:
            qs = qs.filter(status=estado_filtro)
            self.stdout.write(f'Filtrando solo estado: {estado_filtro}')

        pacientes = list(qs.order_by('full_name'))
        self.stdout.write(f'Pacientes encontrados en la app para actualizar: {len(pacientes)}')

        if not pacientes:
            self.stdout.write(self.style.WARNING(
                'No se encontraron pacientes en la app que coincidan '
                '(o ya están todos AUTORIZADOS/REALIZADO).'
            ))
            return

        # Mostrar vista previa
        self.stdout.write('\n--- VISTA PREVIA ---')
        for p in pacientes:
            self.stdout.write(
                f'  DNI {p.dni:>12} | {p.full_name:<40} | Estado actual: {p.status}'
            )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'\n[DRY-RUN] Se actualizarían {len(pacientes)} pacientes. '
                'Ejecutar sin --dry-run para aplicar.'
            ))
            return

        # Confirmar
        if not auto_yes:
            self.stdout.write('')
            confirm = input(f'¿Confirmar marcar {len(pacientes)} pacientes como AUTORIZADO? [s/N]: ')
            if confirm.lower() not in ('s', 'si', 'sí', 'y', 'yes'):
                self.stdout.write(self.style.WARNING('Operación cancelada.'))
                return

        # Aplicar
        now = timezone.now()
        actualizados = 0
        for p in pacientes:
            estado_anterior = p.status
            p.status = Patient.STATUS_AUTORIZADO
            p.status_since = now
            p.save(update_fields=['status', 'status_since', 'updated_at'])

            PatientHistory.objects.create(
                patient=p,
                field_name='Estado',
                old_value=estado_anterior,
                new_value=Patient.STATUS_AUTORIZADO,
                action='UPDATE',
                user=None,
            )
            actualizados += 1

        self.stdout.write(self.style.SUCCESS(
            f'\n✓ {actualizados} pacientes marcados como AUTORIZADO correctamente.'
        ))
