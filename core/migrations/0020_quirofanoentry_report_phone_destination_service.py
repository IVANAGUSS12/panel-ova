from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_remove_reconciliation_tables'),
    ]

    operations = [
        migrations.AddField(
            model_name='quirofanoentry',
            name='destination_service',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='quirofanoentry',
            name='report_phone',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
    ]