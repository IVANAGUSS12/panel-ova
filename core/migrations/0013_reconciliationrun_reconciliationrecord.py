from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_patient_en_quirofano_patient_material_status_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ReconciliationRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date_from', models.DateField(verbose_name='Fecha desde')),
                ('date_to', models.DateField(verbose_name='Fecha hasta')),
                ('source_files', models.JSONField(blank=True, default=list, verbose_name='Archivos origen')),
                ('total_report_rows', models.PositiveIntegerField(default=0, verbose_name='Filas del reporte')),
                ('matched_count', models.PositiveIntegerField(default=0, verbose_name='Coincidencias')),
                ('only_report_count', models.PositiveIntegerField(default=0, verbose_name='Solo reporte')),
                ('only_app_count', models.PositiveIntegerField(default=0, verbose_name='Solo app')),
                ('skipped_count', models.PositiveIntegerField(default=0, verbose_name='Filas omitidas')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Creado')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reconciliation_runs', to=settings.AUTH_USER_MODEL, verbose_name='Creado por')),
            ],
            options={
                'verbose_name': 'Corrida de conciliación',
                'verbose_name_plural': 'Corridas de conciliación',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='ReconciliationRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('result', models.CharField(choices=[('MATCH', 'En app y reporte'), ('ONLY_REPORT', 'Solo reporte'), ('ONLY_APP', 'Solo app')], max_length=20)),
                ('sede', models.CharField(blank=True, default='', max_length=50)),
                ('surgery_date', models.DateField(verbose_name='Fecha de cirugía')),
                ('canonical_service', models.CharField(max_length=255, verbose_name='Especialidad QR')),
                ('report_patient_name', models.CharField(blank=True, default='', max_length=255)),
                ('report_dni', models.CharField(blank=True, default='', max_length=50)),
                ('report_coverage', models.CharField(blank=True, default='', max_length=255)),
                ('report_doctor', models.CharField(blank=True, default='', max_length=255)),
                ('report_specialty', models.CharField(blank=True, default='', max_length=255)),
                ('report_origin', models.CharField(blank=True, default='', max_length=255)),
                ('report_source_file', models.CharField(blank=True, default='', max_length=255)),
                ('app_patient_name', models.CharField(blank=True, default='', max_length=255)),
                ('app_dni', models.CharField(blank=True, default='', max_length=50)),
                ('app_coverage', models.CharField(blank=True, default='', max_length=255)),
                ('app_doctor', models.CharField(blank=True, default='', max_length=255)),
                ('app_service', models.CharField(blank=True, default='', max_length=255)),
                ('app_status', models.CharField(blank=True, default='', max_length=30)),
                ('match_reason', models.CharField(blank=True, default='', max_length=255)),
                ('notes', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('patient', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reconciliation_records', to='core.patient')),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='records', to='core.reconciliationrun')),
            ],
            options={
                'verbose_name': 'Registro de conciliación',
                'verbose_name_plural': 'Registros de conciliación',
                'ordering': ['surgery_date', 'sede', 'canonical_service', 'report_patient_name', 'app_patient_name'],
            },
        ),
        migrations.AddIndex(
            model_name='reconciliationrecord',
            index=models.Index(fields=['run', 'result'], name='recon_run_result_idx'),
        ),
        migrations.AddIndex(
            model_name='reconciliationrecord',
            index=models.Index(fields=['run', 'sede'], name='recon_run_sede_idx'),
        ),
        migrations.AddIndex(
            model_name='reconciliationrecord',
            index=models.Index(fields=['run', 'surgery_date'], name='recon_run_date_idx'),
        ),
    ]