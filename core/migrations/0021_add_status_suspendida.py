from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_quirofanoentry_report_phone_destination_service'),
    ]

    operations = [
        migrations.AlterField(
            model_name='patient',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDIENTE', 'Pendiente'),
                    ('SOLICITADO', 'Solicitado'),
                    ('AUTORIZADO', 'Autorizado'),
                    ('PRESUPUESTO_SI', 'Presupuesto sí'),
                    ('MATERIAL_PENDIENTE', 'Material pendiente'),
                    ('RECHAZO', 'Rechazo'),
                    ('REPROGRAMADO', 'Reprogramado'),
                    ('REALIZADO', 'Realizado'),
                    ('SUSPENDIDA', 'Suspendida'),
                ],
                default='PENDIENTE',
                max_length=30,
            ),
        ),
    ]
