from django.db import migrations, models


OLD_STATUS = 'PRESUPUESTO_SI'
NEW_STATUS = 'PEND_COMERCIAL_PRESUPUESTO'
PENDING_PROVIDER_STATUS = 'PENDIENTE_ENVIO_PRESTADOR'


def forwards(apps, schema_editor):
    Patient = apps.get_model('core', 'Patient')
    Patient.objects.filter(status=OLD_STATUS).update(status=NEW_STATUS)


def backwards(apps, schema_editor):
    Patient = apps.get_model('core', 'Patient')
    Patient.objects.filter(status=NEW_STATUS).update(status=OLD_STATUS)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0025_pending_envio_prestador_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='patient',
            name='status',
            field=models.CharField(
                choices=[
                    (PENDING_PROVIDER_STATUS, 'Pendiente envio prestador'),
                    ('SOLICITADO', 'Solicitado'),
                    ('AUTORIZADO', 'Autorizado'),
                    (NEW_STATUS, 'Pendiente comercial - presupuesto'),
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
        migrations.RunPython(forwards, backwards),
    ]
