from django.db import migrations, models


OLD_STATUS = 'MATERIAL_PENDIENTE'
NEW_STATUS = 'AUTORIZADO_MATERIAL_PEND'
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
    (NEW_STATUS, 'Autorizado - material pendiente'),
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
    (NEW_STATUS, 'Autorizado - material pendiente'),
]


def forwards(apps, schema_editor):
    Patient = apps.get_model('core', 'Patient')
    Patient.objects.filter(status=OLD_STATUS).update(status=NEW_STATUS)

    QuirofanoEntry = apps.get_model('core', 'QuirofanoEntry')
    QuirofanoEntry.objects.filter(manual_workflow_status=OLD_STATUS).update(manual_workflow_status=NEW_STATUS)
    QuirofanoEntry.objects.filter(resolved_workflow_status=OLD_STATUS).update(resolved_workflow_status=NEW_STATUS)


def backwards(apps, schema_editor):
    Patient = apps.get_model('core', 'Patient')
    Patient.objects.filter(status=NEW_STATUS).update(status=OLD_STATUS)

    QuirofanoEntry = apps.get_model('core', 'QuirofanoEntry')
    QuirofanoEntry.objects.filter(manual_workflow_status=NEW_STATUS).update(manual_workflow_status=OLD_STATUS)
    QuirofanoEntry.objects.filter(resolved_workflow_status=NEW_STATUS).update(resolved_workflow_status=OLD_STATUS)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0028_pending_medico_paciente_statuses'),
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
        migrations.RunPython(forwards, backwards),
    ]
