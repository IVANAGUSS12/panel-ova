from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Patient(models.Model):
    STATUS_PENDIENTE = 'PENDIENTE'
    STATUS_SOLICITADO = 'SOLICITADO'
    STATUS_AUTORIZADO = 'AUTORIZADO'
    STATUS_MATERIAL_PENDIENTE = 'MATERIAL_PENDIENTE'
    STATUS_RECHAZO = 'RECHAZO'
    STATUS_REPROGRAMADO = 'REPROGRAMADO'

    STATUS_CHOICES = [
        (STATUS_PENDIENTE, 'Pendiente'),
        (STATUS_SOLICITADO, 'Solicitado'),
        (STATUS_AUTORIZADO, 'Autorizado'),
        (STATUS_MATERIAL_PENDIENTE, 'Material pendiente'),
        (STATUS_RECHAZO, 'Rechazo'),
        (STATUS_REPROGRAMADO, 'Reprogramado'),
    ]

    full_name = models.CharField(max_length=255)
    dni = models.CharField(max_length=50)
    phone = models.CharField(max_length=50, blank=True, null=True)
    email = models.CharField(max_length=255, blank=True, null=True)

    coverage = models.CharField(max_length=255)
    doctor = models.CharField(max_length=255)
    service = models.CharField(max_length=255)
    planned_date = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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

    def save(self, *args, **kwargs):
        # 👇 Normalizar a MAYÚSCULAS
        if self.full_name:
            self.full_name = self.full_name.upper().strip()

        if self.coverage:
            self.coverage = self.coverage.upper().strip()

        if self.doctor:
            self.doctor = self.doctor.upper().strip()

        if self.service:
            self.service = self.service.upper().strip()

        # ID de tracking
        if not self.tracking_id:
            self.tracking_id = f"OVA{int(timezone.now().timestamp())}"

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} ({self.dni})"


class Attachment(models.Model):
    TYPE_CREDENCIAL = 'CREDENCIAL'
    TYPE_DNI = 'DNI'
    TYPE_ORDEN = 'ORDEN'
    TYPE_MATERIALES = 'MATERIALES'
    TYPE_ESTUDIOS = 'ESTUDIOS'

    TYPE_CHOICES = [
        (TYPE_CREDENCIAL, 'Credencial'),
        (TYPE_DNI, 'DNI'),
        (TYPE_ORDEN, 'Orden de intervención'),
        (TYPE_MATERIALES, 'Materiales'),
        (TYPE_ESTUDIOS, 'Estudios / HC'),
	(TYPE_ESTUDIOS, 'Autorizacion')
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
