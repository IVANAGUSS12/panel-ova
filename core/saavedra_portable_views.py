from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.http import require_http_methods

from .views import (
    DEFAULT_WHATSAPP_BETWEEN_SEND_DELAY_MS,
    DEFAULT_WHATSAPP_LOAD_TIMEOUT_MS,
    DEFAULT_WHATSAPP_POST_SEND_DELAY_MS,
    _normalize_whatsapp_phone,
    _parse_positive_int,
    build_patient_whatsapp_message,
)

from . import saavedra_portable_backend as backend


def _json_error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message}, status=status)


@login_required
def quirofano_redirect_view(request):
    agenda = backend.get_agenda_config(request.GET.get("agenda"))["key"]
    return redirect(f"{reverse('core:calendar')}?agenda={agenda}")


@login_required
def calendar_portable_view(request):
    current_agenda = backend.get_agenda_config(request.GET.get("agenda"))
    return render(
        request,
        "core/calendar_portable.html",
        {
            "agendas": backend.get_available_agendas(),
            "current_agenda": current_agenda,
        },
    )


@login_required
@require_GET
def api_surgeries(request, agenda):
    return JsonResponse(backend.list_surgeries(agenda), safe=False)


@login_required
@require_GET
def api_meta(request, agenda):
    return JsonResponse(backend.read_meta(agenda))


@login_required
@require_GET
def api_versions(request, agenda):
    return JsonResponse(backend.read_versions(agenda))


@login_required
@require_GET
def api_movements(request, agenda):
    return JsonResponse(backend.list_movements(agenda), safe=False)


@login_required
def api_status(request, agenda):
    if request.method == "GET":
        return JsonResponse(backend.read_status_store(agenda))
    if request.method != "POST":
        return _json_error("Método no permitido", status=405)

    try:
        import json

        body = json.loads(request.body.decode("utf-8") or "{}")
        result = backend.write_status(agenda, body.get("key", ""), body.get("status", {}))
    except ValueError as exc:
        return _json_error(str(exc), status=400)
    except Exception as exc:
        return _json_error(str(exc), status=500)
    return JsonResponse(result)


@login_required
@require_http_methods(["GET", "POST"])
def api_internaciones_varias(request, agenda):
    if backend.get_agenda_config(agenda)["key"] != "saavedra":
        return _json_error("Internaciones Varias solo esta disponible para Saavedra", status=404)

    if request.method == "GET":
        fecha = request.GET.get("fecha", "")
        if not fecha:
            return _json_error("Fecha requerida", status=400)
        try:
            return JsonResponse(backend.list_internaciones_varias(fecha, agenda_key=agenda), safe=False)
        except ValueError as exc:
            return _json_error(str(exc), status=400)

    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
        item = backend.save_internacion_varias(body)
    except ValueError as exc:
        return _json_error(str(exc), status=400)
    except Exception as exc:
        return _json_error(str(exc), status=500)
    return JsonResponse({"ok": True, "item": item})


@login_required
@require_http_methods(["PUT", "DELETE"])
def api_internacion_varias_detail(request, agenda, item_id):
    if backend.get_agenda_config(agenda)["key"] != "saavedra":
        return _json_error("Internaciones Varias solo esta disponible para Saavedra", status=404)

    try:
        if request.method == "DELETE":
            backend.delete_internacion_varias(item_id)
            return JsonResponse({"ok": True})
        body = json.loads(request.body.decode("utf-8") or "{}")
        item = backend.save_internacion_varias(body, item_id=item_id)
    except ValueError as exc:
        return _json_error(str(exc), status=400)
    except Exception as exc:
        return _json_error(str(exc), status=500)
    return JsonResponse({"ok": True, "item": item})


@login_required
@require_GET
def print_programacion_saavedra(request):
    fecha = request.GET.get("fecha", "")
    if not fecha:
        return _json_error("Fecha requerida", status=400)
    surgeries = backend.list_surgeries("saavedra")
    status_store = backend.read_status_store("saavedra")
    day_surgeries = [item for item in surgeries if item.get("date") == fecha]
    internaciones = backend.list_internaciones_varias(fecha, agenda_key="saavedra")

    def _is_resolved_status(value):
        return (value or "").strip().lower() in {"ok", "no requiere"}

    def _pending_labels(saved):
        fields = [
            ("material", "Material"),
            ("auth", "Aut."),
            ("budget", "Presupuesto"),
            ("order", "Orden"),
        ]
        labels = []
        for field, label in fields:
            value = saved.get(field, "Pendiente")
            if not _is_resolved_status(value):
                labels.append(label)
        return labels

    def _print_destination(value):
        text = (value or "").strip()
        if text in {"-", "—", "–"}:
            return "AMBULATORIO", True
        return text, False

    rows = []
    for surgery in day_surgeries:
        key = backend.surgery_key(surgery)
        saved = status_store.get(key, {})
        destino, ambulatory_destino = _print_destination(
            saved.get("destino_editable") or surgery.get("svc_destination") or surgery.get("destination", "")
        )
        obs_parts = []
        pending = _pending_labels(saved)
        if pending:
            obs_parts.append(f"PENDIENTE: {', '.join(pending)}")
        if saved.get("obs"):
            obs_parts.append(saved.get("obs"))
        rows.append({
            "horario": surgery.get("time", ""),
            "sector": surgery.get("room") or "Sin sector",
            "paciente": surgery.get("patient", ""),
            "edad": f"{surgery.get('age')} anos" if surgery.get("age") else "",
            "prestacion": surgery.get("procedure", ""),
            "profesional": surgery.get("surgeon", ""),
            "obra_social": surgery.get("coverage", ""),
            "origen": surgery.get("origin", ""),
            "destino": destino,
            "ambulatory_destino": ambulatory_destino,
            "cama": saved.get("cama_asignada", ""),
            "estadia": saved.get("stay_days", ""),
            "observaciones": " | ".join(obs_parts),
            "auto": False,
        })

    for item in internaciones:
        auto = item.get("origen_registro") != "manual"
        destino, ambulatory_destino = _print_destination(item.get("destino", ""))
        rows.append({
            "horario": item.get("horario") or item.get("horario_cirugia_original") or "",
            "sector": "Internaciones Varias",
            "paciente": item.get("paciente_nombre", ""),
            "edad": f"{item.get('edad')} anos" if item.get("edad") else "",
            "prestacion": item.get("motivo") or item.get("intervencion_original", ""),
            "profesional": item.get("medico_responsable") or item.get("cirujano_original", ""),
            "obra_social": item.get("obra_social", ""),
            "origen": "Automatica" if auto else "Manual",
            "destino": destino,
            "ambulatory_destino": ambulatory_destino,
            "cama": item.get("cama_asignada", ""),
            "estadia": "",
            "observaciones": " | ".join([value for value in [item.get("motivo_automatico", "") if auto else "", item.get("observaciones", "")] if value]),
            "auto": auto,
        })

    rows.sort(key=lambda item: (item["horario"] or "99:99", item["sector"], item["paciente"]))
    row_count = len(rows)
    if row_count >= 42:
        fit_class = "print-fit-compact"
    elif row_count >= 34:
        fit_class = "print-fit-tight"
    elif row_count >= 26:
        fit_class = "print-fit-medium"
    else:
        fit_class = "print-fit-normal"

    return render(
        request,
        "core/calendar_saavedra_print.html",
        {
            "fecha": fecha,
            "rows": rows,
            "fit_class": fit_class,
        },
    )


@login_required
@require_POST
def api_refresh(request, agenda):
    try:
        result = backend.refresh_report(agenda)
    except Exception as exc:
        return _json_error(str(exc), status=500)
    return JsonResponse(result)


@login_required
@require_POST
def api_upload(request, agenda):
    uploaded_file = request.FILES.get("file")
    if uploaded_file is None:
        return _json_error("No se recibió ningún archivo", status=400)
    if not uploaded_file.name:
        return _json_error("Nombre de archivo vacío", status=400)

    try:
        file_bytes = b"".join(chunk for chunk in uploaded_file.chunks())
        result = backend.save_uploaded_report(agenda, uploaded_file.name, file_bytes)
    except ValueError as exc:
        return _json_error(str(exc), status=400)
    except Exception as exc:
        return _json_error(str(exc), status=500)
    return JsonResponse(result)


@login_required
@require_POST
def api_whatsapp_autosend(request, agenda):
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return _json_error("Body JSON inválido", status=400)

    selected_keys = body.get("keys") or []
    if not isinstance(selected_keys, list) or not selected_keys:
        return _json_error("No se recibieron cirugías seleccionadas", status=400)

    surgeries = backend.list_surgeries(agenda)
    surgeries_by_key = {backend.surgery_key(item): item for item in surgeries}
    selected = [surgeries_by_key[key] for key in selected_keys if key in surgeries_by_key]
    if not selected:
        return _json_error("No se encontraron cirugías válidas para enviar", status=404)

    load_timeout_ms = _parse_positive_int(body.get("load_timeout_ms"), DEFAULT_WHATSAPP_LOAD_TIMEOUT_MS)
    post_send_delay_ms = _parse_positive_int(body.get("post_send_delay_ms"), DEFAULT_WHATSAPP_POST_SEND_DELAY_MS)
    between_send_delay_ms = _parse_positive_int(body.get("between_send_delay_ms"), DEFAULT_WHATSAPP_BETWEEN_SEND_DELAY_MS)

    items = []
    skipped = []
    for item in selected:
        normalized_phone = _normalize_whatsapp_phone(item.get("whatsapp_phone") or item.get("phone") or item.get("app_phone"))
        if not normalized_phone or len(normalized_phone) < 12:
            skipped.append(item.get("patient") or "Paciente sin nombre")
            continue
        items.append({
            "surgery_key": backend.surgery_key(item),
            "patient_name": item.get("patient", ""),
            "phone": normalized_phone,
            "raw_phone": item.get("whatsapp_phone") or item.get("phone") or item.get("app_phone") or "",
            "message": build_patient_whatsapp_message(
                item.get("patient"),
                item.get("date"),
                item.get("surgeon"),
            ),
        })

    if not items:
        return _json_error("Ninguno de los seleccionados tiene un teléfono válido para WhatsApp", status=400)

    batch_dir = Path(settings.MEDIA_ROOT) / "quirofano_reports" / "whatsapp_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    timestamp = backend.datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_path = batch_dir / f"portable_whatsapp_{agenda}_{timestamp}.json"
    batch_payload = {
        "created_at": backend.datetime.now().isoformat(timespec="seconds"),
        "created_by": request.user.username,
        "agenda": agenda,
        "items": items,
        "skipped": skipped,
        "options": {
            "load_timeout_ms": load_timeout_ms,
            "post_send_delay_ms": post_send_delay_ms,
            "between_send_delay_ms": between_send_delay_ms,
        },
    }
    batch_path.write_text(json.dumps(batch_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    manage_py = Path(settings.BASE_DIR) / "manage.py"
    command = [
        sys.executable,
        str(manage_py),
        "send_quirofano_whatsapp",
        "--batch-file", str(batch_path),
        "--load-timeout-ms", str(load_timeout_ms),
        "--post-send-delay-ms", str(post_send_delay_ms),
        "--between-send-delay-ms", str(between_send_delay_ms),
    ]

    creationflags = 0
    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    try:
        subprocess.Popen(
            command,
            cwd=str(settings.BASE_DIR),
            creationflags=creationflags,
            close_fds=bool(creationflags),
        )
    except Exception as exc:
        return _json_error(f"No se pudo iniciar el envío automático: {exc}", status=500)

    return JsonResponse({
        "ok": True,
        "queued": len(items),
        "skipped": skipped,
    })
