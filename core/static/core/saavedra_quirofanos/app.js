// ── Constantes ─────────────────────────────────────────────────────────────
const WEEKDAYS = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
const MONTHS   = [
  "enero","febrero","marzo","abril","mayo","junio",
  "julio","agosto","septiembre","octubre","noviembre","diciembre"
];
const AUTO_SYNC_MS = 2000;
const AGENDA_KEY = window.CALENDAR_AGENDA_KEY || "saavedra";
const API_BASE = `api/${AGENDA_KEY}`;
const MOVEMENT_HISTORY_STORAGE_KEY = `agenda-movement-history-${AGENDA_KEY}`;
// ── Estado global ───────────────────────────────────────────────────────────
let surgeries    = [];   // cirugías del reporte actual
let statusStore  = {};   // { surgeryKey: { obs, material, auth, budget, order } }
let internacionesByDate = {};
let internacionesLoading = new Set();
let movementHistory = []; // historial persistido de movimientos detectados
let movementHistoryApiAvailable = true;
let serverVersions = null;
let syncTimer = null;
let syncInFlight = false;
let currentDate  = new Date();
let selectedDate = new Date();
let appIsLoading = true;

const el = (id) => document.getElementById(id);

function getCsrfToken() {
  const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function apiUrl(path) {
  return `${API_BASE}/${path.replace(/^\/+/, "")}`;
}

// ── Clave única por cirugía ─────────────────────────────────────────────────
function surgeryKey(s) {
  return `${s.patient}|${s.date}|${s.time}|${s.room}`;
}

function normalizeWhatsappPhone(rawPhone) {
  let digits = String(rawPhone || "").replace(/[^0-9]/g, "");
  if (!digits) return "";
  if (digits.startsWith("00")) digits = digits.slice(2);
  if (digits.startsWith("549")) return digits;
  if (digits.startsWith("54")) {
    let national = digits.slice(2);
    if (national.startsWith("9")) return digits;
    if (national.startsWith("0")) national = national.slice(1);
    national = national.replace(/^(\d{2,4})15/, "$1");
    if (national.length >= 10 && national.length <= 11) return "549" + national;
    return "54" + national;
  }
  if (digits.startsWith("0")) digits = digits.slice(1);
  digits = digits.replace(/^(\d{2,4})15/, "$1");
  if (digits.length >= 10 && digits.length <= 11) return "549" + digits;
  return digits;
}

function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return `${d.getDate()} de ${MONTHS[d.getMonth()]} de ${d.getFullYear()}`;
}

// Verdadero si la cirugía está cancelada o suspendida (no se muestra en la grilla)
function isCancelled(s) {
  const st = (s.sys_status || "").toLowerCase();
  return st.includes("cancelado") || (s.suspended || "").toLowerCase() === "si";
}

// ── API helpers ─────────────────────────────────────────────────────────────
async function apiGet(path) {
  const r = await fetch(apiUrl(path));
  if (!r.ok) {
    let detail = "";
    try {
      const payload = await r.json();
      detail = payload && payload.error ? `: ${payload.error}` : "";
    } catch {
      // ignorar respuestas no JSON
    }
    throw new Error(`HTTP ${r.status} en ${path}${detail}`);
  }
  return r.json();
}

async function apiGetOptional(path, fallbackValue = null) {
  try {
    return await apiGet(path);
  } catch (error) {
    if (String(error.message || "").includes(`en ${path}`)) {
      return fallbackValue;
    }
    throw error;
  }
}

async function apiPost(path, body) {
  const r = await fetch(apiUrl(path), {
    method:  "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken(),
    },
    body:    JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = "";
    try {
      const payload = await r.json();
      detail = payload && payload.error ? `: ${payload.error}` : "";
    } catch {
      // ignorar respuestas no JSON
    }
    throw new Error(`HTTP ${r.status} en ${path}${detail}`);
  }
  return r.json();
}

async function apiPut(path, body) {
  const r = await fetch(apiUrl(path), {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken(),
    },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = "";
    try {
      const payload = await r.json();
      detail = payload && payload.error ? `: ${payload.error}` : "";
    } catch {
      // ignorar respuestas no JSON
    }
    throw new Error(`HTTP ${r.status} en ${path}${detail}`);
  }
  return r.json();
}

async function apiDelete(path) {
  const r = await fetch(apiUrl(path), {
    method: "DELETE",
    headers: { "X-CSRFToken": getCsrfToken() },
  });
  if (!r.ok) {
    let detail = "";
    try {
      const payload = await r.json();
      detail = payload && payload.error ? `: ${payload.error}` : "";
    } catch {
      // ignorar respuestas no JSON
    }
    throw new Error(`HTTP ${r.status} en ${path}${detail}`);
  }
  return r.json();
}

function sameVersions(a, b) {
  if (!a || !b) return false;
  return a.current === b.current
    && a.previous === b.previous
    && a.status === b.status
    && a.meta === b.meta
    && a.movements === b.movements;
}

function applyMeta(meta) {
  if (meta && meta.downloaded_at) {
    const manualLabel = meta.manual ? " · cargado manualmente" : "";
    el("lastDownload").textContent =
      "Último reporte: " + meta.downloaded_at.replace("T", " ").slice(0, 16) +
      ` (${meta.count || surgeries.length} cirugías)` + manualLabel;
  }
  if (meta && meta.sede) {
    const subtitle = el("monthSubtitle");
    if (subtitle) subtitle.textContent = `Cantidad de turnos por día · Sede ${meta.sede}`;
  }
}

function ensureSelectedDate() {
  if (!surgeries.length) return;

  const selectedIso = selectedDate.toISOString().slice(0, 10);
  if (surgeries.some(s => s.date === selectedIso)) return;

  const today  = new Date().toISOString().slice(0, 10);
  const future = surgeries.map(s => s.date).sort().find(d => d >= today) || surgeries.map(s => s.date).sort()[0];
  if (future) {
    selectedDate = new Date(future + "T00:00:00");
    currentDate  = new Date(selectedDate);
  }
}

function isEditingLocally() {
  const active = document.activeElement;
  return !!active && active.matches("textarea, select, input:not([type='file'])");
}

async function refreshServerVersions() {
  const versions = await apiGetOptional("versions", null);
  if (versions) serverVersions = versions;
  return versions;
}

async function syncFromServer(force = false) {
  if (syncInFlight) return;
  if (!force && isEditingLocally()) return;

  syncInFlight = true;
  try {
    const nextVersions = await apiGetOptional("versions", null);
    if (!nextVersions) return;

    if (!force && sameVersions(serverVersions, nextVersions)) {
      return;
    }

    const needsSurgeries = force || !serverVersions || serverVersions.current !== nextVersions.current || serverVersions.meta !== nextVersions.meta;
    const needsStatus = force || !serverVersions || serverVersions.status !== nextVersions.status;
    const needsMovements = force || !serverVersions || serverVersions.movements !== nextVersions.movements;

    const [nextSurgeries, nextMeta, nextStatus, nextMovements] = await Promise.all([
      needsSurgeries ? apiGet("surgeries") : Promise.resolve(null),
      needsSurgeries ? apiGet("meta") : Promise.resolve(null),
      needsStatus ? apiGet("status") : Promise.resolve(null),
      needsMovements ? apiGetOptional("movements", null) : Promise.resolve(null),
    ]);

    if (nextSurgeries) {
      surgeries = nextSurgeries || [];
      internacionesByDate = {};
    }
    if (nextMeta) applyMeta(nextMeta || {});
    if (nextStatus) statusStore = nextStatus || {};
    if (needsMovements) {
      movementHistory = nextMovements || loadLocalMovementHistory();
      movementHistoryApiAvailable = nextMovements !== null;
      saveLocalMovementHistory(movementHistory);
    }

    ensureSelectedDate();
    serverVersions = nextVersions;

    if (needsSurgeries || needsStatus || needsMovements) {
      renderAll();
    }
  } catch {
    // silencio: el próximo ciclo vuelve a intentar
  } finally {
    syncInFlight = false;
  }
}

function startAutoSync() {
  if (syncTimer) return;
  syncTimer = window.setInterval(() => {
    syncFromServer(false);
  }, AUTO_SYNC_MS);
}

function flashSaveButton(btn, label) {
  btn.textContent = label;
  window.setTimeout(() => {
    btn.textContent = "Guardar estado";
  }, 1800);
}

function movementIdentity(m) {
  return JSON.stringify({
    type: m.type || "",
    subtype: m.subtype || "",
    patient: m.patient || "",
    detected_at: m.detected_at || "",
    date: m.date || "",
    time: m.time || "",
    room: m.room || "",
    from_date: m.from_date || "",
    from_time: m.from_time || "",
    from_room: m.from_room || "",
    to_date: m.to_date || "",
    to_time: m.to_time || "",
    to_room: m.to_room || "",
    filename: m.filename || "",
    changes: m.changes || [],
  });
}

function loadLocalMovementHistory() {
  try {
    const raw = window.localStorage.getItem(MOVEMENT_HISTORY_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveLocalMovementHistory(items) {
  try {
    window.localStorage.setItem(MOVEMENT_HISTORY_STORAGE_KEY, JSON.stringify(items || []));
  } catch {
    // ignorar storage lleno o no disponible
  }
}

function mergeMovementHistory(currentItems, newItems) {
  const merged = [...(currentItems || [])];
  const seen = new Set(merged.map(movementIdentity));
  for (const item of (newItems || [])) {
    const identity = movementIdentity(item);
    if (seen.has(identity)) continue;
    merged.unshift(item);
    seen.add(identity);
  }
  return merged.sort((a, b) => {
    const ta = String(a.detected_at || "");
    const tb = String(b.detected_at || "");
    return tb.localeCompare(ta);
  });
}

// ── Inicialización ──────────────────────────────────────────────────────────
function renderCalendarLoading(message = "Cargando agenda de quirófanos…") {
  const cal = el("calendar");
  if (!cal) return;
  cal.classList.add("is-loading");
  cal.innerHTML = `
    <div class="calendar-loading" role="status" aria-live="polite">
      <span class="calendar-spinner" aria-hidden="true"></span>
      <div>
        <strong>${escHtml(message)}</strong>
        <p>Estamos trayendo los turnos y el historial actualizado.</p>
      </div>
    </div>
    <div class="calendar-skeleton" aria-hidden="true">
      ${Array.from({ length: 21 }, () => '<span></span>').join("")}
    </div>
  `;
}

async function init() {
  appIsLoading = true;
  renderCalendarLoading("Cargando agenda de quirófanos…");
  el("movementStatus").textContent = "Cargando historial de movimientos…";

  try {
    const [data, status, meta, movements, versions] = await Promise.all([
      apiGet("surgeries"),
      apiGet("status"),
      apiGet("meta"),
      apiGetOptional("movements", null),
      apiGetOptional("versions", null),
    ]);

    surgeries   = data   || [];
    internacionesByDate = {};
    statusStore = status || {};
    movementHistory = movements || loadLocalMovementHistory();
    movementHistoryApiAvailable = movements !== null;
    serverVersions = versions;
    saveLocalMovementHistory(movementHistory);

    applyMeta(meta || {});

    if (movements === null) {
      el("movementStatus").textContent = "Historial persistente no disponible hasta reiniciar el servidor. Los pacientes y estados siguen cargando normalmente.";
    }

    if (surgeries.length) {
      // Posicionarse en el primer día con cirugías a partir de hoy
      const today  = new Date().toISOString().slice(0, 10);
      const future = surgeries.map(s => s.date).sort().find(d => d >= today);
      if (future) {
        selectedDate = new Date(future + "T00:00:00");
        currentDate  = new Date(selectedDate);
      }
    }
  } catch (e) {
    el("lastDownload").textContent = `Error al cargar datos del servidor: ${e.message}`;
  } finally {
    appIsLoading = false;
  }

  renderAll();

  if (AGENDA_KEY === "las-heras") {
    await refreshReport();
  }

  startAutoSync();
}

// ── Generar nuevo reporte ───────────────────────────────────────────────────
async function refreshReport() {
  const btn = el("refreshBtn");
  btn.disabled = true;
  btn.classList.add("is-loading");
  btn.textContent = "Generando reporte…";
  el("loadingMsg").style.display = "";
  renderCalendarLoading("Generando reporte CEMIC…");
  el("movementStatus").textContent = "Descargando reporte de CEMIC y actualizando historial…";

  try {
    const result = await apiPost("refresh", {});

    if (!result.ok) {
      el("movementStatus").textContent = "Error: " + (result.error || "desconocido");
      renderMovementHistory();
      return;
    }

    // Recargar datos frescos
    const [data, status, movements] = await Promise.all([
      apiGet("surgeries"),
      apiGet("status"),
      apiGetOptional("movements", null),
    ]);
    surgeries   = data   || [];
    internacionesByDate = {};
    statusStore = status || {};
    movementHistory = movements || mergeMovementHistory(loadLocalMovementHistory(), result.movements || []);
    movementHistoryApiAvailable = movements !== null;
    saveLocalMovementHistory(movementHistory);
    await refreshServerVersions();

    applyMeta({ downloaded_at: result.downloaded_at, count: result.count });

    const newItems = (result.movements || []).length;
    if (movements === null) {
      el("movementStatus").textContent = newItems
        ? `Se detectaron ${newItems} movimiento${newItems !== 1 ? "s" : ""}. Para guardarlos en historial, reiniciá el servidor.`
        : "No se detectaron movimientos nuevos en esta comparación.";
    } else {
      el("movementStatus").textContent = newItems
        ? `Se agregaron ${newItems} movimiento${newItems !== 1 ? "s" : ""} al historial.`
        : "No se detectaron movimientos nuevos en esta comparación.";
    }

    // Reposicionar al primer día con cirugías
    if (surgeries.length) {
      const today  = new Date().toISOString().slice(0, 10);
      const future = surgeries.map(s => s.date).sort().find(d => d >= today);
      if (future) {
        selectedDate = new Date(future + "T00:00:00");
        currentDate  = new Date(selectedDate);
      }
    }

    renderAll();
  } catch (e) {
    el("movementStatus").textContent = "Error al generar el reporte: " + e.message;
    renderMovementHistory();
  } finally {
    btn.disabled = false;
    btn.classList.remove("is-loading");
    btn.textContent = "↓ Generar nuevo reporte";
    el("loadingMsg").style.display = "none";
  }
}

// ── Historial de movimientos ────────────────────────────────────────────────
function movementCategory(m) {
  if (m.type === "compare") return "all";
  if (m.subtype === "suspended") return "suspended";
  if (m.type === "new") return "added";
  if (m.type === "move") return "changed";
  if (m.subtype === "removed") return "removed";
  return "all";
}

function movementLabel(m) {
  if (m.subtype === "no_changes") return "Sin cambios";
  if (m.subtype === "reprog") return "Reprogramado";
  if (m.subtype === "moved") return "Modificado";
  if (m.subtype === "updated") return "Actualizado";
  if (m.subtype === "suspended") return "Suspendido";
  if (m.subtype === "removed") return "Eliminado";
  if (m.type === "new") return "Agregado";
  return "Movimiento";
}

function movementSourceLabel(source) {
  return source === "manual" ? "Carga manual" : "Descarga CEMIC";
}

function movementChangeArea(m) {
  if (m.type === "compare") return "all";
  const fields = Array.isArray(m.changes) ? m.changes.map(change => change.field) : [];
  if (!fields.length) return "all";

  const commentFields = ["comments", "observations", "delay_reason"];
  const logisticsFields = [
    "destination", "svc_destination", "pharmacy", "obs_pharmacy",
    "orthopedics", "obs_ortho", "anat_pat", "hemotherapy", "rx",
  ];

  if (fields.some(field => commentFields.includes(field))) return "comments";
  if (fields.some(field => logisticsFields.includes(field))) return "logistics";
  return "clinical";
}

function formatTimestamp(raw) {
  if (!raw) return "—";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw.replace("T", " ").slice(0, 16);
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const yy = d.getFullYear();
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${dd}/${mm}/${yy} ${hh}:${mi}`;
}

function formatSchedule(date, time, room) {
  const parts = [];
  if (date) parts.push(fmtIso(date));
  if (time) parts.push(time);
  if (room) parts.push(room);
  return parts.length ? parts.join(" · ") : "—";
}

function filteredMovementHistory() {
  const typeFilter = el("movementTypeFilter").value;
  const changeFilter = el("movementChangeFilter").value;
  const detectedDate = el("movementDetectedDate").value;
  const search = (el("movementSearch").value || "").trim().toLowerCase();

  return movementHistory.filter(m => {
    if (typeFilter !== "all" && movementCategory(m) !== typeFilter) return false;
    if (changeFilter !== "all" && movementChangeArea(m) !== changeFilter) return false;
    if (detectedDate && String(m.detected_at || "").slice(0, 10) !== detectedDate) return false;
    if (!search) return true;
    const haystack = [
      m.patient,
      m.procedure,
      m.procedure2,
      m.surgeon,
      m.coverage,
      m.specialty,
      m.room,
      m.to_room,
      m.from_room,
      m.comments,
      m.reason,
      m.filename,
    ].join(" ").toLowerCase();
    return haystack.includes(search);
  });
}

function renderMovementHistory() {
  const box = el("movementList");
  const summary = el("movementSummary");
  const status = el("movementStatus");
  box.innerHTML = "";

  if (!movementHistory.length) {
    box.className = "movement-list empty";
    box.textContent = "Todavía no hay movimientos guardados.";
    summary.textContent = "El historial se completa cuando comparás un reporte nuevo contra el anterior.";
    if (movementHistoryApiAvailable) {
      status.textContent = "Todavía no hay comparación guardada.";
    }
    return;
  }

  const items = filteredMovementHistory();
  status.textContent = movementHistoryApiAvailable
    ? `Última comparación guardada: ${formatTimestamp(movementHistory[0].detected_at)}.`
    : `Se muestran movimientos de la última comparación, pero el historial persistente requiere reiniciar el servidor.`;
  const typeFilter = el("movementTypeFilter").value;
  const changeFilter = el("movementChangeFilter").value;
  const detectedDate = el("movementDetectedDate").value;
  const filterParts = [];
  if (typeFilter !== "all") filterParts.push(el("movementTypeFilter").selectedOptions[0].textContent.toLowerCase());
  if (changeFilter !== "all") filterParts.push(el("movementChangeFilter").selectedOptions[0].textContent.toLowerCase());
  if (detectedDate) filterParts.push(`detectados el ${fmtIso(detectedDate)}`);
  const filterLabel = filterParts.length ? filterParts.join(" · ") : "todos los movimientos";
  summary.textContent = `${items.length} resultado${items.length !== 1 ? "s" : ""} en ${filterLabel} · historial total ${movementHistory.length}.`;

  if (!items.length) {
    box.className = "movement-list empty";
    box.textContent = "No hay movimientos que coincidan con el filtro.";
    return;
  }

  box.className = "movement-list";

  items.forEach(m => {
    const item = document.createElement("div");
    item.className = `movement-item ${m.type}`;

    let html = `<div class="mvmt-top">`;
    html += `<div class="mvmt-patient">${escHtml(m.patient || "Sin paciente")}</div>`;
    html += `<div class="mvmt-badges">`;
    html += `<span class="mvmt-badge kind">${escHtml(movementLabel(m))}</span>`;
    html += `<span class="mvmt-badge source">${escHtml(movementSourceLabel(m.source))}</span>`;
    html += `</div></div>`;

    html += `<div class="mvmt-meta">Detectado: ${escHtml(formatTimestamp(m.detected_at))}</div>`;
    if (m.filename) html += `<div class="mvmt-meta">Archivo: ${escHtml(m.filename)}</div>`;
    if (m.type === "compare") {
      if (m.summary) html += `<div class="mvmt-row">${escHtml(m.summary)}</div>`;
      html += `<div class="mvmt-row">Cirugías comparadas: ${escHtml(String(m.previous_count || 0))} → ${escHtml(String(m.current_count || 0))}</div>`;
      item.innerHTML = html;
      box.appendChild(item);
      return;
    }
    if (m.procedure) html += `<div class="mvmt-row">Práctica: ${escHtml(m.procedure)}</div>`;
    if (m.procedure2) html += `<div class="mvmt-row">Práctica compl.: ${escHtml(m.procedure2)}</div>`;
    if (m.surgeon) html += `<div class="mvmt-row">Cirujano: ${escHtml(m.surgeon)}</div>`;
    if (m.coverage || m.specialty) {
      html += `<div class="mvmt-row">${escHtml([m.coverage, m.specialty].filter(Boolean).join(" · "))}</div>`;
    }
    if (m.anesthesia || m.destination || m.sys_status) {
      html += `<div class="mvmt-row">${escHtml([m.anesthesia, m.destination, m.sys_status].filter(Boolean).join(" · "))}</div>`;
    }

    if (Array.isArray(m.changes) && m.changes.length) {
      html += `<div class="mvmt-row"><strong>Cambios detectados:</strong></div>`;
      m.changes.forEach(change => {
        const fromValue = change.from || "—";
        const toValue = change.to || "—";
        html += `<div class="mvmt-row">${escHtml(change.label || change.field || "Dato")}: ${escHtml(fromValue)} → ${escHtml(toValue)}</div>`;
      });
    }

    if (m.type === "move") {
      html += `<div class="mvmt-dates">`;
      html += `<span class="mvmt-from">Antes: ${escHtml(formatSchedule(m.from_date, m.from_time, m.from_room))}</span>`;
      html += `<span class="mvmt-to">Ahora: ${escHtml(formatSchedule(m.to_date, m.to_time, m.to_room))}</span>`;
      html += `</div>`;
      if (m.subtype === "reprog" && m.prev_date_raw) {
        html += `<div class="mvmt-row">Fecha informada por CEMIC: ${escHtml(formatDateShort(m.prev_date_raw))}</div>`;
      }
    } else {
      html += `<div class="mvmt-dates">`;
      html += `<span class="mvmt-to">Programación: ${escHtml(formatSchedule(m.date, m.time, m.room))}</span>`;
      html += `</div>`;
    }

    if (m.subtype === "suspended") {
      if (m.suspension_date) html += `<div class="mvmt-row">Fecha suspensión: ${escHtml(formatDateShort(m.suspension_date))}</div>`;
      if (m.reason) html += `<div class="mvmt-row">Motivo: ${escHtml(m.reason)}</div>`;
    }
    if (m.comments) html += `<div class="mvmt-row">Comentarios: ${escHtml(m.comments)}</div>`;
    if (m.observations) html += `<div class="mvmt-row">Observaciones: ${escHtml(m.observations)}</div>`;

    item.innerHTML = html;
    box.appendChild(item);
  });
}

// ── Calendario mensual ──────────────────────────────────────────────────────
function renderCalendar() {
  const cal = el("calendar");
  if (appIsLoading) {
    renderCalendarLoading();
    return;
  }
  cal.classList.remove("is-loading");
  cal.innerHTML = "";

  // Encabezados de días
  WEEKDAYS.forEach(d => {
    const w = document.createElement("div");
    w.className   = "weekday";
    w.textContent = d;
    cal.appendChild(w);
  });

  const year  = currentDate.getFullYear();
  const month = currentDate.getMonth();
  el("monthTitle").textContent = `${MONTHS[month]} de ${year}`;

  const first = new Date(year, month, 1);
  const start = new Date(first);
  start.setDate(first.getDate() - first.getDay());

  for (let i = 0; i < 42; i++) {
    const cellDate = new Date(start);
    cellDate.setDate(start.getDate() + i);
    const iso   = cellDate.toISOString().slice(0, 10);
    const count = surgeries.filter(s => s.date === iso && !isCancelled(s)).length;

    const cell = document.createElement("div");
    cell.className = "day-cell";
    if (cellDate.getMonth() !== month) cell.classList.add("outside");
    if (selectedDate.toISOString().slice(0, 10) === iso) cell.classList.add("selected");

    const isToday = new Date().toISOString().slice(0, 10) === iso;
    if (isToday) cell.classList.add("today");

    cell.innerHTML =
      `<div class="day-num">${cellDate.getDate()}</div>` +
      (count
        ? `<div class="turno-badge">${count} turno${count > 1 ? "s" : ""}</div>`
        : "");

    cell.addEventListener("click", () => {
      selectedDate = new Date(cellDate);
      renderCalendar();
      renderSchedule();
    });

    cal.appendChild(cell);
  }
}

// ── Vista diaria por quirófano ──────────────────────────────────────────────
function canUseInternacionesVarias() {
  return AGENDA_KEY === "saavedra";
}

async function ensureInternacionesForDate(iso) {
  if (!canUseInternacionesVarias()) return;
  if (internacionesByDate[iso] || internacionesLoading.has(iso)) return;
  internacionesLoading.add(iso);
  try {
    internacionesByDate[iso] = await apiGet(`internaciones-varias?fecha=${encodeURIComponent(iso)}`);
  } catch {
    internacionesByDate[iso] = [];
  } finally {
    internacionesLoading.delete(iso);
    if (selectedDate.toISOString().slice(0, 10) === iso) renderSchedule();
  }
}

function invalidateInternaciones(iso) {
  if (iso) delete internacionesByDate[iso];
}

function renderSchedule() {
  const iso = selectedDate.toISOString().slice(0, 10);
  ensureInternacionesForDate(iso);

  // Todos los del día, excluyendo cancelados y suspendidos
  const allDay = surgeries.filter(s => s.date === iso && !isCancelled(s));
  const internaciones = canUseInternacionesVarias() ? (internacionesByDate[iso] || []) : [];

  // Aplicar filtros de búsqueda
  const search      = (el("filterSearch").value || "").trim().toLowerCase();
  const roomF       = el("filterRoom").value;
  const onlyPending = el("filterPending").classList.contains("active");

  const daySurgs = allDay.filter(s => {
    if (search) {
      const hay = `${s.patient} ${s.surgeon} ${s.coverage} ${s.specialty} ${s.procedure}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    if (roomF && s.room !== roomF) return false;
    if (onlyPending) {
      const saved = statusStore[surgeryKey(s)] || {};
      const isPos = v => v === "OK" || v === "No requiere";
      const vals  = [
        saved.material || "Pendiente",
        saved.auth || "Pendiente",
        saved.budget || "Pendiente",
        saved.order || "Pendiente",
      ];
      if (vals.every(isPos)) return false;  // ya resuelto → no mostrar
    }
    return true;
  });

  // Título y subtítulo
  el("dayTitle").textContent = formatDate(iso);
  const total = allDay.length;
  const shown = daySurgs.length;
  el("daySummary").textContent = total
    ? (shown < total
        ? `Mostrando ${shown} de ${total} cirugía${total > 1 ? "s" : ""} · ${internaciones.length} internación${internaciones.length === 1 ? "" : "es"} varias · ${formatDate(iso)}`
        : `${total} cirugía${total > 1 ? "s" : ""} · ${internaciones.length} internación${internaciones.length === 1 ? "" : "es"} varias · ${formatDate(iso)}`)
    : `Sin cirugías registradas para ${formatDate(iso)} · ${internaciones.length} internación${internaciones.length === 1 ? "" : "es"} varias.`;

  // Poblar el select de quirófanos con los del día (sin filtrar)
  const roomSel  = el("filterRoom");
  const prevRoom = roomSel.value;
  roomSel.innerHTML = '<option value="">Todos los quirófanos</option>';
  const dayRooms = [...new Set(allDay.map(s => s.room))].sort((a, b) => {
    const na = parseInt(a.replace(/\D/g, "")) || 99;
    const nb = parseInt(b.replace(/\D/g, "")) || 99;
    return na - nb;
  });
  dayRooms.forEach(r => {
    const opt = document.createElement("option");
    opt.value = r;
    opt.textContent = r;
    if (r === prevRoom) opt.selected = true;
    roomSel.appendChild(opt);
  });

  const grid = el("scheduleGrid");
  grid.innerHTML = "";

  // Sin resultados con filtro activo
  if (!daySurgs.length && (search || roomF || onlyPending)) {
    const empty = document.createElement("div");
    empty.className = "empty-search";
    empty.textContent = "Ninguna cirugía coincide con la búsqueda.";
    grid.appendChild(empty);
    return;
  }

  const hasFilter   = !!(search || roomF || onlyPending);
  const visibleRooms = roomF
    ? [roomF]
    : dayRooms.length
      ? dayRooms
      : ["Quirófano 1","Quirófano 2","Quirófano 3","Quirófano 4","Quirófano 5"];

  visibleRooms.forEach(room => {
    const roomCases = daySurgs
      .filter(s => s.room === room)
      .sort((a, b) => a.time.localeCompare(b.time));

    // Ocultar columnas vacías cuando hay filtro activo
    if (!roomCases.length && hasFilter) return;

    const col = document.createElement("div");
    col.className = "room-column";
    col.innerHTML = `
      <div class="room-column-head">
        <h4>${room}</h4>
        <div class="room-column-tools">
          <label class="room-select-wrap">
            <input type="checkbox" class="room-select-all">
            <span>Todos</span>
          </label>
          <button type="button" class="secondary room-send-whatsapp">Enviar WPP</button>
        </div>
      </div>
    `;

    if (!roomCases.length) {
      const empty = document.createElement("div");
      empty.className = "empty-room";
      empty.textContent = "Sin cirugías.";
      col.appendChild(empty);
    } else {
      roomCases.forEach(s => col.appendChild(buildCaseCard(s)));
      bindRoomWhatsappControls(col, roomCases, room);
    }
    grid.appendChild(col);
  });

  if (canUseInternacionesVarias() && !roomF) {
    grid.appendChild(buildInternacionesColumn(iso, internaciones));
  }
}

function bindRoomWhatsappControls(col, roomCases, roomName) {
  const roomSelectAll = col.querySelector(".room-select-all");
  const sendButton = col.querySelector(".room-send-whatsapp");
  const caseCheckboxes = Array.from(col.querySelectorAll(".case-select-checkbox"));

  function syncRoomCheckboxState() {
    const checked = caseCheckboxes.filter(cb => cb.checked).length;
    roomSelectAll.checked = checked > 0 && checked === caseCheckboxes.length;
    roomSelectAll.indeterminate = checked > 0 && checked < caseCheckboxes.length;
  }

  roomSelectAll.addEventListener("change", () => {
    caseCheckboxes.forEach(cb => {
      cb.checked = roomSelectAll.checked;
    });
    syncRoomCheckboxState();
  });

  caseCheckboxes.forEach(cb => cb.addEventListener("change", syncRoomCheckboxState));

  sendButton.addEventListener("click", async () => {
    const selectedKeys = new Set(
      caseCheckboxes
        .filter(cb => cb.checked)
        .map(cb => cb.dataset.surgeryKey || "")
        .filter(Boolean)
    );
    const selected = roomCases.filter(s => selectedKeys.has(surgeryKey(s)));

    if (!selected.length) {
      alert("Seleccioná al menos un paciente dentro de este quirófano.");
      return;
    }

    const loadTimeout = parseInt((el("wppLoadTimeoutMs").value || "0"), 10);
    const betweenDelay = parseInt((el("wppBetweenDelayMs").value || "0"), 10);
    if (!loadTimeout || loadTimeout < 3000) {
      alert("La espera de carga debe ser al menos de 3000 ms.");
      return;
    }
    if (!betweenDelay || betweenDelay < 1000) {
      alert("La espera entre envíos debe ser al menos de 1000 ms.");
      return;
    }

    const invalid = selected.filter(s => {
      const phone = normalizeWhatsappPhone(s.whatsapp_phone || s.phone || s.app_phone);
      return !phone || phone.length < 12;
    });

    const confirmation = invalid.length
      ? `Se enviará WhatsApp automático a ${selected.length - invalid.length} paciente(s) de ${roomName}. ${invalid.length} quedarán omitidos por teléfono inválido. ¿Continuar?`
      : `Se enviará WhatsApp automático a ${selected.length} paciente(s) de ${roomName}. ¿Continuar?`;

    if (!window.confirm(confirmation)) {
      return;
    }

    sendButton.disabled = true;
    sendButton.classList.add("is-loading");
    sendButton.textContent = "Iniciando envío…";
    renderCalendarLoading(`Iniciando WhatsApp para ${roomName}…`);
    try {
      const payload = {
        keys: selected.map(surgeryKey),
        load_timeout_ms: loadTimeout,
        between_send_delay_ms: betweenDelay,
      };
      const result = await apiPost("whatsapp-autosend", payload);
      const skipped = (result && result.skipped) || [];
      if (skipped.length) {
        alert(`Se inició el envío para ${result.queued} paciente(s). ${skipped.length} quedaron omitidos: ${skipped.join(", ")}`);
      } else {
        alert(`Se inició el envío para ${result.queued} paciente(s) del ${roomName}.`);
      }
    } catch (error) {
      alert(`No se pudo iniciar el envío automático: ${error.message}`);
    } finally {
      sendButton.disabled = false;
      sendButton.classList.remove("is-loading");
      sendButton.textContent = "Enviar WhatsApp";
      renderCalendar();
    }
  });

  syncRoomCheckboxState();
}

// ── Tarjeta de cirugía ──────────────────────────────────────────────────────
function buildInternacionesColumn(iso, items) {
  const col = document.createElement("div");
  col.className = "room-column internaciones-column";
  col.innerHTML = `
    <div class="room-column-head">
      <h4>Servicios de Internaciones Varias</h4>
      <div class="room-column-tools">
        <button type="button" class="secondary add-internacion-btn">+ Agregar paciente</button>
      </div>
    </div>
  `;

  col.querySelector(".add-internacion-btn").addEventListener("click", () => openInternacionModal({ fecha: iso }));

  if (internacionesLoading.has(iso)) {
    const loading = document.createElement("div");
    loading.className = "empty-room";
    loading.textContent = "Cargando internaciones...";
    col.appendChild(loading);
    return col;
  }

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-room";
    empty.textContent = "Sin pacientes cargados.";
    col.appendChild(empty);
    return col;
  }

  [...items]
    .sort((a, b) => (a.horario || "99:99").localeCompare(b.horario || "99:99") || (a.paciente_nombre || "").localeCompare(b.paciente_nombre || ""))
    .forEach(item => col.appendChild(buildInternacionCard(item)));
  return col;
}

function buildInternacionCard(item) {
  const node = document.createElement("div");
  node.className = "case-card internacion-card";
  const isAuto = item.origen_registro !== "manual";
  node.innerHTML = `
    <div class="case-head">
      <strong class="case-patient">${escHtml(item.paciente_nombre || "Sin nombre")}${item.edad ? ` <span class="age-tag">${escHtml(item.edad)} años</span>` : ""}</strong>
      ${item.horario ? `<span class="case-time badge-time">${escHtml(item.horario)}</span>` : ""}
      ${isAuto ? '<span class="sys-status-badge internacion-auto">Internación previa automática</span>' : '<span class="app-match-badge in-app">Manual</span>'}
    </div>
    <div class="case-body">
      ${item.paciente_dni ? `<div><strong>DNI:</strong> ${escHtml(item.paciente_dni)}</div>` : ""}
      ${item.motivo ? `<div><strong>Motivo / prestación:</strong> ${escHtml(item.motivo)}</div>` : ""}
      ${item.motivo_automatico ? `<div><strong>Regla:</strong> ${escHtml(item.motivo_automatico)}</div>` : ""}
      ${item.fecha_cirugia_original ? `<div><strong>Cirugía original:</strong> ${escHtml(item.fecha_cirugia_original)} ${escHtml(item.horario_cirugia_original || "")}</div>` : ""}
      ${item.intervencion_original ? `<div><strong>Intervención:</strong> ${escHtml(item.intervencion_original)}</div>` : ""}
      ${item.cirujano_original ? `<div><strong>Cirujano:</strong> ${escHtml(item.cirujano_original)}</div>` : ""}
      ${item.obra_social ? `<div><strong>Obra social:</strong> ${escHtml(item.obra_social)}</div>` : ""}
      ${item.servicio_solicitante ? `<div><strong>Servicio:</strong> ${escHtml(item.servicio_solicitante)}</div>` : ""}
      ${item.medico_responsable ? `<div><strong>Médico:</strong> ${escHtml(item.medico_responsable)}</div>` : ""}
      ${item.telefono ? `<div><strong>Teléfono:</strong> ${escHtml(item.telefono)}</div>` : ""}
    </div>
    <div class="case-actions">
      <div class="status-grid">
        <label>Horario<input class="internacion-horario" type="time" value="${escAttr(item.horario || "")}"></label>
        <label>Cama asignada<input class="internacion-cama" type="text" value="${escAttr(item.cama_asignada || "")}"></label>
        <label>Destino<input class="internacion-destino" type="text" value="${escAttr(item.destino || "")}"></label>
      </div>
      <label class="obs-label">Observaciones internas</label>
      <textarea class="internacion-obs" rows="2">${escHtml(item.observaciones || "")}</textarea>
      <div class="internacion-actions">
        <button type="button" class="secondary internacion-save">Guardar</button>
        <button type="button" class="secondary internacion-edit">Editar</button>
        ${isAuto ? "" : '<button type="button" class="secondary internacion-delete">Eliminar</button>'}
      </div>
    </div>
  `;

  node.querySelector(".internacion-save").addEventListener("click", async () => {
    await saveInternacionInline(item, node);
  });
  node.querySelector(".internacion-edit").addEventListener("click", () => openInternacionModal(item));
  const deleteBtn = node.querySelector(".internacion-delete");
  if (deleteBtn) deleteBtn.addEventListener("click", async () => deleteInternacion(item));
  return node;
}

function buildCaseCard(s) {
  const tpl  = document.getElementById("caseTemplate");
  const node = tpl.content.firstElementChild.cloneNode(true);

  // ── Encabezado ─────────────────────────────────────────────────────────
  const patientEl = node.querySelector(".case-patient");
  patientEl.innerHTML = escHtml(s.patient) +
    (s.age ? ` <span class="age-tag">${escHtml(s.age)} años</span>` : "");

  node.querySelector(".case-time").textContent = s.time;
  const checkbox = node.querySelector(".case-select-checkbox");
  checkbox.dataset.surgeryKey = surgeryKey(s);

  const appBadge = document.createElement("span");
  if (s.in_app) {
    appBadge.className = "app-match-badge in-app";
    appBadge.textContent = "En app";
  } else if (s.in_app_date_mismatch) {
    appBadge.className = "app-match-badge app-date-mismatch";
    appBadge.title = "Figura en la app pero con fecha diferente (más de 10 días)";
    appBadge.textContent = "Otra fecha";
  } else {
    appBadge.className = "app-match-badge not-in-app";
    appBadge.textContent = "No en app";
  }
  node.querySelector(".case-head").appendChild(appBadge);

  // Badge de estado del sistema (del Excel) — solo si es relevante operativamente
  if (s.sys_status) {
    const cls = sysStatusClass(s.sys_status);
    if (cls !== "pendiente") {   // «Pendiente» del Excel no aporta info útil
      const badge = document.createElement("span");
      badge.className = "sys-status-badge sys-" + cls;
      badge.textContent = s.sys_status;
      node.querySelector(".case-head").appendChild(badge);
    }
  }

  // Badge de suspendido / reprogramado
  if (s.suspended && s.suspended.toLowerCase() === "si") {
    const b = document.createElement("span");
    b.className = "sys-status-badge sys-cancelado";
    b.textContent = "SUSPENDIDO";
    node.querySelector(".case-head").appendChild(b);
  }
  if (s.rescheduled && s.rescheduled.toLowerCase() === "si") {
    const b = document.createElement("span");
    b.className = "sys-status-badge sys-reprog";
    b.textContent = s.prev_date ? `Reprog. de ${formatDateShort(s.prev_date)}` : "Reprogramado";
    node.querySelector(".case-head").appendChild(b);
  }

  // ── Datos clínicos ─────────────────────────────────────────────────────
  const rows = [
    ["DNI",              s.dni],
    ["Intervención",     s.procedure],
    ["I. Complementaria",s.procedure2],
    ["Cirujano",         s.surgeon],
    ["2° Cirujano",      s.surgeon2],
    ["Anestesia",        s.anesthesia],
    ["Obra Social",      s.coverage],
    ["Especialidad",     s.specialty],
    ["Origen",           s.origin],
    ["Destino",          s.svc_destination || s.destination],
    ["App",              s.in_app ? (s.app_service || "Figura en panel") : s.in_app_date_mismatch ? "Figura en panel (fecha distinta)" : "No figura en panel"],
    ["Teléfono WPP",     s.whatsapp_phone || s.phone || s.app_phone],
    ["Emergencia",       s.emergency && s.emergency.toUpperCase() !== "NO" ? s.emergency : ""],
    ["Motivo demora",    s.delay_reason],
  ];

  const bodyHtml = rows
    .filter(([, v]) => v && v.toUpperCase() !== "NONE")
    .map(([k, v]) => `<div><strong>${k}:</strong> ${escHtml(v)}</div>`)
    .join("");

  // Requerimientos (Farmacia, Anat. Pat., Hemoterapia, RX, Ortopedia)
  const reqs = [
    ["Farmacia",    s.pharmacy,    s.obs_pharmacy],
    ["Anat. Pat.",  s.anat_pat,    ""],
    ["Hemoterapia", s.hemotherapy, ""],
    ["RX",          s.rx,          ""],
    ["Ortopedia",   s.orthopedics, s.obs_ortho],
  ].filter(([, v]) => v && v.toUpperCase() !== "NO" && v.toUpperCase() !== "NONE" && v !== "");

  const reqsHtml = reqs.length
    ? '<div class="reqs-grid">' +
      reqs.map(([label, val, obs]) =>
        `<span class="req-tag">${label}: <strong>${escHtml(val)}</strong>`
        + (obs ? ` — ${escHtml(obs)}` : "")
        + `</span>`
      ).join("") +
      "</div>"
    : "";

  node.querySelector(".case-body").innerHTML = bodyHtml + reqsHtml;

  // ── Estados guardados ───────────────────────────────────────────────────
  const key   = surgeryKey(s);
  const saved = statusStore[key] || {
    obs: "",
    stay_days: "",
    cama_asignada: "",
    destino_editable: s.svc_destination || s.destination || "",
    material: "Pendiente",
    auth: "Pendiente",
    budget: "Pendiente",
    order: "Pendiente",
  };
  const normalizeInternalStatus = v => (v === "No OK" ? "No autorizado" : (v || "Pendiente"));
  const normalizeOrderStatus = v => (["OK", "No OK", "Pendiente"].includes(v) ? v : "Pendiente");
  const normalizedStatus = {
    ...saved,
    cama_asignada: saved.cama_asignada || "",
    stay_days: saved.stay_days || "",
    destino_editable: saved.destino_editable || s.svc_destination || s.destination || "",
    material: normalizeInternalStatus(saved.material),
    auth: normalizeInternalStatus(saved.auth),
    budget: normalizeInternalStatus(saved.budget),
    order: normalizeOrderStatus(saved.order),
  };
  const editableFields = document.createElement("div");
  editableFields.className = "status-grid surgery-edit-grid";
  editableFields.innerHTML = `
    <label>Cama asignada
      <input class="status-cama" type="text" value="${escAttr(normalizedStatus.cama_asignada || "")}" placeholder="302A, UTI 4, A definir">
    </label>
    <label>Destino
      <input class="status-destino" type="text" value="${escAttr(normalizedStatus.destino_editable || "")}" placeholder="Sala General Adultos, UTI, UCO...">
    </label>
    <label>Días estimados de estadía
      <input class="status-stay-days" type="number" min="0" max="365" step="1" value="${escAttr(normalizedStatus.stay_days || "")}" placeholder="Ej: 2">
    </label>
  `;
  node.querySelector(".case-actions").prepend(editableFields);
  node.querySelector(".obs-input").value       = normalizedStatus.obs || "";
  node.querySelector(".status-material").value = normalizedStatus.material;
  node.querySelector(".status-auth").value     = normalizedStatus.auth;
  node.querySelector(".status-budget").value   = normalizedStatus.budget;
  node.querySelector(".status-order").value    = normalizedStatus.order;

  applyStatusClasses(node, normalizedStatus);

  // ── Guardar ─────────────────────────────────────────────────────────────
  node.querySelector(".save-case").addEventListener("click", async () => {
    const btn = node.querySelector(".save-case");
    const previousStatus = statusStore[key] || null;
    const newStatus = {
      obs:      node.querySelector(".obs-input").value,
      cama_asignada: node.querySelector(".status-cama").value,
      stay_days: node.querySelector(".status-stay-days").value,
      destino_editable: node.querySelector(".status-destino").value,
      material: node.querySelector(".status-material").value,
      auth:     node.querySelector(".status-auth").value,
      budget:   node.querySelector(".status-budget").value,
      order:    node.querySelector(".status-order").value,
    };

    statusStore[key] = newStatus;
    btn.disabled = true;
    btn.classList.add("is-loading");
    btn.textContent = "Guardando…";

    try {
      const result = await apiPost("status", { key, status: newStatus });
      if (result && result.versions) {
        serverVersions = result.versions;
      } else {
        await refreshServerVersions();
      }
    } catch {
      if (previousStatus) {
        statusStore[key] = previousStatus;
        applyStatusClasses(node, previousStatus);
      } else {
        delete statusStore[key];
        applyStatusClasses(node, normalizedStatus);
      }
      flashSaveButton(btn, "Error al guardar");
      btn.disabled = false;
      btn.classList.remove("is-loading");
      return;
    }

    applyStatusClasses(node, newStatus);
    flashSaveButton(btn, "✔ Guardado");
    btn.disabled = false;
    btn.classList.remove("is-loading");
  });

  return node;
}

// Aplica color al encabezado según el peor estado interno gestionado.
// Verde = material/auth/budget en OK o No requiere, y orden medica en OK.
// Amarillo = algún Pendiente. Rojo = algún No autorizado o No OK.
function applyStatusClasses(node, status) {
  const head = node.querySelector(".case-head");
  head.classList.remove("status-ok", "status-pending", "status-nok");
  const vals = [status.material, status.auth, status.budget, status.order || "Pendiente"];
  const isPositive = v => v === "OK" || v === "No requiere";
  if (vals.includes("No autorizado") || vals.includes("No OK")) {
    head.classList.add("status-nok");
  } else if (vals.every(isPositive)) {
    head.classList.add("status-ok");
  } else {
    head.classList.add("status-pending");
  }
}

// Clase CSS para badge de estado del sistema CEMIC
function sysStatusClass(status) {
  const s = (status || "").toLowerCase();
  if (s.includes("cancelado"))                 return "cancelado";
  if (s.includes("suspendido"))                return "cancelado";
  if (s.includes("cirug"))                     return "encirugia";
  if (s.includes("ingreso"))                   return "ingreso";
  if (s.includes("pendiente"))                 return "pendiente";
  return "pendiente";
}

// Formatea fecha previa (puede venir como '10-04-2026 14:00' o ISO)
function formatDateShort(raw) {
  const m = String(raw).match(/(\d{2}[-\/]\d{2}[-\/]\d{4})/);
  if (m) return m[1];
  const m2 = String(raw).match(/(\d{4}-\d{2}-\d{2})/);
  if (m2) {
    const [y, mo, d] = m2[1].split("-");
    return `${d}/${mo}/${y}`;
  }
  return raw;
}

// Escapa HTML para evitar XSS con datos del reporte
function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escAttr(str) {
  return escHtml(str).replace(/'/g, "&#39;");
}

// Convierte ISO YYYY-MM-DD → DD/MM/YYYY
function fmtIso(iso) {
  if (!iso) return "—";
  const m = String(iso).match(/(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[3]}/${m[2]}/${m[1]}`;
  return iso;
}

// ── Render completo ─────────────────────────────────────────────────────────
function internacionPayloadFromForm() {
  return {
    fecha: el("internacionFecha").value,
    horario: el("internacionHorario").value,
    paciente_nombre: el("internacionNombre").value,
    paciente_dni: el("internacionDni").value,
    edad: el("internacionEdad").value,
    obra_social: el("internacionObraSocial").value,
    motivo: el("internacionMotivo").value,
    servicio_solicitante: el("internacionServicio").value,
    medico_responsable: el("internacionMedico").value,
    cama_asignada: el("internacionCama").value,
    destino: el("internacionDestino").value,
    telefono: el("internacionTelefono").value,
    observaciones: el("internacionObs").value,
  };
}

function fillInternacionForm(item = {}) {
  el("internacionId").value = item.id || "";
  el("internacionFecha").value = item.fecha || selectedDate.toISOString().slice(0, 10);
  el("internacionHorario").value = item.horario || "";
  el("internacionNombre").value = item.paciente_nombre || "";
  el("internacionDni").value = item.paciente_dni || "";
  el("internacionEdad").value = item.edad || "";
  el("internacionObraSocial").value = item.obra_social || "";
  el("internacionMotivo").value = item.motivo || "";
  el("internacionServicio").value = item.servicio_solicitante || "";
  el("internacionMedico").value = item.medico_responsable || "";
  el("internacionCama").value = item.cama_asignada || "";
  el("internacionDestino").value = item.destino || "";
  el("internacionTelefono").value = item.telefono || "";
  el("internacionObs").value = item.observaciones || "";
  el("internacionModalTitle").textContent = item.id ? "Editar paciente" : "Agregar paciente";
}

function openInternacionModal(item = {}) {
  if (!canUseInternacionesVarias()) return;
  fillInternacionForm(item);
  el("internacionModal").hidden = false;
  el("internacionNombre").focus();
}

function closeInternacionModal() {
  const modal = el("internacionModal");
  if (modal) modal.hidden = true;
}

async function saveInternacionInline(item, node) {
  const payload = {
    horario: node.querySelector(".internacion-horario").value,
    cama_asignada: node.querySelector(".internacion-cama").value,
    destino: node.querySelector(".internacion-destino").value,
    observaciones: node.querySelector(".internacion-obs").value,
  };
  const btn = node.querySelector(".internacion-save");
  btn.disabled = true;
  btn.textContent = "Guardando...";
  try {
    await apiPut(`internaciones-varias/${item.id}`, payload);
    invalidateInternaciones(item.fecha);
    await ensureInternacionesForDate(item.fecha);
  } catch (error) {
    alert(`No se pudo guardar: ${error.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Guardar";
  }
}

async function deleteInternacion(item) {
  if (!window.confirm(`Eliminar a ${item.paciente_nombre} de Internaciones Varias?`)) return;
  try {
    await apiDelete(`internaciones-varias/${item.id}`);
    invalidateInternaciones(item.fecha);
    await ensureInternacionesForDate(item.fecha);
  } catch (error) {
    alert(`No se pudo eliminar: ${error.message}`);
  }
}

function renderAll() {
  renderCalendar();
  renderSchedule();
  renderMovementHistory();
}

// ── Eventos ─────────────────────────────────────────────────────────────────
el("refreshBtn").addEventListener("click", refreshReport);

const printDayBtn = el("printDayBtn");
if (printDayBtn) {
  printDayBtn.addEventListener("click", () => {
    const iso = selectedDate.toISOString().slice(0, 10);
    window.open(`saavedra/imprimir?fecha=${encodeURIComponent(iso)}`, "_blank", "noopener");
  });
}

const internacionCloseBtn = el("internacionCloseBtn");
if (internacionCloseBtn) internacionCloseBtn.addEventListener("click", closeInternacionModal);

const internacionModal = el("internacionModal");
if (internacionModal) {
  internacionModal.addEventListener("click", event => {
    if (event.target === internacionModal) closeInternacionModal();
  });
}

const internacionForm = el("internacionForm");
if (internacionForm) {
  internacionForm.addEventListener("submit", async event => {
    event.preventDefault();
    const payload = internacionPayloadFromForm();
    const id = el("internacionId").value;
    const submitBtn = internacionForm.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = "Guardando...";
    try {
      if (id) {
        await apiPut(`internaciones-varias/${id}`, payload);
      } else {
        await apiPost("internaciones-varias", payload);
      }
      closeInternacionModal();
      invalidateInternaciones(payload.fecha);
      await ensureInternacionesForDate(payload.fecha);
    } catch (error) {
      alert(`No se pudo guardar: ${error.message}`);
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Guardar";
    }
  });
}

// ── Carga manual de reporte ─────────────────────────────────────────────────
el("uploadInput").addEventListener("change", async function () {
  const file = this.files[0];
  if (!file) return;

  const label = el("uploadLabel");
  label.textContent = "⏳ Procesando…";
  label.classList.add("uploading");
  renderCalendarLoading("Procesando archivo cargado…");

  el("movementStatus").textContent = `Procesando ${file.name} y actualizando historial…`;

  try {
    const fd = new FormData();
    fd.append("file", file);

    const r = await fetch(apiUrl("upload"), {
      method: "POST",
      headers: { "X-CSRFToken": getCsrfToken() },
      body: fd,
    });

    // Si el servidor devuelve HTML en vez de JSON significa que la ruta no existe.
    // Eso pasa cuando el servidor está corriendo con código viejo → hay que reiniciarlo.
    let result;
    try {
      result = await r.json();
    } catch (_) {
      throw new Error(
        `El servidor no reconoció la ruta de carga (HTTP ${r.status}). ` +
        `Cerrá la ventana negra del servidor y volvé a abrir ejecutar.bat.`
      );
    }

    if (!result.ok) {
      el("movementStatus").textContent = "Error: " + (result.error || "desconocido");
      renderMovementHistory();
      return;
    }

    const [data, status, movements] = await Promise.all([
      apiGet("surgeries"),
      apiGet("status"),
      apiGetOptional("movements", null),
    ]);
    surgeries   = data   || [];
    internacionesByDate = {};
    statusStore = status || {};
    movementHistory = movements || mergeMovementHistory(loadLocalMovementHistory(), result.movements || []);
    movementHistoryApiAvailable = movements !== null;
    saveLocalMovementHistory(movementHistory);
    await refreshServerVersions();

    applyMeta({ downloaded_at: result.downloaded_at, count: result.count, manual: true });

    const newItems = (result.movements || []).length;
    if (movements === null) {
      el("movementStatus").textContent = newItems
        ? `Se detectaron ${newItems} movimiento${newItems !== 1 ? "s" : ""}. Para guardarlos en historial, reiniciá el servidor.`
        : "No se detectaron movimientos nuevos en esta comparación.";
    } else {
      el("movementStatus").textContent = newItems
        ? `Se agregaron ${newItems} movimiento${newItems !== 1 ? "s" : ""} al historial.`
        : "No se detectaron movimientos nuevos en esta comparación.";
    }

    if (surgeries.length) {
      const today  = new Date().toISOString().slice(0, 10);
      const future = surgeries.map(s => s.date).sort().find(d => d >= today);
      if (future) {
        selectedDate = new Date(future + "T00:00:00");
        currentDate  = new Date(selectedDate);
      }
    }
    renderAll();
  } catch (e) {
    el("movementStatus").textContent = "Error al subir archivo: " + e.message;
    renderMovementHistory();
  } finally {
    label.textContent = "📂 Cargar reporte manualmente";
    label.classList.remove("uploading");
    this.value = "";   // reset input para poder volver a subir el mismo archivo
  }
});

el("prevMonth").addEventListener("click", () => {
  currentDate.setMonth(currentDate.getMonth() - 1);
  renderCalendar();
});
el("nextMonth").addEventListener("click", () => {
  currentDate.setMonth(currentDate.getMonth() + 1);
  renderCalendar();
});
el("todayBtn").addEventListener("click", () => {
  currentDate  = new Date();
  selectedDate = new Date();
  renderAll();
});
el("filterSearch").addEventListener("input",  renderSchedule);
el("filterRoom").addEventListener("change",   renderSchedule);
el("filterPending").addEventListener("click", () => {
  el("filterPending").classList.toggle("active");
  renderSchedule();
});
el("movementTypeFilter").addEventListener("change", renderMovementHistory);
el("movementChangeFilter").addEventListener("change", renderMovementHistory);
el("movementDetectedDate").addEventListener("change", renderMovementHistory);
el("movementSearch").addEventListener("input", renderMovementHistory);

// ── Arranque ────────────────────────────────────────────────────────────────
init();
