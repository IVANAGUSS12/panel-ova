from django.db import migrations, models


PENDING_PROVIDER_STATUS = 'PENDIENTE_ENVIO_PRESTADOR'
PENDING_PRESTADOR_STATUS = 'PENDIENTE_PRESTADOR'
PENDING_MEDICO_STATUS = 'PENDIENTE_MEDICO'
PENDING_PACIENTE_STATUS = 'PENDIENTE_PACIENTE'
PENDING_COMMERCIAL_STATUS = 'PEND_COMERCIAL_PRESUPUESTO'


PATIENT_STATUS_CHOICES = [
    (PENDING_PROVIDER_STATUS, 'Pendiente envio prestador'),
    (PENDING_PRESTADOR_STATUS, 'Pendiente prestador'),
    (PENDING_MEDICO_STATUS, 'Pendiente medico'),
    (PENDING_PACIENTE_STATUS, 'Pendiente paciente'),
    ('AUTORIZADO', 'Autorizado'),
    (PENDING_COMMERCIAL_STATUS, 'Pendiente comercial - presupuesto'),
    ('MATERIAL_PENDIENTE', 'Material pendiente'),
    ('RECHAZO', 'Rechazo'),
    ('REPROGRAMADO', 'Reprogramado'),
    ('REALIZADO', 'Realizado'),
    ('SUSPENDIDA', 'Suspendida'),
]


WORKFLOW_STATUS_CHOICES = [
    (PENDING_PROVIDER_STATUS, 'Pendiente envio prestador'),
    (PENDING_PRESTADOR_STATUS, 'Pendiente prestador'),
    (PENDING_MEDICO_STATUS, 'Pendiente medico'),
    (PENDING_PACIENTE_STATUS, 'Pendiente paciente'),
    ('AUTORIZADO', 'Autorizado'),
    ('MATERIAL_PENDIENTE', 'Material pendiente'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0027_pending_prestador_status'),
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
        migrations.AlterField(
            model_name='quirofanoentry',
            name='manual_workflow_status',
            field=models.CharField(
                blank=True,
                choices=WORKFLOW_STATUS_CHOICES,
                default='',
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='quirofanoentry',
            name='resolved_workflow_status',
            field=models.CharField(
                blank=True,
                choices=WORKFLOW_STATUS_CHOICES,
                default='',
                max_length=30,
            ),
        ),
    ]
