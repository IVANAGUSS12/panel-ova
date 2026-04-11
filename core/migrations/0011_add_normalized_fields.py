"""Add normalized fields for Patient search

Generated manually: añade `full_name_norm` y `dni_norm`.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_alter_patient_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='patient',
            name='full_name_norm',
            field=models.CharField(blank=True, db_index=True, max_length=255),
        ),
        migrations.AddField(
            model_name='patient',
            name='dni_norm',
            field=models.CharField(blank=True, db_index=True, max_length=50),
        ),
    ]
