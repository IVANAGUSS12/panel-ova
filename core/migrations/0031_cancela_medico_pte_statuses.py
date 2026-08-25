from django.db import migrations, models


PENDING_PROVIDER_STATUS = 'PENDIENTE_ENVIO_PRESTADOR'
PENDING_PRESTADOR_STATUS = 'PENDIENTE_PRESTADOR'
PENDING_MEDICO_STATUS = 'PENDIENTE_MEDICO'
PENDING_PACIENTE_STATUS = 'PENDIENTE_PACIENTE'
PENDING_COMMERCIAL_STATUS = 'PEND_COMERCIAL_PRESUPUESTO'
AUTHORIZED_MATERIAL_STATUS = 'AUTORIZADO_MATERIAL_PEND'
REJECTION_STATUS = 'RECHAZO_COBERTURA'
CANCELA_MEDICO_STATUS = 'CANCELA_MEDICO'
CANCELA_PTE_STATUS = 'CANCELA_PTE'


PATIENT_STATUS_CHOICES = [
    (PENDING_PROVIDER_STATUS, 'Pendiente envio prestador'),
    (PENDING_PRESTADOR_STATUS, 'Pendiente prestador'),
    (PENDING_MEDICO_STATUS, 'Pendiente medico'),
    (PENDING_PACIENTE_STATUS, 'Pendiente paciente'),
    ('AUTORIZADO', 'Autorizado'),
    (PENDING_COMMERCIAL_STATUS, 'Pendiente comercial - presupuesto'),
    (AUTHORIZED_MATERIAL_STATUS, 'Autorizado - material pendiente'),
    (REJECTION_STATUS, 'Rechazo cobertura'),
    ('REPROGRAMADO', 'Reprogramado'),
    ('REALIZADO', 'Realizado'),
    ('SUSPENDIDA', 'Suspendida'),
    (CANCELA_MEDICO_STATUS, 'Cancela medico'),
    (CANCELA_PTE_STATUS, 'Cancela pte'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0030_rechazo_cobertura_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='patient',
            name='status',
            field=models.CharField(
                choices=PATIENT_STATUS_CHOICES,
                default=PENDING_PROVIDER_STATUS,
                max_length=30,
            ),
        ),
    ]
