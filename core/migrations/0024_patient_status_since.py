from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_patient_solicitado_since'),
    ]

    operations = [
        migrations.AddField(
            model_name='patient',
            name='status_since',
            field=models.DateTimeField(blank=True, null=True, verbose_name='En estado actual desde'),
        ),
    ]
