from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from core.models import Attachment
import io
from PIL import Image


class Command(BaseCommand):
    help = 'Genera miniaturas WEBP para adjuntos existentes (sufijo _thumb.webp)'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Regenerar miniaturas aunque existan')
        parser.add_argument('--limit', type=int, default=0, help='Procesar solo N archivos (0 = todos)')

    def handle(self, *args, **options):
        force = options['force']
        limit = options['limit']

        qs = Attachment.objects.all().order_by('-uploaded_at')
        total = qs.count()
        self.stdout.write(f'Found {total} attachments')

        processed = 0
        for att in qs:
            if limit and processed >= limit:
                break
            try:
                name = att.file.name.lower()
                if not any(name.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif')):
                    self.stdout.write(f'SKIP (not image): {att.file.name}')
                    continue

                base, _ = att.file.name.rsplit('.', 1)
                thumb_path = f"{base}_thumb.webp"
                if default_storage.exists(thumb_path) and not force:
                    self.stdout.write(f'EXISTS: {thumb_path}')
                    processed += 1
                    continue

                with default_storage.open(att.file.name, 'rb') as f:
                    img = Image.open(f)
                    img.thumbnail((1200, 1200), Image.LANCZOS)
                    if img.mode in ('RGBA', 'P'):
                        img = img.convert('RGB')

                    buf = io.BytesIO()
                    img.save(buf, format='WEBP', quality=75, method=6)
                    buf.seek(0)

                    if default_storage.exists(thumb_path):
                        default_storage.delete(thumb_path)
                    default_storage.save(thumb_path, ContentFile(buf.read()))
                    self.stdout.write(f'CREATED: {thumb_path}')
                    processed += 1
            except Exception as e:
                self.stderr.write(f'ERROR processing {att.file.name}: {e}')
        self.stdout.write(self.style.SUCCESS(f'Done. Processed: {processed}'))
