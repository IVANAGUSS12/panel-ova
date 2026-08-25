from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Patient, PatientHistory
import threading

# Thread-local storage para mantener el usuario actual
_thread_locals = threading.local()

def get_current_user():
    """Obtiene el usuario actual desde el thread local"""
    return getattr(_thread_locals, 'user', None)

def set_current_user(user):
    """Establece el usuario actual en el thread local"""
    _thread_locals.user = user


@receiver(pre_save, sender=Patient)
def store_old_patient_values(sender, instance, **kwargs):
    from django.utils import timezone as tz
    if instance.pk:
        try:
            old_instance = Patient.objects.get(pk=instance.pk)
            instance._pre_save_old_values = {
                'status': old_instance.status,
                'full_name': old_instance.full_name,
                'dni': old_instance.dni,
                'phone': old_instance.phone,
                'email': old_instance.email,
                'coverage': old_instance.coverage,
                'doctor': old_instance.doctor,
                'service': old_instance.service,
                'planned_date': old_instance.planned_date,
                'assigned_to': old_instance.assigned_to,
                'internal_observations': old_instance.internal_observations,
                'external_observations': old_instance.external_observations,
            }
            # Cuando el estado cambia, actualizar status_since y solicitado_since
            if old_instance.status != instance.status:
                now = tz.now()
                instance.status_since = now
                # Gestionar solicitado_since específicamente
                if instance.status == Patient.STATUS_PENDIENTE_PRESTADOR:
                    instance.solicitado_since = now
                else:
                    instance.solicitado_since = None
        except Patient.DoesNotExist:
            instance._pre_save_old_values = {}
    else:
        instance._pre_save_old_values = {}
        # Paciente nuevo: registrar desde cuándo está en el estado inicial
        now = tz.now()
        if not instance.status_since:
            instance.status_since = now
        if instance.status == Patient.STATUS_PENDIENTE_PRESTADOR and not instance.solicitado_since:
            instance.solicitado_since = now


@receiver(post_save, sender=Patient)
def log_patient_changes(sender, instance, created, **kwargs):
    """Registra los cambios en el historial"""
    user = get_current_user()
    
    if created:
        # Registro de creación
        PatientHistory.objects.create(
            patient=instance,
            user=user,
            action=PatientHistory.ACTION_CREATE,
            field_name='Creación',
            new_value=f"Paciente {instance.full_name} creado"
        )
    else:
        # Registro de cambios
        old_values = getattr(instance, '_pre_save_old_values', {})

        # Mapeo de nombres de campos a nombres legibles
        field_names = {
            'status': 'Estado',
            'full_name': 'Nombre completo',
            'dni': 'DNI',
            'phone': 'Teléfono',
            'email': 'Email',
            'coverage': 'Cobertura',
            'doctor': 'Médico',
            'service': 'Servicio',
            'planned_date': 'Fecha de cirugía',
            'assigned_to': 'Asignado a',
            'internal_observations': 'Observaciones internas',
            'external_observations': 'Observaciones externas',
        }
        
        # Mapeo de estados a nombres legibles derivado directamente del modelo
        status_display = dict(Patient.STATUS_CHOICES)
        
        # Campos de texto que el modelo normaliza a uppercase en save()
        _uppercase_fields = {'full_name', 'coverage', 'doctor', 'service'}

        try:
            for field, readable_name in field_names.items():
                old_val = old_values.get(field)
                new_val = getattr(instance, field)

                # Normalizar campos de texto igual que lo hace Patient.save()
                # para evitar entradas falsas cuando solo cambia el casing
                if field in _uppercase_fields:
                    if isinstance(old_val, str):
                        old_val = old_val.upper().strip()
                    if isinstance(new_val, str):
                        new_val = new_val.upper().strip()

                # Conversión para campos especiales
                if field == 'status':
                    old_val = status_display.get(old_val, old_val) if old_val else None
                    new_val = status_display.get(new_val, new_val) if new_val else None
                elif field == 'assigned_to':
                    old_val = old_val.get_full_name() or old_val.username if old_val else 'Sin asignar'
                    new_val = new_val.get_full_name() or new_val.username if new_val else 'Sin asignar'
                elif field == 'planned_date':
                    old_val = old_val.strftime('%d/%m/%Y') if old_val else None
                    new_val = new_val.strftime('%d/%m/%Y') if new_val else None

                # Convertir a string para comparación
                old_val_str = str(old_val) if old_val is not None else ''
                new_val_str = str(new_val) if new_val is not None else ''

                if old_val_str != new_val_str:
                    PatientHistory.objects.create(
                        patient=instance,
                        user=user,
                        action=PatientHistory.ACTION_UPDATE,
                        field_name=readable_name,
                        old_value=old_val_str or '(vacío)',
                        new_value=new_val_str or '(vacío)'
                    )
        finally:
            instance._pre_save_old_values = {}
