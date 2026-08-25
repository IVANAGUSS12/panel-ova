from django.db import migrations, models


OLD_STATUS = 'RECHAZO'
NEW_STATUS = 'RECHAZO_COBERTURA'
PENDING_PROVIDER_STATUS = 'PENDIENTE_ENVIO_PRESTADOR'
PENDING_PRESTADOR_STATUS = 'PENDIENTE_PRESTADOR'
PENDING_MEDICO_STATUS = 'PENDIENTE_MEDICO'
PENDING_PACIENTE_STATUS = 'PENDIENTE_PACIENTE'
PENDING_COMMERCIAL_STATUS = 'PEND_COMERCIAL_PRESUPUESTO'
AUTHORIZED_MATERIAL_STATUS = 'AUTORIZADO_MATERIAL_PEND'


PATIENT_STATUS_CHOICES = [
    (PENDING_PROVIDER_STATUS, 'Pendiente envio prestador'),
    (PENDING_PRESTADOR_STATUS, 'Pendiente prestador'),
    (PENDING_MEDICO_STATUS, 'Pendiente medico'),
    (PENDING_PACIENTE_STATUS, 'Pendiente paciente'),
    ('AUTORIZADO', 'Autorizado'),
    (PENDING_COMMERCIAL_STATUS, 'Pendiente comercial - presupuesto'),
    (AUTHORIZED_MATERIAL_STATUS, 'Autorizado - material pendiente'),
    (NEW_STATUS, 'Rechazo cobertura'),
    ('REPROGRAMADO', 'Reprogramado'),
    ('REALIZADO', 'Realizado'),
    ('SUSPENDIDA', 'Suspendida'),
]


def forwards(apps, schema_editor):
    Patient = apps.get_model('core', 'Patient')
    Patient.objects.filter(status=OLD_STATUS).update(status=NEW_STATUS)


def backwards(apps, schema_editor):
    Patient = apps.get_model('core', 'Patient')
    Patient.objects.filter(status=NEW_STATUS).update(status=OLD_STATUS)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0029_autorizado_material_pendiente_status'),
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
        migrations.RunPython(forwards, backwards),
    ]
