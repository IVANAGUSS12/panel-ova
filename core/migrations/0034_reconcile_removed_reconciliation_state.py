from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0033_internacion_varias_horario'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name='reconciliationrun',
                    name='created_by',
                ),
                migrations.DeleteModel(
                    name='ReconciliationRecord',
                ),
                migrations.DeleteModel(
                    name='ReconciliationRun',
                ),
            ],
        ),
    ]
