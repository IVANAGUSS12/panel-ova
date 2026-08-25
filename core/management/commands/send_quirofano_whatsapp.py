from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


AUTHENTICATED_SELECTORS = [
    '[data-testid="chat-list-search"]',
    '[data-testid="chat-list"]',
    '[data-testid="conversation-panel-wrapper"]',
    'div[role="textbox"]',
]

SEND_BUTTON_SELECTORS = [
    '[data-testid="compose-btn-send"]',
    'button[aria-label="Enviar"]',
    'button[aria-label="Send"]',
    'span[data-icon="send"]',
]

ATTACH_BUTTON_SELECTORS = [
    'button[aria-label="Adjuntar"]',
    'button[aria-label="Attach"]',
    'div[title="Adjuntar"]',
    'span[data-icon="plus"]',
    'span[data-icon="clip"]',
]

FILE_INPUT_SELECTORS = [
    'input[type="file"][accept*="pdf"]',
    'input[type="file"]',
]

ERROR_TEXT_MARKERS = [
    'número de teléfono compartido a través de la dirección URL no es válido',
    'phone number shared via url is invalid',
    'no existe ningún usuario con ese número',
    'no phone number shared',
]


class Command(BaseCommand):
    help = 'Envía mensajes de quirófano por WhatsApp Web usando Playwright y una cola JSON.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-file', required=True)
        parser.add_argument('--profile-dir', default='')
        parser.add_argument('--load-timeout-ms', type=int, default=25000)
        parser.add_argument('--post-send-delay-ms', type=int, default=2500)
        parser.add_argument('--between-send-delay-ms', type=int, default=4000)
        parser.add_argument('--login-timeout-ms', type=int, default=180000)

    def handle(self, *args, **options):
        batch_path = Path(options['batch_file']).resolve()
        if not batch_path.exists():
            raise CommandError(f'No existe el archivo de lote: {batch_path}')

        payload = json.loads(batch_path.read_text(encoding='utf-8'))
        items = payload.get('items') or []
        if not items:
            raise CommandError('El lote no contiene pacientes para enviar.')

        profile_dir = Path(options['profile_dir']).resolve() if options['profile_dir'] else Path(settings.BASE_DIR) / '.automation' / 'whatsapp_profile'
        profile_dir.mkdir(parents=True, exist_ok=True)
        result_path = batch_path.with_name(f'{batch_path.stem}_result.json')

        self.stdout.write(self.style.NOTICE(f'Usando perfil de WhatsApp Web en: {profile_dir}'))
        self.stdout.write(self.style.NOTICE(f'Pacientes a enviar: {len(items)}'))

        results = []
        with sync_playwright() as playwright:
            context = self._launch_browser(playwright, profile_dir)
            page = context.pages[0] if context.pages else context.new_page()

            self._ensure_authenticated(page, options['load_timeout_ms'], options['login_timeout_ms'])

            for index, item in enumerate(items, start=1):
                self.stdout.write(f'[{index}/{len(items)}] Enviando a {item.get("patient_name") or "Paciente"}...')
                result = self._send_item(
                    page=page,
                    item=item,
                    load_timeout_ms=options['load_timeout_ms'],
                    post_send_delay_ms=options['post_send_delay_ms'],
                )
                results.append(result)
                if index < len(items):
                    page.wait_for_timeout(options['between_send_delay_ms'])

            context.close()

        result_payload = {
            'batch_file': str(batch_path),
            'results': results,
        }
        result_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'Proceso finalizado. Resultado: {result_path}'))

    def _launch_browser(self, playwright, profile_dir: Path):
        launch_errors = []
        for channel in ('msedge', 'chrome', None):
            try:
                kwargs = {
                    'user_data_dir': str(profile_dir),
                    'headless': False,
                    'viewport': None,
                    'args': ['--start-maximized'],
                }
                if channel:
                    kwargs['channel'] = channel
                return playwright.chromium.launch_persistent_context(**kwargs)
            except Exception as exc:
                launch_errors.append(f'{channel or "chromium"}: {exc}')

        raise CommandError(
            'No se pudo abrir un navegador compatible para WhatsApp Web. '
            'Probé Edge, Chrome y Chromium. Detalle: ' + ' | '.join(launch_errors)
        )

    def _ensure_authenticated(self, page, load_timeout_ms: int, login_timeout_ms: int):
        page.goto('https://web.whatsapp.com/', wait_until='domcontentloaded', timeout=load_timeout_ms)
        if self._has_authenticated_ui(page):
            return

        self.stdout.write(self.style.WARNING('Esperando sesión activa en WhatsApp Web. Si aparece QR, escanealo en esta ventana.'))
        try:
            page.wait_for_selector(', '.join(AUTHENTICATED_SELECTORS), timeout=login_timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise CommandError(
                'No se detectó una sesión iniciada en WhatsApp Web dentro del tiempo esperado. '
                'Abrí el navegador lanzado por el proceso y escaneá el QR, luego reintentá.'
            ) from exc

    def _has_authenticated_ui(self, page) -> bool:
        for selector in AUTHENTICATED_SELECTORS:
            try:
                if page.locator(selector).first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _send_item(self, page, item: dict, load_timeout_ms: int, post_send_delay_ms: int) -> dict:
        phone = item.get('phone', '')
        message = item.get('message', '')
        patient_name = item.get('patient_name', '')
        attachment_paths = self._valid_attachment_paths(item.get('attachment_paths') or [])
        url = f'https://web.whatsapp.com/send/?phone={phone}&text={quote(message)}&type=phone_number&app_absent=1'

        try:
            page.goto(url, wait_until='domcontentloaded', timeout=load_timeout_ms)
            try:
                page.wait_for_load_state('networkidle', timeout=min(load_timeout_ms, 12000))
            except PlaywrightTimeoutError:
                pass

            send_button = page.locator(', '.join(SEND_BUTTON_SELECTORS)).first
            send_button.wait_for(state='visible', timeout=load_timeout_ms)
            page.wait_for_timeout(1200)
            send_button.click(timeout=load_timeout_ms)
            page.wait_for_timeout(post_send_delay_ms)
            attachment_results = self._send_attachments(page, attachment_paths, load_timeout_ms, post_send_delay_ms)
            return {
                'entry_id': item.get('entry_id'),
                'patient_name': patient_name,
                'phone': phone,
                'status': 'sent',
                'attachments': attachment_results,
            }
        except PlaywrightTimeoutError:
            body_text = ''
            try:
                body_text = (page.locator('body').inner_text(timeout=3000) or '').lower()
            except Exception:
                body_text = ''

            error_reason = 'timeout'
            if any(marker in body_text for marker in ERROR_TEXT_MARKERS):
                error_reason = 'invalid_phone_or_chat_unavailable'

            return {
                'entry_id': item.get('entry_id'),
                'patient_name': patient_name,
                'phone': phone,
                'status': 'failed',
                'reason': error_reason,
            }

    def _valid_attachment_paths(self, raw_paths) -> list[Path]:
        valid_paths = []
        for raw_path in raw_paths:
            try:
                path = Path(str(raw_path)).resolve()
            except Exception:
                continue
            if path.exists() and path.is_file():
                valid_paths.append(path)
        return valid_paths

    def _send_attachments(self, page, attachment_paths: list[Path], load_timeout_ms: int, post_send_delay_ms: int) -> list[dict]:
        results = []
        for attachment_path in attachment_paths:
            try:
                self._open_attach_menu(page, load_timeout_ms)
                file_input = self._find_file_input(page, load_timeout_ms)
                file_input.set_input_files(str(attachment_path), timeout=load_timeout_ms)
                page.wait_for_timeout(1200)
                send_button = page.locator(', '.join(SEND_BUTTON_SELECTORS)).last
                send_button.wait_for(state='visible', timeout=load_timeout_ms)
                send_button.click(timeout=load_timeout_ms)
                page.wait_for_timeout(post_send_delay_ms)
                results.append({'path': str(attachment_path), 'status': 'sent'})
            except Exception as exc:
                results.append({'path': str(attachment_path), 'status': 'failed', 'reason': str(exc)})
        return results

    def _open_attach_menu(self, page, load_timeout_ms: int):
        for selector in ATTACH_BUTTON_SELECTORS:
            locator = page.locator(selector).first
            try:
                if locator.is_visible(timeout=1200):
                    locator.click(timeout=load_timeout_ms)
                    page.wait_for_timeout(600)
                    return
            except Exception:
                continue
        raise PlaywrightTimeoutError('No se encontro el boton para adjuntar archivos.')

    def _find_file_input(self, page, load_timeout_ms: int):
        for selector in FILE_INPUT_SELECTORS:
            locator = page.locator(selector).last
            try:
                locator.wait_for(state='attached', timeout=load_timeout_ms)
                return locator
            except Exception:
                continue
        raise PlaywrightTimeoutError('No se encontro el input de archivo para adjuntar el PDF.')
