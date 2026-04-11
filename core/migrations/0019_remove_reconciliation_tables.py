# Generated manually

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_remove_quirofano_gestion_fields'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                'DROP TABLE IF EXISTS core_reconciliationrecord;',
                'DROP TABLE IF EXISTS core_reconciliationrun;',
            ],
            reverse_sql=[
                # No hay reverse - las tablas se eliminan permanentemente
            ],
        ),
    ]
