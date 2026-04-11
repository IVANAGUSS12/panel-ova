from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Patient, PatientHistory
import threading
from django.db.models.signals import post_save
from .models import Attachment
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
import io
from PIL import Image

# Thread-local storage para mantener el usuario actual
_thread_locals = threading.local()

def get_current_user():
    """Obtiene el usuario actual desde el thread local"""
    return getattr(_thread_locals, 'user', None)

def set_current_user(user):
    """Establece el usuario actual en el thread local"""
    _thread_locals.user = user


# Diccionario para almacenar el estado anterior
_patient_old_values = {}


@receiver(pre_save, sender=Patient)
def store_old_patient_values(sender, instance, **kwargs):
    """Guarda los valores anteriores antes de actualizar"""
    if instance.pk:
        try:
            old_instance = Patient.objects.get(pk=instance.pk)
            _patient_old_values[instance.pk] = {
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
        except Patient.DoesNotExist:
            pass


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
        old_values = _patient_old_values.get(instance.pk, {})
        
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
        
        # Mapeo de estados a nombres legibles
        status_display = {
            'PENDIENTE': 'Pendiente',
            'SOLICITADO': 'Solicitado',
            'AUTORIZADO': 'Autorizado',
            'PRESUPUESTO_SI': 'Presupuesto sí',
            'MATERIAL_PENDIENTE': 'Material pendiente',
            'RECHAZO': 'Rechazo',
            'REPROGRAMADO': 'Reprogramado',
            'REALIZADO': 'Realizado',
        }
        
        for field, readable_name in field_names.items():
            old_val = old_values.get(field)
            new_val = getattr(instance, field)
            
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
        
        # Limpiar valores antiguos
        if instance.pk in _patient_old_values:
            del _patient_old_values[instance.pk]


# Thumbnail generation disabled per user request (do not generate or save `_thumb.webp`).
def create_attachment_thumbnail(sender, instance, created, **kwargs):
    """Thumbnail generation intentionally disabled."""
    return
