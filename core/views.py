from datetime import datetime, timedelta
from io import BytesIO

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
from .models import Patient, Attachment

# -------------------------------------------------------------
# LISTAS (servicios, médicos, coberturas)
# -------------------------------------------------------------

SERVICIOS_QR = [
    'TRAUMATOLOGIA',
    'HEMODINAMIA',
    'UROLOGIA',
]

MEDICOS_QR = [
    'ABALO EDUARDO', 'ADROGUE LUIS', 'BARBIERI PABLO', 'CARRIZO JUAN',
    'CHAVEZ ARIEL', 'CONSTANZA EDUARDO', 'CORDOBA ALEJANDRA',
    'DE ZAVALIA MAXIMO', 'DEIMUNDO MARCOS', 'DEVOTO MATIAS',
    'GOBBI ENRIQUE', 'GRANDOLI FERNANDO', 'IGLESIAS ALEJANDRO',
    'MALLEA ANDRES', 'MENINATO MARCOS', 'MOUNIER CARLOS',
    'ORTIZ EZEQUIEL', 'PEREA AGUSTIN', 'PINOTTI NORBERTO',
    'SANNA HERNAN', 'SERE IGNACIO', 'TORGA SPAK ROGER',
    'VALENTINI ROBERTO', 'VILLA NATALIA', 'YAVEN IGNACIO',
    'YEREGUI SANTIAGO',

    # HEMODINAMIA
    'MAFFEO HORACIO', 'D ALESANDRO CIRO', 'DE CANDIDO LAURA',
    'TAMASHIRO GUSTAVO', 'VEGA PABLO', 'MAYDANA MARTIN', 'CAROSELLA LUCILA',
    'GARBUGINO SILVIA', 'SAYAVEDRA RAMIRO', 'BELDI FLORENCIA', 'RIVAROLA MARCELO',
    'VILLAR DIEGO', 'TRENTACOSTE LUIS', 'LUCINI VICTORIO', 'LABADET CARLOS', 'HADID CLAUDIO',
    'DI TORO DARIO', 'MALDONADO', 'LABADET SEBASTIAN', 'BOCHOEYER ANDRES', 'HERRERA VEGAS DIEGO',
    'SALVADORES PABLO', 'SIMONELLI DAMIAN', 'MENGO GUSTAVO',

    # NUEVOS DOCTORES
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

    total = Patient.objects.count()
    total_mes = Patient.objects.filter(created_at__date__gte=inicio_mes).count()
    total_semana = Patient.objects.filter(created_at__date__gte=hace_7_dias).count()

    raw_por_estado = Patient.objects.values('status').annotate(c=Count('id'))
    mapa_estados = {row['status']: row['c'] for row in raw_por_estado}

    context = {
        "hoy": hoy,
        "total": total,
        "total_mes": total_mes,
        "total_semana": total_semana,
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
    form = PatientFilterForm(request.GET or None)
    qs = Patient.objects.all()

    if form.is_valid():

        # Buscar
        q = form.cleaned_data.get('q')
        if q:
            qs = qs.filter(
                Q(full_name__icontains=q) |
                Q(dni__icontains=q)
            )

        # Estado
        status = form.cleaned_data.get('status')
        if status:
            qs = qs.filter(status=status)

        # Cobertura
        coverage = form.cleaned_data.get('coverage')
        if coverage:
            qs = qs.filter(coverage=coverage)

        # Doctor
        doctor = form.cleaned_data.get('doctor')
        if doctor:
            qs = qs.filter(doctor=doctor)

        # Servicio
        service = form.cleaned_data.get('service')
        if service:
            qs = qs.filter(service=service)

        # Fechas
        date_from = form.cleaned_data.get('date_from')
        if date_from:
            qs = qs.filter(planned_date__gte=date_from)

        date_to = form.cleaned_data.get('date_to')
        if date_to:
            qs = qs.filter(planned_date__lte=date_to)

        created_from = form.cleaned_data.get('created_from')
        if created_from:
            qs = qs.filter(created_at__date__gte=created_from)

        created_to = form.cleaned_data.get('created_to')
        if created_to:
            qs = qs.filter(created_at__date__lte=created_to)

        # Orden
        order_by = form.cleaned_data.get('order_by')
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

    return render(request, 'core/patient_list.html', {
        'patients': qs,
        'form': form,
    })


# -------------------------------------------------------------
# DETALLE
# -------------------------------------------------------------
@login_required
def patient_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)

    attachment_form = AttachmentForm()
    reprogram_form = ReprogramForm()

    if request.method == "POST":

        # ACTUALIZAR ESTADO
        if "update_status" in request.POST:
            new_status = request.POST.get("status", patient.status)
            new_obs = request.POST.get("internal_observations", "").strip()
            patient.status = new_status
            patient.internal_observations = new_obs
            patient.save()
            messages.success(request, "Estado actualizado.")
            return redirect("core:patient_detail", pk=patient.pk)

        # ARCHIVOS
        if "add_attachment" in request.POST:
            attachment_form = AttachmentForm(request.POST, request.FILES)
            if attachment_form.is_valid():
                att = attachment_form.save(commit=False)
                att.patient = patient
                att.save()
                messages.success(request, "Archivo agregado.")
                return redirect("core:patient_detail", pk=patient.pk)

        # REPROGRAMAR
        if "reprogram" in request.POST:
            reprogram_form = ReprogramForm(request.POST, instance=patient)
            if reprogram_form.is_valid():
                reprogram_form.save()
                messages.success(request, "Reprogramación realizada.")
                return redirect("core:patient_detail", pk=patient.pk)

        # BORRAR (solo admin)
        if "delete_patient" in request.POST:
            if request.user.is_staff:
                patient.delete()
                messages.success(request, "Paciente eliminado.")
                return redirect("core:patient_list")
            else:
                return HttpResponse("No autorizado", status=403)

    return render(request, "core/patient_detail.html", {
        "patient": patient,
        "attachment_form": attachment_form,
        "reprogram_form": reprogram_form,
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
    data = {
        "by_service": list(Patient.objects.values("service").annotate(count=Count("id")).order_by("-count")),
        "by_coverage": list(Patient.objects.values("coverage").annotate(count=Count("id")).order_by("-count")[:10]),
        "by_status": list(Patient.objects.values("status").annotate(count=Count("id")).order_by("-count")),
    }
    return JsonResponse(data)


# -------------------------------------------------------------
# EXPORTS
# -------------------------------------------------------------
@login_required
def export_excel(request):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Pacientes"

    ws.append([
        "Tracking ID", "Nombre", "DNI", "Cobertura",
        "Médico", "Servicio", "Fecha intervención", "Estado"
    ])

    for p in Patient.objects.all():
        ws.append([
            p.tracking_id, p.full_name, p.dni, p.coverage,
            p.doctor, p.service,
            p.planned_date.isoformat() if p.planned_date else "",
            p.get_status_display(),
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
