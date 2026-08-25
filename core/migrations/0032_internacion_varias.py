from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0031_cancela_medico_pte_statuses'),
    ]

    operations = [
        migrations.CreateModel(
            name='InternacionVarias',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fecha', models.DateField(db_index=True)),
                ('paciente_nombre', models.CharField(max_length=255)),
                ('paciente_dni', models.CharField(blank=True, default='', max_length=50)),
                ('edad', models.CharField(blank=True, default='', max_length=30)),
                ('obra_social', models.CharField(blank=True, default='', max_length=255)),
                ('motivo', models.CharField(blank=True, default='', max_length=255)),
                ('servicio_solicitante', models.CharField(blank=True, default='', max_length=255)),
                ('medico_responsable', models.CharField(blank=True, default='', max_length=255)),
                ('cama_asignada', models.CharField(blank=True, default='', max_length=100)),
                ('destino', models.CharField(blank=True, default='', max_length=255)),
                ('telefono', models.CharField(blank=True, default='', max_length=80)),
                ('observaciones', models.TextField(blank=True, default='')),
                ('origen_registro', models.CharField(choices=[('manual', 'Manual'), ('automatico_intervencion', 'Automatico por intervencion'), ('automatico_cirujano', 'Automatico por cirujano')], default='manual', max_length=40)),
                ('cirugia_origen_id', models.CharField(blank=True, default='', max_length=255)),
                ('motivo_automatico', models.CharField(blank=True, default='', max_length=255)),
                ('fecha_cirugia_original', models.DateField(blank=True, null=True)),
                ('horario_cirugia_original', models.CharField(blank=True, default='', max_length=20)),
                ('intervencion_original', models.CharField(blank=True, default='', max_length=255)),
                ('cirujano_original', models.CharField(blank=True, default='', max_length=255)),
                ('automatic_signature', models.CharField(blank=True, db_index=True, default='', max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Internacion varias',
                'verbose_name_plural': 'Internaciones varias',
                'ordering': ['fecha', 'paciente_nombre'],
            },
        ),
        migrations.AddIndex(
            model_name='internacionvarias',
            index=models.Index(fields=['fecha', 'origen_registro'], name='intern_varias_fecha_origen_idx'),
        ),
        migrations.AddIndex(
            model_name='internacionvarias',
            index=models.Index(fields=['cirugia_origen_id'], name='intern_varias_cirugia_idx'),
        ),
        migrations.AddConstraint(
            model_name='internacionvarias',
            constraint=models.UniqueConstraint(condition=~models.Q(automatic_signature=''), fields=('automatic_signature',), name='uniq_intern_varias_auto_signature'),
        ),
    ]
