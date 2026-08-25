"""
Backend reutilizable para agendas portables de quirófano por sede.
"""

from pathlib import Path
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from contextlib import contextmanager
import openpyxl
import json
import os
import re
import calendar
import time
import unicodedata
from threading import Lock

# ── Rutas base ───────────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent

AGENDA_CONFIGS = {
    "saavedra": {
        "key": "saavedra",
        "title": "Saavedra",
        "short_title": "SQ",
        "download_label": "Saavedra",
        "report_prefix": "Saavedra",
        "excel_sede_match": "SAAVEDRA",
    },
    "las-heras": {
        "key": "las-heras",
        "title": "Las Heras",
        "short_title": "LH",
        "download_label": "Av Las Heras",
        "download_label_aliases": ["Las Heras", "LAS HERAS"],
        "report_prefix": "Las_Heras",
        "excel_sede_match": "LAS HERAS",
    },
    "pombo": {
        "key": "pombo",
        "title": "Pombo",
        "short_title": "PO",
        "download_label": "Pombo",
        "report_prefix": "Pombo",
        "excel_sede_match": "POMBO",
    },
}

_STATUS_LOCKS = {key: Lock() for key in AGENDA_CONFIGS}
_REFRESH_LEADER_FD = None

# ── CEMIC config ─────────────────────────────────────────────────────────────
URL_LOGIN = "http://quirofanos.cemic.edu.ar/login.php"
USUARIO   = os.getenv("CEMIC_USER", "")
PASSWORD  = os.getenv("CEMIC_PASS", "")

MESES_ES = {
    1: "Enero",    2: "Febrero",  3: "Marzo",     4: "Abril",
    5: "Mayo",     6: "Junio",    7: "Julio",      8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

# ── Helpers JSON ─────────────────────────────────────────────────────────────
def load_json(path: Path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None

def save_json(path: Path, data):
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _file_version(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return 0


def surgery_key(entry: dict) -> str:
    return f"{entry.get('patient', '')}|{entry.get('date', '')}|{entry.get('time', '')}|{entry.get('room', '')}"


def _norm_text(value) -> str:
    text = _s(value)
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _normalize_agenda_key(value: str | None) -> str:
    key = (value or "saavedra").strip().lower().replace("_", "-")
    return key if key in AGENDA_CONFIGS else "saavedra"


def get_agenda_config(value: str | None = None) -> dict:
    return AGENDA_CONFIGS[_normalize_agenda_key(value)]


def get_available_agendas() -> list[dict]:
    return [AGENDA_CONFIGS[key] for key in ("saavedra", "las-heras", "pombo")]


def _agenda_paths(agenda_key: str | None = None) -> dict[str, Path]:
    config = get_agenda_config(agenda_key)
    data_dir = BASE / f"agenda_{config['key'].replace('-', '_')}_data"
    downloads_dir = data_dir / "downloads"
    data_dir.mkdir(exist_ok=True)
    downloads_dir.mkdir(exist_ok=True)
    return {
        "data_dir": data_dir,
        "downloads_dir": downloads_dir,
        "current": data_dir / "current.json",
        "previous": data_dir / "previous.json",
        "status": data_dir / "status.json",
        "meta": data_dir / "meta.json",
        "movements": data_dir / "movements.json",
        "refresh_lock": data_dir / ".refresh.lock",
    }


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_refresh_leader_lock() -> bool:
    global _REFRESH_LEADER_FD

    lock_path = BASE / ".agenda_refresh_leader.lock"

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            payload = f"pid={os.getpid()} started_at={datetime.now().isoformat(timespec='seconds')}\n"
            os.write(fd, payload.encode("utf-8", errors="ignore"))
            _REFRESH_LEADER_FD = fd
            return True
        except FileExistsError:
            try:
                content = lock_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return False

            pid = 0
            for part in content.split():
                if part.startswith("pid="):
                    try:
                        pid = int(part.split("=", 1)[1])
                    except ValueError:
                        pid = 0
                    break

            if pid and _pid_is_alive(pid):
                return False

            try:
                lock_path.unlink()
            except OSError:
                return False


@contextmanager
def agenda_refresh_lock(agenda_key: str | None = None, timeout_seconds: int = 300, poll_seconds: float = 1.0):
    lock_path = _agenda_paths(agenda_key)["refresh_lock"]
    deadline = time.monotonic() + max(timeout_seconds, 1)
    lock_fd = None

    while True:
        try:
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            payload = f"pid={os.getpid()} started_at={datetime.now().isoformat(timespec='seconds')}\n"
            os.write(lock_fd, payload.encode("utf-8", errors="ignore"))
            break
        except FileExistsError:
            try:
                content = lock_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                content = ""

            lock_age_seconds = 0.0
            try:
                lock_age_seconds = max(time.time() - lock_path.stat().st_mtime, 0.0)
            except OSError:
                lock_age_seconds = 0.0

            lock_pid = 0
            has_pid_token = False
            for part in content.split():
                if part.startswith("pid="):
                    has_pid_token = True
                    try:
                        lock_pid = int(part.split("=", 1)[1])
                    except ValueError:
                        lock_pid = 0
                    break

            if lock_pid <= 0:
                # Si no hay pid válido y el lock no es reciente, se considera huérfano.
                if lock_age_seconds > 2 or not has_pid_token:
                    try:
                        lock_path.unlink()
                        continue
                    except OSError:
                        pass

            if lock_pid and not _pid_is_alive(lock_pid):
                try:
                    lock_path.unlink()
                    continue
                except OSError:
                    pass

            if time.monotonic() >= deadline:
                raise TimeoutError(f"La agenda {get_agenda_config(agenda_key)['title']} ya se está actualizando.")
            time.sleep(max(poll_seconds, 0.1))

    try:
        yield
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def _status_lock(agenda_key: str | None = None) -> Lock:
    config = get_agenda_config(agenda_key)
    return _STATUS_LOCKS[config["key"]]


def secure_filename(value: str) -> str:
    text = (value or "").strip().replace("\x00", "")
    if not text:
        return ""

    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("/", "_").replace("\\", "_")
    text = re.sub(r"[^A-Za-z0-9._ -]", "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text[:240]


def _patient_group_key(value) -> str:
    return _norm_text(value)


def _extract_schedule_parts(raw_value) -> tuple[str, str]:
    raw = _s(raw_value)
    if not raw:
        return "", ""
    date_part = _to_iso(raw)
    time_match = re.search(r"(\d{1,2}:\d{2})", raw)
    return date_part, time_match.group(1) if time_match else ""


def _parse_status_key(key: str) -> dict:
    patient, date_value, time_value, room = (key.split("|", 3) + ["", "", "", ""])[:4]
    return {
        "key": key,
        "patient": patient,
        "patient_norm": _patient_group_key(patient),
        "date": date_value,
        "time": time_value,
        "room": room,
    }


def _status_has_manual_data(status: dict | None) -> bool:
    if not isinstance(status, dict):
        return False
    if _s(status.get("obs", "")):
        return True
    for field in ("material", "auth", "budget", "order"):
        value = _s(status.get(field, ""))
        if value and value.lower() != "pendiente":
            return True
    return False


def _find_status_store_match(curr_entry: dict, store: dict) -> str | None:
    patient_norm = _patient_group_key(curr_entry.get("patient", ""))
    if not patient_norm:
        return None

    curr_date = curr_entry.get("date", "")
    curr_time = curr_entry.get("time", "")
    curr_room = curr_entry.get("room", "")
    prev_date, prev_time = _extract_schedule_parts(curr_entry.get("prev_date", ""))
    curr_rescheduled = _norm_text(curr_entry.get("rescheduled", "")) == "si"
    curr_key = surgery_key(curr_entry)

    best_key = None
    best_score = -1

    for key, status in store.items():
        if key == curr_key or not _status_has_manual_data(status):
            continue

        parsed = _parse_status_key(key)
        if parsed["patient_norm"] != patient_norm:
            continue

        score = 0
        parsed_date = parsed["date"]
        parsed_time = parsed["time"]
        parsed_room = parsed["room"]

        if prev_date and parsed["date"] == prev_date:
            score += 12
        if prev_time and parsed["time"] == prev_time:
            score += 8
        if parsed_date == curr_date:
            score += 12
        if parsed_time == curr_time:
            score += 2
        if parsed_room == curr_room:
            score += 1
        if curr_rescheduled and parsed_date == curr_date:
            score += 3

        if score > best_score:
            best_score = score
            best_key = key

    return best_key if best_score >= 12 else None


def get_data_versions(agenda_key: str | None = None) -> dict:
    paths = _agenda_paths(agenda_key)
    return {
        "current": _file_version(paths["current"]),
        "previous": _file_version(paths["previous"]),
        "status": _file_version(paths["status"]),
        "meta": _file_version(paths["meta"]),
        "movements": _file_version(paths["movements"]),
    }

# ── Parser Excel ─────────────────────────────────────────────────────────────
# ── Posiciones exactas según el Excel real de CEMIC Saavedra ─────────────────
# Fila 0 = título, Fila 1 = encabezados, datos desde Fila 2
_C = {
    "room":             0,
    "date":             1,
    "time":             2,
    "patient":          7,
    "age":              8,
    "coverage":         9,
    "dni":              10,
    "phone":            11,
    "procedure":        12,
    "procedure2":       13,
    "anesthesia":       14,
    "surgeon":          15,
    "surgeon2":         16,
    "pharmacy":         17,
    "obs_pharmacy":     18,
    "anat_pat":         19,
    "hemotherapy":      20,
    "rx":               21,
    "orthopedics":      22,
    "obs_ortho":        23,
    "destination":      24,
    "svc_destination":  25,
    "specialty":        26,
    "origin":           27,
    "emergency":        28,
    "sys_status":       29,
    "observations":     30,
    "comments":         31,
    "delay_reason":     32,
    "suspended":        33,
    "suspension_date":  34,
    "rescheduled":      35,
    "prev_date":        36,
    "sede":             37,
}

def _s(val) -> str:
    """Limpia un valor de celda a string; devuelve '' si es None/none."""
    if val is None:
        return ""
    v = str(val).strip()
    return "" if v.lower() in ("none", "nan") else v

def _to_iso(val) -> str:
    """Convierte fecha DD-MM-YYYY o YYYY-MM-DD a ISO YYYY-MM-DD."""
    v = _s(val)
    if not v:
        return ""
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(v[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return v

def _to_time(val) -> str:
    """Extrae HH:MM de un valor de celda."""
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%H:%M")
    import datetime as _dt
    if isinstance(val, _dt.time):
        return val.strftime("%H:%M")
    m = re.match(r"(\d{1,2}:\d{2})", str(val).strip())
    return m.group(1) if m else _s(val)

def _norm_room(val) -> str:
    """Normaliza 'quirofano 1' → 'Quirófano 1', 's. hemodinamia' → 'Hemodinamia'."""
    v = _s(val).lower()
    m = re.match(r"quirofano\s*(\d+)", v)
    if m:
        return f"Quirófano {m.group(1)}"
    if "hemodinam" in v:
        return "Hemodinamia"
    return _s(val).title() if val else ""

def _get(row, key):
    i = _C.get(key)
    if i is None or i >= len(row):
        return None
    return row[i]


def _normalize_dni(value) -> str:
    return re.sub(r"\D+", "", _s(value))

def _find_header_row(all_rows: list) -> int:
    """Busca la fila que tenga 'quirofano' y 'paciente' como encabezados."""
    for i, row in enumerate(all_rows[:10]):
        strs = [str(c).lower().strip() if c else "" for c in row]
        if "quirofano" in strs and "paciente" in strs:
            return i
    return 1  # fallback

# ── Parser HTML (CEMIC exporta tablas HTML con extensión .xlsx) ───────────────
class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell = ""
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = ""
            self._in_cell = True

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            if self._row is not None:
                self._row.append(self._cell.strip())
            self._in_cell = False
        elif tag == "tr":
            if self._row is not None and any(c.strip() for c in self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_cell:
            self._cell += data

def _smart_decode(raw: bytes) -> str:
    """Decodifica HTML de CEMIC que mezcla UTF-8 y latin-1.
    Intenta cada secuencia como UTF-8; si falla, la trata como latin-1.
    Así 'Pediatría' (UTF-8) y 'ADMISIÓN' (latin-1 crudo) quedan bien.
    """
    result = []
    i = 0
    while i < len(raw):
        b = raw[i]
        if b < 0x80:
            result.append(chr(b))
            i += 1
        elif 0xC2 <= b <= 0xDF and i + 1 < len(raw) and 0x80 <= raw[i+1] <= 0xBF:
            # Secuencia UTF-8 de 2 bytes válida
            result.append(raw[i:i+2].decode('utf-8'))
            i += 2
        elif 0xE0 <= b <= 0xEF and i + 2 < len(raw) and all(0x80 <= raw[j] <= 0xBF for j in (i+1, i+2)):
            # Secuencia UTF-8 de 3 bytes válida
            result.append(raw[i:i+3].decode('utf-8'))
            i += 3
        elif 0xF0 <= b <= 0xF4 and i + 3 < len(raw) and all(0x80 <= raw[j] <= 0xBF for j in (i+1, i+2, i+3)):
            # Secuencia UTF-8 de 4 bytes válida
            result.append(raw[i:i+4].decode('utf-8'))
            i += 4
        else:
            # Byte aislado → latin-1 (cubre Á É Í Ó Ú Ñ y sus minúsculas)
            result.append(raw[i:i+1].decode('latin-1'))
            i += 1
    return ''.join(result)

def _parse_html_table(path: Path) -> list[list[str]]:
    """Lee el archivo como HTML y extrae todas las filas de la tabla.
    CEMIC envía HTML con extensión .xlsx con bytes de encoding mixto.
    """
    raw  = path.read_bytes()
    text = _smart_decode(raw)
    p    = _TableParser()
    p.feed(text)
    return p.rows

def _rows_to_surgeries(all_rows: list[list], agenda_key: str | None = None) -> list:
    """Convierte una lista de listas (header + data) al formato de cirugías."""
    header_idx = _find_header_row(all_rows)
    if len(all_rows) <= header_idx:
        return []

    target_sede = get_agenda_config(agenda_key)["excel_sede_match"]
    results = []
    for row in all_rows[header_idx + 1:]:
        # Para HTML las sedes no están en columna 37 — aceptar todo
        sede = _s(_get(row, "sede")).upper()
        if sede and sede != target_sede:
            continue

        iso_date = _to_iso(_get(row, "date"))
        patient  = _s(_get(row, "patient"))
        if not iso_date or not patient:
            continue

        results.append({
            "date":            iso_date,
            "time":            _to_time(_get(row, "time")),
            "room":            _norm_room(_get(row, "room")),
            "patient":         patient,
            "age":             _s(_get(row, "age")),
            "coverage":        _s(_get(row, "coverage")),
            "dni":             _normalize_dni(_get(row, "dni")),
            "phone":           _s(_get(row, "phone")),
            "procedure":       _s(_get(row, "procedure")),
            "procedure2":      _s(_get(row, "procedure2")),
            "surgeon":         _s(_get(row, "surgeon")),
            "surgeon2":        _s(_get(row, "surgeon2")),
            "anesthesia":      _s(_get(row, "anesthesia")),
            "origin":          _s(_get(row, "origin")),
            "specialty":       _s(_get(row, "specialty")),
            "destination":     _s(_get(row, "destination")),
            "svc_destination": _s(_get(row, "svc_destination")),
            "emergency":       _s(_get(row, "emergency")),
            "sys_status":      _s(_get(row, "sys_status")),
            "pharmacy":        _s(_get(row, "pharmacy")),
            "obs_pharmacy":    _s(_get(row, "obs_pharmacy")),
            "anat_pat":        _s(_get(row, "anat_pat")),
            "hemotherapy":     _s(_get(row, "hemotherapy")),
            "rx":              _s(_get(row, "rx")),
            "orthopedics":     _s(_get(row, "orthopedics")),
            "obs_ortho":       _s(_get(row, "obs_ortho")),
            "observations":    _s(_get(row, "observations")),
            "comments":        _s(_get(row, "comments")),
            "delay_reason":    _s(_get(row, "delay_reason")),
            "suspended":       _s(_get(row, "suspended")),
            "suspension_date": _s(_get(row, "suspension_date")),
            "rescheduled":     _s(_get(row, "rescheduled")),
            "prev_date":       _s(_get(row, "prev_date")),
        })

    return results

def parse_excel(path: Path, agenda_key: str | None = None) -> list:
    """Parsea el reporte de CEMIC: acepta XLSX real, XLS real, o HTML disfrazado de XLSX."""
    try:
        header = path.read_bytes()[:8]
    except Exception:
        header = b''

    is_zip  = header[:2] == b'PK'                    # XLSX real
    is_xls  = header[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'  # XLS binario OLE2

    if is_zip:
        # XLSX real
        wb       = openpyxl.load_workbook(str(path), data_only=True)
        ws       = wb.active
        all_rows = [list(r) for r in ws.iter_rows(values_only=True)]
    elif is_xls:
        # XLS antiguo (formato binario) — requiere xlrd
        try:
            import xlrd
            wb = xlrd.open_workbook(str(path))
            ws = wb.sheet_by_index(0)
            all_rows = [ws.row_values(i) for i in range(ws.nrows)]
        except ImportError:
            raise RuntimeError(
                "El archivo es un .xls antiguo. Instalá xlrd: pip install xlrd==1.2.0"
            )
    else:
        # HTML disfrazado de .xlsx (lo que exporta CEMIC normalmente)
        all_rows = _parse_html_table(path)

    return _rows_to_surgeries(all_rows, agenda_key=agenda_key)

# ── Descarga CEMIC ───────────────────────────────────────────────────────────
def _next_month(d: date) -> date:
    m, y = d.month + 1, d.year
    if m > 12:
        m, y = 1, y + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


# ── Descarga: patrón robusto copiado de report_downloader.py ───────────────

_MONTHS_ES_DL = {
    1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
    5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
    9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre',
}


def _login_for_sede(page, sede_label: str, sede_aliases: list[str] | None = None) -> None:
    page.goto(URL_LOGIN, wait_until='domcontentloaded', timeout=30_000)
    page.get_by_role('textbox', name='Usuario').fill(USUARIO, timeout=10_000)
    page.get_by_role('textbox', name='Contraseña').fill(PASSWORD, timeout=10_000)
    page.locator('#select2-idsanatorio-container').click(timeout=10_000)
    candidate_labels = [sede_label]
    if sede_aliases:
        candidate_labels.extend(sede_aliases)

    selected = False
    for label in candidate_labels:
        if not _s(label):
            continue
        try:
            page.get_by_role('option', name=label).click(timeout=4_000)
            selected = True
            break
        except Exception:
            try:
                page.locator('.select2-results__option').filter(has_text=label).first.click(timeout=4_000)
                selected = True
                break
            except Exception:
                continue

    if not selected:
        # Ultimo fallback para variantes del texto de Las Heras en el select2.
        page.locator('.select2-results__option').filter(has_text=re.compile(r'las\s*heras', re.I)).first.click(timeout=8_000)

    page.get_by_role('button', name=re.compile('Iniciar Sesión', re.I)).click(timeout=15_000)
    page.wait_for_load_state('networkidle', timeout=30_000)


def _navigate_to_reports(page) -> None:
    page.wait_for_selector('a, button, li, span', timeout=15_000)
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


def _click_day_in_picker(page, target: date) -> None:
    """
    Con el picker ya abierto, navega al mes de `target` usando las flechas
    y hace click en el día correspondiente.
    """
    for _ in range(24):   # máximo 24 meses de navegación
        # Leer el mes/año que muestra el calendario izquierdo
        header = page.locator('.daterangepicker .drp-calendar.left .month').first
        header.wait_for(state='visible', timeout=5_000)
        header_text = header.inner_text().strip()   # ej: "abril 2026" o "April 2026"

        # Parsear mes y año del header
        parts = header_text.replace(',', '').split()
        if len(parts) >= 2:
            try:
                # Puede venir como "abril 2026" (es-AR) o "April 2026" (en)
                import locale as _locale
                for fmt in ('%B %Y', '%b %Y'):
                    try:
                        cal_date = datetime.strptime(header_text.strip(), fmt)
                        break
                    except ValueError:
                        cal_date = None
                if not cal_date:
                    # fallback: último token es el año
                    year  = int(parts[-1])
                    month_str = parts[0].lower()
                    _months_map = {
                        'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
                        'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12,
                        'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
                        'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
                        'jan':1,'feb':2,'mar':3,'apr':4,'jun':6,'jul':7,'aug':8,
                        'sep':9,'oct':10,'nov':11,'dec':12,
                    }
                    month = _months_map.get(month_str, 0)
                    cal_date = date(year, month, 1) if month else None
                else:
                    cal_date = cal_date.date()
            except Exception:
                cal_date = None
        else:
            cal_date = None

        if cal_date:
            diff = (target.year - cal_date.year) * 12 + (target.month - cal_date.month)
            if diff == 0:
                break
            if diff > 0:
                page.locator('.daterangepicker .drp-calendar.left .next').click()
            else:
                page.locator('.daterangepicker .drp-calendar.left .prev').click()
            page.wait_for_timeout(300)
        else:
            break

    # Hacer click en el día dentro del calendario activo
    # Los td con el número de día tienen class 'available' y no 'off'
    day_str = str(target.day)
    cells = page.locator(
        f'.daterangepicker .drp-calendar.left td.available:not(.off)'
    ).all()
    for cell in cells:
        if cell.inner_text().strip() == day_str:
            cell.click()
            page.wait_for_timeout(200)
            return
    # fallback: click por texto
    page.locator(f'.daterangepicker .drp-calendar.left td:text-is("{day_str}")').first.click()


def _apply_date_range(page, date_from: date, date_to: date) -> None:
    # Abrir el picker
    page.wait_for_selector('#daterange', state='visible', timeout=15_000)
    page.locator('#daterange').click()
    page.wait_for_selector('.daterangepicker', state='visible', timeout=10_000)
    page.wait_for_timeout(400)

    # Navegar y clickear fecha inicio
    _click_day_in_picker(page, date_from)
    page.wait_for_timeout(300)

    # Para la fecha fin el picker pasa al calendario derecho o permite
    # seleccionar en cualquier calendario; simplemente clickeamos el día
    day_str = str(date_to.day)

    # Navegar al mes de fecha_fin si hace falta (puede estar en otro mes)
    for _ in range(6):
        # Leer el mes del calendario derecho
        right_header = page.locator('.daterangepicker .drp-calendar.right .month').first
        try:
            right_text = right_header.inner_text().strip()
        except Exception:
            break
        parts = right_text.replace(',', '').split()
        try:
            year  = int(parts[-1])
            month_str = parts[0].lower()
            _months_map = {
                'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,
                'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12,
                'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
                'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
            }
            month = _months_map.get(month_str, 0)
            if month and date(year, month, 1).replace(day=1) == date_to.replace(day=1):
                break
            # avanzar si fecha_to está en el futuro respecto al calendario derecho
            if month and (date_to.year * 12 + date_to.month) > (year * 12 + month):
                page.locator('.daterangepicker .drp-calendar.right .next').click()
                page.wait_for_timeout(300)
            else:
                break
        except Exception:
            break

    cells = page.locator(
        '.daterangepicker .drp-calendar.right td.available:not(.off)'
    ).all()
    clicked = False
    for cell in cells:
        if cell.inner_text().strip() == day_str:
            cell.click()
            clicked = True
            page.wait_for_timeout(200)
            break
    if not clicked:
        # Si no encontró en el derecho, buscar en cualquier calendario
        page.locator(f'.daterangepicker td.available:not(.off):text-is("{day_str}")').last.click()

    page.wait_for_timeout(300)

    # Confirmar con el botón Apply
    apply = page.locator('.daterangepicker .applyBtn')
    if apply.is_visible():
        apply.click()
    page.wait_for_timeout(400)

 
def download_report(agenda_key: str | None = None) -> Path:
    from playwright.sync_api import sync_playwright

    config = get_agenda_config(agenda_key)
    paths = _agenda_paths(config["key"])

    if not USUARIO or not PASSWORD:
        raise ValueError(
            "Faltan credenciales. Completar CEMIC_USER y CEMIC_PASS en el archivo ejecutar.bat"
        )

    hoy       = date.today()
    fecha_fin = hoy + timedelta(days=45)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            accept_downloads=True,
            locale='es-AR',
            timezone_id='America/Argentina/Buenos_Aires',
        )
        try:
            page = ctx.new_page()
            _login_for_sede(page, config["download_label"], config.get("download_label_aliases"))
            _navigate_to_reports(page)
            _apply_date_range(page, hoy, fecha_fin)

            # Botón exacto "Descargar Reporte" — no usar .first con regex genérico
            # para no agarrar "Descargar Backup" u otros botones
            btn_dl = page.get_by_role('button', name=re.compile(r'Descargar\s+Reporte', re.I))
            btn_dl.wait_for(state='visible', timeout=15_000)
            btn_dl.scroll_into_view_if_needed()

            with page.expect_download(timeout=90_000) as dl:
                btn_dl.click()

            ruta = paths["downloads_dir"] / f"{config['report_prefix']}_{hoy.strftime('%Y-%m-%d_%H%M')}.xlsx"
            dl.value.save_as(str(ruta))
        finally:
            ctx.close()
            browser.close()

    return ruta

# ── Detección de movimientos ─────────────────────────────────────────────────
def _fmt(iso: str) -> str:
    """YYYY-MM-DD → DD/MM/YYYY para mensajes legibles."""
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
        return d.strftime("%d/%m/%Y")
    except Exception:
        return iso


def _movement_details(s: dict | None) -> dict:
    s = s or {}
    return {
        "procedure":   s.get("procedure", ""),
        "procedure2":  s.get("procedure2", ""),
        "surgeon":     s.get("surgeon", ""),
        "surgeon2":    s.get("surgeon2", ""),
        "coverage":    s.get("coverage", ""),
        "specialty":   s.get("specialty", ""),
        "anesthesia":  s.get("anesthesia", ""),
        "origin":      s.get("origin", ""),
        "destination": s.get("svc_destination") or s.get("destination", ""),
        "sys_status":  s.get("sys_status", ""),
        "emergency":   s.get("emergency", ""),
        "observations": s.get("observations", ""),
        "comments":     s.get("comments", ""),
    }


def _entry_signature(s: dict) -> tuple:
    return (
        _norm_text(s.get("patient")),
        _norm_text(s.get("procedure")),
        _norm_text(s.get("surgeon")),
        _norm_text(s.get("coverage")),
        _norm_text(s.get("specialty")),
    )


def _match_score(prev_entry: dict, curr_entry: dict) -> int:
    score = 0
    if _entry_signature(prev_entry) == _entry_signature(curr_entry):
        score += 14
    prev_date_hint, prev_time_hint = _extract_schedule_parts(curr_entry.get("prev_date", ""))
    if prev_date_hint and prev_entry.get("date") == prev_date_hint:
        score += 10
    if prev_time_hint and prev_entry.get("time") == prev_time_hint:
        score += 6
    if prev_entry.get("date") == curr_entry.get("date"):
        score += 8
    if prev_entry.get("time") == curr_entry.get("time"):
        score += 2
    if prev_entry.get("room") == curr_entry.get("room"):
        score += 1
    if _norm_text(prev_entry.get("procedure")) == _norm_text(curr_entry.get("procedure")):
        score += 5
    if _norm_text(prev_entry.get("surgeon")) == _norm_text(curr_entry.get("surgeon")):
        score += 4
    if _norm_text(prev_entry.get("coverage")) == _norm_text(curr_entry.get("coverage")):
        score += 2
    if _norm_text(prev_entry.get("specialty")) == _norm_text(curr_entry.get("specialty")):
        score += 2
    return score


def _find_best_match(curr_entry: dict, prev_entries: list[dict], used_prev: set[int]) -> int | None:
    best_idx = None
    best_score = -1
    for idx, prev_entry in enumerate(prev_entries):
        if idx in used_prev:
            continue
        score = _match_score(prev_entry, curr_entry)
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx if best_score >= 10 else None


def migrate_status_store(prev: list[dict], curr: list[dict], agenda_key: str | None = None) -> dict:
    paths = _agenda_paths(agenda_key)
    with _status_lock(agenda_key):
        store = load_json(paths["status"]) or {}
        migrated = dict(store)

        prev_map: dict[str, list[dict]] = {}
        for entry in prev:
            prev_map.setdefault(_patient_group_key(entry.get("patient", "")), []).append(entry)

        curr_map: dict[str, list[dict]] = {}
        for entry in curr:
            curr_map.setdefault(_patient_group_key(entry.get("patient", "")), []).append(entry)

        for patient, curr_entries in curr_map.items():
            prev_entries = prev_map.get(patient, [])

            used_prev: set[int] = set()
            for curr_entry in curr_entries:
                curr_key = surgery_key(curr_entry)
                if curr_key in migrated:
                    continue

                match_idx = _find_best_match(curr_entry, prev_entries, used_prev)
                if match_idx is not None:
                    used_prev.add(match_idx)
                    prev_key = surgery_key(prev_entries[match_idx])
                    prev_status = migrated.get(prev_key)
                    if _status_has_manual_data(prev_status):
                        migrated[curr_key] = prev_status
                        continue

                fallback_key = _find_status_store_match(curr_entry, migrated)
                fallback_status = migrated.get(fallback_key) if fallback_key else None
                if _status_has_manual_data(fallback_status):
                    migrated[curr_key] = fallback_status

        if migrated != store:
            save_json(paths["status"], migrated)

        return migrated


def _movement_common(entry: dict, detected_at: str, source: str, filename: str) -> dict:
    return {
        "patient": entry.get("patient", ""),
        "detected_at": detected_at,
        "source": source,
        "filename": filename,
        **_movement_details(entry),
    }


TRACKED_CHANGE_FIELDS = [
    ("procedure", "Práctica"),
    ("procedure2", "Práctica complementaria"),
    ("surgeon", "Cirujano"),
    ("surgeon2", "2° Cirujano"),
    ("anesthesia", "Anestesia"),
    ("coverage", "Obra social"),
    ("specialty", "Especialidad"),
    ("origin", "Origen"),
    ("destination", "Destino"),
    ("svc_destination", "Destino servicio"),
    ("pharmacy", "Farmacia"),
    ("obs_pharmacy", "Obs. farmacia"),
    ("anat_pat", "Anatomía patológica"),
    ("hemotherapy", "Hemoterapia"),
    ("rx", "RX"),
    ("orthopedics", "Ortopedia"),
    ("obs_ortho", "Obs. ortopedia"),
    ("observations", "Observaciones"),
    ("comments", "Comentarios"),
    ("delay_reason", "Motivo demora"),
    ("sys_status", "Estado sistema"),
    ("emergency", "Emergencia"),
]


def _clean_diff_value(value) -> str:
    v = _s(value)
    return v if v else ""


def _collect_field_changes(prev_entry: dict, curr_entry: dict) -> list[dict]:
    changes = []
    for field, label in TRACKED_CHANGE_FIELDS:
        before = _clean_diff_value(prev_entry.get(field, ""))
        after = _clean_diff_value(curr_entry.get(field, ""))
        if before == after:
            continue
        changes.append({
            "field": field,
            "label": label,
            "from": before,
            "to": after,
        })
    return changes


def _build_new_event(entry: dict, detected_at: str, source: str, filename: str) -> dict:
    return {
        "type": "new",
        "subtype": "added",
        "date": entry.get("date", ""),
        "time": entry.get("time", ""),
        "room": entry.get("room", ""),
        **_movement_common(entry, detected_at, source, filename),
    }


def _build_removed_event(entry: dict, detected_at: str, source: str, filename: str) -> dict:
    return {
        "type": "delete",
        "subtype": "removed",
        "date": entry.get("date", ""),
        "time": entry.get("time", ""),
        "room": entry.get("room", ""),
        **_movement_common(entry, detected_at, source, filename),
    }


def _build_moved_event(prev_entry: dict, curr_entry: dict, detected_at: str, source: str, filename: str) -> dict:
    return {
        "type": "move",
        "subtype": "moved",
        "from_date": prev_entry.get("date", ""),
        "from_time": prev_entry.get("time", ""),
        "from_room": prev_entry.get("room", ""),
        "to_date": curr_entry.get("date", ""),
        "to_time": curr_entry.get("time", ""),
        "to_room": curr_entry.get("room", ""),
        **_movement_common(curr_entry, detected_at, source, filename),
    }


def _build_reprog_event(prev_entry: dict | None, curr_entry: dict, detected_at: str, source: str, filename: str) -> dict:
    prev_raw = curr_entry.get("prev_date", "")
    from_date = _to_iso(prev_raw) if prev_raw else (prev_entry.get("date", "") if prev_entry else "")
    return {
        "type": "move",
        "subtype": "reprog",
        "from_date": from_date,
        "from_time": prev_entry.get("time", "") if prev_entry else "",
        "from_room": prev_entry.get("room", "") if prev_entry else "",
        "to_date": curr_entry.get("date", ""),
        "to_time": curr_entry.get("time", ""),
        "to_room": curr_entry.get("room", ""),
        "prev_date_raw": prev_raw,
        **_movement_common(curr_entry, detected_at, source, filename),
    }


def _build_suspended_event(prev_entry: dict | None, curr_entry: dict, detected_at: str, source: str, filename: str) -> dict:
    base_entry = curr_entry if curr_entry else prev_entry or {}
    return {
        "type": "delete",
        "subtype": "suspended",
        "date": base_entry.get("date", ""),
        "time": base_entry.get("time", ""),
        "room": base_entry.get("room", ""),
        "reason": base_entry.get("delay_reason", ""),
        "suspension_date": base_entry.get("suspension_date", ""),
        **_movement_common(base_entry, detected_at, source, filename),
    }


def _build_updated_event(prev_entry: dict, curr_entry: dict, changes: list[dict], detected_at: str, source: str, filename: str) -> dict:
    return {
        "type": "move",
        "subtype": "updated",
        "date": curr_entry.get("date", ""),
        "time": curr_entry.get("time", ""),
        "room": curr_entry.get("room", ""),
        "from_date": prev_entry.get("date", ""),
        "from_time": prev_entry.get("time", ""),
        "from_room": prev_entry.get("room", ""),
        "to_date": curr_entry.get("date", ""),
        "to_time": curr_entry.get("time", ""),
        "to_room": curr_entry.get("room", ""),
        "changes": changes,
        **_movement_common(curr_entry, detected_at, source, filename),
    }


def _build_no_changes_event(prev: list, curr: list, detected_at: str, source: str, filename: str) -> dict:
    return {
        "type": "compare",
        "subtype": "no_changes",
        "patient": "Sin cambios entre reportes",
        "detected_at": detected_at,
        "source": source,
        "filename": filename,
        "previous_count": len(prev),
        "current_count": len(curr),
        "summary": "El reporte nuevo se comparó contra el anterior y no se detectaron diferencias.",
    }


def _movement_signature(movement: dict) -> str:
    relevant = {
        "type": movement.get("type", ""),
        "subtype": movement.get("subtype", ""),
        "patient": movement.get("patient", ""),
        "procedure": movement.get("procedure", ""),
        "surgeon": movement.get("surgeon", ""),
        "coverage": movement.get("coverage", ""),
        "date": movement.get("date", ""),
        "time": movement.get("time", ""),
        "room": movement.get("room", ""),
        "from_date": movement.get("from_date", ""),
        "from_time": movement.get("from_time", ""),
        "from_room": movement.get("from_room", ""),
        "to_date": movement.get("to_date", ""),
        "to_time": movement.get("to_time", ""),
        "to_room": movement.get("to_room", ""),
        "reason": movement.get("reason", ""),
        "suspension_date": movement.get("suspension_date", ""),
        "prev_date_raw": movement.get("prev_date_raw", ""),
        "changes": movement.get("changes", []),
        "summary": movement.get("summary", ""),
        "previous_count": movement.get("previous_count", 0),
        "current_count": movement.get("current_count", 0),
    }
    return json.dumps(relevant, ensure_ascii=False, sort_keys=True)


def append_movement_history(movements: list[dict], agenda_key: str | None = None) -> list[dict]:
    paths = _agenda_paths(agenda_key)
    history = load_json(paths["movements"]) or []
    existing = {item.get("signature") for item in history if item.get("signature")}
    next_id = max((int(item.get("id", 0)) for item in history), default=0)
    added = []

    for movement in movements:
        signature = _movement_signature(movement)
        if signature in existing:
            continue
        next_id += 1
        item = {
            "id": next_id,
            "signature": signature,
            **movement,
        }
        history.append(item)
        existing.add(signature)
        added.append(item)

    if added or not paths["movements"].exists():
        save_json(paths["movements"], history)

    return added

def detect_movements(prev: list, curr: list, detected_at: str | None = None, source: str = "system", filename: str = "") -> list:
    out = []
    detected_at = detected_at or datetime.now().isoformat(timespec="seconds")

    prev_map: dict[str, list[dict]] = {}
    for s in prev:
        prev_map.setdefault(_patient_group_key(s.get("patient", "")), []).append(s)

    curr_map: dict[str, list[dict]] = {}
    for s in curr:
        curr_map.setdefault(_patient_group_key(s.get("patient", "")), []).append(s)

    all_patients = sorted(set(prev_map) | set(curr_map))

    for patient in all_patients:
        prev_entries = prev_map.get(patient, [])
        curr_entries = curr_map.get(patient, [])
        used_prev: set[int] = set()
        matched_pairs: list[tuple[dict | None, dict]] = []

        for curr_entry in curr_entries:
            match_idx = _find_best_match(curr_entry, prev_entries, used_prev)
            if match_idx is None:
                out.append(_build_new_event(curr_entry, detected_at, source, filename))
                matched_pairs.append((None, curr_entry))
                continue

            prev_entry = prev_entries[match_idx]
            used_prev.add(match_idx)
            matched_pairs.append((prev_entry, curr_entry))

            if (
                prev_entry.get("date") != curr_entry.get("date")
                or prev_entry.get("time") != curr_entry.get("time")
                or prev_entry.get("room") != curr_entry.get("room")
            ):
                out.append(_build_moved_event(prev_entry, curr_entry, detected_at, source, filename))

        for idx, prev_entry in enumerate(prev_entries):
            if idx not in used_prev:
                out.append(_build_removed_event(prev_entry, detected_at, source, filename))

        for prev_entry, curr_entry in matched_pairs:
            prev_rescheduled = (prev_entry or {}).get("rescheduled", "").lower() == "si"
            curr_rescheduled = curr_entry.get("rescheduled", "").lower() == "si"
            prev_prev_date = _to_iso((prev_entry or {}).get("prev_date", "")) if prev_entry else ""
            curr_prev_date = _to_iso(curr_entry.get("prev_date", "")) if curr_entry.get("prev_date") else ""

            if curr_rescheduled and (
                not prev_rescheduled
                or prev_prev_date != curr_prev_date
                or (prev_entry or {}).get("date") != curr_entry.get("date")
                or (prev_entry or {}).get("time") != curr_entry.get("time")
                or (prev_entry or {}).get("room") != curr_entry.get("room")
            ):
                out.append(_build_reprog_event(prev_entry, curr_entry, detected_at, source, filename))

            prev_suspended = (prev_entry or {}).get("suspended", "").lower() == "si"
            curr_suspended = curr_entry.get("suspended", "").lower() == "si"
            if curr_suspended and not prev_suspended:
                out.append(_build_suspended_event(prev_entry, curr_entry, detected_at, source, filename))

            if prev_entry:
                changes = _collect_field_changes(prev_entry, curr_entry)
                if changes:
                    out.append(_build_updated_event(prev_entry, curr_entry, changes, detected_at, source, filename))

    return out

_APP_MATCH_DAYS = 10  # ventana en días para considerar que un pte de la app corresponde a esta cirugía


def _parse_surgery_date(raw: str) -> date | None:
    """Convierte una fecha ISO (YYYY-MM-DD) a date. Retorna None si no es válida."""
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def list_surgeries(agenda_key: str | None = None) -> list[dict]:
    surgeries = load_json(_agenda_paths(agenda_key)["current"]) or []
    if not surgeries:
        return []

    try:
        from .models import Patient
    except Exception:
        return surgeries

    dnis = sorted({re.sub(r"\D+", "", str(item.get("dni", ""))) for item in surgeries if item.get("dni")})
    # Calcular rango de fechas de las cirugías para acotar la búsqueda en la app
    surgery_dates = [d for item in surgeries if (d := _parse_surgery_date(item.get("date", "")))]
    patients_by_dni: dict[str, list] = {}
    if dnis and surgery_dates:
        window_start = min(surgery_dates) - timedelta(days=_APP_MATCH_DAYS)
        window_end = max(surgery_dates) + timedelta(days=_APP_MATCH_DAYS)
        patients = Patient.objects.filter(
            dni_norm__in=dnis,
            planned_date__gte=window_start,
            planned_date__lte=window_end,
        ).only("id", "dni_norm", "phone", "service", "planned_date")
        for patient in patients:
            if patient.dni_norm:
                patients_by_dni.setdefault(patient.dni_norm, []).append(patient)

    enriched = []
    for item in surgeries:
        surgery = dict(item)
        dni = re.sub(r"\D+", "", str(surgery.get("dni", "")))
        surgery_date = _parse_surgery_date(surgery.get("date", ""))

        candidates = patients_by_dni.get(dni, [])
        app_patient = None
        date_mismatch = False

        if candidates:
            # Buscar el candidato con fecha más cercana
            if surgery_date:
                by_distance = sorted(candidates, key=lambda p: abs((p.planned_date - surgery_date).days))
                closest = by_distance[0]
                if abs((closest.planned_date - surgery_date).days) <= _APP_MATCH_DAYS:
                    app_patient = closest
                else:
                    # Existe en la app pero con fecha muy distinta — avisar
                    date_mismatch = True
                    app_patient = closest
            else:
                app_patient = candidates[0]

        surgery["in_app"] = bool(app_patient) and not date_mismatch
        surgery["in_app_date_mismatch"] = date_mismatch
        surgery["app_patient_id"] = app_patient.id if app_patient else None
        surgery["app_service"] = app_patient.service if app_patient else ""
        surgery["app_phone"] = app_patient.phone if app_patient and app_patient.phone else ""
        surgery["whatsapp_phone"] = surgery.get("phone") or (app_patient.phone if app_patient and app_patient.phone else "")
        enriched.append(surgery)
    return enriched


def _norm_rule_text(value) -> str:
    text = _s(value).upper().strip()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return " ".join(text.split())


def _internacion_auto_reason(surgery: dict) -> tuple[str, str] | None:
    procedure_norm = _norm_rule_text(" ".join([surgery.get("procedure", ""), surgery.get("procedure2", "")]))
    surgeon_norm = _norm_rule_text(" ".join([surgery.get("surgeon", ""), surgery.get("surgeon2", "")]))

    procedure_rules = [
        ("BY PASS AO CO", "Por intervencion: BY PASS AO CO"),
        ("BYPASS AO CO", "Por intervencion: BY PASS AO CO"),
        ("HDM IMPLANTE DE VALVULA AORTICA PERCUTANEA POR CATETERISMO", "Por intervencion: Implante de valvula aortica percutanea por cateterismo"),
    ]
    for needle, label in procedure_rules:
        if needle in procedure_norm:
            return "automatico_intervencion", label

    surgeon_tokens = {"RIVAROLA", "MARCELO"}
    if surgeon_tokens.issubset(set(surgeon_norm.split())):
        return "automatico_cirujano", "Por cirujano: Rivarola, Marcelo Damian"
    return None


def _internacion_signature(surgery: dict) -> str:
    parts = [
        _norm_rule_text(surgery.get("patient", "")),
        _s(surgery.get("date", "")),
        _norm_rule_text(" ".join([surgery.get("procedure", ""), surgery.get("procedure2", "")])),
        _norm_rule_text(" ".join([surgery.get("surgeon", ""), surgery.get("surgeon2", "")])),
    ]
    return "|".join(parts)[:500]


def _manual_internacion_exists(model, fecha_prev: date, surgery: dict) -> bool:
    patient_norm = _norm_rule_text(surgery.get("patient", ""))
    dni = re.sub(r"\D+", "", str(surgery.get("dni", "")))
    qs = model.objects.filter(fecha=fecha_prev, origen_registro="manual")
    for item in qs.only("paciente_nombre", "paciente_dni"):
        same_patient = _norm_rule_text(item.paciente_nombre) == patient_norm
        same_dni = dni and re.sub(r"\D+", "", item.paciente_dni or "") == dni
        if same_patient or same_dni:
            return True
    return False


def sync_internaciones_automaticas(fecha: str | date, agenda_key: str | None = None) -> None:
    config = get_agenda_config(agenda_key)
    if config["key"] != "saavedra":
        return
    target_date = date.fromisoformat(fecha) if isinstance(fecha, str) else fecha
    source_date = target_date + timedelta(days=1)

    try:
        from .models import InternacionVarias
    except Exception:
        return

    surgeries = load_json(_agenda_paths(config["key"])["current"]) or []
    for surgery in surgeries:
        if surgery.get("date") != source_date.isoformat() or is_cancelled_for_internacion(surgery):
            continue
        reason = _internacion_auto_reason(surgery)
        if not reason:
            continue
        origen, motivo_automatico = reason
        signature = _internacion_signature(surgery)
        if not signature:
            continue
        if InternacionVarias.objects.filter(automatic_signature=signature).exists():
            continue
        if _manual_internacion_exists(InternacionVarias, target_date, surgery):
            continue

        InternacionVarias.objects.create(
            fecha=target_date,
            horario=_s(surgery.get("time")),
            paciente_nombre=_s(surgery.get("patient")),
            paciente_dni=_s(surgery.get("dni")),
            edad=_s(surgery.get("age")),
            obra_social=_s(surgery.get("coverage")),
            motivo="Internacion previa a cirugia",
            servicio_solicitante=_s(surgery.get("specialty")),
            medico_responsable=_s(surgery.get("surgeon")),
            destino=_s(surgery.get("svc_destination") or surgery.get("destination")),
            telefono=_s(surgery.get("phone")),
            observaciones="",
            origen_registro=origen,
            cirugia_origen_id=surgery_key(surgery),
            motivo_automatico=motivo_automatico,
            fecha_cirugia_original=source_date,
            horario_cirugia_original=_s(surgery.get("time")),
            intervencion_original=_s(surgery.get("procedure") or surgery.get("procedure2")),
            cirujano_original=_s(surgery.get("surgeon")),
            automatic_signature=signature,
        )


def is_cancelled_for_internacion(surgery: dict) -> bool:
    st = _s(surgery.get("sys_status")).lower()
    return "cancelado" in st or _s(surgery.get("suspended")).lower() == "si"


def serialize_internacion(item) -> dict:
    return {
        "id": item.id,
        "fecha": item.fecha.isoformat(),
        "horario": item.horario,
        "paciente_nombre": item.paciente_nombre,
        "paciente_dni": item.paciente_dni,
        "edad": item.edad,
        "obra_social": item.obra_social,
        "motivo": item.motivo,
        "servicio_solicitante": item.servicio_solicitante,
        "medico_responsable": item.medico_responsable,
        "cama_asignada": item.cama_asignada,
        "destino": item.destino,
        "telefono": item.telefono,
        "observaciones": item.observaciones,
        "origen_registro": item.origen_registro,
        "cirugia_origen_id": item.cirugia_origen_id,
        "motivo_automatico": item.motivo_automatico,
        "fecha_cirugia_original": item.fecha_cirugia_original.isoformat() if item.fecha_cirugia_original else "",
        "horario_cirugia_original": item.horario_cirugia_original,
        "intervencion_original": item.intervencion_original,
        "cirujano_original": item.cirujano_original,
    }


def list_internaciones_varias(fecha: str, agenda_key: str | None = None) -> list[dict]:
    sync_internaciones_automaticas(fecha, agenda_key=agenda_key)
    from .models import InternacionVarias

    day = date.fromisoformat(fecha)
    items = InternacionVarias.objects.filter(fecha=day).order_by("horario", "paciente_nombre", "id")
    return [serialize_internacion(item) for item in items]


def save_internacion_varias(data: dict, item_id: int | None = None):
    from .models import InternacionVarias

    allowed = {
        "fecha", "horario", "paciente_nombre", "paciente_dni", "edad", "obra_social", "motivo",
        "servicio_solicitante", "medico_responsable", "cama_asignada", "destino",
        "telefono", "observaciones",
    }
    payload = {field: _s(data.get(field, "")) for field in allowed if field in data}
    if "fecha" in payload:
        payload["fecha"] = date.fromisoformat(payload["fecha"])
    if not item_id:
        if not payload.get("fecha"):
            raise ValueError("Fecha requerida")
        if not payload.get("paciente_nombre"):
            raise ValueError("Nombre y apellido requerido")
        payload["origen_registro"] = "manual"
        item = InternacionVarias.objects.create(**payload)
    else:
        item = InternacionVarias.objects.get(pk=item_id)
        for field, value in payload.items():
            setattr(item, field, value)
        item.save(update_fields=list(payload.keys()) + ["updated_at"])
    return serialize_internacion(item)


def delete_internacion_varias(item_id: int) -> None:
    from .models import InternacionVarias

    item = InternacionVarias.objects.get(pk=item_id)
    if item.origen_registro != "manual":
        raise ValueError("Las internaciones automaticas no se eliminan; se actualizan desde la regla.")
    item.delete()


def read_meta(agenda_key: str | None = None) -> dict:
    meta = load_json(_agenda_paths(agenda_key)["meta"]) or {}
    meta.setdefault("sede", get_agenda_config(agenda_key)["title"])
    return meta


def read_versions(agenda_key: str | None = None) -> dict:
    return get_data_versions(agenda_key)


def list_movements(agenda_key: str | None = None) -> list[dict]:
    history = load_json(_agenda_paths(agenda_key)["movements"]) or []
    return sorted(
        history,
        key=lambda item: (item.get("detected_at", ""), int(item.get("id", 0))),
        reverse=True,
    )


def read_status_store(agenda_key: str | None = None) -> dict:
    return load_json(_agenda_paths(agenda_key)["status"]) or {}


def _normalize_stay_days(value) -> str:
    raw = _s(value)
    if not raw:
        return ""
    try:
        days = int(float(raw.replace(",", ".")))
    except ValueError:
        return ""
    if days < 0:
        return ""
    return str(min(days, 365))


def _normalize_status_payload(status: dict | None) -> dict:
    cleaned = dict(status or {})
    cleaned["stay_days"] = _normalize_stay_days(cleaned.get("stay_days"))
    return cleaned


def write_status(agenda_key: str | None, key: str, status: dict) -> dict:
    if not key:
        raise ValueError("key requerido")
    paths = _agenda_paths(agenda_key)
    with _status_lock(agenda_key):
        store = load_json(paths["status"]) or {}
        store[key] = _normalize_status_payload(status)
        save_json(paths["status"], store)
    return {"ok": True, "versions": get_data_versions(agenda_key)}


def _rotate_current_to_previous(agenda_key: str | None = None) -> list[dict]:
    paths = _agenda_paths(agenda_key)
    curr = load_json(paths["current"]) or []
    if curr:
        save_json(paths["previous"], curr)
    return curr


def _import_report_file(ruta: Path, agenda_key: str | None, source: str, manual: bool = False) -> dict:
    paths = _agenda_paths(agenda_key)
    config = get_agenda_config(agenda_key)
    curr = _rotate_current_to_previous(agenda_key)
    surgeries = parse_excel(ruta, agenda_key=agenda_key)
    if not surgeries:
        raise ValueError("No se encontraron cirugías en el archivo. Verificar que sea el reporte correcto.")

    migrate_status_store(curr, surgeries, agenda_key=agenda_key)
    save_json(paths["current"], surgeries)

    now = datetime.now().isoformat(timespec="seconds")
    meta = {"downloaded_at": now, "count": len(surgeries), "sede": config["title"], "agenda": config["key"]}
    if manual:
        meta.update({"manual": True, "filename": ruta.name})
    save_json(paths["meta"], meta)

    prev = load_json(paths["previous"]) or []
    movements = detect_movements(prev, surgeries, detected_at=now, source=source, filename=ruta.name)
    if prev and not movements:
        movements = [_build_no_changes_event(prev, surgeries, now, source, ruta.name)]
    movements = append_movement_history(movements, agenda_key=agenda_key)

    return {
        "ok": True,
        "count": len(surgeries),
        "movements": movements,
        "downloaded_at": now,
        "versions": get_data_versions(agenda_key),
    }


def refresh_report(agenda_key: str | None = None) -> dict:
    with agenda_refresh_lock(agenda_key):
        ruta = download_report(agenda_key)
        return _import_report_file(ruta, agenda_key, source="auto")


def save_uploaded_report(agenda_key: str | None, filename: str, file_bytes: bytes) -> dict:
    with agenda_refresh_lock(agenda_key):
        paths = _agenda_paths(agenda_key)
        safe_name = secure_filename(filename) or "reporte_manual.xlsx"
        ruta = paths["downloads_dir"] / safe_name
        ruta.write_bytes(file_bytes)
        return _import_report_file(ruta, agenda_key, source="manual", manual=True)
