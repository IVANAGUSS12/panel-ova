from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
import re


# Spanish month names used by the daterangepicker calendar UI
_MONTHS_ES = {
    1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
    5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
    9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre',
}


@dataclass(frozen=True)
class QRDownloadSettings:
    login_url: str
    username: str
    password: str
    headless: bool


SEDE_OPTIONS = {
    'SAAVEDRA': 'Saavedra',
    'POMBO': 'POMBO',
    'LAS HERAS': 'Av Las Heras',
}


def _env_truthy(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {'0', 'false', 'no', 'off'}


def get_qr_download_settings(username: str | None = None, password: str | None = None) -> QRDownloadSettings:
    username = (username or os.getenv('QR_REPORT_USER') or os.getenv('QR_REPORT_USERNAME') or '').strip()
    password = (password or os.getenv('QR_REPORT_PASSWORD') or '').strip()
    if not username or not password:
        raise RuntimeError(
            'Falta usuario y/o contraseña QR. Cargalos en el formulario o en '
            'QR_REPORT_USER / QR_REPORT_PASSWORD.'
        )
    return QRDownloadSettings(
        login_url=(os.getenv('QR_REPORT_LOGIN_URL') or 'http://quirofanos.cemic.edu.ar/login.php').strip(),
        username=username,
        password=password,
        headless=_env_truthy('QR_PLAYWRIGHT_HEADLESS', default=True),
    )


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', value.strip())
    return cleaned.strip('._') or 'reporte.xlsx'


def _login_for_sede(page, settings: QRDownloadSettings, sede_label: str) -> None:
    """Exact recorded flow: fill credentials, select sede in select2, submit."""
    page.goto(settings.login_url, wait_until='domcontentloaded', timeout=30_000)
    page.get_by_role('textbox', name='Usuario').fill(settings.username, timeout=10_000)
    page.get_by_role('textbox', name='Contraseña').fill(settings.password, timeout=10_000)
    # Open the sede select2 dropdown and pick the right option
    page.locator('#select2-idsanatorio-container').click(timeout=10_000)
    try:
        page.get_by_role('option', name=sede_label).click(timeout=10_000)
    except Exception:
        page.locator('.select2-results__option').filter(has_text=sede_label).first.click(timeout=10_000)
    page.get_by_role('button', name=re.compile('Iniciar Sesión', re.I)).click(timeout=15_000)
    page.wait_for_load_state('networkidle', timeout=30_000)


def _navigate_to_reports(page) -> None:
    """Click Perfil dropdown > Reportes.

    The 'Perfil' element is a Bootstrap dropdown toggle — it may render as
    <a data-toggle="dropdown"> which Playwright does NOT classify as role='link'.
    We therefore target it with a CSS text locator, which works regardless of
    the underlying HTML element type.
    """
    # Wait for the navbar to become interactive
    page.wait_for_selector('a, button, li, span', timeout=15_000)
    # Try the most specific selector first, then progressively widen
    for selector in (
        'a:text("Perfil")',
        'a:text-matches("Perfil", "i")',
        ':is(a, button, li, span):text-matches("Perfil", "i")',
    ):
        try:
            page.locator(selector).first.click(timeout=8_000)
            break
        except Exception:
            continue
    # Reportes is a sub-menu item — wait for it to appear after the dropdown opens
    for selector in (
        'a:text("Reportes")',
        'a:text-matches("Reportes", "i")',
        ':is(a, li):text-matches("Reportes", "i")',
    ):
        try:
            page.locator(selector).first.click(timeout=8_000)
            break
        except Exception:
            continue
    page.wait_for_load_state('networkidle', timeout=30_000)


def _set_date_range_via_js(page, date_from: date, date_to: date) -> bool:
    """Try to set dates via the daterangepicker JS API.  Returns True on success."""
    # Use ISO 8601 so moment.js always parses correctly regardless of locale
    start_iso = date_from.strftime('%Y-%m-%d')
    end_iso = date_to.strftime('%Y-%m-%d')
    return page.evaluate(
        """
        ({ startIso, endIso }) => {
            const jq = window.jQuery || window.$;
            if (!jq) return false;
            const picker = jq('#daterange').data('daterangepicker');
            if (!picker) return false;
            const mk = (iso) => window.moment ? window.moment(iso, 'YYYY-MM-DD') : iso;
            picker.setStartDate(mk(startIso));
            picker.setEndDate(mk(endIso));
            jq('#daterange').trigger('change');
            return true;
        }
        """,
        {'startIso': start_iso, 'endIso': end_iso},
    )


def _set_date_range_via_calendar(page, date_from: date, date_to: date) -> None:
    """Fallback: drive the calendar UI to pick start and end dates."""
    page.locator('#daterange').click()
    page.wait_for_timeout(600)

    # Navigate calendar to start month and click the day
    from_month = _MONTHS_ES[date_from.month]
    page.get_by_role('columnheader', name=from_month).first.click(timeout=10_000)
    from_day = str(date_from.day)
    (
        page.locator('td.available')
        .filter(has_not_text=re.compile(r'\S{2,}'))  # exclude multi-char labels
        .filter(has_text=re.compile(f'^{from_day}$'))
        .first.click()
    )

    # Navigate calendar to end month and click the day
    to_month = _MONTHS_ES[date_to.month]
    page.get_by_role('columnheader', name=to_month).first.click(timeout=10_000)
    to_day = str(date_to.day)
    (
        page.locator('td.available')
        .filter(has_not_text=re.compile(r'\S{2,}'))
        .filter(has_text=re.compile(f'^{to_day}$'))
        .first.click()
    )


def _apply_date_range(page, date_from: date, date_to: date) -> None:
    """Set the date range (JS first, calendar UI as fallback) then click Filtrar."""
    page.wait_for_selector('#daterange', timeout=15_000)
    if not _set_date_range_via_js(page, date_from, date_to):
        _set_date_range_via_calendar(page, date_from, date_to)
    page.get_by_role('button', name=re.compile('Filtrar', re.I)).click(timeout=15_000)
    page.wait_for_load_state('networkidle', timeout=30_000)


def download_qr_reports(
    *,
    date_from: date,
    date_to: date,
    output_dir: Path,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, Path]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError('Playwright no está instalado en este entorno.') from exc

    settings = get_qr_download_settings(username=username, password=password)
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded_paths: dict[str, Path] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=settings.headless)
        try:
            for sede, sede_label in SEDE_OPTIONS.items():
                # Fresh context per sede — equivalent to logout + re-login
                context = browser.new_context(
                    accept_downloads=True,
                    locale='es-AR',
                    timezone_id='America/Argentina/Buenos_Aires',
                )
                try:
                    page = context.new_page()
                    _login_for_sede(page, settings, sede_label)
                    _navigate_to_reports(page)
                    _apply_date_range(page, date_from, date_to)

                    with page.expect_download(timeout=60_000) as download_info:
                        page.get_by_role('button', name=re.compile('Descargar Reporte', re.I)).click(timeout=15_000)

                    download = download_info.value
                    suffix = Path(download.suggested_filename or 'reporte.xlsx').suffix or '.xlsx'
                    filename = _safe_filename(f'{sede.lower()}_{date_from:%Y%m%d}_{date_to:%Y%m%d}{suffix}')
                    target_path = output_dir / filename
                    download.save_as(str(target_path))
                    downloaded_paths[sede] = target_path
                finally:
                    context.close()
        finally:
            browser.close()

    return downloaded_paths
