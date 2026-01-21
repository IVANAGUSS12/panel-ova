/* global Chart, flatpickr */
(function () {
  // --------- helpers ----------
  const STATUS_LABELS = {
    AUTORIZADO: "Autorizado",
    SOLICITADO: "Solicitado",
    MATERIAL_PENDIENTE: "Material pendiente",
    PENDIENTE: "Pendiente",
    RECHAZO: "Rechazo",
    REPROGRAMADO: "Reprogramado",
  };

  function prettyStatus(code) {
    return STATUS_LABELS[code] || code || "Sin estado";
  }

  function parseDDMMYYYY(s) {
    // expected dd/mm/yyyy
    if (!s) return null;
    const [dd, mm, yyyy] = s.split("/").map(x => x.trim());
    if (!dd || !mm || !yyyy) return null;
    return `${yyyy}-${mm.padStart(2, "0")}-${dd.padStart(2, "0")}`;
  }

  function formatISOToDDMMYYYY(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.split("-");
    return `${d}/${m}/${y}`;
  }

  function qs(params) {
    const u = new URLSearchParams(params);
    return u.toString();
  }

  function topN(arr, n) {
    return [...arr].sort((a, b) => (b.count || 0) - (a.count || 0)).slice(0, n);
  }

  // --------- charts state ----------
  const charts = {
    status: null,
    service: null,
    coverage: null,
    periods: null,
    serviceStacked: null,
    serviceDetail: null,
  };

  function destroyChart(c) {
    if (c && typeof c.destroy === "function") c.destroy();
  }

  function setKpis(payload) {
    const period = `${formatISOToDDMMYYYY(payload.range?.start)} → ${formatISOToDDMMYYYY(payload.range?.end)}`;
    document.getElementById("kpiPeriod").textContent = period;

    document.getElementById("kpiTotal").textContent = payload.overall?.total ?? 0;

    const byStatus = payload.overall?.by_status || [];
    const map = {};
    byStatus.forEach(x => { map[x.status] = x.count; });

    document.getElementById("kpiAutorizadas").textContent = map["AUTORIZADO"] ?? 0;
    document.getElementById("kpiPendientes").textContent = map["PENDIENTE"] ?? 0;
  }

  function renderStatusChart(payload) {
    const ctx = document.getElementById("chartStatus");
    if (!ctx) return;

    destroyChart(charts.status);

    const rows = payload.overall?.by_status || [];
    const labels = rows.map(r => prettyStatus(r.status));
    const data = rows.map(r => r.count);

    charts.status = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{ label: "Cantidad", data }] },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } }
      }
    });
  }

  function renderServiceChart(payload) {
    const ctx = document.getElementById("chartService");
    if (!ctx) return;

    destroyChart(charts.service);

    const rows = topN(payload.by_service || [], 12);
    const labels = rows.map(x => x.service || "Sin servicio");
    const data = rows.map(x => x.count);

    charts.service = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{ label: "Cantidad", data }] },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } }
      }
    });
  }

  function renderCoverageChart(payload) {
    const ctx = document.getElementById("chartCoverage");
    if (!ctx) return;

    destroyChart(charts.coverage);

    const rows = topN(payload.by_coverage || [], 12);
    const labels = rows.map(x => x.coverage || "Sin cobertura");
    const data = rows.map(x => x.count);

    charts.coverage = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{ label: "Cantidad", data }] },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } }
      }
    });
  }

  function renderPeriodsChart(payload) {
    const ctx = document.getElementById("chartPeriods");
    if (!ctx) return;

    destroyChart(charts.periods);

    const periods = payload.periods || [];
    if (!periods.length) {
      charts.periods = new Chart(ctx, {
        type: "bar",
        data: { labels: ["Rango seleccionado"], datasets: [{ label: "Total", data: [payload.overall?.total ?? 0] }] },
        options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
      });
      return;
    }

    const labels = periods.map(p => p.label);
    const data = periods.map(p => p.total);

    charts.periods = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{ label: "Total", data }] },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } }
      }
    });
  }

  function buildStatusSet(payload) {
    const set = new Set();
    (payload.overall?.by_status || []).forEach(r => set.add(r.status));
    const preferred = ["AUTORIZADO", "SOLICITADO", "MATERIAL_PENDIENTE", "PENDIENTE", "RECHAZO", "REPROGRAMADO"];
    const final = [];
    preferred.forEach(s => { if (set.has(s)) final.push(s); });
    [...set].forEach(s => { if (!final.includes(s)) final.push(s); });
    return final;
  }

  function renderServiceStacked(payload) {
    const ctx = document.getElementById("chartServiceStacked");
    if (!ctx) return;

    destroyChart(charts.serviceStacked);

    const statuses = buildStatusSet(payload);
    const rows = payload.by_service_status || [];
    const top = [...rows].sort((a, b) => (b.total || 0) - (a.total || 0)).slice(0, 10);
    const labels = top.map(r => r.service || "Sin servicio");

    const datasets = statuses.map(st => ({
      label: prettyStatus(st),
      data: top.map(r => (r.status_counts?.[st] ?? 0)),
      stack: "stack1",
    }));

    charts.serviceStacked = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets },
      options: {
        responsive: true,
        plugins: { legend: { position: "bottom" } },
        scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } }
      }
    });
  }

  function populateServiceDetailSelect(payload) {
    const sel = document.getElementById("service_detail");
    if (!sel) return;

    const rows = payload.by_service_status || [];
    const services = [...rows]
      .filter(r => (r.service || "").trim() !== "" && (r.service || "") !== "Sin servicio")
      .sort((a, b) => (a.service || "").localeCompare(b.service || ""))
      .map(r => r.service);

    sel.innerHTML = '<option value="">Seleccioná un servicio...</option>';
    services.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      sel.appendChild(opt);
    });
  }

  function renderServiceDetail(payload, serviceName) {
    const ctx = document.getElementById("chartServiceDetail");
    if (!ctx) return;

    destroyChart(charts.serviceDetail);

    if (!serviceName) {
      charts.serviceDetail = new Chart(ctx, {
        type: "bar",
        data: { labels: ["Elegí un servicio"], datasets: [{ label: "Cantidad", data: [0] }] },
        options: { responsive: true, plugins: { legend: { display: false } } }
      });
      return;
    }

    const row = (payload.by_service_status || []).find(r => (r.service || "") === serviceName);
    const statusCounts = row?.status_counts || {};
    const statuses = Object.keys(statusCounts);
    const labels = statuses.map(prettyStatus);
    const data = statuses.map(s => statusCounts[s]);

    charts.serviceDetail = new Chart(ctx, {
      type: "doughnut",
      data: { labels, datasets: [{ label: "Cantidad", data }] },
      options: { responsive: true }
    });
  }

  async function fetchStats(fromISO, toISO) {
    const url = "/estadisticas/data/?" + qs({ from: fromISO || "", to: toISO || "" });
    const r = await fetch(url);
    if (!r.ok) throw new Error("No pude cargar estadísticas");
    return r.json();
  }

  async function refresh() {
    const from = document.getElementById("stats_from").value;
    const to = document.getElementById("stats_to").value;
    const fromISO = parseDDMMYYYY(from);
    const toISO = parseDDMMYYYY(to);

    const payload = await fetchStats(fromISO, toISO);

    setKpis(payload);
    renderStatusChart(payload);
    renderPeriodsChart(payload);
    renderServiceChart(payload);
    renderCoverageChart(payload);
    renderServiceStacked(payload);

    populateServiceDetailSelect(payload);
    renderServiceDetail(payload, "");
  }

  document.addEventListener("DOMContentLoaded", function () {
    const today = new Date();
    const sevenAgo = new Date(today.getTime() - 6 * 24 * 60 * 60 * 1000);

    flatpickr("#stats_from", { dateFormat: "d/m/Y", defaultDate: sevenAgo });
    flatpickr("#stats_to", { dateFormat: "d/m/Y", defaultDate: today });

    document.getElementById("stats_apply")?.addEventListener("click", () => {
      refresh().catch(console.error);
    });

    document.getElementById("service_detail")?.addEventListener("change", (e) => {
      const selected = e.target.value;
      fetch("/estadisticas/data/?" + qs({
        from: parseDDMMYYYY(document.getElementById("stats_from").value),
        to: parseDDMMYYYY(document.getElementById("stats_to").value),
      }))
        .then(r => r.json())
        .then(payload => renderServiceDetail(payload, selected))
        .catch(console.error);
    });

    refresh().catch(console.error);
  });
})();
