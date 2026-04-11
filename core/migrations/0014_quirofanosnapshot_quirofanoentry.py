from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_reconciliationrun_reconciliationrecord'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='QuirofanoSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('window_start', models.DateField(verbose_name='Fecha desde')),
                ('window_end', models.DateField(verbose_name='Fecha hasta')),
                ('source_files', models.JSONField(blank=True, default=list, verbose_name='Archivos origen')),
                ('total_entries', models.PositiveIntegerField(default=0, verbose_name='Entradas actuales')),
                ('new_count', models.PositiveIntegerField(default=0, verbose_name='Nuevos')),
                ('changed_count', models.PositiveIntegerField(default=0, verbose_name='Cambiados')),
                ('unchanged_count', models.PositiveIntegerField(default=0, verbose_name='Sin cambios')),
                ('removed_count', models.PositiveIntegerField(default=0, verbose_name='Quitados')),
                ('imported_at', models.DateTimeField(auto_now_add=True, verbose_name='Importado')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='quirofano_snapshots', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Snapshot de quirófano',
                'verbose_name_plural': 'Snapshots de quirófano',
                'ordering': ['-imported_at'],
            },
        ),
        migrations.CreateModel(
            name='QuirofanoEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('change_type', models.CharField(choices=[('NEW', 'Nuevo'), ('CHANGED', 'Cambiado'), ('UNCHANGED', 'Sin cambios'), ('REMOVED', 'Quitado')], db_index=True, max_length=16)),
                ('match_key', models.CharField(blank=True, db_index=True, default='', max_length=255)),
                ('sede', models.CharField(blank=True, default='', max_length=50)),
                ('surgery_date', models.DateField(verbose_name='Fecha de cirugía')),
                ('surgery_time', models.TimeField(blank=True, null=True, verbose_name='Hora programada')),
                ('patient_name', models.CharField(blank=True, default='', max_length=255)),
                ('patient_name_norm', models.CharField(blank=True, db_index=True, default='', max_length=255)),
                ('coverage', models.CharField(blank=True, default='', max_length=255)),
                ('dni', models.CharField(blank=True, default='', max_length=50)),
                ('doctor', models.CharField(blank=True, default='', max_length=255)),
                ('doctor_norm', models.CharField(blank=True, default='', max_length=255)),
                ('specialty_raw', models.CharField(blank=True, default='', max_length=255)),
                ('canonical_service', models.CharField(blank=True, db_index=True, default='', max_length=255)),
                ('origin', models.CharField(blank=True, default='', max_length=255)),
                ('source_file', models.CharField(blank=True, default='', max_length=255)),
                ('source_row_number', models.PositiveIntegerField(default=0)),
                ('previous_sede', models.CharField(blank=True, default='', max_length=50)),
                ('previous_surgery_date', models.DateField(blank=True, null=True)),
                ('previous_surgery_time', models.TimeField(blank=True, null=True)),
                ('previous_doctor', models.CharField(blank=True, default='', max_length=255)),
                ('previous_coverage', models.CharField(blank=True, default='', max_length=255)),
                ('previous_specialty_raw', models.CharField(blank=True, default='', max_length=255)),
                ('previous_origin', models.CharField(blank=True, default='', max_length=255)),
                ('imported_at', models.DateTimeField(auto_now_add=True)),
                ('app_patient', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='quirofano_entries', to='core.patient')),
                ('snapshot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entries', to='core.quirofanosnapshot')),
            ],
            options={
                'verbose_name': 'Entrada de quirófano',
                'verbose_name_plural': 'Entradas de quirófano',
                'ordering': ['surgery_date', 'surgery_time', 'sede', 'patient_name'],
            },
        ),
        migrations.AddIndex(
            model_name='quirofanoentry',
            index=models.Index(fields=['snapshot', 'change_type'], name='quirof_snapshot_change_idx'),
        ),
        migrations.AddIndex(
            model_name='quirofanoentry',
            index=models.Index(fields=['snapshot', 'surgery_date'], name='quirof_snapshot_date_idx'),
        ),
        migrations.AddIndex(
            model_name='quirofanoentry',
            index=models.Index(fields=['snapshot', 'sede'], name='quirof_snapshot_sede_idx'),
        ),
    ]