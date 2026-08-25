from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0022_add_impreso_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='patient',
            name='solicitado_since',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Solicitado desde'),
        ),
    ]
