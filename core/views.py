from datetime import datetime, timedelta
from io import BytesIO

import unicodedata
from urllib.parse import urlencode

from django.db.models import F, Value
from django.db.models.functions import Lower, Replace
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
import openpyxl
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from django.views.decorators.csrf import csrf_exempt

from django.utils import timezone
from django.db.models import Count, Q
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt

from .forms import QRPatientForm, AttachmentForm, PatientFilterForm, ReprogramForm
from .models import Patient, Attachment, PatientHistory

# -------------------------------------------------------------
# LISTAS (servicios, médicos, coberturas)
# -------------------------------------------------------------

# -------------------------------------------------------------
# HELPERS (persistencia filtros / búsqueda tolerante)
# -------------------------------------------------------------
def _normalize_text(value: str) -> str:
    """Normaliza texto: sin acentos + case-insensitive (para búsquedas más humanas)."""
    s = (value or "").strip()
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.casefold()


def _normalized_expr(field_name: str):
    """
    Normaliza un campo en SQL para comparar sin acentos/ñ y sin mayúsculas.
    Funciona en SQLite/MySQL/Postgres (LOWER + REPLACE).
    """
    expr = Lower(F(field_name))
    mapping = [
        ("á", "a"), ("à", "a"), ("ä", "a"), ("â", "a"), ("ã", "a"),
        ("é", "e"), ("è", "e"), ("ë", "e"), ("ê", "e"),
        ("í", "i"), ("ì", "i"), ("ï", "i"), ("î", "i"),
        ("ó", "o"), ("ò", "o"), ("ö", "o"), ("ô", "o"), ("õ", "o"),
        ("ú", "u"), ("ù", "u"), ("ü", "u"), ("û", "u"),
        ("ñ", "n"),
        ("ç", "c"),
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

SERVICIOS_QR = [
    'TRAUMATOLOGIA',
    'HEMODINAMIA',
    'UROLOGIA',
    'CIRUGIA GENERAL',
    'CIRUGIA CABEZA Y CUELLO',
    'CIRUGIA TORACICA',
    'OTORRINOLARINGOLOGIA',
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
    'GOBBI ENRIQUE',
    'GRANDOLI FERNANDO',
    'IGLESIAS ALEJANDRO',
    'MALLEA ANDRES',
    'MENINATO MARCOS',
    'MOUNIER CARLOS',
    'ORTIZ EZEQUIEL',
    'PEREA AGUSTIN',
    'PINOTTI NORBERTO',
    'SANNA HERNAN',
    'SERE IGNACIO',
    'TORGA SPAK ROGER',
    'VALENTINI ROBERTO',
    'VILLA NATALIA',
    'YAVEN IGNACIO',
    'YEREGUI SANTIAGO',

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
    'BARRERA DARIO',
 
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

    # CIRUGIA TORACICA
    'NAZAR PEIRANO AGUSTIN',
    'VIOLA AGUSTIN JAVIER',
	
    # CIRUGIA OTORRINO',
    'MARENGO RICARDO',
    'SZTAJN MARCELO',
    'MICHALSKI JULIÁN',
    'VALDEZ GABRIEL',
    'GONZÁLEZ ARECES MARIELA',
    'SARTORI MARÍA VERÓNICA',
    'JUCHLI MARIANA',
    'VITI MARÍA',
    'LÓPEZ MORIS CARLOS',
    'NISTAL COELHO CLARA',
    'BIALOLIENKIER SEBASTIÁN',
    'MERESMAN GRACIELA',
    'GOLIAN IGNACIO',
    'MONDINO GUSTAVO',
    'BERMÚDEZ ARIEL',
    'MUSACCHIO CECILIA',
    'EISENBERG GUILLERMO',
    'CASARETTO JUAN',
    'SOTO PAULA',
    'TISCORNIA CARLOS',
    'PIRAS DANIELA',
    'GATICA VERÓNICA',
    'GRIMOLDI HÉCTOR',
    'CURI JUAN RAMÓN',
    'RIOLFI NAZARENO',
    'RAMÍREZ ZAIDA',
    'VALERIO ANDREA',
    'NEMESIO ALAN',
    'BLANC ARIANA',
    'DOMEG BELEN',
    'FARAGO ESTEBAN',
    'SCHLENKER GERMAN',
    'ARMIJOS KARLA',
    'PICCOLETTI LAURA',
    'FERNANDEZ LUCÍA'
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
    'COLEGIO ESCRIBANOS PROVINCIA',
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
    'OSPE',
    'OSPIDA',
    'OSPOCE',
    'OSRJA',
    'PODER JUDICIAL', 
    'PREMEDIC',
    'PREVENCION SALUD',
    'RED PRESTACIONAL CASA BAYRES',
    'ROI',
    'SEMPRE',
    'SWISS MEDICAL',
]


# -------------------------------------------------------------
# CARGA PÚBLICA (QR)
# -------------------------------------------------------------
def qr_patient_create(request):
    if request.method == 'POST':
        full_name = request.POST.get('full_name', '').strip()
        dni = request.POST.get('dni', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()
        coverage = request.POST.get('coverage', '').strip()
        doctor = request.POST.get('doctor', '').strip()
        service = request.POST.get('service', '').strip()
        planned_date_raw = request.POST.get('planned_date')
        external_observations = request.POST.get('external_observations', '').strip()

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
            external_observations=external_observations,
            assigned_to=request.user if request.user.is_authenticated else None,
        )
        
        # Registrar creación en historial
        PatientHistory.objects.create(
            patient=patient,
            user=request.user if request.user.is_authenticated else None,
            action=PatientHistory.ACTION_CREATE,
            notes=f'Paciente creado - {service}'
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
            'services': SERVICIOS_QR,
            'doctors': MEDICOS_QR,
            'coverages': COBERTURAS_QR,
        })

    return render(request, 'core/patient_form.html', {
        'services': SERVICIOS_QR,
        'doctors': MEDICOS_QR,
        'coverages': COBERTURAS_QR,
    })


# -------------------------------------------------------------
# DASHBOARD
# -------------------------------------------------------------
@login_required
def dashboard(request):
    hoy = timezone.localdate()
    inicio_mes = hoy.replace(day=1)
    hace_7_dias = hoy - timedelta(days=7)
    dos_dias_adelante = hoy + timedelta(days=2)

    total = Patient.objects.count()
    total_mes = Patient.objects.filter(created_at__date__gte=inicio_mes).count()
    total_semana = Patient.objects.filter(created_at__date__gte=hace_7_dias).count()

    raw_por_estado = Patient.objects.values('status').annotate(c=Count('id'))
    mapa_estados = {row['status']: row['c'] for row in raw_por_estado}
    
    # Urgencias (cirugías en 0-2 días)
    urgencias = Patient.objects.filter(
        planned_date__gte=hoy,
        planned_date__lte=dos_dias_adelante
    ).select_related('assigned_to').order_by('planned_date', 'service')
    
    # Casos asignados al usuario actual
    mis_casos = Patient.objects.filter(
        assigned_to=request.user
    ).order_by('-created_at')[:10] if request.user.is_authenticated else []
    
    # Casos sin asignar
    sin_asignar = Patient.objects.filter(
        assigned_to__isnull=True
    ).exclude(status=Patient.STATUS_RECHAZO).count()

    context = {
        "hoy": hoy,
        "total": total,
        "total_mes": total_mes,
        "total_semana": total_semana,
        "urgencias": urgencias,
        "urgencias_count": urgencias.count(),
        "mis_casos": mis_casos,
        "sin_asignar": sin_asignar,
        "status_summary": [
            {"label": "Pendientes", "code": Patient.STATUS_PENDIENTE, "count": mapa_estados.get(Patient.STATUS_PENDIENTE, 0)},
            {"label": "Solicitados", "code": Patient.STATUS_SOLICITADO, "count": mapa_estados.get(Patient.STATUS_SOLICITADO, 0)},
            {"label": "Autorizados", "code": Patient.STATUS_AUTORIZADO, "count": mapa_estados.get(Patient.STATUS_AUTORIZADO, 0)},
            {"label": "Material pendiente", "code": Patient.STATUS_MATERIAL_PENDIENTE, "count": mapa_estados.get(Patient.STATUS_MATERIAL_PENDIENTE, 0)},
            {"label": "Rechazos", "code": Patient.STATUS_RECHAZO, "count": mapa_estados.get(Patient.STATUS_RECHAZO, 0)},
            {"label": "Reprogramados", "code": Patient.STATUS_REPROGRAMADO, "count": mapa_estados.get(Patient.STATUS_REPROGRAMADO, 0)},
        ],
        "proximas_hoy": Patient.objects.filter(planned_date=hoy).order_by('planned_date', 'service', 'full_name'),
        "proximas_semana": Patient.objects.filter(planned_date__gt=hoy, planned_date__lte=hoy + timedelta(days=7)),
        "ultimas": Patient.objects.order_by('-created_at')[:5],
        "top_servicios": Patient.objects.values('service').annotate(c=Count('id')).order_by('-c')[:5],
        "top_coberturas": Patient.objects.values('coverage').annotate(c=Count('id')).order_by('-c')[:5],
    }
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
    qs = Patient.objects.all()

    if form.is_valid():

        # Buscar (SIN acentos y SIN mayus/minus)
        q = form.cleaned_data.get("q")
        if q:
            qn = _normalize_text(q)
            qs = qs.annotate(
                full_name_norm=_normalized_expr("full_name"),
                dni_norm=_normalized_expr("dni"),
            ).filter(
                Q(full_name_norm__contains=qn) |
                Q(dni_norm__contains=qn)
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
                qs = qs.filter(assigned_to__id=assigned_to)
        
        # Solo urgentes
        urgent_only = form.cleaned_data.get("urgent_only")
        if urgent_only:
            today = timezone.localdate()
            two_days_later = today + timedelta(days=2)
            qs = qs.filter(planned_date__gte=today, planned_date__lte=two_days_later)
        
        # Con documentos faltantes
        missing_docs = form.cleaned_data.get("missing_docs")
        if missing_docs:
            # Filtrar pacientes que no tienen todos los docs requeridos
            from django.db.models import Count
            qs = qs.annotate(
                attachment_count=Count('attachments')
            ).filter(attachment_count__lt=3)  # Menos de 3 docs obligatorios

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

    # Opciones para datalist (como Servicio)
    service_options = list(
        Patient.objects.exclude(service__isnull=True)
        .exclude(service__exact="")
        .values_list("service", flat=True)
        .distinct()
        .order_by("service")
    )
    doctor_options = list(
        Patient.objects.exclude(doctor__isnull=True)
        .exclude(doctor__exact="")
        .values_list("doctor", flat=True)
        .distinct()
        .order_by("doctor")
    )
    coverage_options = list(
        Patient.objects.exclude(coverage__isnull=True)
        .exclude(coverage__exact="")
        .values_list("coverage", flat=True)
        .distinct()
        .order_by("coverage")
    )

    return render(request, "core/patient_list.html", {
        "patients": qs,
        "form": form,
        "service_options": service_options,
        "doctor_options": doctor_options,
        "coverage_options": coverage_options,
    })

# -------------------------------------------------------------
# DETALLE
# -------------------------------------------------------------
@login_required
def patient_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)

    attachment_form = AttachmentForm()
    reprogram_form = ReprogramForm()

    back_url = _resolve_next_url(request)

    if request.method == "POST":
        next_url = _resolve_next_url(request)

        # ACTUALIZAR ESTADO
        if "update_status" in request.POST:
            old_status = patient.status
            old_assigned = patient.assigned_to
            
            new_status = request.POST.get("status", patient.status)
            new_obs = request.POST.get("internal_observations", "").strip()
            assigned_to_id = request.POST.get("assigned_to")
            
            patient.status = new_status
            patient.internal_observations = new_obs
            
            # Registrar cambio de estado
            if old_status != new_status:
                PatientHistory.objects.create(
                    patient=patient,
                    user=request.user,
                    action=PatientHistory.ACTION_STATUS_CHANGE,
                    field_name='status',
                    old_value=old_status,
                    new_value=new_status
                )
            
            # Actualizar usuario asignado
            if assigned_to_id:
                from django.contrib.auth.models import User
                try:
                    new_assigned = User.objects.get(pk=assigned_to_id)
                    if old_assigned != new_assigned:
                        patient.assigned_to = new_assigned
                        PatientHistory.objects.create(
                            patient=patient,
                            user=request.user,
                            action=PatientHistory.ACTION_ASSIGN,
                            field_name='assigned_to',
                            old_value=old_assigned.username if old_assigned else 'Sin asignar',
                            new_value=new_assigned.username
                        )
                except User.DoesNotExist:
                    pass
            else:
                if old_assigned is not None:
                    patient.assigned_to = None
                    PatientHistory.objects.create(
                        patient=patient,
                        user=request.user,
                        action=PatientHistory.ACTION_ASSIGN,
                        field_name='assigned_to',
                        old_value=old_assigned.username,
                        new_value='Sin asignar'
                    )
                
            patient.save()
            messages.success(request, "Estado actualizado.")
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
                old_date = patient.planned_date
                reprogram_form.save()
                
                # Registrar reprogramación en historial
                PatientHistory.objects.create(
                    patient=patient,
                    user=request.user,
                    action=PatientHistory.ACTION_REPROGRAM,
                    field_name='planned_date',
                    old_value=str(old_date) if old_date else 'Sin fecha',
                    new_value=str(patient.planned_date),
                    notes=patient.last_reprogram_reason
                )
                
                messages.success(request, "Reprogramación realizada.")
                return redirect(_build_patient_detail_url(patient.pk, next_url))

        # BORRAR (solo admin)
        if "delete_patient" in request.POST:
            if request.user.is_staff:
                patient.delete()
                messages.success(request, "Paciente eliminado.")
                return redirect(next_url)
            return HttpResponse("No autorizado", status=403)

    from django.contrib.auth.models import User
    users = User.objects.filter(is_active=True).order_by('username')
    
    return render(request, "core/patient_detail.html", {
        "patient": patient,
        "attachment_form": attachment_form,
        "reprogram_form": reprogram_form,
        "back_url": back_url,
        "users": users,
    })
# -------------------------------------------------------------
# COLORES CALENDARIO
# -------------------------------------------------------------
def _service_color(service_name: str) -> str:
    s = (service_name or "").lower()
    if "trauma" in s:
        return "#ff5722"
    if "hemod" in s:
        return "#e91e63"
    if "uro" in s:
        return "#009688"
    if "cardio" in s:
        return "#2196f3"
    if "neuro" in s:
        return "#9c27b0"
    return "#607d8b"


# -------------------------------------------------------------
# CALENDARIO
# -------------------------------------------------------------
@login_required
def calendar_view(request):
    return render(request, 'core/calendar.html')


@login_required
def calendar_day_view(request):
    date_str = request.GET.get("date")
    date_obj = parse_date(date_str) if date_str else None

    patients = []
    if date_obj:
        patients = (
            Patient.objects.filter(planned_date=date_obj)
            .order_by("service", "full_name")
        )

    return render(request, "core/calendar_day.html", {
        "date": date_obj,
        "patients": patients,
    })


@login_required
def calendar_events(request):
    events = []
    for p in Patient.objects.all():
        if not p.planned_date:
            continue
        events.append({
            "id": p.id,
            "title": f"{p.full_name} - {p.service}",
            "start": p.planned_date.isoformat(),
            "allDay": True,
            "backgroundColor": _service_color(p.service),
            "borderColor": _service_color(p.service),
        })
    return JsonResponse(events, safe=False)


@login_required
@require_POST
def calendar_move_event(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    new_date_str = request.POST.get("date")

    try:
        new_date = datetime.fromisoformat(new_date_str).date()
    except:
        return JsonResponse({"error": "Fecha inválida"}, 400)

    patient.planned_date = new_date
    patient.last_reprogram_date = timezone.now()
    patient.last_reprogram_reason = "Reprogramado desde calendario"
    patient.status = Patient.STATUS_REPROGRAMADO
    patient.save()

    return JsonResponse({"status": "ok"})


# -------------------------------------------------------------
# ESTADISTICAS
# -------------------------------------------------------------
@login_required
def stats_view(request):
    return render(request, 'core/stats.html')


@login_required
def stats_data(request):
    # Rango por created_at (fecha de carga)
    today = timezone.localdate()
    default_from = today - timedelta(days=6)
    default_to = today

    start = request.GET.get("from") or str(default_from)   # YYYY-MM-DD
    end = request.GET.get("to") or str(default_to)

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

    base = Patient.objects.filter(created_at__date__gte=start_d, created_at__date__lte=end_d)

    overall_total = base.count()
    overall_by_status = list(base.values("status").annotate(count=Count("id")).order_by("-count"))

    by_service = list(base.values("service").annotate(count=Count("id")).order_by("-count"))
    by_coverage = list(base.values("coverage").annotate(count=Count("id")).order_by("-count"))

    # service + status pivot (para stacked + detalle)
    raw = list(base.values("service", "status").annotate(count=Count("id")))
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
            pq = Patient.objects.filter(created_at__date__gte=p_start, created_at__date__lte=p_end)
            periods.append({
                "label": f"{p_start.strftime('%d/%m')}–{p_end.strftime('%d/%m')}",
                "start": str(p_start),
                "end": str(p_end),
                "total": pq.count(),
                "by_status": list(pq.values("status").annotate(count=Count("id")).order_by("-count")),
            })
            i += 1
            p_start = p_start + timedelta(days=7)

    return JsonResponse({
        "range": {"start": str(start_d), "end": str(end_d), "date_field": "created_at"},
        "overall": {"total": overall_total, "by_status": overall_by_status},
        "by_service": by_service,
        "by_coverage": by_coverage,
        "by_service_status": by_service_status,
        "periods": periods,
    })


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

    for p in Patient.objects.all().order_by("-created_at"):
        ws.append([
            p.tracking_id,
            p.full_name,
            p.dni,
            p.coverage,
            p.doctor,
            p.service,
            p.planned_date.isoformat() if getattr(p, "planned_date", None) else "",
            p.get_status_display() if hasattr(p, "get_status_display") else (p.status or ""),
            p.created_at.date().isoformat() if getattr(p, "created_at", None) else "",
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
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, height - 50, "Reporte de Pacientes - Panel OVA")

    y = height - 80
    pdf.setFont("Helvetica", 10)

    for p in Patient.objects.all()[:100]:
        pdf.drawString(50, y, f"{p.tracking_id} - {p.full_name} - {p.coverage} - {p.get_status_display()}")
        y -= 14
        if y < 50:
            pdf.showPage()
            y = height - 80

    pdf.showPage()
    pdf.save()

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = "attachment; filename=pacientes.pdf"
    return response


# -------------------------------------------------------------
# SEGUIMIENTO PUBLICO
# -------------------------------------------------------------
@csrf_exempt
def tracking_view(request):
    tracking_id = request.GET.get("id", "").strip()
    patient = None

    if tracking_id:
        patient = Patient.objects.filter(tracking_id=tracking_id).first()

    return render(request, "core/tracking_form.html", {
        "tracking_id": tracking_id,
        "patient": patient,
    })