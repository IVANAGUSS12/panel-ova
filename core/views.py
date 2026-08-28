from collections import defaultdict
from datetime import datetime, timedelta
from io import BytesIO
import json
import logging
import re
import smtplib
import subprocess
import sys
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

from urllib.parse import urlencode
import os
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Case, F, IntegerField, Value, When
from django.db.models.functions import Lower, Replace
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
import openpyxl
from reportlab.lib.pagesizes import A4, letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
import matplotlib
matplotlib.use('Agg')  # Backend sin GUI
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from django.utils import timezone
from django.db.models import Count, Q
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.utils.dateparse import parse_date
from django.core.cache import cache
from django.core.paginator import Paginator

from .forms import AttachmentForm, PatientFilterForm, QRPatientForm, ReprogramForm
from .models import Attachment, Patient, QuirofanoEntry, QuirofanoSnapshot
from .quirofano import canonicalize_service, default_quirofano_window, import_daily_quirofano_snapshot, normalize_dni
from .text_utils import normalize_text as _normalize_text

def build_patient_whatsapp_message(patient_name, surgery_date, doctor) -> str:
    patient_name = str(patient_name or '').strip() or 'paciente'
    doctor = str(doctor or '').strip() or 'profesional a confirmar'

    if hasattr(surgery_date, 'strftime'):
        surgery_date_label = surgery_date.strftime('%d/%m/%Y')
    else:
        raw_date = str(surgery_date or '').strip()
        try:
            surgery_date_label = datetime.strptime(raw_date[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
        except (TypeError, ValueError):
            surgery_date_label = raw_date or 'fecha a confirmar'

    return (
        f'Estimado/a paciente y/o familiar de {patient_name}, nos comunicamos desde el Área de Administración Quirúrgica de CEMIC.\n\n'
        f'Nos contactamos por la intervención que tiene programada para el día {surgery_date_label} con el profesional {doctor}.\n\n'
        'Queríamos consultar sobre el estado de la autorización de dicha intervención.\n\n'
        'Asimismo, aprovechamos para informarle que CEMIC cuenta con un área de autorizaciones, desde donde podemos gestionar la autorización de la intervención por usted.\n\n'
        'Para iniciar la gestión, le solicitamos que cargue la documentación correspondiente en el siguiente enlace:\n\n'
        'https://panel.oficinavirtualcemic.com/qr/carga/\n\n'
        'Muchas gracias.\n'
        'Área de Administración Quirúrgica\n'
        'CEMIC'
    )

DEFAULT_WHATSAPP_LOAD_TIMEOUT_MS = 25000
DEFAULT_WHATSAPP_POST_SEND_DELAY_MS = 2500
DEFAULT_WHATSAPP_BETWEEN_SEND_DELAY_MS = 4000


def _normalize_whatsapp_phone(raw_phone: str | None) -> str:
    digits = re.sub(r'\D+', '', str(raw_phone or ''))
    if not digits:
        return ''

    if digits.startswith('00'):
        digits = digits[2:]

    if digits.startswith('549'):
        return digits

    if digits.startswith('54'):
        national = digits[2:]
        if national.startswith('9'):
            return digits
        if national.startswith('0'):
            national = national[1:]
        national = re.sub(r'^(\d{2,4})15', r'\1', national)
        if 10 <= len(national) <= 11:
            return f'549{national}'
        return f'54{national}'

    if digits.startswith('0'):
        digits = digits[1:]

    digits = re.sub(r'^(\d{2,4})15', r'\1', digits)
    if 10 <= len(digits) <= 11:
        return f'549{digits}'
    return digits


def _parse_positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(str(value or '').strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _quirofano_management_next_url(request, active_snapshot_id: int | None = None) -> str:
    next_url = request.POST.get('next', '').strip() or request.GET.get('next', '').strip()
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return next_url
    if active_snapshot_id:
        return f"{reverse('core:quirofano_management')}?snapshot={active_snapshot_id}"
    return reverse('core:quirofano_management')


def _build_quirofano_whatsapp_batch(entries: list[QuirofanoEntry]) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    skipped: list[str] = []

    for entry in entries:
        raw_phone = entry.report_phone or (entry.app_patient.phone if entry.app_patient_id else '')
        normalized_phone = _normalize_whatsapp_phone(raw_phone)
        if not normalized_phone or len(normalized_phone) < 12:
            skipped.append(entry.patient_name or f'Entrada {entry.pk}')
            continue

        items.append({
            'entry_id': entry.pk,
            'patient_name': entry.patient_name,
            'phone': normalized_phone,
            'raw_phone': raw_phone,
            'message': build_patient_whatsapp_message(
                entry.patient_name,
                entry.surgery_date,
                entry.doctor,
            ),
        })

    return items, skipped


def _build_public_url(request, path: str) -> str:
    public_base_url = os.getenv("PANEL_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if public_base_url:
        return f"{public_base_url}{path}"
    return request.build_absolute_uri(path)


def _resolve_anesthesia_pdf_path() -> Path | None:
    configured_path = os.getenv("ANESTHESIA_PDF_PATH", "").strip()
    if configured_path:
        pdf_path = Path(configured_path)
        if not pdf_path.is_absolute():
            pdf_path = Path(settings.BASE_DIR) / pdf_path
        return pdf_path if pdf_path.exists() and pdf_path.suffix.lower() == ".pdf" else None

    pdf_dir = Path(settings.MEDIA_ROOT) / "anestesia"
    if not pdf_dir.exists():
        return None

    pdf_files = sorted(
        [path for path in pdf_dir.glob("*.pdf") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return pdf_files[0] if pdf_files else None


def _build_media_url_for_file(request, file_path: Path) -> str | None:
    try:
        relative = file_path.resolve().relative_to(Path(settings.MEDIA_ROOT).resolve()).as_posix()
    except ValueError:
        return None
    return _build_public_url(request, f"{settings.MEDIA_URL}{relative}")


def _get_anesthesia_links_and_attachment(request) -> tuple[str, str | None, Path | None]:
    pdf_path = _resolve_anesthesia_pdf_path()
    pdf_url = _build_media_url_for_file(request, pdf_path) if pdf_path else None
    info_url = os.getenv("ANESTHESIA_INFO_URL", "").strip() or pdf_url or ""
    return info_url, pdf_url, pdf_path


def _build_patient_tracking_whatsapp_message(request, patient: Patient) -> str:
    tracking_path = f"{reverse('core:tracking')}?{urlencode({'id': patient.tracking_id})}"
    tracking_url = _build_public_url(request, tracking_path)
    anesthesia_info_url, anesthesia_pdf_url, _ = _get_anesthesia_links_and_attachment(request)
    patient_name = (patient.full_name or "paciente").title()

    lines = [
        f"Hola {patient_name}.",
        "",
        "Recibimos correctamente su solicitud en Panel OVA.",
        f"Codigo de seguimiento: {patient.tracking_id}",
        f"Puede consultar el estado aca: {tracking_url}",
    ]
    if anesthesia_info_url:
        lines.extend(["", f"Link de informacion de anestesia: {anesthesia_info_url}"])
    has_pdf_attachment = bool(anesthesia_pdf_url)
    if anesthesia_pdf_url and anesthesia_pdf_url != anesthesia_info_url:
        lines.append(f"PDF de anestesia: {anesthesia_pdf_url}")
    if has_pdf_attachment:
        lines.extend(["", "Tambien le enviamos el PDF de anestesia adjunto en este chat."])
    lines.extend(["", "Este mensaje es automatico. Ante dudas medicas o indicaciones particulares, siga lo informado por su equipo de salud."])
    return "\n".join(lines)


def _send_whatsapp_cloud_payload(payload: dict) -> bool:
    phone_number_id = settings.WHATSAPP_CLOUD_PHONE_NUMBER_ID.strip()
    access_token = settings.WHATSAPP_CLOUD_ACCESS_TOKEN.strip()
    if not phone_number_id or not access_token:
        logger.warning("WhatsApp Cloud API no configurado: falta PHONE_NUMBER_ID o ACCESS_TOKEN.")
        return False

    api_version = settings.WHATSAPP_CLOUD_API_VERSION.strip() or "v20.0"
    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.WHATSAPP_CLOUD_TIMEOUT) as response:
            body = response.read().decode("utf-8", errors="ignore")
            logger.info("WhatsApp Cloud API OK: %s", body)
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        logger.error("WhatsApp Cloud API HTTP %s: %s", exc.code, detail)
    except Exception:
        logger.exception("No se pudo enviar WhatsApp por Cloud API.")
    return False


def _build_whatsapp_template_payload(phone: str, request, patient: Patient, tracking_url: str, anesthesia_info_url: str, anesthesia_pdf_url: str | None) -> dict:
    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": (patient.full_name or "Paciente").title()},
                {"type": "text", "text": patient.tracking_id or "-"},
                {"type": "text", "text": tracking_url},
                {"type": "text", "text": anesthesia_info_url or (anesthesia_pdf_url or "-")},
            ],
        }
    ]
    return {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_TRACKING_TEMPLATE_NAME.strip(),
            "language": {"code": settings.WHATSAPP_TRACKING_TEMPLATE_LANGUAGE.strip() or "es_AR"},
            "components": components,
        },
    }


def _send_patient_tracking_whatsapp(request, patient: Patient) -> bool:
    phone = _normalize_whatsapp_phone(patient.phone)
    if not phone:
        logger.info("No se envia WhatsApp de tracking: paciente %s sin telefono valido.", patient.pk)
        return False

    tracking_path = f"{reverse('core:tracking')}?{urlencode({'id': patient.tracking_id})}"
    tracking_url = _build_public_url(request, tracking_path)
    anesthesia_info_url, anesthesia_pdf_url, _ = _get_anesthesia_links_and_attachment(request)

    sent_any = False
    if settings.WHATSAPP_TRACKING_TEMPLATE_NAME.strip():
        sent_any = _send_whatsapp_cloud_payload(
            _build_whatsapp_template_payload(
                phone=phone,
                request=request,
                patient=patient,
                tracking_url=tracking_url,
                anesthesia_info_url=anesthesia_info_url,
                anesthesia_pdf_url=anesthesia_pdf_url,
            )
        )
    else:
        text_payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "text",
            "text": {
                "preview_url": True,
                "body": _build_patient_tracking_whatsapp_message(request, patient),
            },
        }
        sent_any = _send_whatsapp_cloud_payload(text_payload)

    if anesthesia_pdf_url:
        document_payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "document",
            "document": {
                "link": anesthesia_pdf_url,
                "filename": Path(anesthesia_pdf_url).name or "anestesia.pdf",
                "caption": f"PDF de anestesia - Tracking {patient.tracking_id}",
            },
        }
        sent_any = _send_whatsapp_cloud_payload(document_payload) or sent_any

    return sent_any


def _queue_patient_tracking_whatsapp(request, patient: Patient) -> bool:
    """Compatibilidad con el flujo existente: ahora envia por API, sin navegador ni QR."""
    return _send_patient_tracking_whatsapp(request, patient)


def _build_patient_tracking_email(request, patient: Patient) -> tuple[str, str, Path | None]:
    tracking_path = f"{reverse('core:tracking')}?{urlencode({'id': patient.tracking_id})}"
    tracking_url = _build_public_url(request, tracking_path)
    anesthesia_info_url, anesthesia_pdf_url, anesthesia_pdf_path = _get_anesthesia_links_and_attachment(request)
    patient_name = (patient.full_name or "paciente").title()

    tracking_home_url = _build_public_url(request, reverse('core:tracking'))
    anesthesia_contact_email = getattr(settings, "ANESTHESIA_CONTACT_EMAIL", "").strip()

    subject = f"CEMIC - Solicitud recibida y seguimiento {patient.tracking_id}"
    lines = [
        f"Estimado/a {patient_name}:",
        "",
        "Recibimos correctamente la documentacion cargada en la Oficina Virtual de Autorizaciones de CEMIC.",
        "A partir de ahora puede seguir el estado de su solicitud con el codigo informado debajo.",
        "",
        f"Codigo de seguimiento: {patient.tracking_id}",
        f"Link directo de seguimiento: {tracking_url}",
        f"Tambien puede ingresar a {tracking_home_url} y colocar su codigo de seguimiento.",
    ]
    if anesthesia_info_url:
        lines.extend([
            "",
            "Dudas frecuentes sobre anestesia:",
            anesthesia_info_url,
        ])
    if anesthesia_contact_email:
        lines.extend([
            "",
            "Para consultas con el equipo de anestesia puede escribir a:",
            anesthesia_contact_email,
        ])
    if anesthesia_pdf_path:
        lines.extend([
            "",
            "Adjuntamos a este correo el consentimiento de anestesia.",
            "Por favor, lealo con atencion antes de la intervencion. La firma del consentimiento se realizara el dia de la intervencion segun las indicaciones del equipo asistencial.",
        ])
    lines.extend([
        "",
        "Este mensaje es automatico. No responda este correo.",
        "Ante dudas medicas o indicaciones particulares, siga siempre lo informado por su equipo de salud.",
        "",
        "Oficina Virtual de Autorizaciones",
        "CEMIC",
    ])
    return subject, "\n".join(lines), anesthesia_pdf_path


def _send_patient_tracking_email(request, patient: Patient) -> bool:
    recipient = (patient.email or "").strip()
    if not recipient:
        logger.info("No se envia mail de tracking: paciente %s sin email.", patient.pk)
        return False

    subject, body, anesthesia_pdf_path = _build_patient_tracking_email(request, patient)
    from_email = (
        getattr(settings, "PATIENT_TRACKING_FROM_EMAIL", "")
        or settings.DEFAULT_FROM_EMAIL
        or "oficinavirtualdeautorizaciones@cemic.edu.ar"
    )
    email_message = EmailMessage(
        subject=subject,
        body=body,
        from_email=from_email,
        to=[recipient],
    )
    if anesthesia_pdf_path and anesthesia_pdf_path.exists():
        email_message.attach_file(str(anesthesia_pdf_path))

    try:
        sent_count = email_message.send(fail_silently=False)
        logger.info("Mail de tracking enviado a paciente %s: %s", patient.pk, recipient)
        return sent_count > 0
    except Exception:
        logger.exception("No se pudo enviar mail de tracking a paciente %s.", patient.pk)
        return False


def _build_patient_authorized_email(request, patient: Patient) -> tuple[str, str]:
    tracking_path = f"{reverse('core:tracking')}?{urlencode({'id': patient.tracking_id})}"
    tracking_url = _build_public_url(request, tracking_path)
    patient_name = (patient.full_name or "paciente").title()
    surgery_date = patient.planned_date.strftime("%d/%m/%Y") if patient.planned_date else "A confirmar"
    doctor = (patient.doctor or "A confirmar").title()

    subject = "CEMIC - Su cirugia se encuentra autorizada"
    lines = [
        f"Estimado/a {patient_name}:",
        "",
        "Le informamos desde la Oficina Virtual de Autorizaciones de CEMIC que la autorizacion de su cirugia se encuentra aprobada.",
        "",
        f"Fecha probable de intervencion: {surgery_date}",
        f"Medico: {doctor}",
        f"Codigo de seguimiento: {patient.tracking_id}",
        "",
        f"Puede consultar el estado actualizado aca: {tracking_url}",
        "",
        "Este mensaje es automatico. No responda este correo.",
        "Ante dudas medicas o indicaciones particulares, siga siempre lo informado por su equipo de salud.",
        "",
        "Oficina Virtual de Autorizaciones",
        "CEMIC",
    ]
    return subject, "\n".join(lines)


def _send_patient_authorized_email(request, patient: Patient) -> bool:
    recipient = (patient.email or "").strip()
    if not recipient:
        logger.info("No se envia mail de autorizacion: paciente %s sin email.", patient.pk)
        return False

    subject, body = _build_patient_authorized_email(request, patient)
    from_email = (
        getattr(settings, "PATIENT_TRACKING_FROM_EMAIL", "")
        or settings.DEFAULT_FROM_EMAIL
        or "oficinavirtualdeautorizaciones@cemic.edu.ar"
    )
    email_message = EmailMessage(
        subject=subject,
        body=body,
        from_email=from_email,
        to=[recipient],
    )

    try:
        sent_count = email_message.send(fail_silently=False)
        logger.info("Mail de autorizacion enviado a paciente %s: %s", patient.pk, recipient)
        return sent_count > 0
    except Exception:
        logger.exception("No se pudo enviar mail de autorizacion a paciente %s.", patient.pk)
        return False


def _legacy_patient_tracking_whatsapp_batch_payload(request, patient: Patient) -> dict:
    """Referencia inactiva del formato anterior de lote. No se usa para pacientes QR."""
    return {
        "created_at": timezone.localtime().isoformat(),
        "source": "qr_patient_create",
        "items": [
            {
                "entry_id": patient.pk,
                "patient_name": patient.full_name,
                "phone": _normalize_whatsapp_phone(patient.phone),
                "message": _build_patient_tracking_whatsapp_message(request, patient),
            }
        ],
    }

# -------------------------------------------------------------
# LISTAS (servicios, médicos, coberturas)
# -------------------------------------------------------------

# -------------------------------------------------------------
# HELPERS (persistencia filtros / búsqueda tolerante)
# -------------------------------------------------------------
def _normalized_expr(field_name: str):
    """
    Normaliza un campo en SQL para comparar sin acentos/ñ y sin mayúsculas.
    Funciona en SQLite/MySQL/Postgres (LOWER + REPLACE).
    """
    expr = Lower(F(field_name))
    mapping = [
        ("áàäâãÁÀÄÂÃ", "a"),
        ("éèëêÉÈËÊ", "e"),
        ("íìïîÍÌÏÎ", "i"),
        ("óòöôõÓÒÖÔÕ", "o"),
        ("úùüûÚÙÜÛ", "u"),
        ("ñÑ", "n"),
        ("çÇ", "c"),
    ]
    expanded_mapping = []
    for chars, dst in mapping:
        for src in chars:
            expanded_mapping.append((src, dst))
            for encoding in ("latin1", "cp1252"):
                try:
                    mojibake = src.encode("utf-8").decode(encoding)
                except UnicodeError:
                    continue
                expanded_mapping.append((mojibake, dst))
    for src, dst in expanded_mapping:
        expr = Replace(expr, Value(src), Value(dst))
    return expr


def _build_patient_list_url(request) -> str:
    base = reverse("core:patient_list")
    qs = request.session.get("patient_list_qs", "")
    return base + (f"?{qs}" if qs else "")


def _resolve_next_url(request) -> str:
    """Lee next de GET/POST y lo valida; si no es válido vuelve al listado con filtros guardados."""
    candidate = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if candidate:
        if candidate.startswith("/"):
            return candidate
        if url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return candidate
    return _build_patient_list_url(request)


def _build_patient_detail_url(pk: int, next_url: str | None = None) -> str:
    url = reverse("core:patient_detail", kwargs={"pk": pk})
    if next_url:
        url += "?" + urlencode({"next": next_url})
    return url


ADMISSION_EMAILS_BY_SEDE = {
    Patient.SEDE_SAAVEDRA: "admisionsaav@cemic.edu.ar",
    Patient.SEDE_LAS_HERAS: "admisionlh@cemic.edu.ar",
    Patient.SEDE_POMBO: "admisionpombo@cemic.edu.ar",
}

ADMISSION_CC_EMAILS_BY_SEDE = {
    Patient.SEDE_SAAVEDRA: [
        "ccastillo@cemic.edu.ar",
        "etournie@cemic.edu.ar",
    ],
    Patient.SEDE_POMBO: [
        "bambrosini@cemic.edu.ar",
        "pgerenciados@cemic.edu.ar",
    ],
}

ADMISSION_REQUIRED_ATTACHMENT_TYPES = (
    Attachment.TYPE_ORDEN,
    Attachment.TYPE_AUTORIZACION,
)

ADMISSION_OPTIONAL_ATTACHMENT_TYPES = (
    Attachment.TYPE_MATERIALES,
)

ADMISSION_MAX_TOTAL_ATTACHMENT_BYTES = 18 * 1024 * 1024

ADMISSION_ATTACHMENT_LABELS = {
    Attachment.TYPE_ORDEN: "Orden de intervención",
    Attachment.TYPE_MATERIALES: "Materiales",
    Attachment.TYPE_AUTORIZACION: "Autorización",
}


def _get_admission_email_for_sede(sede: str | None) -> str:
    return ADMISSION_EMAILS_BY_SEDE.get((sede or "").strip().upper(), "")


def _get_admission_cc_emails_for_sede(sede: str | None) -> list[str]:
    return ADMISSION_CC_EMAILS_BY_SEDE.get((sede or "").strip().upper(), [])


def _get_patient_admission_attachments(patient: Patient):
    attachments_by_type = defaultdict(list)
    for attachment in patient.attachments.all():
        if attachment.type in ADMISSION_REQUIRED_ATTACHMENT_TYPES or attachment.type in ADMISSION_OPTIONAL_ATTACHMENT_TYPES:
            attachments_by_type[attachment.type].append(attachment)

    selected_attachments = []
    missing_labels = []
    for attachment_type in ADMISSION_REQUIRED_ATTACHMENT_TYPES:
        current = attachments_by_type.get(attachment_type, [])
        if current:
            selected_attachments.extend(current)
        else:
            missing_labels.append(ADMISSION_ATTACHMENT_LABELS[attachment_type])

    for attachment_type in ADMISSION_OPTIONAL_ATTACHMENT_TYPES:
        selected_attachments.extend(attachments_by_type.get(attachment_type, []))

    return selected_attachments, missing_labels


def _get_admission_attachments_total_bytes(attachments: list[Attachment]) -> int:
    total_bytes = 0
    for attachment in attachments:
        if not attachment.file:
            continue
        try:
            total_bytes += attachment.file.size
        except Exception:
            try:
                total_bytes += os.path.getsize(attachment.file.path)
            except Exception:
                continue
    return total_bytes


def _send_patient_admission_email(request, patient: Patient) -> tuple[str, list[Attachment]]:
    recipient = _get_admission_email_for_sede(patient.sede)
    cc_recipients = _get_admission_cc_emails_for_sede(patient.sede)
    if not recipient:
        raise ValueError("El paciente no tiene una sede válida para enviar a admisión.")

    attachments, missing_labels = _get_patient_admission_attachments(patient)
    if missing_labels:
        raise ValueError(f"Faltan adjuntos obligatorios: {', '.join(missing_labels)}.")

    total_attachment_bytes = _get_admission_attachments_total_bytes(attachments)
    if total_attachment_bytes > ADMISSION_MAX_TOTAL_ATTACHMENT_BYTES:
        total_mb = total_attachment_bytes / (1024 * 1024)
        max_mb = ADMISSION_MAX_TOTAL_ATTACHMENT_BYTES / (1024 * 1024)
        raise ValueError(
            "Los adjuntos superan el tamaño máximo permitido para enviar por mail. "
            f"Total actual: {total_mb:.1f} MB. Máximo permitido: {max_mb:.0f} MB."
        )

    admission_backend = settings.ADMISSION_EMAIL_BACKEND
    admission_host = (settings.ADMISSION_EMAIL_HOST or "").strip()
    if admission_backend.endswith("smtp.EmailBackend") and not admission_host:
        raise ValueError("El envío por mail a admisión no está configurado en el servidor. Falta ADMISSION_EMAIL_HOST o EMAIL_HOST.")

    surgery_date = patient.planned_date.strftime("%d/%m/%Y") if patient.planned_date else "Sin fecha"
    surgery_time = patient.surgery_time.strftime("%H:%M") if patient.surgery_time else "A confirmar"
    sede_label = patient.get_sede_display() if patient.sede else "Sin sede"
    sender_name = request.user.get_full_name() or request.user.username

    body_lines = [
        "Hola,",
        "",
        "Se envía documentación para admisión desde Panel OVA.",
        "",
        f"Paciente: {patient.full_name}",
        f"DNI: {patient.dni or '-'}",
        f"Cobertura: {patient.coverage or '-'}",
        f"Servicio: {patient.service or '-'}",
        f"Médico: {patient.doctor or '-'}",
        f"Fecha de cirugía: {surgery_date}",
        f"Hora: {surgery_time}",
        f"Sede: {sede_label}",
        f"Tracking: {patient.tracking_id or '-'}",
        "",
        "Adjuntos incluidos:",
    ]

    for attachment in attachments:
        body_lines.append(f"- {ADMISSION_ATTACHMENT_LABELS.get(attachment.type, attachment.get_type_display())}: {getattr(attachment, 'display_name', os.path.basename(attachment.file.name or 'archivo'))}")

    body_lines.extend([
        "",
        f"Enviado por: {sender_name}",
        "",
        "Saludos,",
        "Panel OVA",
    ])

    email_message = EmailMessage(
        subject=f"Panel OVA | Documentación admisión | {patient.full_name} | {sede_label}",
        body="\n".join(body_lines),
        from_email=settings.ADMISSION_DEFAULT_FROM_EMAIL,
        to=[recipient],
        cc=cc_recipients,
        reply_to=[request.user.email] if request.user.email else None,
    )

    for attachment in attachments:
        if not attachment.file:
            continue
        email_message.attach_file(attachment.file.path)

    def _deliver_admission_email() -> int:
        email_connection = get_connection(
            backend=admission_backend,
            host=settings.ADMISSION_EMAIL_HOST,
            port=settings.ADMISSION_EMAIL_PORT,
            username=settings.ADMISSION_EMAIL_HOST_USER,
            password=settings.ADMISSION_EMAIL_HOST_PASSWORD,
            use_tls=settings.ADMISSION_EMAIL_USE_TLS,
            use_ssl=settings.ADMISSION_EMAIL_USE_SSL,
            timeout=settings.ADMISSION_EMAIL_TIMEOUT,
        )
        try:
            email_connection.open()
            return email_connection.send_messages([email_message])
        finally:
            email_connection.close()

    try:
        sent_count = _deliver_admission_email()
    except smtplib.SMTPServerDisconnected:
        sent_count = _deliver_admission_email()

    if not sent_count:
        raise ValueError("El servidor no confirmó el envío del mail.")

    return recipient, attachments


def _format_admission_email_error(exc: Exception) -> str:
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return (
            "La conexión con el servidor de mail se cortó antes de completar el envío. "
            "Se reintentó automáticamente, pero el servidor volvió a cerrar la sesión."
        )

    if isinstance(exc, smtplib.SMTPAuthenticationError):
        smtp_host = (settings.EMAIL_HOST or "").strip().lower()
        error_detail = ""
        if len(exc.args) > 1 and isinstance(exc.args[1], (bytes, bytearray)):
            error_detail = exc.args[1].decode(errors="ignore")
        elif exc.args:
            error_detail = str(exc.args[-1])

        if "gmail.com" in smtp_host or "google" in error_detail.lower():
            return (
                "Google rechazó la autenticación del mail emisor. "
                "Esa cuenta necesita una App Password para usar SMTP; la clave normal no alcanza."
            )

        return (
            "El servidor SMTP rechazó la autenticación del mail emisor. "
            "Verificá que la clave sea correcta y que la cuenta tenga habilitado SMTP autenticado."
        )
    return f"No se pudo enviar el mail a admisión: {exc}"

SERVICIOS_QR = [
    'TRAUMATOLOGIA',
    'HEMODINAMIA',
    'UROLOGIA',
    'CIRUGIA GENERAL',
    'CIRUGIA CABEZA Y CUELLO',
    'CIRUGIA TORACICA',
    'CIRUGIA PLASTICA',
    'OTORRINOLARINGOLOGIA',
    'NEUROCIRUGIA',
    'FLEBOLOGIA',
    'OFTALMOLOGIA',
    'PEDIATRIA',
    'GINECOLOGIA Y OBSTETRICIA',
    'SERVICIO DE ODONTOLOGIA',
]

MEDICOS_QR = [
    "ABALO EDUARDO DIEGO",
    "ABALO EDUARDO RUDECINDO",
    "ACOSTA GUEMES LUCIANA",
    "ACOSTA JORGE EDUARDO PATRICIO",
    "ADROGUE LUIS MARTIN",
    "ALEJANDRO DAVID PINTO MOLINA",
    "ALTUVE IGNACIO",
    "ANTONIAZZI SAMANTA",
    "ARMIJOS KARLA",
    "AVELLANEDA NICOLAS LUIS",
    "BALDESSARI MARTIN",
    "BALESTRINI JULIAN",
    "BALLESTER ANGELES",
    "BARBIERI PABLO GASTON",
    "BARDOT GONZALO",
    "BARREIRO CATALINA",
    "BARRERA DARIO",
    "BARRIENTOS CATALINA",
    "BARTULUCCHI MARCELO",
    "BAUMEISTER GUSTAVO",	
    "BELDI MARIA FLORENCIA",
    "BERMUDEZ ARIEL LEONARDO",
    "BIGNON RAUL HORACIO",
    "BIGNOTTI JOSE AGUSTIN",
    "BISTOLETTI PEDRO HORACIO",
    "BLANC ARIANA",
    "BOLOGNA MICAELA",
    "BORRE BRENDA",
    "CABEDALE JIMENA",
    "CAERO ROMINA ALEJANDRA",
    "CANO RODRIGO",
    "CANTISANI RAFAEL",
    "CAPIEL LEANDRO",
    "CARRIE AUGUSTO JAVIER",
    "CARRIZO GONZALEZ JUAN ALBERTO",
    "CASARETTO JUAN",
    "CAVEDALE JIMENA",
    "CHAVES LEANDRO",
    "CHAVEZ ARIEL",
    "CILLA ELIANA GISELA",
    "CLEMENTE OCHOTECO GASTON ALFREDO",
    "CORDERO HERNAN",
    "CORDOBA MARIA ALEJANDRA",
    "COSTANZA EDUARDO",
    "CRIMI GABRIEL ALFREDO",
    "DE TOMMASO GONZALO",
    "DE ZAVALIA MAXIMO",
    "DEIMUNDO MARCOS",
    "DELLO RUSSO",
    "DEVOTO ANABELLA",
    "DEVOTO MATIAS ALEJANDRO",
    "DI RADO LEANDRO",
    "DIANA PEREZ",
    "FARACE TSARDIKOS DIMITRA",
    "DIRIBARNE JUAN CRUZ",
    "DOMEG MARIA BELEN",
    "DOMINGUEZ JULIETA",
    "DRIOLLET LASPIUR SANTIAGO",
    "EHRMAN PATRICIO",
    "ETCHEVERRY IGNACIO ALFREDO",
    "ETCHEVERRY TOMAS",
    "EISEMBERG GUILLERMO DANIEL",
    "FARAGO ESTEBAN",
    "FERNANDEZ JOSE ALBERTO",
    "FERNANDEZ LUCIA",
    "FERNANDEZ SASSO EZEQUIEL",
    "FINKELSTEIN JONATHAN",
    "FISHKEL VANINA",
    "FLORES LEVALLE",
    "GARBUGINO DE NORMANDI SILVIA",
    "GOBBI ENRIQUE AUGUSTO",
    "GODOY MACARENA",
    "GOLIAN IGNACIO",
    "GONZALEZ ARECES MARIELA ALEJANDRA",
    "GRADIN SAMUEL",
    "GRANDOLI FERNANDO M.",
    "GRUN ALEJANDRO DANIEL",
    "GRYNBLAT PEDRO SILVIO",
    "GUEVARA MENDEZ MARTIN",
    "HERRERA VEGAS DIEGO JORGE",
    "HURTADO NICOLE",
    "IGLESIAS GONZALEZ ALEJANDRO ABEL",
    "KIENAST NATALIA",
    "KLINGER DANIEL",
    "KOREN GUIDO",
    "KRUPITZKI HUGO",
    "LABADET CARLOS DAVID",
    "LACORAZZA DARIO",
    "LANCELOTTI TOMAS",
    "LARIGUET INES",
    "LEGUIZAMON GUSTAVO",
    "LOPEZ MORIS CARLOS BENJAMIN",
    "MAFFEO HORACIO",
    "MALLEA ANDRES",
    "MARENGO RICARDO LUIS",
    "MARENZI GUSTAVO JUAN",
    "MARRUGAT MARCOS",
    "MARRUGAT RODOLFO EMILIO",
    "MASKIN LUIS PATRICIO",
    "MELGAREJO ANA BELEN",
    "MENA AGUSTINA",
    "MENDEZ GRACIELA",
    "MENGO GUSTAVO EDUARDO",
    "MENINATO MARCOS",
    "MICHALSKI DIEGO JULIAN",
    "MONGE FERNANDO CARLOS",
    "MOSNA LEANDRO",
    "MOUNIER CARLOS",
    "MOURAS PABLO",
    "MUSACCHIO CECILIA",
    "NANA MARIANA",
    "NAPOLITANO MILENA",
    "NAVARRO JUAN",
    "NAZAR PEIRANO MAXIMILIANO",
    "NEGRI MERCEDES",
    "NEMECIO ALAN",
    "NISTAL CLARA",
    "ORTIZ EZEQUIEL",
    "PAESANI FERNANDO",
    "PAGANINI RAUL",
    "PAOLINI JULIETA",
    "PAUL NICOLAS CARLOS",
    "PERALTA ANGEL DANIEL",
    "PEREA AGUSTIN OSCAR",
    "PEREIRA JUAN IGNACIO",
    "PIANTONI LUCAS",
    "PICCININI PABLO",
    "PICCOLETTI LAURA",
    "PODESTA MIGUEL",
    "POMBO LUIS",
    "POTOLICCHIO ANALIA",
    "POZZONI CARLOS",
    "PRODAN SILVANA",
    "QUINTAIE AGUSTIN",
    "QUIROZ",
    "RABINOVICH FERNANDO",
    "RAMIL VERONICA",
    "RAMIREZ GUTIERREZ DANIELA",
    "RAMIREZ ZAIDA",
    "RICHARDS NICOLAS",
    "RICHARDS TOMAS",
    "RIOLFI NAZARENO",
    "RIOS MATIAS NICOLAS",
    "RIVAROLA MARCELO DAMIAN",
    "RODRIGO JIMENA MARIA DEL PILAR",
    "RODRIGUEZ JORGE",
    "RODRIGUEZ OLIVIERI MANUELA",
    "RODRIGUEZ PABLO OSCAR",
    "RONCORONI",
    "ROUAUX GUILLERMINA",
    "ROVEGNO AGUSTIN ROBERTO",
    "ROVEGNO FEDERICO AGUSTIN",
    "SALGADO ROBERTO",
    "SALVADORES MARTINEZ PABLO JAVIER",
    "SANCHEZ NICOLAS",
    "SANNA HERNAN",
    "SARTORI MARIA VERONICA",
    "SCHLICHTER ANDRES",
    "SCHWARTZMAN JAVIER",
    "SCHYGIEL GUADALUPE",
    "SERE IGNACIO ALFREDO",
    "SEREDAY PAUL",
    "SIMONELLI DAMIAN ARTURO JAVIER",
    "SOLINAS DAVID",
    "SPONTON LUIS",
    "STOPPINI GALLAGHER MAXIMILIANO JOSE",
    "SUAREZ JACKELINE",
    "SZTAJN MARCELO",
    "TABOADA SUSANA",
    "TORGA SPAK ROGER PATRICK",
    "TRIGUBO DENISE",
    "TRONCOSO IGNACIO",
    "TRUFFINI LUCIANA",
    "TRENTADUE AGUSTIN",
    "VALDEZ GABRIEL ANIBAL",
    "VALENTINI ROBERTO RAUL",
    "VALERIO ANDREA",
    "VACCAREZZA HERNAN",
    "VEGA PABLO",
    "VERA JULIETA",
    "VERACIERTO FEDERICO",
    "VILLA NATALIA ANDREA",
    "VIOLA AGUSTIN",
    "VITI MARIA MARTA",
    "VIZCAINO ALDO NORBERTO",
    "VIZCAINO FRANCISCO",
    "VON STECHER FRANCISCO",
    "WEIL ANTONELLA",
    "YAVEN IGNACIO ALEJANDRO",
    "YEREGUI SANTIAGO",
    "ZAMPEDRI ANA",
    "ZEFF NATALIA PAULA",
    "ZUCCARO GRACIELA NOEMI",
    "ZUGASTI JULIA",
    "ZUND SANTIAGO",
     
]

DOCTOR_OTRO_QR = 'OTROS'
 
COBERTURAS_QR = [
    'ACADEMIA NACIONAL DE MEDICINA',
    'ACMED',
    'AMEPBA',
    'ANDAR',
    'APM',
    'APRES',
    'APSOT',
    'SANCOR',
    'ASOCIACION MUTUAL RURALISTA',
    'AVALIAN',
    'C.S.I.L',
    'CAJA NOTARIAL',
    'CEMIC',
    'CINME',
    'CIRCULO MEDICO DE LA MATANZA',
    'CIRCULO MEDICO LOMAS DE ZAMORA',
    'COBENSIL',
    'COBERMED',
    'COLEGIO ESCRIBANOS PROVINCIA',
    'CORPORACION ASISTENCIAL',
    'DASMI',
    'ENSALUD',
    'FAMYL',
    'GALENO',
    'GENESEN',
    'HOPE',
    'HOSPITAL BRITANICO',
    'INST.O.S EMPLEADO PROVINCIAL',
    'JERARQUICOS SALUD',
    'LUIS PASTEUR',
    'MEDICUS',
    'MEDIFE',
    'MEDIN',
    'MEDIPREMIUM',
    'MUTUAL FEDERADA 25 DE JUNIO',
    'NEFRA',
    'OBSBA',
    'OMINT',
    'OPDEA',
    'OSADEF',
    'OSDE',
    'OSME',
    'OSMITA',
    'OSPE',
    'OSPIDA',
    'OSPOCE',
    'OSRJA',
    'OSFATUN',  
    'PATRONES DE CABOTAJE',
    'PRIVADO',
    'PODER JUDICIAL', 
    'PREMEDIC',
    'PREVENCION SALUD',
    'PRIVADO',
    'RED PRESTACIONAL CASA',
    'RED ARGENTINA DE SALUD',
    'ROI',
    'SEMPRE',
    'SMAUNSE',
    'SWISS MEDICAL',
]


# -------------------------------------------------------------
# CARGA PÚBLICA (QR)
# -------------------------------------------------------------
def qr_patient_create(request):
    if request.method == 'POST':
        last_name = request.POST.get('last_name', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        full_name = ' '.join(part for part in [last_name, first_name] if part)
        dni = request.POST.get('dni', '').strip()
        dni_digits = re.sub(r'\D+', '', dni)
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()
        coverage = request.POST.get('coverage', '').strip()
        selected_doctor = request.POST.get('doctor', '').strip()
        custom_doctor = request.POST.get('other_doctor', '').strip()
        doctor = custom_doctor if selected_doctor == DOCTOR_OTRO_QR else selected_doctor
        service = request.POST.get('service', '').strip()
        planned_date_raw = request.POST.get('planned_date')
        sede = request.POST.get('sede', '').strip()
        external_observations = request.POST.get('external_observations', '').strip()
        # Validar campos obligatorios
        errors = []
        field_errors = defaultdict(list)

        def add_error(field, message):
            errors.append(message)
            field_errors[field].append(message)
        if not last_name:
            add_error('last_name', 'El apellido es obligatorio.')
        if not first_name:
            add_error('first_name', 'El nombre es obligatorio.')
        if not dni:
            add_error('dni', 'El DNI es obligatorio.')
        elif not re.match(r'^[\d\s\.\-]+$', dni) or not re.match(r'^\d{7,8}$', dni_digits):
            add_error('dni', 'El DNI debe tener 7 u 8 numeros. Puede escribirlo con puntos o guiones.')
        if not email:
            add_error('email', 'El correo electronico es obligatorio para enviar el seguimiento.')
        else:
            try:
                validate_email(email)
            except ValidationError:
                add_error('email', 'Ingrese un correo electronico valido.')
        if not coverage:
            add_error('coverage', 'La cobertura es obligatoria.')
        elif coverage not in COBERTURAS_QR:
            add_error('coverage', 'La cobertura seleccionada no es valida. Elija una opcion de la lista.')
        if not selected_doctor:
            add_error('doctor', 'El medico tratante es obligatorio.')
        elif selected_doctor == DOCTOR_OTRO_QR:
            if not custom_doctor:
                add_error('other_doctor', 'Debe ingresar el nombre del medico.')
            else:
                doctor = custom_doctor.upper()
        elif selected_doctor not in MEDICOS_QR:
            add_error('doctor', 'El medico seleccionado no es valido. Elija una opcion de la lista.')
        if not service:
            add_error('service', 'El servicio es obligatorio.')
        elif service not in SERVICIOS_QR:
            add_error('service', 'El servicio seleccionado no es valido. Elija una opcion de la lista.')
        if not planned_date_raw:
            add_error('planned_date', 'La fecha probable de intervencion es obligatoria.')
        
        # Validar archivos obligatorios
        if not request.FILES.get('dni_file'):
            add_error('dni_file', 'Falta subir el archivo de DNI.')
        if not request.FILES.get('credencial_file'):
            add_error('credencial_file', 'Falta subir la credencial de cobertura.')
        if not request.FILES.get('orden_intervencion_file'):
            add_error('orden_intervencion_file', 'Falta subir la orden de intervencion.')
        
        if errors:
            return render(request, 'core/patient_form.html', {
                'errors': errors,
                'field_errors': dict(field_errors),
                'form_data': request.POST,
                'services': SERVICIOS_QR,
                'doctors': MEDICOS_QR,
                'coverages': COBERTURAS_QR,
            })

        planned_date = None
        if planned_date_raw:
            try:
                planned_date = datetime.strptime(planned_date_raw, '%Y-%m-%d').date()
            except ValueError:
                add_error('planned_date', 'El formato de fecha es invalido.')

        if errors:
            return render(request, 'core/patient_form.html', {
                'errors': errors,
                'field_errors': dict(field_errors),
                'form_data': request.POST,
                'services': SERVICIOS_QR,
                'doctors': MEDICOS_QR,
                'coverages': COBERTURAS_QR,
            })

        patient = Patient.objects.create(
            full_name=full_name,
            dni=dni_digits,
            phone=phone,
            email=email,
            coverage=coverage,
            doctor=doctor,
            service=service,
            planned_date=planned_date,
            sede=sede,
            external_observations=external_observations,
        )

        # Archivos
        file_map = [
            ('dni_file', Attachment.TYPE_DNI),
            ('credencial_file', Attachment.TYPE_CREDENCIAL),
            ('orden_intervencion_file', Attachment.TYPE_ORDEN),
            ('orden_material_file', Attachment.TYPE_MATERIALES),
            ('hc_file', Attachment.TYPE_ESTUDIOS),
        ]

        for field_name, att_type in file_map:
            f = request.FILES.get(field_name)
            if f:
                Attachment.objects.create(
                    patient=patient,
                    file=f,
                    type=att_type,
                )

        email_sent = _send_patient_tracking_email(request, patient)

        return render(request, 'core/patient_form.html', {
            'success': True,
            'tracking_id': patient.tracking_id,
            'tracking_url': f"{reverse('core:tracking')}?{urlencode({'id': patient.tracking_id})}" if patient.tracking_id else reverse('core:tracking'),
            'email_sent': email_sent,
            'form_data': {},
            'services': SERVICIOS_QR,
            'doctors': MEDICOS_QR,
            'coverages': COBERTURAS_QR,
        })

    return render(request, 'core/patient_form.html', {
        'form_data': {},
        'services': SERVICIOS_QR,
        'doctors': MEDICOS_QR,
        'coverages': COBERTURAS_QR,
    })


def admission_patient_create(request):
    if request.method == 'POST':
        last_name = request.POST.get('last_name', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        dni = request.POST.get('dni', '').strip()
        dni_digits = re.sub(r'\D+', '', dni)
        full_name = ' '.join(part for part in [last_name, first_name] if part) or 'PACIENTE ADMISION'
        observations = request.POST.get('external_observations', '').strip()

        patient = Patient.objects.create(
            full_name=full_name,
            dni=dni_digits,
            phone='',
            email='',
            coverage='ADMISION',
            doctor='ADMISION',
            service='ADMISION',
            planned_date=timezone.localdate(),
            sede='',
            external_observations=observations,
        )

        for uploaded_file in request.FILES.getlist('ordenes_files'):
            Attachment.objects.create(
                patient=patient,
                file=uploaded_file,
                type=Attachment.TYPE_ORDEN,
            )

        return render(request, 'core/admission_patient_form.html', {
            'success': True,
            'patient': patient,
            'tracking_id': patient.tracking_id,
            'tracking_url': f"{reverse('core:tracking')}?{urlencode({'id': patient.tracking_id})}" if patient.tracking_id else reverse('core:tracking'),
            'form_data': {},
        })

    return render(request, 'core/admission_patient_form.html', {
        'form_data': {},
    })


# -------------------------------------------------------------
# DASHBOARD
# -------------------------------------------------------------
@login_required
def dashboard(request):
    # Cachear resumen del dashboard por 30 segundos para reducir carga en consultas agregadas
    cache_key = 'dashboard_summary_v1'
    context = cache.get(cache_key)
    if context is None:
        hoy = timezone.localdate()
        inicio_mes = hoy.replace(day=1)
        hace_7_dias = hoy - timedelta(days=7)

        summary = Patient.objects.aggregate(
            total=Count("id"),
            total_mes=Count("id", filter=Q(created_at__date__gte=inicio_mes)),
            total_semana=Count("id", filter=Q(created_at__date__gte=hace_7_dias)),
        )

        raw_por_estado = Patient.objects.values('status').annotate(c=Count('id'))
        mapa_estados = {row['status']: row['c'] for row in raw_por_estado}

        context = {
            "hoy": hoy,
            "total": summary["total"],
            "total_mes": summary["total_mes"],
            "total_semana": summary["total_semana"],
            "status_summary": [
                {"label": "Pendiente envio prestador", "code": Patient.STATUS_PENDIENTE, "count": mapa_estados.get(Patient.STATUS_PENDIENTE, 0)},
                {"label": "Pendiente prestador", "code": Patient.STATUS_PENDIENTE_PRESTADOR, "count": mapa_estados.get(Patient.STATUS_PENDIENTE_PRESTADOR, 0)},
                {"label": "Pendiente medico", "code": Patient.STATUS_PENDIENTE_MEDICO, "count": mapa_estados.get(Patient.STATUS_PENDIENTE_MEDICO, 0)},
                {"label": "Pendiente paciente", "code": Patient.STATUS_PENDIENTE_PACIENTE, "count": mapa_estados.get(Patient.STATUS_PENDIENTE_PACIENTE, 0)},
                {"label": "Autorizados", "code": Patient.STATUS_AUTORIZADO, "count": mapa_estados.get(Patient.STATUS_AUTORIZADO, 0)},
                {"label": "Pendiente comercial - presupuesto", "code": Patient.STATUS_PENDIENTE_COMERCIAL_PRESUPUESTO, "count": mapa_estados.get(Patient.STATUS_PENDIENTE_COMERCIAL_PRESUPUESTO, 0)},
                {"label": "Autorizado - material pendiente", "code": Patient.STATUS_AUTORIZADO_MATERIAL_PENDIENTE, "count": mapa_estados.get(Patient.STATUS_AUTORIZADO_MATERIAL_PENDIENTE, 0)},
                {"label": "Rechazo cobertura", "code": Patient.STATUS_RECHAZO_COBERTURA, "count": mapa_estados.get(Patient.STATUS_RECHAZO_COBERTURA, 0)},
                {"label": "Reprogramados", "code": Patient.STATUS_REPROGRAMADO, "count": mapa_estados.get(Patient.STATUS_REPROGRAMADO, 0)},
                {"label": "Cancela medico", "code": Patient.STATUS_CANCELA_MEDICO, "count": mapa_estados.get(Patient.STATUS_CANCELA_MEDICO, 0)},
                {"label": "Cancela pte", "code": Patient.STATUS_CANCELA_PTE, "count": mapa_estados.get(Patient.STATUS_CANCELA_PTE, 0)},
            ],
            "proximas_hoy": list(Patient.objects.filter(planned_date=hoy).only("id", "full_name", "service", "coverage", "planned_date", "status").order_by('planned_date', 'service', 'full_name')),
            "proximas_semana": [
                {"patient": p, "days_until": (p.planned_date - hoy).days}
                for p in Patient.objects.filter(
                    planned_date__gt=hoy, planned_date__lte=hoy + timedelta(days=7)
                ).only("id", "full_name", "service", "coverage", "planned_date", "status").order_by('planned_date', 'full_name')
            ],
            "ultimas": list(Patient.objects.only("id", "created_at", "full_name", "service", "coverage", "status").order_by('-created_at')[:5]),
            "top_servicios": list(Patient.objects.values('service').annotate(c=Count('id')).order_by('-c')[:5]),
            "top_coberturas": list(Patient.objects.values('coverage').annotate(c=Count('id')).order_by('-c')[:5]),
        }
        cache.set(cache_key, context, 30)
    return render(request, "core/dashboard.html", context)


# -------------------------------------------------------------
# LISTADO + FILTROS
# -------------------------------------------------------------
@login_required
def patient_list(request):
    base_url = reverse("core:patient_list")

    # Limpiar filtros (borra sesión y deja listado limpio)
    if request.GET.get("clear") == "1":
        request.session.pop("patient_list_qs", None)
        return redirect(base_url)

    # Si volvés al listado sin querystring, restaurar últimos filtros guardados
    saved_qs = request.session.get("patient_list_qs", "")
    if not request.GET and saved_qs:
        return redirect(f"{base_url}?{saved_qs}")

    # Guardar el estado actual de filtros (para volver desde el detalle)
    if request.GET:
        request.session["patient_list_qs"] = request.GET.urlencode()

    form = PatientFilterForm(request.GET or None)
    qs = Patient.objects.select_related('assigned_to').all()

    if form.is_valid():

        # Buscar (SIN acentos y SIN mayus/minus)
        q = form.cleaned_data.get("q")
        if q:
            qn = _normalize_text(q)
            qs = qs.filter(
                Q(full_name_norm__contains=qn) |
                Q(dni_norm__contains=qn) |
                Q(tracking_id__icontains=q)  # Búsqueda por tracking OVA
            )

        # Estado
        status = form.cleaned_data.get("status")
        if status:
            qs = qs.filter(status=status)

        # Cobertura (tolerante)
        coverage = form.cleaned_data.get("coverage")
        if coverage:
            qs = qs.filter(coverage__icontains=coverage)

        # Doctor (tolerante)
        doctor = form.cleaned_data.get("doctor")
        if doctor:
            qs = qs.filter(doctor__icontains=doctor)

        # Servicio (tolerante)
        service = form.cleaned_data.get("service")
        if service:
            qs = qs.filter(service__icontains=service)

        # Sede
        sede = form.cleaned_data.get("sede")
        if sede:
            qs = qs.filter(sede=sede)

        # Usuario asignado
        assigned_to = form.cleaned_data.get("assigned_to")
        if assigned_to:
            if assigned_to == 'unassigned':
                qs = qs.filter(assigned_to__isnull=True)
            else:
                qs = qs.filter(assigned_to_id=assigned_to)

        # Fechas cirugía
        date_from = form.cleaned_data.get("date_from")
        if date_from:
            qs = qs.filter(planned_date__gte=date_from)

        date_to = form.cleaned_data.get("date_to")
        if date_to:
            qs = qs.filter(planned_date__lte=date_to)

        # Fechas carga
        created_from = form.cleaned_data.get("created_from")
        if created_from:
            qs = qs.filter(created_at__date__gte=created_from)

        created_to = form.cleaned_data.get("created_to")
        if created_to:
            qs = qs.filter(created_at__date__lte=created_to)

        # Orden
        order_by = form.cleaned_data.get("order_by")
        if order_by == "planned_date_asc":
            qs = qs.order_by("planned_date")
        elif order_by == "planned_date_desc":
            qs = qs.order_by("-planned_date")
        elif order_by == "created_at_asc":
            qs = qs.order_by("created_at")
        elif order_by == "created_at_desc":
            qs = qs.order_by("-created_at")
        else:
            qs = qs.order_by("-created_at")
    else:
        qs = qs.order_by("-created_at")

    # Opciones para datalist (como Servicio) - con caché
    service_options = cache.get('patient_service_options')
    if service_options is None:
        service_options = list(
            Patient.objects.exclude(service__isnull=True)
            .exclude(service__exact="")
            .values_list("service", flat=True)
            .distinct()
            .order_by("service")
        )
        cache.set('patient_service_options', service_options, 3600)  # 1 hora
    
    doctor_options = cache.get('patient_doctor_options')
    if doctor_options is None:
        db_doctors = list(
            Patient.objects.exclude(doctor__isnull=True)
            .exclude(doctor__exact="")
            .values_list("doctor", flat=True)
            .distinct()
        )
        # Fusionar DB + predefinidos, dedup case-insensitive, preferir valor de DB
        seen: dict[str, str] = {}
        for d in db_doctors:
            d = d.strip()
            if d:
                seen[d.upper()] = d
        for d in MEDICOS_QR:
            if d.upper() not in seen:
                seen[d.upper()] = d
        doctor_options = sorted(seen.values(), key=lambda x: x.upper())
        cache.set('patient_doctor_options', doctor_options, 3600)  # 1 hora
    
    coverage_options = cache.get('patient_coverage_options')
    if coverage_options is None:
        coverage_options = list(
            Patient.objects.exclude(coverage__isnull=True)
            .exclude(coverage__exact="")
            .values_list("coverage", flat=True)
            .distinct()
            .order_by("coverage")
        )
        cache.set('patient_coverage_options', coverage_options, 3600)  # 1 hora

    paginator = Paginator(qs, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)
    pagination_qs = pagination_params.urlencode()

    # Calcular días restantes para cada paciente de la página actual
    today = timezone.now()
    today_date = today.date()
    patients_list = []
    for p in page_obj.object_list:
        if p.planned_date:
            days_diff = (p.planned_date - today_date).days
            p.days_until_surgery = days_diff
        else:
            p.days_until_surgery = None
        # Días en estado pendiente prestador para alertas de seguimiento
        if p.status == Patient.STATUS_PENDIENTE_PRESTADOR:
            since = p.solicitado_since or p.created_at
            p.days_in_solicitado = (today - since).days
            # Tiempo desde creación hasta que se marcó como pendiente prestador
            if p.solicitado_since:
                p.days_carga_to_solicitado = (p.solicitado_since - p.created_at).days
            else:
                p.days_carga_to_solicitado = None
        else:
            p.days_in_solicitado = None
            p.days_carga_to_solicitado = None

        # Días en el estado actual (excepto pendiente envio prestador y autorizado)
        if p.status not in (Patient.STATUS_PENDIENTE, Patient.STATUS_AUTORIZADO):
            since_status = p.status_since or p.created_at
            p.days_in_status = (today - since_status).days
        else:
            p.days_in_status = None
        patients_list.append(p)

    latest_quirofano_snapshot = QuirofanoSnapshot.objects.first()
    latest_quirofano_label = None
    if latest_quirofano_snapshot is not None and patients_list:
        visible_patient_ids = [patient.id for patient in patients_list]
        in_latest_quirofano_ids = set(
            latest_quirofano_snapshot.entries
            .filter(app_patient_id__in=visible_patient_ids)
            .exclude(change_type=QuirofanoEntry.CHANGE_REMOVED)
            .exclude(resolved_comparison_status=QuirofanoEntry.COMPARISON_ONLY_APP)
            .values_list('app_patient_id', flat=True)
            .distinct()
        )
        latest_quirofano_label = latest_quirofano_snapshot.imported_at
        for patient in patients_list:
            patient.in_latest_quirofano = patient.id in in_latest_quirofano_ids
    else:
        for patient in patients_list:
            patient.in_latest_quirofano = False

    # Lista de usuarios activos para asignación en bulk (solo campos necesarios)
    users = User.objects.filter(is_active=True).order_by('first_name', 'last_name').only('id', 'username', 'first_name', 'last_name')

    return render(request, "core/patient_list.html", {
        "patients": patients_list,
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "form": form,
        "service_options": service_options,
        "doctor_options": doctor_options,
        "coverage_options": coverage_options,
        "users": users,
        "status_choices": Patient.STATUS_CHOICES,
        "latest_quirofano_label": latest_quirofano_label,
    })


# -------------------------------------------------------------
# DETALLE
# -------------------------------------------------------------
@login_required
def patient_detail(request, pk):
    # Traer paciente con assigned_to y attachments prefeteched para evitar N+1 en templates
    try:
        patient = Patient.objects.select_related('assigned_to').prefetch_related('attachments').get(pk=pk)
    except Patient.DoesNotExist:
        from django.http import Http404
        raise Http404()

    # Preparar atributos para la plantilla: nombre base del archivo (sin la ruta)
    for att in patient.attachments.all():
        try:
            att.display_name = os.path.basename(att.file.name or "")
        except Exception:
            att.display_name = att.file.name

    attachment_form = AttachmentForm()
    reprogram_form = ReprogramForm()

    back_url = _resolve_next_url(request)

    if request.method == "POST":
        next_url = _resolve_next_url(request)

        if "send_admission_email" in request.POST:
            try:
                recipient, sent_attachments = _send_patient_admission_email(request, patient)
                messages.success(
                    request,
                    f"Mail enviado a {recipient} con {len(sent_attachments)} archivo(s).",
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            except Exception as exc:
                logger.exception(
                    "Error enviando mail de admisión para paciente %s (ID=%s): %s",
                    patient.full_name,
                    patient.pk,
                    exc,
                )
                messages.error(request, _format_admission_email_error(exc))
            return redirect(_build_patient_detail_url(patient.pk, next_url))

        # ACTUALIZAR ESTADO
        if "update_status" in request.POST:
            new_status = request.POST.get("status", patient.status)
            new_obs = request.POST.get("internal_observations", patient.internal_observations or "").strip()
            old_status = patient.status
            patient.status = new_status
            patient.internal_observations = new_obs
            patient.save()
            if old_status != Patient.STATUS_AUTORIZADO and new_status == Patient.STATUS_AUTORIZADO:
                if _send_patient_authorized_email(request, patient):
                    messages.success(request, "Estado actualizado. Mail de autorizacion enviado al paciente.")
                elif patient.email:
                    messages.warning(request, "Estado actualizado, pero no se pudo enviar el mail de autorizacion.")
                else:
                    messages.warning(request, "Estado actualizado. No se envio mail porque el paciente no tiene email cargado.")
            else:
                messages.success(request, "Estado actualizado.")
            return redirect(_build_patient_detail_url(patient.pk, next_url))

        # TOGGLE IMPRESO
        if "toggle_impreso" in request.POST:
            patient.impreso = not patient.impreso
            patient.save()
            messages.success(request, "Estado de impreso actualizado.")
            return redirect(_build_patient_detail_url(patient.pk, next_url))

        # ASIGNAR USUARIO
        if "assign_user" in request.POST:
            user_id = request.POST.get("assigned_to")
            if user_id:
                try:
                    user = User.objects.get(id=user_id)
                    patient.assigned_to = user
                except User.DoesNotExist:
                    patient.assigned_to = None
            else:
                patient.assigned_to = None
            patient.save()
            messages.success(request, "Usuario asignado actualizado.")
            return redirect(_build_patient_detail_url(patient.pk, next_url))

        # ARCHIVOS
        if "add_attachment" in request.POST:
            attachment_form = AttachmentForm(request.POST, request.FILES)
            if attachment_form.is_valid():
                att = attachment_form.save(commit=False)
                att.patient = patient
                att.save()
                messages.success(request, "Archivo agregado.")
                return redirect(_build_patient_detail_url(patient.pk, next_url))

        # REPROGRAMAR
        if "reprogram" in request.POST:
            reprogram_form = ReprogramForm(request.POST, instance=patient)
            if reprogram_form.is_valid():
                p = reprogram_form.save(commit=False)
                p.last_reprogram_date = timezone.now()
                p.last_reprogram_reason = reprogram_form.cleaned_data.get('reprogram_reason', '')
                p.status = Patient.STATUS_REPROGRAMADO
                p.save()
                messages.success(request, "Reprogramación realizada.")
                return redirect(_build_patient_detail_url(patient.pk, next_url))

        # BORRAR (solo admin)
        if "delete_patient" in request.POST:
            if request.user.is_staff:
                patient.delete()
                messages.success(request, "Paciente eliminado.")
                return redirect(next_url)
            return HttpResponse("No autorizado", status=403)

        # ELIMINAR ATTACHMENT (solo staff)
        if "delete_attachment" in request.POST and request.user.is_staff:
            att_id = request.POST.get("attachment_id")
            try:
                att = Attachment.objects.get(pk=att_id, patient=patient)
                att.delete()
                messages.success(request, "Archivo eliminado.")
            except Attachment.DoesNotExist:
                messages.error(request, "Archivo no encontrado.")
            return redirect(_build_patient_detail_url(patient.pk, next_url))

        # CAMBIAR TIPO DE ATTACHMENT (solo staff)
        if "update_attachment_type" in request.POST and request.user.is_staff:
            att_id = request.POST.get("attachment_id")
            new_type = request.POST.get("new_type")
            try:
                att = Attachment.objects.get(pk=att_id, patient=patient)
                att.type = new_type
                att.save()
                messages.success(request, "Tipo de archivo actualizado.")
            except Attachment.DoesNotExist:
                messages.error(request, "Archivo no encontrado.")
            return redirect(_build_patient_detail_url(patient.pk, next_url))

        # EDITAR DATOS DEL PACIENTE (solo staff)
        if "update_patient_data" in request.POST and request.user.is_staff:
            patient.coverage = request.POST.get("coverage", patient.coverage).strip()
            patient.sede = request.POST.get("sede", patient.sede).strip()
            
            # Actualizar hora de cirugía
            surgery_time_str = request.POST.get("surgery_time", "").strip()
            if surgery_time_str:
                try:
                    # Parsear hora en formato HH:MM
                    time_obj = datetime.strptime(surgery_time_str, "%H:%M").time()
                    patient.surgery_time = time_obj
                except ValueError:
                    messages.warning(request, "Formato de hora inválido. Debe ser HH:MM")
            else:
                patient.surgery_time = None
            
            # Actualizar campos del calendario
            patient.material_status = request.POST.get("material_status", patient.material_status).strip()
            patient.en_quirofano = bool(request.POST.get("en_quirofano"))
            patient.observaciones_calendario = request.POST.get("observaciones_calendario", "").strip()
            
            patient.save()
            messages.success(request, "Datos del paciente actualizados.")
            return redirect(_build_patient_detail_url(patient.pk, next_url))

    # Lista de usuarios para asignación
    users = User.objects.filter(is_active=True).order_by('username')

    # Historial de cambios (últimos 50)
    history = patient.history.select_related('user').all()[:50]

    # Lista de coberturas para el selector
    coverages = COBERTURAS_QR
    admission_recipient = _get_admission_email_for_sede(patient.sede)
    admission_attachments, admission_missing_labels = _get_patient_admission_attachments(patient)

    return render(request, "core/patient_detail.html", {
        "patient": patient,
        "attachment_form": attachment_form,
        "reprogram_form": reprogram_form,
        "back_url": back_url,
        "users": users,
        "history": history,
        "coverages": coverages,
        "admission_recipient": admission_recipient,
        "admission_attachment_names": [
            ADMISSION_ATTACHMENT_LABELS.get(attachment.type, attachment.get_type_display())
            for attachment in admission_attachments
        ],
        "admission_missing_labels": admission_missing_labels,
    })
# -------------------------------------------------------------
# COLORES CALENDARIO
# -------------------------------------------------------------
def _service_color(service_name: str) -> str:
    s = (service_name or "").upper().strip()
    if "TRAUMATOLOGIA" in s:
        return "#ff5722"  # Rojo-naranja
    if "HEMODINAMIA" in s:
        return "#e91e63"  # Rosa
    if "UROLOGIA" in s:
        return "#009688"  # Teal
    if "CABEZA" in s or "CUELLO" in s:
        return "#2196f3"  # Azul
    if "GENERAL" in s:
        return "#4caf50"  # Verde
    if "PLASTICA" in s:
        return "#9c27b0"  # Púrpura
    if "TORACICA" in s:
        return "#ff9800"  # Naranja
    if "OTORRINOLARINGOLOGIA" in s:
        return "#00bcd4"  # Cyan
    return "#607d8b"  # Gris por defecto


# -------------------------------------------------------------
# CALENDARIO
# -------------------------------------------------------------
def _get_calendar_snapshot(snapshot_id: str | None = None):
    active_snapshot = None
    if snapshot_id and snapshot_id.isdigit():
        active_snapshot = QuirofanoSnapshot.objects.select_related('created_by').filter(pk=int(snapshot_id)).first()
    if active_snapshot is None:
        active_snapshot = QuirofanoSnapshot.objects.select_related('created_by').first()
    return active_snapshot


def _build_snapshot_source_links(snapshot: QuirofanoSnapshot | None) -> list[dict]:
    if snapshot is None:
        return []

    media_base = (settings.MEDIA_URL or '/media/').rstrip('/')
    items = []
    for source in snapshot.source_files or []:
        item = dict(source)
        stored_path = (item.get('stored_path') or '').strip()
        if stored_path:
            item['url'] = f"{media_base}/{stored_path.lstrip('/')}"
        items.append(item)
    return items


def _suggest_linked_patients(entry: QuirofanoEntry, limit: int = 8) -> list[Patient]:
    nearby_patients = list(
        Patient.objects.filter(
            planned_date__gte=entry.surgery_date - timedelta(days=14),
            planned_date__lte=entry.surgery_date + timedelta(days=14),
        )
        .exclude(pk=entry.app_patient_id)
        .order_by('planned_date', 'full_name')[:250]
    )

    target_dni = normalize_dni(entry.dni)
    target_name = entry.patient_name_norm or _normalize_text(entry.patient_name)
    target_doctor = entry.doctor_norm or _normalize_text(entry.doctor)
    target_service = entry.canonical_service or canonicalize_service(entry.specialty_raw)
    target_coverage = _normalize_text(entry.coverage)

    scored_candidates = []
    for patient in nearby_patients:
        score = 0
        patient_dni = normalize_dni(patient.dni)
        patient_name = patient.full_name_norm or _normalize_text(patient.full_name)
        patient_doctor = _normalize_text(patient.doctor)
        patient_service = canonicalize_service(patient.service)
        patient_coverage = _normalize_text(patient.coverage)

        if target_dni and patient_dni == target_dni:
            score += 100
        if target_name and patient_name == target_name:
            score += 70
        if patient.planned_date == entry.surgery_date:
            score += 20
        if target_service and patient_service == target_service:
            score += 10
        if target_doctor and patient_doctor == target_doctor:
            score += 5
        if target_coverage and patient_coverage == target_coverage:
            score += 5

        if score > 0:
            scored_candidates.append((score, patient))

    scored_candidates.sort(key=lambda item: (-item[0], item[1].planned_date, item[1].full_name))
    return [patient for _, patient in scored_candidates[:limit]]


@login_required
def calendar_view(request):
    if request.method == 'POST':
        try:
            snapshot = import_daily_quirofano_snapshot(user=request.user)
        except RuntimeError as exc:
            messages.error(request, str(exc))
        except Exception as exc:
            messages.error(request, f'No se pudo actualizar el calendario-quirófano: {exc}')
        else:
            messages.success(
                request,
                (
                    f'Calendario-quirófano actualizado. {snapshot.total_entries} cirugías en quirófano, '
                    f'{snapshot.new_count} nuevas y {snapshot.changed_count} cambiadas.'
                )
            )
            return redirect(f"{reverse('core:calendar')}?snapshot={snapshot.pk}")

    requested_snapshot = (request.GET.get('snapshot') or '').strip()
    active_snapshot = _get_calendar_snapshot(requested_snapshot)
    snapshots = QuirofanoSnapshot.objects.select_related('created_by').all()[:20]

    comparison_summary = {
        'both': 0,
        'only_quirofano': 0,
        'only_app': 0,
    }
    source_files = []
    if active_snapshot is not None:
        entries = active_snapshot.entries.exclude(change_type=QuirofanoEntry.CHANGE_REMOVED)
        comparison_summary = {
            'both': entries.filter(resolved_comparison_status=QuirofanoEntry.COMPARISON_BOTH).count(),
            'only_quirofano': entries.filter(resolved_comparison_status=QuirofanoEntry.COMPARISON_ONLY_QUIROFANO).count(),
            'only_app': entries.filter(resolved_comparison_status=QuirofanoEntry.COMPARISON_ONLY_APP).count(),
        }
        source_files = _build_snapshot_source_links(active_snapshot)

    return render(request, 'core/calendar.html', {
        'active_snapshot': active_snapshot,
        'comparison_summary': comparison_summary,
        'snapshots': snapshots,
        'source_files': source_files,
    })


@login_required
def calendar_day_view(request):
    date_str = request.GET.get("date")
    date_obj = parse_date(date_str) if date_str else None

    requested_snapshot = (request.GET.get('snapshot') or '').strip()
    active_snapshot = _get_calendar_snapshot(requested_snapshot)

    q = (request.GET.get('q') or '').strip()
    comparison_filter = (request.GET.get('comparison') or '').strip()
    workflow_filter = (request.GET.get('workflow') or '').strip()
    change_filter = (request.GET.get('change') or '').strip()
    sede_filter = (request.GET.get('sede') or '').strip()
    service_filter = (request.GET.get('service') or '').strip()
    entries = []
    filter_options = {'sedes': [], 'services': []}
    day_summary = {
        'total': 0,
        'both': 0,
        'only_quirofano': 0,
        'only_app': 0,
        'new': 0,
        'changed': 0,
    }
    if date_obj and active_snapshot is not None:
        base_qs = active_snapshot.entries.select_related('app_patient').exclude(change_type=QuirofanoEntry.CHANGE_REMOVED).filter(surgery_date=date_obj)
        filter_options = {
            'sedes': list(base_qs.exclude(sede='').values_list('sede', flat=True).distinct().order_by('sede')),
            'services': list(base_qs.exclude(canonical_service='').values_list('canonical_service', flat=True).distinct().order_by('canonical_service')),
        }
        day_summary = {
            'total': base_qs.count(),
            'both': base_qs.filter(resolved_comparison_status=QuirofanoEntry.COMPARISON_BOTH).count(),
            'only_quirofano': base_qs.filter(resolved_comparison_status=QuirofanoEntry.COMPARISON_ONLY_QUIROFANO).count(),
            'only_app': base_qs.filter(resolved_comparison_status=QuirofanoEntry.COMPARISON_ONLY_APP).count(),
            'new': base_qs.filter(change_type=QuirofanoEntry.CHANGE_NEW).count(),
            'changed': base_qs.filter(change_type=QuirofanoEntry.CHANGE_CHANGED).count(),
        }

        filtered_qs = base_qs
        if q:
            q_norm = _normalize_text(q)
            filtered_qs = filtered_qs.filter(
                Q(patient_name_norm__contains=q_norm) |
                Q(dni__icontains=q) |
                Q(doctor__icontains=q) |
                Q(coverage__icontains=q)
            )
        if comparison_filter:
            filtered_qs = filtered_qs.filter(resolved_comparison_status=comparison_filter)
        if workflow_filter:
            filtered_qs = filtered_qs.filter(resolved_workflow_status=workflow_filter)
        if change_filter:
            filtered_qs = filtered_qs.filter(change_type=change_filter)
        if sede_filter:
            filtered_qs = filtered_qs.filter(sede=sede_filter)
        if service_filter:
            filtered_qs = filtered_qs.filter(canonical_service=service_filter)

        entries = list(
            filtered_qs.order_by('surgery_time', 'change_type', 'resolved_comparison_status', 'canonical_service', 'patient_name')
        )
        for entry in entries:
            entry.link_candidates = []
            entry.can_create_patient = not bool(entry.app_patient_id) and entry.resolved_comparison_status == QuirofanoEntry.COMPARISON_ONLY_QUIROFANO and entry.change_type != QuirofanoEntry.CHANGE_REMOVED
            if entry.can_create_patient:
                entry.link_candidates = _suggest_linked_patients(entry)

    return render(request, "core/calendar_day.html", {
        "date": date_obj,
        "entries": entries,
        'active_snapshot': active_snapshot,
        'current_path': request.get_full_path(),
        'filter_options': filter_options,
        'day_summary': day_summary,
        'filters': {
            'q': q,
            'comparison': comparison_filter,
            'workflow': workflow_filter,
            'change': change_filter,
            'sede': sede_filter,
            'service': service_filter,
        },
    })


@login_required
def calendar_events(request):
    # Obtener rango de fechas del calendario (parámetros start y end de FullCalendar)
    start = request.GET.get('start')
    end = request.GET.get('end')
    
    # Si no se envían fechas, usar un rango razonable (mes anterior y 2 meses adelante)
    if not start or not end:
        today = timezone.now().date()
        start_date = today - timedelta(days=30)
        end_date = today + timedelta(days=60)
    else:
        start_date = datetime.fromisoformat(start.replace('Z', '')).date()
        end_date = datetime.fromisoformat(end.replace('Z', '')).date()
    
    # Filtrar por rango de fechas y solo pacientes con planned_date
    # Limitar rango máximo para evitar recuperar demasiados eventos en una sola petición
    max_days = 120
    days = (end_date - start_date).days if (end_date and start_date) else 0
    if days > max_days:
        start_date = end_date - timedelta(days=max_days)

    events = []
    requested_snapshot = (request.GET.get('snapshot') or '').strip()
    active_snapshot = _get_calendar_snapshot(requested_snapshot)

    if active_snapshot is None:
        return JsonResponse(events, safe=False)

    qs = active_snapshot.entries.select_related('app_patient').exclude(change_type=QuirofanoEntry.CHANGE_REMOVED).filter(
        surgery_date__gte=start_date,
        surgery_date__lte=end_date,
    ).order_by('surgery_date', 'surgery_time', 'patient_name')

    status_colors = {
        QuirofanoEntry.COMPARISON_BOTH: '#10b981',
        QuirofanoEntry.COMPARISON_ONLY_QUIROFANO: '#f97316',
        QuirofanoEntry.COMPARISON_ONLY_APP: '#3b82f6',
    }

    snapshot_suffix = f"&snapshot={active_snapshot.id}" if active_snapshot is not None else ''

    for entry in qs:
        color = status_colors.get(entry.resolved_comparison_status, '#64748b')
        title = entry.patient_name
        if entry.surgery_time:
            title = f"{entry.surgery_time.strftime('%H:%M')} - {entry.patient_name}"

        if entry.surgery_time:
            start_datetime = datetime.combine(entry.surgery_date, entry.surgery_time)
            start_str = start_datetime.isoformat()
            all_day = False
        else:
            start_str = entry.surgery_date.isoformat()
            all_day = True

        app_patient = entry.app_patient
        day_url = f"{reverse('core:calendar_day')}?date={entry.surgery_date.isoformat()}{snapshot_suffix}"
        patient_url = _build_patient_detail_url(app_patient.id, day_url) if app_patient else ''
        events.append({
            'id': f'entry-{entry.id}',
            'title': title,
            'start': start_str,
            'allDay': all_day,
            'backgroundColor': color,
            'borderColor': color,
            'textColor': '#ffffff',
            'editable': bool(app_patient),
            'classNames': [
                f"comparison-{entry.resolved_comparison_status.lower()}",
                f"change-{entry.change_type.lower()}",
            ],
            'extendedProps': {
                'entry_id': entry.id,
                'patient_id': app_patient.id if app_patient else None,
                'patient_url': patient_url,
                'day_url': day_url,
                'coverage': entry.coverage,
                'doctor': entry.doctor,
                'service': entry.canonical_service or entry.specialty_raw,
                'status': app_patient.get_status_display() if app_patient else 'Sin paciente en panel',
                'workflow_status': entry.get_resolved_workflow_status_display() if entry.resolved_workflow_status else '',
                'comparison_status': entry.get_resolved_comparison_status_display(),
                'comparison_status_code': entry.resolved_comparison_status,
                'change_type': entry.get_change_type_display(),
                'change_type_code': entry.change_type,
                'sede': entry.sede or '',
                'surgery_time': entry.surgery_time.strftime('%H:%M') if entry.surgery_time else '',
                'dni': entry.dni,
                'material_status': app_patient.material_status if app_patient else '',
                'en_quirofano': app_patient.en_quirofano if app_patient else False,
                'observaciones_calendario': app_patient.observaciones_calendario if app_patient else '',
                'source_file': entry.source_file or '',
                'previous_summary': (
                    f"Antes: {entry.previous_surgery_date.strftime('%d/%m/%Y')}"
                    if entry.previous_surgery_date else ''
                ),
                'source': 'QUIROFANO' if entry.comparison_status != QuirofanoEntry.COMPARISON_ONLY_APP else 'APP',
            }
        })
    return JsonResponse(events, safe=False)


@login_required
@require_POST
def calendar_move_event(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    try:
        data = json.loads(request.body)
        new_date_str = data.get("new_date")
    except:
        new_date_str = request.POST.get("date")

    try:
        new_date = datetime.strptime(new_date_str, '%Y-%m-%d').date()
    except:
        return JsonResponse({"success": False, "error": "Fecha inválida"}, status=400)

    patient.planned_date = new_date
    patient.last_reprogram_date = timezone.now()
    patient.last_reprogram_reason = "Reprogramado desde calendario"
    patient.status = Patient.STATUS_REPROGRAMADO
    patient.save()

    return JsonResponse({"success": True})


# -------------------------------------------------------------
# QUIROFANO
# -------------------------------------------------------------
@login_required
def quirofano_view(request):
    from collections import defaultdict as _defaultdict

    if request.method == 'POST':
        try:
            snapshot = import_daily_quirofano_snapshot(user=request.user)
        except RuntimeError as exc:
            messages.error(request, str(exc))
        except Exception as exc:
            messages.error(request, f'No se pudo actualizar el quirófano: {exc}')
        else:
            messages.success(
                request,
                f'Quirófano actualizado. {snapshot.total_entries} cirugías, '
                f'{snapshot.new_count} nuevas, {snapshot.changed_count} cambiadas.',
            )
            return redirect(f"{reverse('core:quirofano_management')}?snapshot={snapshot.pk}")

    requested_snapshot = (request.GET.get('snapshot') or '').strip()
    active_snapshot = _get_calendar_snapshot(requested_snapshot)
    snapshots = QuirofanoSnapshot.objects.select_related('created_by').all()[:20]

    date_w, date_to_w = default_quirofano_window()

    comparison_summary = {'both': 0, 'only_quirofano': 0, 'only_app': 0}
    active_snapshot_files = []
    day_groups = []
    removed_entries = []
    sede_choices = list(Patient.SEDE_CHOICES)
    service_choices = []

    current_filters = {
        'date': request.GET.get('date', ''),
        'change': request.GET.get('change', ''),
        'comparison': request.GET.get('comparison', ''),
        'sede': request.GET.get('sede', ''),
        'service': request.GET.get('service', ''),
        'q': request.GET.get('q', ''),
    }

    default_whatsapp_timings = {
        'load_timeout_ms': DEFAULT_WHATSAPP_LOAD_TIMEOUT_MS,
        'post_send_delay_ms': DEFAULT_WHATSAPP_POST_SEND_DELAY_MS,
        'between_send_delay_ms': DEFAULT_WHATSAPP_BETWEEN_SEND_DELAY_MS,
    }

    if active_snapshot is not None:
        base_qs = active_snapshot.entries.select_related('app_patient')
        active_qs = base_qs.exclude(change_type=QuirofanoEntry.CHANGE_REMOVED)

        comparison_summary = {
            'both': active_qs.filter(resolved_comparison_status=QuirofanoEntry.COMPARISON_BOTH).count(),
            'only_quirofano': active_qs.filter(resolved_comparison_status=QuirofanoEntry.COMPARISON_ONLY_QUIROFANO).count(),
            'only_app': active_qs.filter(resolved_comparison_status=QuirofanoEntry.COMPARISON_ONLY_APP).count(),
        }

        active_snapshot_files = _build_snapshot_source_links(active_snapshot)
        service_choices = sorted(
            active_qs.exclude(canonical_service='').values_list('canonical_service', flat=True).distinct()
        )

        removed_entries = list(base_qs.filter(change_type=QuirofanoEntry.CHANGE_REMOVED).order_by('patient_name'))

        filtered_qs = active_qs
        if current_filters['date']:
            try:
                filter_date = datetime.strptime(current_filters['date'], '%Y-%m-%d').date()
                filtered_qs = filtered_qs.filter(surgery_date=filter_date)
            except ValueError:
                pass
        if current_filters['change']:
            filtered_qs = filtered_qs.filter(change_type=current_filters['change'])
        if current_filters['comparison']:
            filtered_qs = filtered_qs.filter(resolved_comparison_status=current_filters['comparison'])
        if current_filters['sede']:
            filtered_qs = filtered_qs.filter(sede=current_filters['sede'])
        if current_filters['service']:
            filtered_qs = filtered_qs.filter(canonical_service=current_filters['service'])
        if current_filters['q']:
            q = current_filters['q'].strip()
            filtered_qs = filtered_qs.filter(
                Q(patient_name_norm__icontains=_normalize_text(q))
                | Q(dni__icontains=q)
                | Q(doctor_norm__icontains=_normalize_text(q))
            )

        filtered_qs = filtered_qs.order_by('surgery_date', 'surgery_time', 'patient_name')

        grouped: dict = _defaultdict(list)
        for entry in filtered_qs:
            entry.is_in_panel = bool(entry.app_patient_id)
            entry.panel_service = entry.app_patient.service if entry.app_patient_id else ''
            entry.whatsapp_phone = entry.report_phone or (entry.app_patient.phone if entry.app_patient_id else '')
            entry.whatsapp_source = 'Reporte' if entry.report_phone else ('Panel' if entry.app_patient_id and entry.app_patient.phone else '')
            grouped[entry.surgery_date].append(entry)
        day_groups = [
            {'date': d, 'entries': grouped[d]}
            for d in sorted(grouped.keys())
        ]

    filter_params = {k: v for k, v in current_filters.items() if v}
    if active_snapshot:
        filter_params['snapshot'] = active_snapshot.pk
    pagination_qs = urlencode(filter_params)

    return render(request, 'core/quirofano.html', {
        'active_snapshot': active_snapshot,
        'comparison_summary': comparison_summary,
        'snapshots': snapshots,
        'current_filters': current_filters,
        'sede_choices': sede_choices,
        'service_choices': service_choices,
        'active_snapshot_files': active_snapshot_files,
        'day_groups': day_groups,
        'removed_entries': removed_entries,
        'pagination_qs': pagination_qs,
        'default_window': {'date_from': date_w, 'date_to': date_to_w},
        'default_whatsapp_timings': default_whatsapp_timings,
        'quirofano_next_url': _quirofano_management_next_url(request, active_snapshot.pk if active_snapshot else None),
    })


@login_required
@require_POST
def quirofano_entry_status_update(request, pk):
    entry = get_object_or_404(QuirofanoEntry, pk=pk)
    manual_comparison = request.POST.get('manual_comparison_status', '').strip()
    valid_comparisons = {'', 'BOTH', 'ONLY_QUIROFANO', 'ONLY_APP'}
    if manual_comparison in valid_comparisons:
        entry.manual_comparison_status = manual_comparison
        entry.resolved_comparison_status = manual_comparison or entry.comparison_status
        entry.save(update_fields=['manual_comparison_status', 'resolved_comparison_status'])
    next_url = request.POST.get('next') or reverse('core:quirofano_management')
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse('core:quirofano_management')
    return redirect(next_url)


@login_required
@require_POST
def quirofano_whatsapp_autosend(request):
    raw_ids = (request.POST.get('entry_ids') or '').strip()
    next_url = _quirofano_management_next_url(request)
    entry_ids = [int(value) for value in raw_ids.split(',') if value.strip().isdigit()]

    if not entry_ids:
        messages.error(request, 'Seleccioná al menos un paciente visible para iniciar el envío automático.')
        return redirect(next_url)

    entries_by_id = {
        entry.id: entry
        for entry in QuirofanoEntry.objects.select_related('app_patient').filter(pk__in=entry_ids)
    }
    ordered_entries = [entries_by_id[entry_id] for entry_id in entry_ids if entry_id in entries_by_id]

    if not ordered_entries:
        messages.error(request, 'No se encontraron entradas válidas para el envío automático.')
        return redirect(next_url)

    items, skipped = _build_quirofano_whatsapp_batch(ordered_entries)
    if not items:
        messages.error(request, 'Ninguno de los seleccionados tiene un teléfono válido para WhatsApp.')
        return redirect(next_url)

    load_timeout_ms = _parse_positive_int(request.POST.get('load_timeout_ms'), DEFAULT_WHATSAPP_LOAD_TIMEOUT_MS)
    post_send_delay_ms = _parse_positive_int(request.POST.get('post_send_delay_ms'), DEFAULT_WHATSAPP_POST_SEND_DELAY_MS)
    between_send_delay_ms = _parse_positive_int(request.POST.get('between_send_delay_ms'), DEFAULT_WHATSAPP_BETWEEN_SEND_DELAY_MS)

    batch_dir = Path(settings.MEDIA_ROOT) / 'quirofano_reports' / 'whatsapp_batches'
    batch_dir.mkdir(parents=True, exist_ok=True)
    timestamp = timezone.localtime().strftime('%Y%m%d_%H%M%S')
    batch_path = batch_dir / f'whatsapp_autosend_{timestamp}.json'
    batch_payload = {
        'created_at': timezone.localtime().isoformat(),
        'created_by': request.user.username,
        'items': items,
        'skipped': skipped,
        'options': {
            'load_timeout_ms': load_timeout_ms,
            'post_send_delay_ms': post_send_delay_ms,
            'between_send_delay_ms': between_send_delay_ms,
        },
    }
    batch_path.write_text(json.dumps(batch_payload, ensure_ascii=False, indent=2), encoding='utf-8')

    manage_py = Path(settings.BASE_DIR) / 'manage.py'
    command = [
        sys.executable,
        str(manage_py),
        'send_quirofano_whatsapp',
        '--batch-file', str(batch_path),
        '--load-timeout-ms', str(load_timeout_ms),
        '--post-send-delay-ms', str(post_send_delay_ms),
        '--between-send-delay-ms', str(between_send_delay_ms),
    ]

    creationflags = 0
    creationflags |= getattr(subprocess, 'DETACHED_PROCESS', 0)
    creationflags |= getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)

    try:
        subprocess.Popen(
            command,
            cwd=str(settings.BASE_DIR),
            creationflags=creationflags,
            close_fds=bool(creationflags),
        )
    except Exception as exc:
        messages.error(request, f'No se pudo iniciar el flujo automático de WhatsApp: {exc}')
        return redirect(next_url)

    success_message = (
        f'Se inició el envío automático para {len(items)} paciente(s). '
        'Se abrirá/continuará WhatsApp Web en una ventana controlada con espera de carga.'
    )
    if skipped:
        success_message += f' Se omitieron {len(skipped)} sin teléfono válido.'
    messages.success(request, success_message)
    return redirect(next_url)



# -------------------------------------------------------------
# ESTADISTICAS
# -------------------------------------------------------------
@login_required
def stats_view(request):
    return render(request, 'core/stats.html')


def _apply_stats_filters(qs, service_filter="", coverage_filter="", doctor_filter=""):
    if service_filter:
        qs = qs.filter(service__icontains=service_filter)
    if coverage_filter:
        qs = qs.filter(coverage__icontains=coverage_filter)
    if doctor_filter:
        qs = qs.filter(doctor__icontains=doctor_filter)
    return qs


@login_required
def stats_data(request):
    """
    Retorna estadísticas de TODAS las solicitudes cargadas en el sistema.
    El total general y conteos por servicio/cobertura NO están limitados por fecha.
    El período seleccionado se usa solo para análisis de evolución temporal.
    
    NOTA: El estado REALIZADO se excluye de todas las estadísticas
    ya que es específico únicamente para hemodinámica.
    """
    # Rango por fecha seleccionada (cirugía o pedido)
    today = timezone.localdate()
    default_from = today - timedelta(days=6)
    default_to = today

    start = request.GET.get("from") or str(default_from)   # YYYY-MM-DD
    end = request.GET.get("to") or str(default_to)
    
    # Filtros adicionales
    service_filter = request.GET.get("service", "").strip()
    coverage_filter = request.GET.get("coverage", "").strip()
    doctor_filter = request.GET.get("doctor", "").strip()
    date_field = (request.GET.get("date_field") or "planned_date").strip()
    if date_field not in {"planned_date", "created_at"}:
        date_field = "planned_date"

    date_lookup = "planned_date" if date_field == "planned_date" else "created_at__date"

    cache_key = f"stats_data:{start}:{end}:{service_filter}:{coverage_filter}:{doctor_filter}:{date_field}"
    cached_payload = cache.get(cache_key)
    if cached_payload is not None:
        return JsonResponse(cached_payload)

    # parse (seguro)
    try:
        start_d = parse_date(start) or default_from
    except Exception:
        start_d = default_from
    try:
        end_d = parse_date(end) or default_to
    except Exception:
        end_d = default_to

    if end_d < start_d:
        start_d, end_d = end_d, start_d

    # TOTAL GENERAL: Todas las solicitudes del sistema (sin filtro de fecha)
    base_all = Patient.objects.exclude(status=Patient.STATUS_REALIZADO)
    base_all = _apply_stats_filters(base_all, service_filter, coverage_filter, doctor_filter)

    doctors_base = Patient.objects.exclude(status=Patient.STATUS_REALIZADO)
    if service_filter:
        doctors_base = doctors_base.filter(service__icontains=service_filter)
    if coverage_filter:
        doctors_base = doctors_base.filter(coverage__icontains=coverage_filter)

    overall_total = base_all.count()
    overall_by_status = list(base_all.values("status").annotate(count=Count("id")).order_by("-count"))
    by_service = list(base_all.values("service").annotate(count=Count("id")).order_by("-count"))
    by_coverage = list(base_all.values("coverage").annotate(count=Count("id")).order_by("-count"))
    by_doctor = list(
        base_all.exclude(doctor__isnull=True)
        .exclude(doctor__exact="")
        .values("doctor")
        .annotate(count=Count("id"))
        .order_by("-count", "doctor")
    )

    # Reusar conteos para evitar llamadas repetidas
    authorized_count = base_all.filter(status=Patient.STATUS_AUTORIZADO).count()
    not_authorized_count = overall_total - authorized_count

    # Queryset base según fecha elegida (para períodos y evolución temporal)
    base = Patient.objects.filter(**{
        f"{date_lookup}__gte": start_d,
        f"{date_lookup}__lte": end_d,
    }).exclude(status=Patient.STATUS_REALIZADO)
    
    # Aplicar filtros
    base = _apply_stats_filters(base, service_filter, coverage_filter, doctor_filter)

    # Totales paralelos para ver ambas fechas con el mismo rango (excluir REALIZADO)
    base_by_created = Patient.objects.filter(created_at__date__gte=start_d, created_at__date__lte=end_d).exclude(status=Patient.STATUS_REALIZADO)
    base_by_planned = Patient.objects.filter(planned_date__gte=start_d, planned_date__lte=end_d).exclude(status=Patient.STATUS_REALIZADO)
    base_by_created = _apply_stats_filters(base_by_created, service_filter, coverage_filter, doctor_filter)
    base_by_planned = _apply_stats_filters(base_by_planned, service_filter, coverage_filter, doctor_filter)
    # Reusar conteos (ejecuta .count() una vez cada uno)
    base_by_created_count = base_by_created.count()
    base_by_planned_count = base_by_planned.count()
    
    # Listas únicas para filtros (todos los pacientes, no filtrados)
    all_services = cache.get("stats_all_services")
    if all_services is None:
        all_services = list(Patient.objects.values_list('service', flat=True).distinct().order_by('service'))
        all_services = [s for s in all_services if s]
        cache.set("stats_all_services", all_services, 3600)

    all_doctors = cache.get("stats_all_doctors")
    if all_doctors is None:
        all_doctors = list(Patient.objects.values_list('doctor', flat=True).distinct().order_by('doctor'))
        all_doctors = [d for d in all_doctors if d]
        cache.set("stats_all_doctors", all_doctors, 3600)

    all_coverages = cache.get("stats_all_coverages")
    if all_coverages is None:
        all_coverages = list(Patient.objects.values_list('coverage', flat=True).distinct().order_by('coverage'))
        all_coverages = [c for c in all_coverages if c]
        cache.set("stats_all_coverages", all_coverages, 3600)

    filtered_doctors = list(
        doctors_base.exclude(doctor__isnull=True)
        .exclude(doctor__exact="")
        .values_list('doctor', flat=True)
        .distinct()
        .order_by('doctor')
    )

    # service + status pivot (para stacked + detalle) - usar todas las solicitudes
    raw = list(base_all.values("service", "status").annotate(count=Count("id")))
    pivot = {}
    for r in raw:
        svc = (r.get("service") or "").strip() or "Sin servicio"
        st = (r.get("status") or "").strip() or "Sin estado"
        pivot.setdefault(svc, {"service": svc, "total": 0, "status_counts": {}})
        pivot[svc]["status_counts"][st] = pivot[svc]["status_counts"].get(st, 0) + r["count"]
        pivot[svc]["total"] += r["count"]
    by_service_status = sorted(pivot.values(), key=lambda x: x["total"], reverse=True)

    # Períodos: si el rango > 7 días => se parte en bloques consecutivos de 7 días
    # Periodo 1 = primeros 7 días; Periodo 2 = siguientes 7; Periodo 3 = siguientes 7; etc.
    days = (end_d - start_d).days + 1
    periods = []
    if days > 7:
        i = 0
        p_start = start_d
        while p_start <= end_d:
            p_end = min(p_start + timedelta(days=6), end_d)
            pq = Patient.objects.filter(**{
                f"{date_lookup}__gte": p_start,
                f"{date_lookup}__lte": p_end,
            }).exclude(status=Patient.STATUS_REALIZADO)
            pq = _apply_stats_filters(pq, service_filter, coverage_filter, doctor_filter)
            
            # Datos adicionales por período (excluir REALIZADO)
            pq_created = Patient.objects.filter(created_at__date__gte=p_start, created_at__date__lte=p_end).exclude(status=Patient.STATUS_REALIZADO)
            pq_planned = Patient.objects.filter(planned_date__gte=p_start, planned_date__lte=p_end).exclude(status=Patient.STATUS_REALIZADO)
            pq_created = _apply_stats_filters(pq_created, service_filter, coverage_filter, doctor_filter)
            pq_planned = _apply_stats_filters(pq_planned, service_filter, coverage_filter, doctor_filter)
            
            periods.append({
                "label": f"{p_start.strftime('%d/%m')}–{p_end.strftime('%d/%m')}",
                "start": str(p_start),
                "end": str(p_end),
                "total": pq.count(),
                "by_status": list(pq.values("status").annotate(count=Count("id")).order_by("-count")),
                "created_count": pq_created.count(),
                "planned_count": pq_planned.count(),
                "authorized_count": pq_planned.filter(status=Patient.STATUS_AUTORIZADO).count(),
            })
            i += 1
            p_start = p_start + timedelta(days=7)

    # Evolución diaria (útil para gráficos de línea de tiempo)
    daily_created = []
    daily_planned = []
    daily_authorized = []
    
    if days <= 90:  # Solo para rangos razonables
        current = start_d
        while current <= end_d:
            dc = Patient.objects.filter(created_at__date=current).exclude(status=Patient.STATUS_REALIZADO)
            dp = Patient.objects.filter(planned_date=current).exclude(status=Patient.STATUS_REALIZADO)
            dc = _apply_stats_filters(dc, service_filter, coverage_filter, doctor_filter)
            dp = _apply_stats_filters(dp, service_filter, coverage_filter, doctor_filter)
            
            daily_created.append({
                "date": str(current),
                "count": dc.count()
            })
            daily_planned.append({
                "date": str(current),
                "count": dp.count(),
                "authorized": dp.filter(status=Patient.STATUS_AUTORIZADO).count()
            })
            daily_authorized.append({
                "date": str(current),
                "count": dp.filter(status=Patient.STATUS_AUTORIZADO).count()
            })
            current += timedelta(days=1)

    payload = {
        "range": {"start": str(start_d), "end": str(end_d), "date_field": date_field, "days": days},
        "overall": {"total": overall_total, "by_status": overall_by_status},
        "authorization": {
            "authorized": authorized_count,
            "not_authorized": not_authorized_count,
        },
        "dual_totals": {
            "created_at": base_by_created_count,
            "planned_date": base_by_planned_count,
        },
        "by_service": by_service,
        "by_doctor": by_doctor,
        "by_coverage": by_coverage,
        "by_service_status": by_service_status,
        "periods": periods,
        "daily_created": daily_created if days <= 90 else [],
        "daily_planned": daily_planned if days <= 90 else [],
        "daily_authorized": daily_authorized if days <= 90 else [],
        "all_services": all_services,
        "all_coverages": all_coverages,
        "all_doctors": all_doctors,
        "filtered_doctors": filtered_doctors,
    }
    cache.set(cache_key, payload, 600)
    return JsonResponse(payload)


@login_required
def export_excel(request):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pacientes"

    ws.append([
        "Tracking ID", "Nombre", "DNI", "Cobertura",
        "Médico", "Servicio", "Fecha intervención", "Estado",
        "Fecha carga"
    ])

    # Usar values_list + iterator para reducir memoria y evitar instanciar modelos completos
    status_map = dict(Patient.STATUS_CHOICES)
    service_filter = request.GET.get("service", "").strip()
    coverage_filter = request.GET.get("coverage", "").strip()
    doctor_filter = request.GET.get("doctor", "").strip()
    date_field = (request.GET.get("date_field") or "").strip()
    start = request.GET.get("from", "").strip()
    end = request.GET.get("to", "").strip()

    qs = _apply_stats_filters(Patient.objects.all(), service_filter, coverage_filter, doctor_filter)
    if date_field in {"planned_date", "created_at"}:
        date_lookup = "planned_date" if date_field == "planned_date" else "created_at__date"
        start_d = parse_date(start) if start else None
        end_d = parse_date(end) if end else None
        if start_d and end_d and end_d < start_d:
            start_d, end_d = end_d, start_d
        if start_d:
            qs = qs.filter(**{f"{date_lookup}__gte": start_d})
        if end_d:
            qs = qs.filter(**{f"{date_lookup}__lte": end_d})

    qs = qs.values_list(
        'tracking_id', 'full_name', 'dni', 'coverage', 'doctor', 'service', 'planned_date', 'status', 'created_at'
    ).order_by('-created_at').iterator()
    for tracking_id, full_name, dni, coverage, doctor, service, planned_date, status, created_at in qs:
        ws.append([
            tracking_id,
            full_name,
            dni,
            coverage,
            doctor,
            service,
            planned_date.isoformat() if planned_date else "",
            status_map.get(status, status) if status is not None else "",
            created_at.date().isoformat() if created_at else "",
        ])

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = "attachment; filename=pacientes.xlsx"
    return response

@login_required
def export_pdf(request):
    """
    Genera un PDF completo con todas las estadísticas, gráficos y detalles.
    
    IMPORTANTE: El total general y conteos por servicio/cobertura muestran
    TODAS las solicitudes cargadas en el sistema, sin límite de fecha.
    El período seleccionado se usa solo para análisis de evolución temporal
    y comparativas por período.
    
    Soporta filtros por servicio y profesional.
    
    NOTA: El estado REALIZADO se excluye de todas las estadísticas
    ya que es específico únicamente para hemodinámica.
    """
    # Obtener datos de estadísticas (igual que stats_data)
    today = timezone.localdate()
    default_from = today - timedelta(days=6)
    default_to = today

    start = request.GET.get("from") or str(default_from)
    end = request.GET.get("to") or str(default_to)
    
    # Filtros adicionales
    service_filter = request.GET.get("service", "").strip()
    coverage_filter = request.GET.get("coverage", "").strip()
    doctor_filter = request.GET.get("doctor", "").strip()
    
    # Campo de fecha a usar (planned_date o created_at)
    date_field = (request.GET.get("date_field") or "planned_date").strip()
    if date_field not in {"planned_date", "created_at"}:
        date_field = "planned_date"
    
    date_lookup = "planned_date" if date_field == "planned_date" else "created_at__date"

    try:
        start_d = parse_date(start) or default_from
    except Exception:
        start_d = default_from
    try:
        end_d = parse_date(end) or default_to
    except Exception:
        end_d = default_to

    if end_d < start_d:
        start_d, end_d = end_d, start_d

    # TOTAL GENERAL: Todas las solicitudes del sistema (sin filtro de fecha)
    base_all = Patient.objects.exclude(status=Patient.STATUS_REALIZADO)
    base_all = _apply_stats_filters(base_all, service_filter, coverage_filter, doctor_filter)

    overall_total = base_all.count()
    overall_by_status = list(base_all.values("status").annotate(count=Count("id")).order_by("-count"))
    by_service = list(base_all.values("service").annotate(count=Count("id")).order_by("-count"))
    by_coverage = list(base_all.values("coverage").annotate(count=Count("id")).order_by("-count"))

    # Pivot service + status (para todas las solicitudes)
    raw = list(base_all.values("service", "status").annotate(count=Count("id")))
    pivot = {}
    for r in raw:
        svc = (r.get("service") or "").strip() or "Sin servicio"
        st = (r.get("status") or "").strip() or "Sin estado"
        pivot.setdefault(svc, {"service": svc, "total": 0, "status_counts": {}})
        pivot[svc]["status_counts"][st] = pivot[svc]["status_counts"].get(st, 0) + r["count"]
        pivot[svc]["total"] += r["count"]
    by_service_status = sorted(pivot.values(), key=lambda x: x["total"], reverse=True)

    # Queryset base según fecha elegida (para períodos y evolución temporal)
    base = Patient.objects.filter(**{
        f"{date_lookup}__gte": start_d,
        f"{date_lookup}__lte": end_d,
    }).exclude(status=Patient.STATUS_REALIZADO)
    
    # Aplicar filtros
    base = _apply_stats_filters(base, service_filter, coverage_filter, doctor_filter)

    # Períodos de 7 días
    days = (end_d - start_d).days + 1
    periods = []
    
    # Totales duales para comparativa (excluir REALIZADO que es solo para hemodinámica)
    base_by_created = Patient.objects.filter(created_at__date__gte=start_d, created_at__date__lte=end_d).exclude(status=Patient.STATUS_REALIZADO)
    base_by_planned = Patient.objects.filter(planned_date__gte=start_d, planned_date__lte=end_d).exclude(status=Patient.STATUS_REALIZADO)
    base_by_created = _apply_stats_filters(base_by_created, service_filter, coverage_filter, doctor_filter)
    base_by_planned = _apply_stats_filters(base_by_planned, service_filter, coverage_filter, doctor_filter)
    
    total_created = base_by_created.count()
    total_planned = base_by_planned.count()
    total_authorized = base_by_planned.filter(status=Patient.STATUS_AUTORIZADO).count()
    
    if days > 7:
        p_start = start_d
        while p_start <= end_d:
            p_end = min(p_start + timedelta(days=6), end_d)
            pq = Patient.objects.filter(**{
                f"{date_lookup}__gte": p_start,
                f"{date_lookup}__lte": p_end,
            }).exclude(status=Patient.STATUS_REALIZADO)
            pq = _apply_stats_filters(pq, service_filter, coverage_filter, doctor_filter)
            
            # Datos adicionales por período (excluir REALIZADO)
            pq_created = Patient.objects.filter(created_at__date__gte=p_start, created_at__date__lte=p_end).exclude(status=Patient.STATUS_REALIZADO)
            pq_planned = Patient.objects.filter(planned_date__gte=p_start, planned_date__lte=p_end).exclude(status=Patient.STATUS_REALIZADO)
            pq_created = _apply_stats_filters(pq_created, service_filter, coverage_filter, doctor_filter)
            pq_planned = _apply_stats_filters(pq_planned, service_filter, coverage_filter, doctor_filter)
            
            periods.append({
                "label": f"{p_start.strftime('%d/%m')}–{p_end.strftime('%d/%m')}",
                "start": str(p_start),
                "end": str(p_end),
                "total": pq.count(),
                "by_status": list(pq.values("status").annotate(count=Count("id")).order_by("-count")),
                "created_count": pq_created.count(),
                "planned_count": pq_planned.count(),
                "authorized_count": pq_planned.filter(status=Patient.STATUS_AUTORIZADO).count(),
            })
            p_start = p_start + timedelta(days=7)

    # Crear PDF
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Colores consistentes para estados
    status_labels = dict(Patient.STATUS_CHOICES)
    status_colors = {
        Patient.STATUS_PENDIENTE: '#ffc107',
        Patient.STATUS_PENDIENTE_PRESTADOR: '#17a2b8',
        Patient.STATUS_PENDIENTE_MEDICO: '#0ea5e9',
        Patient.STATUS_PENDIENTE_PACIENTE: '#14b8a6',
        'AUTORIZADO': '#28a745',
        Patient.STATUS_PENDIENTE_COMERCIAL_PRESUPUESTO: '#6f42c1',
        Patient.STATUS_AUTORIZADO_MATERIAL_PENDIENTE: '#fd7e14',
        Patient.STATUS_RECHAZO_COBERTURA: '#dc3545',
        'REPROGRAMADO': '#6c757d',
        Patient.STATUS_CANCELA_MEDICO: '#991b1b',
        Patient.STATUS_CANCELA_PTE: '#b91c1c',
    }

    def add_header(pdf, y):
        """Agrega encabezado a cada página"""
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(50, y, "Estadísticas - Panel OVA")
        pdf.setFont("Helvetica", 10)
        pdf.drawString(50, y - 20, f"Total de todas las solicitudes cargadas en el sistema")
        base_label = "Fecha de cirugía" if date_field == "planned_date" else "Fecha de pedido"
        pdf.drawString(50, y - 35, f"Período de análisis: {start_d.strftime('%d/%m/%Y')} - {end_d.strftime('%d/%m/%Y')} ({base_label})")
        y_offset = 50
        if service_filter:
            pdf.drawString(50, y - y_offset, f"Servicio: {service_filter}")
            y_offset += 15
        if coverage_filter:
            pdf.drawString(50, y - y_offset, f"Cobertura: {coverage_filter}")
            y_offset += 15
        if doctor_filter:
            pdf.drawString(50, y - y_offset, f"Profesional: {doctor_filter}")
            y_offset += 15
        pdf.drawString(50, y - y_offset, f"Generado: {timezone.now().strftime('%d/%m/%Y %H:%M')}")
        return y - y_offset - 25

    def create_pie_chart(data, title, labels_key='status', values_key='count'):
        """Crea gráfico de torta y retorna buffer de imagen"""
        fig, ax = plt.subplots(figsize=(8, 6.5))
        
        labels = []
        sizes = []
        colors_list = []
        legend_labels = []
        
        # Paleta de colores vibrantes para servicios/coberturas
        default_colors = [
            '#FF6B6B', '#4ECDC4', '#45B7D1', '#FFA07A', '#98D8C8',
            '#F7DC6F', '#BB8FCE', '#85C1E2', '#F8B739', '#52B788'
        ]
        
        total = sum(item[values_key] for item in data)
        
        for idx, item in enumerate(data):
            label = item.get(labels_key) or "Sin especificar"
            if labels_key == 'status':
                label_display = status_labels.get(label, label)
            else:
                label_display = label
            
            percentage = (item[values_key] / total * 100) if total > 0 else 0
            legend_labels.append(f"{label_display}: {item[values_key]} ({percentage:.1f}%)")
            sizes.append(item[values_key])
            
            if labels_key == 'status':
                status_key = item.get(labels_key)
                colors_list.append(status_colors.get(status_key, '#6c757d'))
            else:
                colors_list.append(default_colors[idx % len(default_colors)])
        
        # Crear el pie chart SIN porcentajes internos para evitar superposición
        wedges, texts = ax.pie(
            sizes, 
            labels=None,
            startangle=90,
            colors=colors_list
        )
        
        ax.set_title(title, fontsize=14, fontweight='bold', pad=20)
        
        # Agregar leyenda grande y clara debajo del gráfico
        ax.legend(
            wedges,
            legend_labels,
            loc='center',
            bbox_to_anchor=(0.5, -0.08),
            ncol=2 if len(labels) > 4 else 1,
            fontsize=10,
            frameon=True,
            shadow=True,
            fancybox=True
        )
        
        buf = BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return buf

    def create_bar_chart(data, title, xlabel, ylabel):
        """Crea gráfico de barras y retorna buffer de imagen"""
        fig, ax = plt.subplots(figsize=(7, 4.5))
        
        labels = [item['label'] for item in data]
        values = [item['total'] for item in data]
        
        # Colores degradados para las barras
        colors_gradient = [
            '#3498db', '#5dade2', '#85c1e9', '#aed6f1', 
            '#d6eaf8', '#ebf5fb', '#f0f3f4', '#d5d8dc'
        ]
        bar_colors = [colors_gradient[i % len(colors_gradient)] for i in range(len(labels))]
        
        bars = ax.bar(labels, values, color=bar_colors, edgecolor='#2c3e50', linewidth=0.5)
        ax.set_xlabel(xlabel, fontsize=10, fontweight='bold')
        ax.set_ylabel(ylabel, fontsize=10, fontweight='bold')
        ax.set_title(title, fontsize=12, fontweight='bold', pad=15)
        
        # Rotar etiquetas si son muchas
        if len(labels) > 5:
            plt.xticks(rotation=45, ha='right', fontsize=8)
        else:
            plt.xticks(fontsize=9)
        
        # Agregar valores sobre barras
        for bar in bars:
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height,
                   f'{int(height)}', ha='center', va='bottom', fontsize=9, fontweight='bold')
        
        # Grid para mejor legibilidad
        ax.grid(axis='y', alpha=0.3, linestyle='--', linewidth=0.5)
        ax.set_axisbelow(True)
        
        buf = BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return buf

    def create_stacked_bar_chart(data, title):
        """Crea gráfico de barras apiladas por servicio y estado"""
        fig, ax = plt.subplots(figsize=(9, 5.5))
        
        services = [item['service'][:25] for item in data[:10]]  # Top 10 servicios
        
        # Obtener todos los estados posibles
        all_statuses = set()
        for item in data[:10]:
            all_statuses.update(item['status_counts'].keys())
        
        # Preparar datos para cada estado
        bottom = [0] * len(services)
        
        for status in ['AUTORIZADO', Patient.STATUS_PENDIENTE_COMERCIAL_PRESUPUESTO, Patient.STATUS_PENDIENTE_PRESTADOR, Patient.STATUS_PENDIENTE_MEDICO, Patient.STATUS_PENDIENTE_PACIENTE, Patient.STATUS_AUTORIZADO_MATERIAL_PENDIENTE, Patient.STATUS_PENDIENTE, Patient.STATUS_RECHAZO_COBERTURA, 'REPROGRAMADO', Patient.STATUS_CANCELA_MEDICO, Patient.STATUS_CANCELA_PTE]:
            if status in all_statuses:
                values = [item['status_counts'].get(status, 0) for item in data[:10]]
                ax.bar(services, values, bottom=bottom, 
                      label=status_labels.get(status, status),
                      color=status_colors.get(status, '#6c757d'),
                      edgecolor='white',
                      linewidth=0.5)
                bottom = [b + v for b, v in zip(bottom, values)]
        
        ax.set_xlabel('Servicio', fontsize=11, fontweight='bold')
        ax.set_ylabel('Cantidad', fontsize=11, fontweight='bold')
        ax.set_title(title, fontsize=13, fontweight='bold', pad=15)
        ax.legend(loc='upper right', fontsize=9, frameon=True, shadow=True)
        plt.xticks(rotation=45, ha='right', fontsize=8)
        
        # Grid para mejor legibilidad
        ax.grid(axis='y', alpha=0.3, linestyle='--', linewidth=0.5)
        ax.set_axisbelow(True)
        
        buf = BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return buf

    # PÁGINA 1: Resumen general
    y = add_header(pdf, height - 50)
    
    # KPIs principales
    pdf.setFont("Helvetica-Bold", 14)
    if service_filter and doctor_filter:
        pdf.drawString(50, y, f"Resumen: {service_filter} - {doctor_filter}")
    elif service_filter:
        pdf.drawString(50, y, f"Resumen por Servicio: {service_filter}")
    elif doctor_filter:
        pdf.drawString(50, y, f"Resumen por Profesional: {doctor_filter}")
    else:
        pdf.drawString(50, y, "Resumen General")
    y -= 30
    
    # Aclarar qué tipo de total es según el filtro de fecha
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, f"Total de solicitudes cargadas en el sistema: {overall_total}")
    y -= 15
    pdf.setFont("Helvetica", 9)
    pdf.drawString(50, y, f"(Todas las solicitudes, sin límite de fecha)")
    y -= 20
    
    # Desglose por estado
    pdf.setFont("Helvetica", 10)
    for item in overall_by_status:
        status_display = status_labels.get(item['status'], item['status'])
        pct = (item['count'] / overall_total * 100) if overall_total else 0
        pdf.drawString(70, y, f"• {status_display}: {item['count']} ({pct:.1f}%)")
        y -= 15
    
    # Gráfico de estados
    if overall_by_status:
        y -= 20
        chart_buf = create_pie_chart(overall_by_status, "Distribución de Solicitudes por Estado")
        pdf.drawImage(ImageReader(chart_buf), 30, y - 350, width=320, height=280, preserveAspectRatio=True)
        y -= 370

    # PÁGINA 2: Por servicio
    if y < 200:
        pdf.showPage()
        y = add_header(pdf, height - 50)
    
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y, "Estadísticas por Especialidad Médica (Servicio)")
    y -= 30
    
    # Top servicios
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, y, f"Total de especialidades/servicios diferentes registrados: {len(by_service)}")
    y -= 20
    
    for idx, item in enumerate(by_service[:10], 1):
        service = item.get('service') or "Sin servicio"
        pdf.drawString(70, y, f"{idx}. {service}: {item['count']} solicitudes")
        y -= 15
        if y < 100:
            pdf.showPage()
            y = add_header(pdf, height - 50)
    
    # Gráfico de servicios
    if by_service:
        y -= 20
        if y < 400:
            pdf.showPage()
            y = add_header(pdf, height - 50)
        chart_buf = create_pie_chart(by_service[:8], "Top 8 Especialidades/Servicios con Más Solicitudes", labels_key='service', values_key='count')
        pdf.drawImage(ImageReader(chart_buf), 30, y - 350, width=320, height=280, preserveAspectRatio=True)
        y -= 370

    # PÁGINA 3: Estados por servicio (stacked)
    pdf.showPage()
    y = add_header(pdf, height - 50)
    
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y, "Estados de Tramitación por Servicio")
    y -= 30
    
    # Tabla detallada
    pdf.setFont("Helvetica", 9)
    for item in by_service_status[:5]:
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(50, y, f"{item['service']}: {item['total']} total")
        y -= 15
        pdf.setFont("Helvetica", 9)
        for status, count in item['status_counts'].items():
            status_display = status_labels.get(status, status)
            pdf.drawString(70, y, f"  • {status_display}: {count}")
            y -= 12
        y -= 5
        if y < 100:
            pdf.showPage()
            y = add_header(pdf, height - 50)
    
    # Gráfico stacked
    if by_service_status:
        y -= 20
        if y < 350:
            pdf.showPage()
            y = add_header(pdf, height - 50)
        chart_buf = create_stacked_bar_chart(by_service_status, "Estados de Tramitación por Servicio (Top 10 Servicios)")
        pdf.drawImage(ImageReader(chart_buf), 30, y - 300, width=520, height=250, preserveAspectRatio=True)
        y -= 320

    # PÁGINA 4: Por cobertura
    pdf.showPage()
    y = add_header(pdf, height - 50)
    
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y, "Estadísticas por Cobertura Médica (Obras Sociales)")
    y -= 30
    
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, y, f"Total de coberturas/obras sociales diferentes: {len(by_coverage)}")
    y -= 20
    
    for idx, item in enumerate(by_coverage[:15], 1):
        coverage = item.get('coverage') or "Sin cobertura"
        pdf.drawString(70, y, f"{idx}. {coverage}: {item['count']} solicitudes")
        y -= 15
        if y < 100:
            pdf.showPage()
            y = add_header(pdf, height - 50)
    
    # Gráfico de coberturas
    if by_coverage:
        y -= 20
        if y < 400:
            pdf.showPage()
            y = add_header(pdf, height - 50)
        chart_buf = create_pie_chart(by_coverage[:8], "Top 8 Coberturas Médicas con Más Solicitudes", labels_key='coverage', values_key='count')
        pdf.drawImage(ImageReader(chart_buf), 30, y - 350, width=320, height=280, preserveAspectRatio=True)
        y -= 370

    # PÁGINA 5: Períodos (si hay)
    if periods:
        pdf.showPage()
        y = add_header(pdf, height - 50)
        
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(50, y, "Comparativa y Evolución por Períodos de 7 Días")
        y -= 25
        pdf.setFont("Helvetica", 9)
        pdf.drawString(50, y, f"(Estas cifras corresponden solo al período de análisis: {start_d.strftime('%d/%m/%Y')} - {end_d.strftime('%d/%m/%Y')})")
        y -= 25
        
        pdf.setFont("Helvetica", 10)
        for idx, period in enumerate(periods, 1):
            pdf.setFont("Helvetica-Bold", 10)
            pdf.drawString(50, y, f"Período {idx}: {period['label']}")
            y -= 15
            pdf.setFont("Helvetica", 9)
            pdf.drawString(70, y, f"Total de solicitudes: {period['total']}")
            y -= 12
            for item in period['by_status']:
                status_display = status_labels.get(item['status'], item['status'])
                pdf.drawString(90, y, f"• {status_display}: {item['count']}")
                y -= 12
            y -= 8
            if y < 100:
                pdf.showPage()
                y = add_header(pdf, height - 50)
        
        # Gráfico de períodos
        y -= 20
        if y < 350:
            pdf.showPage()
            y = add_header(pdf, height - 50)
        chart_buf = create_bar_chart(periods, "Solicitudes por Período", "Período", "Cantidad")
        pdf.drawImage(ImageReader(chart_buf), 50, y - 280, width=500, height=240, preserveAspectRatio=True)

    # PÁGINA 6: Comparativa Pedidos vs Realizados
    pdf.showPage()
    y = add_header(pdf, height - 50)
    
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, y, "Análisis del Período: Solicitudes Ingresadas vs Cirugías Programadas")
    y -= 25
    
    # Explicación de las tres métricas
    pdf.setFont("Helvetica", 9)
    pdf.drawString(50, y, f"Los siguientes valores corresponden únicamente al período de análisis: {start_d.strftime('%d/%m/%Y')} al {end_d.strftime('%d/%m/%Y')}")
    y -= 20
    
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, f"Total de pedidos cargados en el sistema: {total_created}")
    y -= 15
    pdf.setFont("Helvetica", 9)
    pdf.drawString(70, y, "→ Solicitudes creadas e ingresadas al sistema en este período (fecha de carga)")
    y -= 20
    
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, f"Total de cirugías programadas para intervención: {total_planned}")
    y -= 15
    pdf.setFont("Helvetica", 9)
    pdf.drawString(70, y, "→ Intervenciones con fecha de cirugía planificada en este período")
    y -= 20
    
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, f"Total autorizadas por la cobertura: {total_authorized}")
    y -= 15
    pdf.setFont("Helvetica", 9)
    pdf.drawString(70, y, f"→ De las {total_planned} cirugías programadas, cuántas fueron autorizadas por la cobertura médica")
    y -= 20
    
    if total_planned > 0:
        auth_rate = (total_authorized / total_planned) * 100
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(50, y, f"Tasa de autorización de coberturas: {auth_rate:.1f}%")
        y -= 5
    y -= 20
    
    # Gráfico comparativo
    if total_created > 0 or total_planned > 0:
        comparison_data = [
            {"label": "Pedidos ingresados", "total": total_created},
            {"label": "Cirugías programadas", "total": total_planned},
            {"label": "Autorizadas", "total": total_authorized}
        ]
        chart_buf = create_bar_chart(comparison_data, "Comparativa: Pedidos, Programadas y Autorizaciones", "Categoría", "Cantidad")
        pdf.drawImage(ImageReader(chart_buf), 50, y - 280, width=500, height=240, preserveAspectRatio=True)
        y -= 300
    
    # Gráficos de períodos si existen
    if periods and len(periods) > 0:
        pdf.showPage()
        y = add_header(pdf, height - 50)
        
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(50, y, "Evolución por Períodos")
        y -= 40
        
        # Gráfico de pedidos por período
        periods_created = [{"label": p["label"], "total": p.get("created_count", 0)} for p in periods]
        chart_buf = create_bar_chart(periods_created, "Pedidos Ingresados al Sistema por Período", "Período", "Cantidad")
        pdf.drawImage(ImageReader(chart_buf), 50, y - 250, width=500, height=220, preserveAspectRatio=True)
        y -= 270
        
        # Gráfico de cirugías por período
        if y < 300:
            pdf.showPage()
            y = add_header(pdf, height - 50)
        
        periods_planned = [{"label": p["label"], "total": p.get("planned_count", 0)} for p in periods]
        chart_buf = create_bar_chart(periods_planned, "Cirugías Programadas para Intervención por Período", "Período", "Cantidad")
        pdf.drawImage(ImageReader(chart_buf), 50, y - 250, width=500, height=220, preserveAspectRatio=True)
        y -= 270
        
        # Gráfico de autorizadas por período
        if y < 300:
            pdf.showPage()
            y = add_header(pdf, height - 50)
        
        periods_authorized = [{"label": p["label"], "total": p.get("authorized_count", 0)} for p in periods]
        chart_buf = create_bar_chart(periods_authorized, "Autorizadas por la Cobertura Médica por Período", "Período", "Cantidad")
        pdf.drawImage(ImageReader(chart_buf), 50, y - 250, width=500, height=220, preserveAspectRatio=True)

    # Guardar PDF
    pdf.save()

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    filename_parts = ["estadisticas", start_d.strftime('%Y%m%d'), end_d.strftime('%Y%m%d')]
    if service_filter:
        filename_parts.append(service_filter[:20].replace(' ', '_'))
    if coverage_filter:
        filename_parts.append(coverage_filter[:20].replace(' ', '_'))
    if doctor_filter:
        filename_parts.append(doctor_filter[:20].replace(' ', '_'))
    response["Content-Disposition"] = f"attachment; filename={'_'.join(filename_parts)}.pdf"
    return response


# -------------------------------------------------------------
# SEGUIMIENTO PUBLICO
# -------------------------------------------------------------
def _build_public_tracking_context(patient: Patient | None) -> dict:
    if not patient:
        return {}

    review_statuses = {
        Patient.STATUS_PENDIENTE,
        Patient.STATUS_PENDIENTE_PRESTADOR,
        Patient.STATUS_PENDIENTE_MEDICO,
        Patient.STATUS_PENDIENTE_PACIENTE,
        Patient.STATUS_PENDIENTE_COMERCIAL_PRESUPUESTO,
        Patient.STATUS_AUTORIZADO_MATERIAL_PENDIENTE,
    }
    final_statuses = {
        Patient.STATUS_AUTORIZADO,
        Patient.STATUS_REALIZADO,
    }
    exception_statuses = {
        Patient.STATUS_RECHAZO_COBERTURA,
        Patient.STATUS_REPROGRAMADO,
        Patient.STATUS_SUSPENDIDA,
        Patient.STATUS_CANCELA_MEDICO,
        Patient.STATUS_CANCELA_PTE,
    }

    status = patient.status
    if status in final_statuses:
        current_step = 3
        tone = "success"
        title = "Solicitud autorizada"
        message = "La autorizacion ya figura lista en el sistema."
    elif status in exception_statuses:
        current_step = 2
        tone = "warning" if status == Patient.STATUS_REPROGRAMADO else "danger"
        title = patient.get_status_display()
        message = "El equipo de autorizaciones revisara este caso y se comunicara si necesita nueva documentacion."
    elif status in review_statuses:
        current_step = 2
        tone = "info"
        title = "Solicitud en revision"
        message = "La documentacion fue recibida y esta siendo gestionada por el equipo correspondiente."
    else:
        current_step = 1
        tone = "info"
        title = patient.get_status_display()
        message = "La solicitud fue registrada correctamente."

    steps = [
        {"number": 1, "label": "Recibida", "state": "done" if current_step >= 1 else "pending"},
        {"number": 2, "label": "En revision", "state": "done" if current_step > 2 else "current" if current_step == 2 else "pending"},
        {"number": 3, "label": "Resolucion", "state": "done" if current_step >= 3 else "pending"},
    ]

    return {
        "tracking_title": title,
        "tracking_message": message,
        "tracking_tone": tone,
        "tracking_steps": steps,
        "status_updated_at": patient.status_since or patient.updated_at,
    }


def tracking_view(request):
    tracking_id = request.GET.get("id", "").strip()[:32]
    patient = None

    if tracking_id:
        patient = Patient.objects.filter(tracking_id=tracking_id).first()

    return render(request, "core/tracking_form.html", {
        "tracking_id": tracking_id,
        "patient": patient,
        **_build_public_tracking_context(patient),
    })

# -------------------------------------------------------------
# ACCIONES EN LOTE
# -------------------------------------------------------------
@login_required
@require_POST
def bulk_change_status(request):
    """Cambia el estado de múltiples pacientes"""
    try:
        data = json.loads(request.body)
        patient_ids = data.get('patient_ids', [])
        new_status = data.get('new_status', '')
        
        if not patient_ids or not new_status:
            return JsonResponse({'success': False, 'error': 'Datos incompletos'})
        
        # Verificar que el estado sea válido
        valid_statuses = [choice[0] for choice in Patient.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return JsonResponse({'success': False, 'error': 'Estado inválido'})
        
        # Validar y convertir patient_ids a enteros (los data-attributes HTML llegan como strings)
        if not isinstance(patient_ids, list):
            return JsonResponse({'success': False, 'error': 'Datos inválidos'})
        try:
            patient_ids = [int(i) for i in patient_ids]
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'error': 'Datos inválidos'})

        # Actualizar pacientes uno a uno para disparar signals e invalidar caché
        patients = Patient.objects.filter(pk__in=patient_ids)
        updated = 0
        authorized_email_sent = 0
        authorized_email_failed = 0
        authorized_email_missing = 0
        for patient in patients:
            old_status = patient.status
            patient.status = new_status
            # Incluir solicitado_since para que el pre_save signal pueda actualizarlo
            patient.save(update_fields=['status', 'updated_at', 'solicitado_since'])
            updated += 1
            if old_status != Patient.STATUS_AUTORIZADO and new_status == Patient.STATUS_AUTORIZADO:
                if not patient.email:
                    authorized_email_missing += 1
                elif _send_patient_authorized_email(request, patient):
                    authorized_email_sent += 1
                else:
                    authorized_email_failed += 1

        return JsonResponse({
            'success': True,
            'updated': updated,
            'authorized_email_sent': authorized_email_sent,
            'authorized_email_failed': authorized_email_failed,
            'authorized_email_missing': authorized_email_missing,
        })
    except Exception as e:
        logger.exception("Error en bulk_change_status")
        return JsonResponse({'success': False, 'error': 'Error interno al procesar la solicitud'})


@login_required
@require_POST
def bulk_assign_user(request):
    """Asigna un usuario a múltiples pacientes"""
    try:
        data = json.loads(request.body)
        patient_ids = data.get('patient_ids', [])
        user_id = data.get('user_id')
        
        if not patient_ids:
            return JsonResponse({'success': False, 'error': 'No se seleccionaron pacientes'})
        
        # Si user_id es None o vacío, desasignar
        if user_id:
            try:
                user = User.objects.get(pk=user_id)
                updated = Patient.objects.filter(pk__in=patient_ids).update(assigned_to=user)
            except User.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Usuario no encontrado'})
        else:
            updated = Patient.objects.filter(pk__in=patient_ids).update(assigned_to=None)
        
        return JsonResponse({'success': True, 'updated': updated})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def export_selected(request):
    """Exporta solo los pacientes seleccionados a Excel"""
    patient_ids = request.GET.getlist('ids')
    
    if not patient_ids:
        messages.error(request, "No se seleccionaron pacientes para exportar")
        return redirect('core:patient_list')
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pacientes Seleccionados"

    ws.append([
        "Tracking ID", "Nombre", "DNI", "Cobertura",
        "Médico", "Servicio", "Fecha intervención", "Estado",
        "Asignado a", "Fecha carga"
    ])

    # Usar select_related + iterator para reducir número de consultas y memoria
    qs = Patient.objects.filter(pk__in=patient_ids).select_related('assigned_to').order_by('-created_at').iterator()
    status_map = dict(Patient.STATUS_CHOICES)
    for p in qs:
        ws.append([
            p.tracking_id,
            p.full_name,
            p.dni,
            p.coverage,
            p.doctor,
            p.service,
            p.planned_date.isoformat() if getattr(p, "planned_date", None) else "",
            status_map.get(p.status, p.status) if p.status is not None else "",
            p.assigned_to.get_full_name() if p.assigned_to else "Sin asignar",
            p.created_at.date().isoformat() if getattr(p, "created_at", None) else "",
        ])

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f"attachment; filename=pacientes_seleccionados_{timezone.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return response


# -------------------------------------------------------------
# GESTIÓN DE USUARIOS (solo superadmin)
# -------------------------------------------------------------
@login_required
def user_management(request):
    if not request.user.is_superuser:
        return HttpResponse("No autorizado. Solo superusuarios pueden acceder.", status=403)
    
    users = User.objects.all().order_by('username')
    return render(request, 'core/user_management.html', {'users': users})


@login_required
@require_POST
def update_user_permissions(request):
    if not request.user.is_superuser:
        return HttpResponse("No autorizado", status=403)
    
    user_id = request.POST.get('user_id')
    try:
        user = User.objects.get(pk=user_id)
        
        # No permitir que se modifique a sí mismo
        if user.id == request.user.id:
            messages.error(request, "No puedes modificar tus propios permisos")
            return redirect('core:user_management')
        
        user.is_staff = request.POST.get('is_staff') == '1'
        user.is_superuser = request.POST.get('is_superuser') == '1'
        user.is_active = request.POST.get('is_active') == '1'
        user.save()
        
        messages.success(request, f"Permisos actualizados para {user.username}")
    except User.DoesNotExist:
        messages.error(request, "Usuario no encontrado")
    
    return redirect('core:user_management')
