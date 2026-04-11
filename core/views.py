from collections import defaultdict
from datetime import datetime, timedelta
from io import BytesIO
import smtplib

from urllib.parse import urlencode
import os
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import F, Value
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
        ("á", "a"), ("à", "a"), ("ä", "a"), ("â", "a"), ("ã", "a"),
        ("Á", "a"), ("À", "a"), ("Ä", "a"), ("Â", "a"), ("Ã", "a"),
        ("é", "e"), ("è", "e"), ("ë", "e"), ("ê", "e"),
        ("É", "e"), ("È", "e"), ("Ë", "e"), ("Ê", "e"),
        ("í", "i"), ("ì", "i"), ("ï", "i"), ("î", "i"),
        ("Í", "i"), ("Ì", "i"), ("Ï", "i"), ("Î", "i"),
        ("ó", "o"), ("ò", "o"), ("ö", "o"), ("ô", "o"), ("õ", "o"),
        ("Ó", "o"), ("Ò", "o"), ("Ö", "o"), ("Ô", "o"), ("Õ", "o"),
        ("ú", "u"), ("ù", "u"), ("ü", "u"), ("û", "u"),
        ("Ú", "u"), ("Ù", "u"), ("Ü", "u"), ("Û", "u"),
        ("ñ", "n"), ("Ñ", "n"),
        ("ç", "c"), ("Ç", "c"),
    ]
    for src, dst in mapping:
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

ADMISSION_REQUIRED_ATTACHMENT_TYPES = (
    Attachment.TYPE_ORDEN,
    Attachment.TYPE_AUTORIZACION,
)

ADMISSION_OPTIONAL_ATTACHMENT_TYPES = (
    Attachment.TYPE_MATERIALES,
)

ADMISSION_ATTACHMENT_LABELS = {
    Attachment.TYPE_ORDEN: "Orden de intervención",
    Attachment.TYPE_MATERIALES: "Materiales",
    Attachment.TYPE_AUTORIZACION: "Autorización",
}


def _get_admission_email_for_sede(sede: str | None) -> str:
    return ADMISSION_EMAILS_BY_SEDE.get((sede or "").strip().upper(), "")


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


def _send_patient_admission_email(request, patient: Patient) -> tuple[str, list[Attachment]]:
    recipient = _get_admission_email_for_sede(patient.sede)
    if not recipient:
        raise ValueError("El paciente no tiene una sede válida para enviar a admisión.")

    attachments, missing_labels = _get_patient_admission_attachments(patient)
    if missing_labels:
        raise ValueError(f"Faltan adjuntos obligatorios: {', '.join(missing_labels)}.")

    if settings.EMAIL_BACKEND.endswith("smtp.EmailBackend") and not (settings.EMAIL_HOST or "").strip():
        raise ValueError("El envío por mail no está configurado en el servidor. Falta EMAIL_HOST.")

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
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        reply_to=[request.user.email] if request.user.email else None,
    )

    for attachment in attachments:
        if not attachment.file:
            continue
        email_message.attach_file(attachment.file.path)

    sent_count = email_message.send(fail_silently=False)
    if not sent_count:
        raise ValueError("El servidor no confirmó el envío del mail.")

    return recipient, attachments


def _format_admission_email_error(exc: Exception) -> str:
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
]

MEDICOS_QR = [
    # TRAUMATOLOGIA
    'ABALO EDUARDO',
    'ADROGUE LUIS',
    'BARBIERI PABLO',
    'CARRIZO JUAN',
    'CHAVEZ ARIEL',
    'CONSTANZA EDUARDO',
    'CORDOBA ALEJANDRA',
    'DE ZAVALIA MAXIMO',
    'DEIMUNDO MARCOS',
    'DEVOTO MATIAS',
    'DI RADO LEANDRO', 
    'GOBBI ENRIQUE',
    'GRANDOLI FERNANDO',
    'IGLESIAS ALEJANDRO',
    'MALLEA ANDRES',
    'MENINATO MARCOS',
    'MOUNIER CARLOS',
    'ORTIZ EZEQUIEL',
    'PEREA AGUSTIN',
    'PINOTTI NORBERTO',
    'PEREYRA LEONARDO',
    'SANNA HERNAN',
    'SERE IGNACIO',
    'TORGA SPAK ROGER',
    'VALENTINI ROBERTO',
    'VILLA NATALIA',
    'YAVEN IGNACIO',
    'YEREGUI SANTIAGO',
    'RONCORONI',
    'TRONCOSO IGNACIO',
    # HEMODINAMIA
    'BELDI FLORENCIA',
    'BOCHOEYER ANDRES',
    'CAROSELLA LUCILA',
    'D ALESANDRO CIRO',
    'DE CANDIDO LAURA',
    'DI TORO DARIO',
    'GARBUGINO SILVIA',
    'HADID CLAUDIO',
    'HERRERA VEGAS DIEGO',
    'LABADET CARLOS',
    'LABADET SEBASTIAN',
    'LUCINI VICTORIO',
    'MAFFEO HORACIO',
    'MALDONADO',
    'MAYDANA MARTIN',
    'MENGO GUSTAVO',
    'RIVAROLA MARCELO',
    'SALVADORES PABLO',
    'SAYAVEDRA RAMIRO',
    'SIMONELLI DAMIAN',
    'TAMASHIRO GUSTAVO',
    'TRENTACOSTE LUIS',
    'VEGA PABLO',
    'VILLAR DIEGO',

    # UROLOGIA
    'ANGELONI, BRUNO GABRIEL',
    'BALDESSARI, CARLOS MARTIN',
    'CAPIEL, LEANDRO',
    'COLLAVINI, MARTIN GABRIEL',
    'FERNANDEZ SPONTON, LUIS HUMBERTO',
    'FERNANDEZ, HECTOR',
    'FINKELSTEIN, JONATHAN EZEQUIEL',
    'GONZALEZ, VICTORIA SOLEDAD',
    'GRADIN, SAMUEL',
    'GREGORIO BERUTI, MANUEL',
    'KOREN, GUIDO',
    'LACORAZZA, DARIO',
    'MARRUGAT, MARCOS JOSE',
    'MARRUGAT, RODOLFO EMILIO',
    'MONTENEGRO, LUIS CARLOS',
    'PERCOVICH, FEDERICO',
    'RICHARDS, NICOLAS',
    'RICHARDS, TOMAS',
    'RODRIGUEZ OLIVIERI, MANUELA',
    'ROVEGNO, AGUSTIN ROBERTO',

    # CIRUGIA GENERAL
    'CARRIE',
    'CLEMENTE',
    'LANCELOTTI',
    'PICCININI',
    'SALGADO',
    'SIMONELLI',
    'SOLINAS',
    'VERACIERTO',
    'ZUND SANTIAGO',
    'AVELLANEDA NICOLAS',
    'BARRERA DARIO',

    # CIRUGIA TORACICA
    'NAZAR PEIRANO AGUSTIN',
    'VIOLA AGUSTIN JAVIER',

    # CIRUGIA PLASTICA
    'BISTOLETTI PEDRO',
    'MARENZI GUSTAVO',
    'BIGNOTTI AGUSTIN',
    'RODRIGO JIMENA',
    'MIRO ANTONIO',
    'BARDOT GONZALO',
    'BARREIRO CATALINA',
    'STOPPINI MAXIMILIANO',
	
    # CIRUGIA OTORRINO',
    'NEMECIO ALAN',
    'BLANC ARIANA',
    'VALDEZ GABRIEL ANIBAL',
    'RAMIREZ ZAIDA',
    'GONZALEZ ARECES MARIELA ALEJANDRA',
    'BERMUDEZ ARIEL LEONARDO',
    'VITI MARIA MARTA',
    'MAZZEI PAULA CECILIA',
    'ARMIJOS KARLA',
    'MICHALSKI DIEGO JULIAN',
    'LOPEZ MORIS CARLOS BENJAMIN',
    'SARTORI MARIA VERONICA',
    'MUSACCHIO CECILIA',
    'MARENGO RICARDO LUIS',
    'VALERIO ANDREA',
    'SZTAJN MARCELO',
    'EISEMBERG GULLERMO DANIEL',
    'FERNANDEZ LUCIA',
    'JUCHLI MARIANA LIA',
    'GATICA VERONICA DEL ROSARIO',
    'NISTAL CLARA',
    'CURI JUAN RAMON',
    'FARAGO ESTEBAN',
    'DOMEG MARIA BELEN',

     # CIRUGIA NEURO',
    'GUEVARA MENDEZ MARTIN',
    'DRIOLLET SANTIAGO',
    'MELGAREJO ANA',

    # CIRUGIA FLEBOLOGIA',
    'DE TOMMASO GONZALO',

    # GINECOLOGIA
    'CRIMI GABRIEL',
    'ANTONIAZZI SAMANTA',
    'BALLESTER ANGELES',
    'BOLOGNA MICAELA',
    'FERNANDEZ ALBERTO',
    'BAUMESITER GUSTAVO',
    'DIRIBARNE JUAN CRUZ',
    'EHRMAN PATRICIO',
    'FISHKEL VANINA',
    'MONGE FERNANDO',
    'SCHYGIEL GUADALUPE',
    'PAESANI FERNANDO',
    'KIENAST NATALIA',
    'QUINTAIE AGUSTIN',
    'SCHVARTZMAN JAVIER',
    'SEREDAY PAUL',
    'TAPPER KAREN ELIZABETH',
    'CAERO ROMINA',
    'VERA JULIETA',
    'VIZCAINO FRANCISCO',
    'VON STECHER FRANCISCO',
    'ZEFF NATALIA',
    'ZUGASTI JULIA',
    'TRIGUBO DENISE',
    'PEREIRA JUAN IGNACIO',
    'TRUFFINI LUCIANA',
    'NEGRI MERCEDES',

    
     
]
 
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
    'O.S.D.I.P.P.',
    'OBSBA',
    'OMINT',
    'OPDEA',
    'OSDE',
    'OSME',
    'OSPE',
    'OSPIDA',
    'OSPOCE',
    'OSRJA',
    'PATRONES DE CABOTAJE',
    'PODER JUDICIAL', 
    'PREMEDIC',
    'PREVENCION SALUD',
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
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()
        coverage = request.POST.get('coverage', '').strip()
        doctor = request.POST.get('doctor', '').strip()
        service = request.POST.get('service', '').strip()
        planned_date_raw = request.POST.get('planned_date')
        sede = request.POST.get('sede', '').strip()
        external_observations = request.POST.get('external_observations', '').strip()
        material_status = request.POST.get('material_status', 'PENDIENTE').strip()
        en_quirofano = bool(request.POST.get('en_quirofano'))
        observaciones_calendario = request.POST.get('observaciones_calendario', '').strip()

        # Validar campos obligatorios
        errors = []
        if not last_name:
            errors.append('El apellido es obligatorio')
        if not first_name:
            errors.append('El nombre es obligatorio')
        if not dni:
            errors.append('El DNI es obligatorio')
        if not coverage:
            errors.append('La cobertura es obligatoria')
        if not doctor:
            errors.append('El médico tratante es obligatorio')
        if not service:
            errors.append('El servicio es obligatorio')
        if not planned_date_raw:
            errors.append('La fecha de intervención es obligatoria')
        
        # Validar archivos obligatorios
        if not request.FILES.get('dni_file'):
            errors.append('El archivo de DNI es obligatorio')
        if not request.FILES.get('credencial_file'):
            errors.append('El archivo de credencial es obligatorio')
        if not request.FILES.get('orden_intervencion_file'):
            errors.append('La orden de intervención es obligatoria')
        
        if errors:
            return render(request, 'core/patient_form.html', {
                'errors': errors,
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
                planned_date = None

        patient = Patient.objects.create(
            full_name=full_name,
            dni=dni,
            phone=phone,
            email=email,
            coverage=coverage,
            doctor=doctor,
            service=service,
            planned_date=planned_date,
            sede=sede,
            external_observations=external_observations,
            material_status=material_status,
            en_quirofano=en_quirofano,
            observaciones_calendario=observaciones_calendario,
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

        return render(request, 'core/patient_form.html', {
            'success': True,
            'tracking_id': patient.tracking_id,
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
                {"label": "Pendientes", "code": Patient.STATUS_PENDIENTE, "count": mapa_estados.get(Patient.STATUS_PENDIENTE, 0)},
                {"label": "Solicitados", "code": Patient.STATUS_SOLICITADO, "count": mapa_estados.get(Patient.STATUS_SOLICITADO, 0)},
                {"label": "Autorizados", "code": Patient.STATUS_AUTORIZADO, "count": mapa_estados.get(Patient.STATUS_AUTORIZADO, 0)},
                {"label": "Presupuesto sí", "code": Patient.STATUS_PRESUPUESTO_SI, "count": mapa_estados.get(Patient.STATUS_PRESUPUESTO_SI, 0)},
                {"label": "Material pendiente", "code": Patient.STATUS_MATERIAL_PENDIENTE, "count": mapa_estados.get(Patient.STATUS_MATERIAL_PENDIENTE, 0)},
                {"label": "Rechazos", "code": Patient.STATUS_RECHAZO, "count": mapa_estados.get(Patient.STATUS_RECHAZO, 0)},
                {"label": "Reprogramados", "code": Patient.STATUS_REPROGRAMADO, "count": mapa_estados.get(Patient.STATUS_REPROGRAMADO, 0)},
            ],
            "proximas_hoy": list(Patient.objects.filter(planned_date=hoy).only("id", "full_name", "service", "coverage", "planned_date").order_by('planned_date', 'service', 'full_name')),
            "proximas_semana": list(Patient.objects.filter(planned_date__gt=hoy, planned_date__lte=hoy + timedelta(days=7)).only("id", "full_name", "service", "coverage", "planned_date")),
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
            # Si el modelo tiene campos normalizados, úsalos (más eficiente);
            # si no, caer al enfoque por anotación como antes.
            try:
                Patient._meta.get_field('full_name_norm')
                use_field = True
            except Exception:
                use_field = False

            if use_field:
                qs = qs.filter(
                    Q(full_name_norm__contains=qn) |
                    Q(dni_norm__contains=qn) |
                    Q(tracking_id__icontains=q)  # Búsqueda por tracking OVA
                )
            else:
                qs = qs.annotate(
                    full_name_norm=_normalized_expr("full_name"),
                    dni_norm=_normalized_expr("dni"),
                ).filter(
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
        doctor_options = list(
            Patient.objects.exclude(doctor__isnull=True)
            .exclude(doctor__exact="")
            .values_list("doctor", flat=True)
            .distinct()
            .order_by("doctor")
        )
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
    today = timezone.now().date()
    patients_list = []
    for p in page_obj.object_list:
        if p.planned_date:
            days_diff = (p.planned_date - today).days
            p.days_until_surgery = days_diff
        else:
            p.days_until_surgery = None
        patients_list.append(p)

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
                messages.error(request, _format_admission_email_error(exc))
            return redirect(_build_patient_detail_url(patient.pk, next_url))

        # ACTUALIZAR ESTADO
        if "update_status" in request.POST:
            new_status = request.POST.get("status", patient.status)
            new_obs = request.POST.get("internal_observations", "").strip()
            patient.status = new_status
            patient.internal_observations = new_obs
            patient.save()
            messages.success(request, "Estado actualizado.")
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
                reprogram_form.save()
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
                from datetime import datetime
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
    from datetime import datetime, timedelta
    
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
    
    import json
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
# ESTADISTICAS
# -------------------------------------------------------------
@login_required
def stats_view(request):
    return render(request, 'core/stats.html')


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
    doctor_filter = request.GET.get("doctor", "").strip()
    date_field = (request.GET.get("date_field") or "planned_date").strip()
    if date_field not in {"planned_date", "created_at"}:
        date_field = "planned_date"

    date_lookup = "planned_date" if date_field == "planned_date" else "created_at__date"
    date_field = (request.GET.get("date_field") or "planned_date").strip()
    if date_field not in {"planned_date", "created_at"}:
        date_field = "planned_date"

    date_lookup = "planned_date" if date_field == "planned_date" else "created_at__date"

    cache_key = f"stats_data:{start}:{end}:{service_filter}:{doctor_filter}:{date_field}"
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
    if service_filter:
        base_all = base_all.filter(service__icontains=service_filter)
    if doctor_filter:
        base_all = base_all.filter(doctor__icontains=doctor_filter)

    doctors_base = Patient.objects.exclude(status=Patient.STATUS_REALIZADO)
    if service_filter:
        doctors_base = doctors_base.filter(service__icontains=service_filter)

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
    if service_filter:
        base = base.filter(service__icontains=service_filter)
    if doctor_filter:
        base = base.filter(doctor__icontains=doctor_filter)

    # Totales paralelos para ver ambas fechas con el mismo rango (excluir REALIZADO)
    base_by_created = Patient.objects.filter(created_at__date__gte=start_d, created_at__date__lte=end_d).exclude(status=Patient.STATUS_REALIZADO)
    base_by_planned = Patient.objects.filter(planned_date__gte=start_d, planned_date__lte=end_d).exclude(status=Patient.STATUS_REALIZADO)
    if service_filter:
        base_by_created = base_by_created.filter(service__icontains=service_filter)
        base_by_planned = base_by_planned.filter(service__icontains=service_filter)
    if doctor_filter:
        base_by_created = base_by_created.filter(doctor__icontains=doctor_filter)
        base_by_planned = base_by_planned.filter(doctor__icontains=doctor_filter)
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
            if service_filter:
                pq = pq.filter(service__icontains=service_filter)
            if doctor_filter:
                pq = pq.filter(doctor__icontains=doctor_filter)
            
            # Datos adicionales por período (excluir REALIZADO)
            pq_created = Patient.objects.filter(created_at__date__gte=p_start, created_at__date__lte=p_end).exclude(status=Patient.STATUS_REALIZADO)
            pq_planned = Patient.objects.filter(planned_date__gte=p_start, planned_date__lte=p_end).exclude(status=Patient.STATUS_REALIZADO)
            if service_filter:
                pq_created = pq_created.filter(service__icontains=service_filter)
                pq_planned = pq_planned.filter(service__icontains=service_filter)
            if doctor_filter:
                pq_created = pq_created.filter(doctor__icontains=doctor_filter)
                pq_planned = pq_planned.filter(doctor__icontains=doctor_filter)
            
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
            if service_filter:
                dc = dc.filter(service__icontains=service_filter)
                dp = dp.filter(service__icontains=service_filter)
            if doctor_filter:
                dc = dc.filter(doctor__icontains=doctor_filter)
                dp = dp.filter(doctor__icontains=doctor_filter)
            
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
    qs = Patient.objects.values_list(
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
    if service_filter:
        base_all = base_all.filter(service__icontains=service_filter)
    if doctor_filter:
        base_all = base_all.filter(doctor__icontains=doctor_filter)

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
    if service_filter:
        base = base.filter(service__icontains=service_filter)
    if doctor_filter:
        base = base.filter(doctor__icontains=doctor_filter)

    # Períodos de 7 días
    days = (end_d - start_d).days + 1
    periods = []
    
    # Totales duales para comparativa (excluir REALIZADO que es solo para hemodinámica)
    base_by_created = Patient.objects.filter(created_at__date__gte=start_d, created_at__date__lte=end_d).exclude(status=Patient.STATUS_REALIZADO)
    base_by_planned = Patient.objects.filter(planned_date__gte=start_d, planned_date__lte=end_d).exclude(status=Patient.STATUS_REALIZADO)
    if service_filter:
        base_by_created = base_by_created.filter(service__icontains=service_filter)
        base_by_planned = base_by_planned.filter(service__icontains=service_filter)
    if doctor_filter:
        base_by_created = base_by_created.filter(doctor__icontains=doctor_filter)
        base_by_planned = base_by_planned.filter(doctor__icontains=doctor_filter)
    
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
            if service_filter:
                pq = pq.filter(service__icontains=service_filter)
            if doctor_filter:
                pq = pq.filter(doctor__icontains=doctor_filter)
            
            # Datos adicionales por período (excluir REALIZADO)
            pq_created = Patient.objects.filter(created_at__date__gte=p_start, created_at__date__lte=p_end).exclude(status=Patient.STATUS_REALIZADO)
            pq_planned = Patient.objects.filter(planned_date__gte=p_start, planned_date__lte=p_end).exclude(status=Patient.STATUS_REALIZADO)
            if service_filter:
                pq_created = pq_created.filter(service__icontains=service_filter)
                pq_planned = pq_planned.filter(service__icontains=service_filter)
            if doctor_filter:
                pq_created = pq_created.filter(doctor__icontains=doctor_filter)
                pq_planned = pq_planned.filter(doctor__icontains=doctor_filter)
            
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
    status_colors = {
        'PENDIENTE': '#ffc107',
        'SOLICITADO': '#17a2b8',
        'AUTORIZADO': '#28a745',
        'PRESUPUESTO_SI': '#6f42c1',
        'MATERIAL_PENDIENTE': '#fd7e14',
        'RECHAZO': '#dc3545',
        'REPROGRAMADO': '#6c757d',
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
                label_display = {
                    'PENDIENTE': 'Pendiente (Pendiente de envío)',
                    'SOLICITADO': 'Se envió a la cobertura',
                    'AUTORIZADO': 'Autorizado por la cobertura',
                    'PRESUPUESTO_SI': 'Presupuesto aprobado',
                    'MATERIAL_PENDIENTE': 'Pendiente de material',
                    'RECHAZO': 'Rechazado por la cobertura',
                    'REPROGRAMADO': 'Reprogramado',
                }.get(label, label)
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
        
        status_labels = {
            'PENDIENTE': 'Pendiente (Pendiente de envío)',
            'SOLICITADO': 'Se envió a la cobertura',
            'AUTORIZADO': 'Autorizado por la cobertura',
            'PRESUPUESTO_SI': 'Presupuesto aprobado',
            'MATERIAL_PENDIENTE': 'Pendiente de material',
            'RECHAZO': 'Rechazado por la cobertura',
            'REPROGRAMADO': 'Reprogramado',
        }
        
        # Preparar datos para cada estado
        bottom = [0] * len(services)
        
        for status in ['AUTORIZADO', 'SOLICITADO', 'MATERIAL_PENDIENTE', 'PENDIENTE', 'RECHAZO', 'REPROGRAMADO']:
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
        status_display = {
            'PENDIENTE': 'Pendiente (Pendiente de envío)',
            'SOLICITADO': 'Se envió a la cobertura',
            'AUTORIZADO': 'Autorizado por la cobertura',
            'PRESUPUESTO_SI': 'Presupuesto aprobado',
            'MATERIAL_PENDIENTE': 'Pendiente de material',
            'RECHAZO': 'Rechazado por la cobertura',
            'REPROGRAMADO': 'Reprogramado',
        }.get(item['status'], item['status'])
        pdf.drawString(70, y, f"• {status_display}: {item['count']} ({item['count']/overall_total*100:.1f}%)")
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
            status_display = {
                'PENDIENTE': 'Pendiente (Pendiente de envío)',
                'SOLICITADO': 'Se envió a la cobertura',
                'AUTORIZADO': 'Autorizado por la cobertura',
                'PRESUPUESTO_SI': 'Presupuesto aprobado',
                'MATERIAL_PENDIENTE': 'Pendiente de material',
                'RECHAZO': 'Rechazado por la cobertura',
                'REPROGRAMADO': 'Reprogramado',
            }.get(status, status)
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
                status_display = {
                    'PENDIENTE': 'Pendiente (Pendiente de envío)',
                    'SOLICITADO': 'Se envió a la cobertura',
                    'AUTORIZADO': 'Autorizado por la cobertura',
                    'PRESUPUESTO_SI': 'Presupuesto aprobado',
                    'MATERIAL_PENDIENTE': 'Pendiente de material',
                    'RECHAZO': 'Rechazado por la cobertura',
                    'REPROGRAMADO': 'Reprogramado',
                }.get(item['status'], item['status'])
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
    if doctor_filter:
        filename_parts.append(doctor_filter[:20].replace(' ', '_'))
    response["Content-Disposition"] = f"attachment; filename={'_'.join(filename_parts)}.pdf"
    return response


# -------------------------------------------------------------
# SEGUIMIENTO PUBLICO
# -------------------------------------------------------------
def tracking_view(request):
    tracking_id = request.GET.get("id", "").strip()
    patient = None

    if tracking_id:
        patient = Patient.objects.filter(tracking_id=tracking_id).first()

    return render(request, "core/tracking_form.html", {
        "tracking_id": tracking_id,
        "patient": patient,
    })

# -------------------------------------------------------------
# ACCIONES EN LOTE
# -------------------------------------------------------------
@login_required
@require_POST
def bulk_change_status(request):
    """Cambia el estado de múltiples pacientes"""
    import json
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
        
        # Actualizar pacientes
        updated = Patient.objects.filter(pk__in=patient_ids).update(status=new_status)
        
        return JsonResponse({'success': True, 'updated': updated})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def bulk_assign_user(request):
    """Asigna un usuario a múltiples pacientes"""
    import json
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



