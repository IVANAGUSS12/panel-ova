from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_quirofanosnapshot_quirofanoentry'),
    ]

    operations = [
        migrations.AddField(
            model_name='quirofanoentry',
            name='comparison_status',
            field=models.CharField(choices=[('BOTH', 'En ambos'), ('ONLY_QUIROFANO', 'Solo quirófano'), ('ONLY_APP', 'Solo app')], db_index=True, default='ONLY_QUIROFANO', max_length=20),
        ),
        migrations.AddField(
            model_name='quirofanoentry',
            name='manual_comparison_status',
            field=models.CharField(blank=True, choices=[('BOTH', 'En ambos'), ('ONLY_QUIROFANO', 'Solo quirófano'), ('ONLY_APP', 'Solo app')], default='', max_length=20),
        ),
        migrations.AddField(
            model_name='quirofanoentry',
            name='resolved_comparison_status',
            field=models.CharField(choices=[('BOTH', 'En ambos'), ('ONLY_QUIROFANO', 'Solo quirófano'), ('ONLY_APP', 'Solo app')], db_index=True, default='ONLY_QUIROFANO', max_length=20),
        ),
    ]