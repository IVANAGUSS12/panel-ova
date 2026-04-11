from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0015_quirofanoentry_comparison_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='quirofanoentry',
            name='manual_workflow_status',
            field=models.CharField(blank=True, choices=[('PENDIENTE', 'Pendiente'), ('SOLICITADO', 'Solicitado'), ('AUTORIZADO', 'Autorizado'), ('MATERIAL_PENDIENTE', 'Material pendiente')], default='', max_length=30),
        ),
        migrations.AddField(
            model_name='quirofanoentry',
            name='resolved_workflow_status',
            field=models.CharField(blank=True, choices=[('PENDIENTE', 'Pendiente'), ('SOLICITADO', 'Solicitado'), ('AUTORIZADO', 'Autorizado'), ('MATERIAL_PENDIENTE', 'Material pendiente')], default='', max_length=30),
        ),
    ]