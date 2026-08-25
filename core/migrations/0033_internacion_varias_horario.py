from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_internacion_varias'),
    ]

    operations = [
        migrations.AddField(
            model_name='internacionvarias',
            name='horario',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.AlterModelOptions(
            name='internacionvarias',
            options={'ordering': ['fecha', 'horario', 'paciente_nombre'], 'verbose_name': 'Internacion varias', 'verbose_name_plural': 'Internaciones varias'},
        ),
    ]
