from django.db import migrations, models


OLD_STATUS = 'PENDIENTE'
NEW_STATUS = 'PENDIENTE_ENVIO_PRESTADOR'


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
        ('core', '0024_patient_status_since'),
    ]

    operations = [
        migrations.AlterField(
            model_name='patient',
            name='status',
            field=models.CharField(
                choices=[
                    (NEW_STATUS, 'Pendiente envio prestador'),
                    ('SOLICITADO', 'Solicitado'),
                    ('AUTORIZADO', 'Autorizado'),
                    ('PRESUPUESTO_SI', 'Presupuesto sí'),
                    ('MATERIAL_PENDIENTE', 'Material pendiente'),
                    ('RECHAZO', 'Rechazo'),
                    ('REPROGRAMADO', 'Reprogramado'),
                    ('REALIZADO', 'Realizado'),
                    ('SUSPENDIDA', 'Suspendida'),
                ],
                default=NEW_STATUS,
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='quirofanoentry',
            name='manual_workflow_status',
            field=models.CharField(
                blank=True,
                choices=[
                    (NEW_STATUS, 'Pendiente envio prestador'),
                    ('SOLICITADO', 'Solicitado'),
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
                    (NEW_STATUS, 'Pendiente envio prestador'),
                    ('SOLICITADO', 'Solicitado'),
                    ('AUTORIZADO', 'Autorizado'),
                    ('MATERIAL_PENDIENTE', 'Material pendiente'),
                ],
                default='',
                max_length=30,
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
