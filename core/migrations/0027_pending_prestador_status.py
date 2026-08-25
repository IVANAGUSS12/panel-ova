from django.db import migrations, models


OLD_STATUS = 'SOLICITADO'
NEW_STATUS = 'PENDIENTE_PRESTADOR'
PENDING_PROVIDER_STATUS = 'PENDIENTE_ENVIO_PRESTADOR'
PENDING_COMMERCIAL_STATUS = 'PEND_COMERCIAL_PRESUPUESTO'


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
        ('core', '0026_pending_comercial_presupuesto_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='patient',
            name='status',
            field=models.CharField(
                choices=[
                    (PENDING_PROVIDER_STATUS, 'Pendiente envio prestador'),
                    (NEW_STATUS, 'Pendiente prestador'),
                    ('AUTORIZADO', 'Autorizado'),
                    (PENDING_COMMERCIAL_STATUS, 'Pendiente comercial - presupuesto'),
                    ('MATERIAL_PENDIENTE', 'Material pendiente'),
                    ('RECHAZO', 'Rechazo'),
                    ('REPROGRAMADO', 'Reprogramado'),
                    ('REALIZADO', 'Realizado'),
                    ('SUSPENDIDA', 'Suspendida'),
                ],
                default=PENDING_PROVIDER_STATUS,
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='patient',
            name='solicitado_since',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Pendiente prestador desde'),
        ),
        migrations.AlterField(
            model_name='quirofanoentry',
            name='manual_workflow_status',
            field=models.CharField(
                blank=True,
                choices=[
                    (PENDING_PROVIDER_STATUS, 'Pendiente envio prestador'),
                    (NEW_STATUS, 'Pendiente prestador'),
                    ('AUTORIZADO', 'Autorizado'),
                    ('MATERIAL_PENDIENTE', 'Material pendiente'),
                ],
                default='',
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='quirofanoentry',
            name='resolved_workflow_status',
            field=models.CharField(
                blank=True,
                choices=[
                    (PENDING_PROVIDER_STATUS, 'Pendiente envio prestador'),
                    (NEW_STATUS, 'Pendiente prestador'),
                    ('AUTORIZADO', 'Autorizado'),
                    ('MATERIAL_PENDIENTE', 'Material pendiente'),
                ],
                default='',
                max_length=30,
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
