import uuid

from django.db import models, IntegrityError
from django.contrib.auth.models import User
from django.utils import timezone

from .text_utils import normalize_text


class Patient(models.Model):
    STATUS_PENDIENTE_ENVIO_PRESTADOR = 'PENDIENTE_ENVIO_PRESTADOR'
    STATUS_PENDIENTE = STATUS_PENDIENTE_ENVIO_PRESTADOR
    STATUS_PENDIENTE_PRESTADOR = 'PENDIENTE_PRESTADOR'
    STATUS_SOLICITADO = STATUS_PENDIENTE_PRESTADOR
    STATUS_PENDIENTE_MEDICO = 'PENDIENTE_MEDICO'
    STATUS_PENDIENTE_PACIENTE = 'PENDIENTE_PACIENTE'
    STATUS_AUTORIZADO = 'AUTORIZADO'
    STATUS_PENDIENTE_COMERCIAL_PRESUPUESTO = 'PEND_COMERCIAL_PRESUPUESTO'
    STATUS_PRESUPUESTO_SI = STATUS_PENDIENTE_COMERCIAL_PRESUPUESTO
    STATUS_AUTORIZADO_MATERIAL_PENDIENTE = 'AUTORIZADO_MATERIAL_PEND'
    STATUS_MATERIAL_PENDIENTE = STATUS_AUTORIZADO_MATERIAL_PENDIENTE
    STATUS_RECHAZO_COBERTURA = 'RECHAZO_COBERTURA'
    STATUS_RECHAZO = STATUS_RECHAZO_COBERTURA
    STATUS_REPROGRAMADO = 'REPROGRAMADO'
    STATUS_REALIZADO = 'REALIZADO'
    STATUS_SUSPENDIDA = 'SUSPENDIDA'
    STATUS_CANCELA_MEDICO = 'CANCELA_MEDICO'
    STATUS_CANCELA_PTE = 'CANCELA_PTE'

    STATUS_CHOICES = [
        (STATUS_PENDIENTE_ENVIO_PRESTADOR, 'Pendiente envio prestador'),
        (STATUS_PENDIENTE_PRESTADOR, 'Pendiente prestador'),
        (STATUS_PENDIENTE_MEDICO, 'Pendiente medico'),
        (STATUS_PENDIENTE_PACIENTE, 'Pendiente paciente'),
        (STATUS_AUTORIZADO, 'Autorizado'),
        (STATUS_PENDIENTE_COMERCIAL_PRESUPUESTO, 'Pendiente comercial - presupuesto'),
        (STATUS_AUTORIZADO_MATERIAL_PENDIENTE, 'Autorizado - material pendiente'),
        (STATUS_RECHAZO_COBERTURA, 'Rechazo cobertura'),
        (STATUS_REPROGRAMADO, 'Reprogramado'),
        (STATUS_REALIZADO, 'Realizado'),
        (STATUS_SUSPENDIDA, 'Suspendida'),
        (STATUS_CANCELA_MEDICO, 'Cancela medico'),
        (STATUS_CANCELA_PTE, 'Cancela pte'),
    ]

    SEDE_SAAVEDRA = 'SAAVEDRA'
    SEDE_POMBO = 'POMBO'
    SEDE_LAS_HERAS = 'LAS HERAS'

    SEDE_CHOICES = [
        ('', 'Sin especificar'),
        (SEDE_SAAVEDRA, 'Saavedra'),
        (SEDE_POMBO, 'Pombo'),
        (SEDE_LAS_HERAS, 'Las Heras'),
    ]

    # Estados de material
    MATERIAL_PENDIENTE = 'PENDIENTE'
    MATERIAL_AUTORIZADO = 'AUTORIZADO'
    MATERIAL_RECHAZADO = 'RECHAZADO'

    MATERIAL_CHOICES = [
        (MATERIAL_PENDIENTE, 'Pendiente'),
        (MATERIAL_AUTORIZADO, 'Autorizado'),
        (MATERIAL_RECHAZADO, 'Rechazado por profesional'),
    ]

    full_name = models.CharField(max_length=255)
    # Campos normalizados para búsquedas rápidas (sin acentos, en minúsculas)
    full_name_norm = models.CharField(max_length=255, blank=True, db_index=True)
    dni = models.CharField(max_length=50)
    dni_norm = models.CharField(max_length=50, blank=True, db_index=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    email = models.CharField(max_length=255, blank=True, null=True)

    coverage = models.CharField(max_length=255)
    doctor = models.CharField(max_length=255)
    service = models.CharField(max_length=255)
    planned_date = models.DateField()
    surgery_time = models.TimeField(blank=True, null=True, verbose_name="Hora de cirugía")
    sede = models.CharField(max_length=50, choices=SEDE_CHOICES, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Fecha/hora en que el paciente entró al estado pendiente prestador
    # Se gestiona automáticamente vía la señal pre_save en signals.py
    solicitado_since = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Pendiente prestador desde'
    )

    # Fecha/hora en que el paciente entró al estado ACTUAL (cualquier estado)
    status_since = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='En estado actual desde'
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDIENTE
    )

    internal_observations = models.TextField(blank=True, null=True)
    external_observations = models.TextField(blank=True, null=True)

    last_reprogram_date = models.DateTimeField(blank=True, null=True)
    last_reprogram_reason = models.TextField(blank=True, null=True)

    tracking_id = models.CharField(max_length=32, unique=True, editable=False)
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_patients',
        verbose_name='Asignado a'
    )

    # Nuevos campos para checks del calendario
    material_status = models.CharField(
        max_length=20,
        choices=MATERIAL_CHOICES,
        default=MATERIAL_PENDIENTE,
        verbose_name='Estado de material'
    )
    en_quirofano = models.BooleanField(
        default=False,
        verbose_name='En quirófano'
    )
    impreso = models.BooleanField(
        default=False,
        verbose_name='Impreso'
    )
    observaciones_calendario = models.TextField(
        blank=True,
        null=True,
        verbose_name='Observaciones del calendario'
    )

    class Meta:
        indexes = [
            models.Index(fields=['planned_date'], name='patient_planned_date_idx'),
            models.Index(fields=['status'], name='patient_status_idx'),
            models.Index(fields=['service'], name='patient_service_idx'),
            models.Index(fields=['doctor'], name='patient_doctor_idx'),
            models.Index(fields=['coverage'], name='patient_coverage_idx'),
            models.Index(fields=['created_at'], name='patient_created_at_idx'),
            models.Index(fields=['planned_date', 'status'], name='patient_date_status_idx'),
        ]

    def save(self, *args, **kwargs):
        from django.core.cache import cache
        # Normalizar a MAYÚSCULAS para visualización
        if self.full_name:
            self.full_name = self.full_name.upper().strip()

        if self.coverage:
            self.coverage = self.coverage.upper().strip()

        if self.doctor:
            self.doctor = self.doctor.upper().strip()

        if self.service:
            self.service = self.service.upper().strip()

        try:
            self.full_name_norm = normalize_text(self.full_name)
        except Exception:
            self.full_name_norm = ""

        try:
            self.dni_norm = normalize_text(self.dni)
        except Exception:
            self.dni_norm = ""

        # ID de tracking
        if not self.tracking_id:
            self.tracking_id = f"OVA{uuid.uuid4().hex[:12].upper()}"

        # Invalidar caché de opciones cuando se guarda un paciente
        cache.delete('patient_service_options')
        cache.delete('patient_doctor_options')
        cache.delete('patient_coverage_options')
        cache.delete('stats_all_services')
        cache.delete('stats_all_doctors')

        for _attempt in range(10):
            try:
                super().save(*args, **kwargs)
                break
            except IntegrityError as exc:
                is_tracking_collision = 'tracking_id' in str(exc).lower()
                if is_tracking_collision and (not kwargs.get('update_fields') or 'tracking_id' in (kwargs.get('update_fields') or [])):
                    self.tracking_id = f"OVA{uuid.uuid4().hex[:12].upper()}"
                else:
                    raise
        else:
            raise RuntimeError("No se pudo generar un tracking_id único tras 10 intentos.")

    def __str__(self):
        return f"{self.full_name} ({self.dni})"


class Attachment(models.Model):
    TYPE_CREDENCIAL = 'CREDENCIAL'
    TYPE_DNI = 'DNI'
    TYPE_ORDEN = 'ORDEN'
    TYPE_AUTORIZACION = 'AUTORIZACION'
    TYPE_MATERIALES = 'MATERIALES'
    TYPE_ESTUDIOS = 'ESTUDIOS'

    TYPE_CHOICES = [
        (TYPE_CREDENCIAL, 'Credencial'),
        (TYPE_DNI, 'DNI'),
        (TYPE_ORDEN, 'Orden de intervención'),
        (TYPE_AUTORIZACION, 'Autorización'),
        (TYPE_MATERIALES, 'Materiales'),
        (TYPE_ESTUDIOS, 'Estudios / HC'),
    ]

    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name='attachments'
    )
    file = models.FileField(upload_to='attachments/%Y/%m/%d/')
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.patient} - {self.get_type_display()}"

    def get_thumbnail_path(self):
        import os
        base, _ = os.path.splitext(self.file.name)
        return f"{base}_thumb.webp"

    def get_thumbnail_url(self):
        from django.core.files.storage import default_storage
        thumb_path = self.get_thumbnail_path()
        if default_storage.exists(thumb_path):
            return default_storage.url(thumb_path)
        return self.file.url


class AuditLog(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    path = models.CharField(max_length=512)
    method = models.CharField(max_length=10)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        who = self.user.username if self.user else 'anon'
        return f"[{self.created_at}] {who} {self.method} {self.path}"


class PatientHistory(models.Model):
    """Historial de cambios de un paciente para auditoría"""
    ACTION_CREATE = 'CREATE'
    ACTION_UPDATE = 'UPDATE'
    ACTION_DELETE = 'DELETE'
    
    ACTION_CHOICES = [
        (ACTION_CREATE, 'Creado'),
        (ACTION_UPDATE, 'Actualizado'),
        (ACTION_DELETE, 'Eliminado'),
    ]
    
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name='history'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Usuario'
    )
    action = models.CharField(
        max_length=10,
        choices=ACTION_CHOICES,
        default=ACTION_UPDATE
    )
    field_name = models.CharField(max_length=100, blank=True, null=True, verbose_name='Campo')
    old_value = models.TextField(blank=True, null=True, verbose_name='Valor anterior')
    new_value = models.TextField(blank=True, null=True, verbose_name='Valor nuevo')
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name='Fecha y hora')
    
    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Historial de cambio'
        verbose_name_plural = 'Historial de cambios'
    
    def __str__(self):
        return f"{self.patient.tracking_id} - {self.get_action_display()} - {self.timestamp}"


class SavedFilter(models.Model):
    """Filtros guardados para búsquedas rápidas"""
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='saved_filters'
    )
    name = models.CharField(max_length=100, verbose_name='Nombre del filtro')
    filter_params = models.JSONField(verbose_name='Parámetros')
    created_at = models.DateTimeField(auto_now_add=True)
    is_default = models.BooleanField(default=False, verbose_name='Filtro por defecto')
    
    class Meta:
        ordering = ['-is_default', 'name']
        unique_together = [['user', 'name']]
        verbose_name = 'Filtro guardado'
        verbose_name_plural = 'Filtros guardados'
    
    def __str__(self):
        return f"{self.user.username} - {self.name}"


class QuirofanoSnapshot(models.Model):
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='quirofano_snapshots'
    )
    window_start = models.DateField(verbose_name='Fecha desde')
    window_end = models.DateField(verbose_name='Fecha hasta')
    source_files = models.JSONField(default=list, blank=True, verbose_name='Archivos origen')
    total_entries = models.PositiveIntegerField(default=0, verbose_name='Entradas actuales')
    new_count = models.PositiveIntegerField(default=0, verbose_name='Nuevos')
    changed_count = models.PositiveIntegerField(default=0, verbose_name='Cambiados')
    unchanged_count = models.PositiveIntegerField(default=0, verbose_name='Sin cambios')
    removed_count = models.PositiveIntegerField(default=0, verbose_name='Quitados')
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name='Importado')

    class Meta:
        ordering = ['-imported_at']
        verbose_name = 'Snapshot de quirófano'
        verbose_name_plural = 'Snapshots de quirófano'

    def __str__(self):
        return (
            f"Quirófano {self.imported_at:%d/%m/%Y %H:%M} "
            f"({self.window_start:%d/%m/%Y} - {self.window_end:%d/%m/%Y})"
        )


class QuirofanoEntry(models.Model):
    CHANGE_NEW = 'NEW'
    CHANGE_CHANGED = 'CHANGED'
    CHANGE_UNCHANGED = 'UNCHANGED'
    CHANGE_REMOVED = 'REMOVED'

    COMPARISON_BOTH = 'BOTH'
    COMPARISON_ONLY_QUIROFANO = 'ONLY_QUIROFANO'
    COMPARISON_ONLY_APP = 'ONLY_APP'

    CHANGE_CHOICES = [
        (CHANGE_NEW, 'Nuevo'),
        (CHANGE_CHANGED, 'Cambiado'),
        (CHANGE_UNCHANGED, 'Sin cambios'),
        (CHANGE_REMOVED, 'Quitado'),
    ]

    COMPARISON_CHOICES = [
        (COMPARISON_BOTH, 'En ambos'),
        (COMPARISON_ONLY_QUIROFANO, 'Solo quirófano'),
        (COMPARISON_ONLY_APP, 'Solo app'),
    ]

    WORKFLOW_STATUS_CHOICES = [
        (Patient.STATUS_PENDIENTE, 'Pendiente envio prestador'),
        (Patient.STATUS_PENDIENTE_PRESTADOR, 'Pendiente prestador'),
        (Patient.STATUS_PENDIENTE_MEDICO, 'Pendiente medico'),
        (Patient.STATUS_PENDIENTE_PACIENTE, 'Pendiente paciente'),
        (Patient.STATUS_AUTORIZADO, 'Autorizado'),
        (Patient.STATUS_AUTORIZADO_MATERIAL_PENDIENTE, 'Autorizado - material pendiente'),
    ]

    snapshot = models.ForeignKey(
        QuirofanoSnapshot,
        on_delete=models.CASCADE,
        related_name='entries'
    )
    app_patient = models.ForeignKey(
        Patient,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='quirofano_entries'
    )
    change_type = models.CharField(max_length=16, choices=CHANGE_CHOICES, db_index=True)
    comparison_status = models.CharField(max_length=20, choices=COMPARISON_CHOICES, default=COMPARISON_ONLY_QUIROFANO, db_index=True)
    manual_comparison_status = models.CharField(max_length=20, choices=COMPARISON_CHOICES, blank=True, default='')
    resolved_comparison_status = models.CharField(max_length=20, choices=COMPARISON_CHOICES, default=COMPARISON_ONLY_QUIROFANO, db_index=True)
    manual_workflow_status = models.CharField(max_length=30, choices=WORKFLOW_STATUS_CHOICES, blank=True, default='')
    resolved_workflow_status = models.CharField(max_length=30, choices=WORKFLOW_STATUS_CHOICES, blank=True, default='')
    match_key = models.CharField(max_length=255, blank=True, default='', db_index=True)
    sede = models.CharField(max_length=50, blank=True, default='')
    surgery_date = models.DateField(verbose_name='Fecha de cirugía')
    surgery_time = models.TimeField(blank=True, null=True, verbose_name='Hora programada')
    patient_name = models.CharField(max_length=255, blank=True, default='')
    patient_name_norm = models.CharField(max_length=255, blank=True, default='', db_index=True)
    coverage = models.CharField(max_length=255, blank=True, default='')
    dni = models.CharField(max_length=50, blank=True, default='')
    report_phone = models.CharField(max_length=50, blank=True, default='')
    doctor = models.CharField(max_length=255, blank=True, default='')
    doctor_norm = models.CharField(max_length=255, blank=True, default='')
    destination_service = models.CharField(max_length=255, blank=True, default='')
    specialty_raw = models.CharField(max_length=255, blank=True, default='')
    canonical_service = models.CharField(max_length=255, blank=True, default='', db_index=True)
    origin = models.CharField(max_length=255, blank=True, default='')
    source_file = models.CharField(max_length=255, blank=True, default='')
    source_row_number = models.PositiveIntegerField(default=0)
    previous_sede = models.CharField(max_length=50, blank=True, default='')
    previous_surgery_date = models.DateField(null=True, blank=True)
    previous_surgery_time = models.TimeField(null=True, blank=True)
    previous_doctor = models.CharField(max_length=255, blank=True, default='')
    previous_coverage = models.CharField(max_length=255, blank=True, default='')
    previous_specialty_raw = models.CharField(max_length=255, blank=True, default='')
    previous_origin = models.CharField(max_length=255, blank=True, default='')
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['surgery_date', 'surgery_time', 'patient_name']
        indexes = [
            models.Index(fields=['snapshot', 'change_type'], name='quirof_snapshot_change_idx'),
            models.Index(fields=['snapshot', 'surgery_date'], name='quirof_snapshot_date_idx'),
            models.Index(fields=['snapshot', 'sede'], name='quirof_snapshot_sede_idx'),
        ]
        verbose_name = 'Entrada de quirófano'
        verbose_name_plural = 'Entradas de quirófano'

    def __str__(self):
        return f"{self.patient_name or 'Sin nombre'} - {self.surgery_date:%d/%m/%Y}"

    def get_active_comparison_status(self):
        return self.manual_comparison_status or self.comparison_status

    def get_active_workflow_status(self):
        return self.resolved_workflow_status


class InternacionVarias(models.Model):
    ORIGEN_MANUAL = 'manual'
    ORIGEN_AUTOMATICO_INTERVENCION = 'automatico_intervencion'
    ORIGEN_AUTOMATICO_CIRUJANO = 'automatico_cirujano'

    ORIGEN_CHOICES = [
        (ORIGEN_MANUAL, 'Manual'),
        (ORIGEN_AUTOMATICO_INTERVENCION, 'Automatico por intervencion'),
        (ORIGEN_AUTOMATICO_CIRUJANO, 'Automatico por cirujano'),
    ]

    fecha = models.DateField(db_index=True)
    horario = models.CharField(max_length=20, blank=True, default='')
    paciente_nombre = models.CharField(max_length=255)
    paciente_dni = models.CharField(max_length=50, blank=True, default='')
    edad = models.CharField(max_length=30, blank=True, default='')
    obra_social = models.CharField(max_length=255, blank=True, default='')
    motivo = models.CharField(max_length=255, blank=True, default='')
    servicio_solicitante = models.CharField(max_length=255, blank=True, default='')
    medico_responsable = models.CharField(max_length=255, blank=True, default='')
    cama_asignada = models.CharField(max_length=100, blank=True, default='')
    destino = models.CharField(max_length=255, blank=True, default='')
    telefono = models.CharField(max_length=80, blank=True, default='')
    observaciones = models.TextField(blank=True, default='')
    origen_registro = models.CharField(max_length=40, choices=ORIGEN_CHOICES, default=ORIGEN_MANUAL)
    cirugia_origen_id = models.CharField(max_length=255, blank=True, default='')
    motivo_automatico = models.CharField(max_length=255, blank=True, default='')
    fecha_cirugia_original = models.DateField(null=True, blank=True)
    horario_cirugia_original = models.CharField(max_length=20, blank=True, default='')
    intervencion_original = models.CharField(max_length=255, blank=True, default='')
    cirujano_original = models.CharField(max_length=255, blank=True, default='')
    automatic_signature = models.CharField(max_length=500, blank=True, default='', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['fecha', 'horario', 'paciente_nombre']
        verbose_name = 'Internacion varias'
        verbose_name_plural = 'Internaciones varias'
        indexes = [
            models.Index(fields=['fecha', 'origen_registro'], name='intern_varias_fecha_origen_idx'),
            models.Index(fields=['cirugia_origen_id'], name='intern_varias_cirugia_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['automatic_signature'],
                condition=~models.Q(automatic_signature=''),
                name='uniq_intern_varias_auto_signature',
            ),
        ]

    def __str__(self):
        return f"{self.paciente_nombre} - {self.fecha:%d/%m/%Y}"
