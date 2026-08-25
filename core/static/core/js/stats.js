/* global Chart, flatpickr */
(function () {
  // --------- helpers ----------
  const STATUS_LABELS = {
    AUTORIZADO: "Autorizado",
    PEND_COMERCIAL_PRESUPUESTO: "Pendiente comercial - presupuesto",
    PENDIENTE_PRESTADOR: "Pendiente prestador",
    PENDIENTE_MEDICO: "Pendiente medico",
    PENDIENTE_PACIENTE: "Pendiente paciente",
    AUTORIZADO_MATERIAL_PEND: "Autorizado - material pendiente",
    PENDIENTE: "Pendiente",
    RECHAZO_COBERTURA: "Rechazo cobertura",
    REPROGRAMADO: "Reprogramado",
    CANCELA_MEDICO: "Cancela medico",
    CANCELA_PTE: "Cancela pte",
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

  function updateExportLinks(from, to, service, coverage, doctor, dateField) {
    // Actualizar los enlaces de exportación con las fechas y filtros seleccionados
    const params = qs({ from, to, service: service || "", coverage: coverage || "", doctor: doctor || "", date_field: dateField || "planned_date" });
    const excelBtn = document.getElementById("exportExcelBtn");
    const pdfBtn = document.getElementById("exportPdfBtn");
    
    if (excelBtn) {
      excelBtn.href = "/export/excel/?" + params;
    }
    if (pdfBtn) {
      pdfBtn.href = "/export/pdf/?" + params;
    }
  }

  function topN(arr, n) {
    return [...arr].sort((a, b) => (b.count || 0) - (a.count || 0)).slice(0, n);
  }

  // --------- charts state ----------
  const charts = {
    dualComparison: null,
    status: null,
    service: null,
    doctor: null,
    coverage: null,
    periods: null,
    timeline: null,
    periodCreated: null,
    periodPlanned: null,
    periodAuthorized: null,
    authorizationRate: null,
    serviceStacked: null,
    serviceDetail: null,
  };
  let lastPayload = null;
  let filtersLoaded = false;

  function destroyChart(c) {
    if (c && typeof c.destroy === "function") c.destroy();
  }

  function setKpis(payload) {
    const dateField = payload.range?.date_field === "created_at" ? "pedido" : "cirugía";
    const period = `${formatISOToDDMMYYYY(payload.range?.start)} → ${formatISOToDDMMYYYY(payload.range?.end)}`;
    document.getElementById("kpiPeriod").textContent = `${period} (${dateField})`;

    document.getElementById("kpiTotal").textContent = payload.overall?.total ?? 0;
    document.getElementById("kpiTotalPedido").textContent = payload.dual_totals?.created_at ?? 0;
    document.getElementById("kpiTotalCirugia").textContent = payload.dual_totals?.planned_date ?? 0;
    document.getElementById("kpiAutorizadas").textContent = payload.authorization?.authorized ?? 0;
    document.getElementById("kpiNoAutorizadas").textContent = payload.authorization?.not_authorized ?? 0;
  }

  function renderDualComparisonChart(payload) {
    const ctx = document.getElementById("chartDualComparison");
    if (!ctx) return;

    destroyChart(charts.dualComparison);

    const totalPedido = payload.dual_totals?.created_at ?? 0;
    const totalCirugia = payload.dual_totals?.planned_date ?? 0;

    charts.dualComparison = new Chart(ctx, {
      type: "bar",
      data: {
        labels: ["Pedidos (fecha de creación)", "Realizados (fecha de cirugía)"],
        datasets: [{
          label: "Cantidad de pacientes",
          data: [totalPedido, totalCirugia],
          backgroundColor: [
            "rgba(54, 162, 235, 0.7)",  // Azul para pedidos
            "rgba(75, 192, 192, 0.7)"   // Verde para realizados
          ],
          borderColor: [
            "rgba(54, 162, 235, 1)",
            "rgba(75, 192, 192, 1)"
          ],
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function(context) {
                const label = context.label || '';
                const value = context.parsed.y || 0;
                return `${label}: ${value} paciente${value !== 1 ? 's' : ''}`;
              }
            }
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            ticks: {
              stepSize: 1
            }
          }
        }
      }
    });
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

  function renderDoctorChart(payload) {
    const ctx = document.getElementById("chartDoctor");
    if (!ctx) return;

    destroyChart(charts.doctor);

    const rows = topN(payload.by_doctor || [], 12);
    const title = document.getElementById("chartDoctorTitle");
    const selectedService = document.getElementById("filter_service")?.value || "";

    if (title) {
      title.textContent = selectedService
        ? `Por profesional de ${selectedService}`
        : "Por profesional";
    }

    if (!rows.length) {
      charts.doctor = new Chart(ctx, {
        type: "bar",
        data: { labels: ["Sin datos"], datasets: [{ label: "Cantidad", data: [0] }] },
        options: { responsive: true, plugins: { legend: { display: false } } }
      });
      return;
    }

    const labels = rows.map(x => x.doctor || "Sin profesional");
    const data = rows.map(x => x.count || 0);

    charts.doctor = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Cantidad",
          data,
          backgroundColor: "rgba(255, 159, 64, 0.7)",
          borderColor: "rgba(255, 159, 64, 1)",
          borderWidth: 1
        }]
      },
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

  function renderTimelineChart(payload) {
    const ctx = document.getElementById("chartTimeline");
    if (!ctx) return;

    destroyChart(charts.timeline);

    const dailyCreated = payload.daily_created || [];
    const dailyPlanned = payload.daily_planned || [];

    if (!dailyCreated.length && !dailyPlanned.length) {
      return;
    }

    const labels = dailyCreated.map(d => {
      const date = new Date(d.date);
      return date.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' });
    });

    charts.timeline = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Pedidos cargados",
            data: dailyCreated.map(d => d.count),
            borderColor: "rgba(54, 162, 235, 1)",
            backgroundColor: "rgba(54, 162, 235, 0.1)",
            borderWidth: 2,
            fill: true,
            tension: 0.1
          },
          {
            label: "Cirugías realizadas",
            data: dailyPlanned.map(d => d.count),
            borderColor: "rgba(75, 192, 192, 1)",
            backgroundColor: "rgba(75, 192, 192, 0.1)",
            borderWidth: 2,
            fill: true,
            tension: 0.1
          },
          {
            label: "Autorizadas",
            data: dailyPlanned.map(d => d.authorized || 0),
            borderColor: "rgba(40, 167, 69, 1)",
            backgroundColor: "rgba(40, 167, 69, 0.1)",
            borderWidth: 2,
            fill: true,
            tension: 0.1
          }
        ]
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: true, position: 'top' }
        },
        scales: {
          y: { beginAtZero: true, ticks: { stepSize: 1 } }
        }
      }
    });
  }

  function renderPeriodCreatedChart(payload) {
    const ctx = document.getElementById("chartPeriodCreated");
    if (!ctx) return;

    destroyChart(charts.periodCreated);

    const periods = payload.periods || [];
    if (!periods.length) return;

    const labels = periods.map(p => p.label);
    const data = periods.map(p => p.created_count || 0);

    charts.periodCreated = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Pedidos cargados",
          data,
          backgroundColor: "rgba(54, 162, 235, 0.7)",
          borderColor: "rgba(54, 162, 235, 1)",
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } }
      }
    });
  }

  function renderPeriodPlannedChart(payload) {
    const ctx = document.getElementById("chartPeriodPlanned");
    if (!ctx) return;

    destroyChart(charts.periodPlanned);

    const periods = payload.periods || [];
    if (!periods.length) return;

    const labels = periods.map(p => p.label);
    const data = periods.map(p => p.planned_count || 0);

    charts.periodPlanned = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Cirugías realizadas",
          data,
          backgroundColor: "rgba(75, 192, 192, 0.7)",
          borderColor: "rgba(75, 192, 192, 1)",
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } }
      }
    });
  }

  function renderPeriodAuthorizedChart(payload) {
    const ctx = document.getElementById("chartPeriodAuthorized");
    if (!ctx) return;

    destroyChart(charts.periodAuthorized);

    const periods = payload.periods || [];
    if (!periods.length) return;

    const labels = periods.map(p => p.label);
    const data = periods.map(p => p.authorized_count || 0);

    charts.periodAuthorized = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Autorizadas",
          data,
          backgroundColor: "rgba(40, 167, 69, 0.7)",
          borderColor: "rgba(40, 167, 69, 1)",
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } }
      }
    });
  }

  function renderAuthorizationRateChart(payload) {
    const ctx = document.getElementById("chartAuthorizationRate");
    if (!ctx) return;

    destroyChart(charts.authorizationRate);

    const periods = payload.periods || [];
    if (!periods.length) return;

    const labels = periods.map(p => p.label);
    const data = periods.map(p => {
      const total = p.planned_count || 0;
      const authorized = p.authorized_count || 0;
      return total > 0 ? ((authorized / total) * 100).toFixed(1) : 0;
    });

    charts.authorizationRate = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "% Autorización",
          data,
          borderColor: "rgba(153, 102, 255, 1)",
          backgroundColor: "rgba(153, 102, 255, 0.1)",
          borderWidth: 2,
          fill: true,
          tension: 0.1
        }]
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function(context) {
                return `Tasa de autorización: ${context.parsed.y}%`;
              }
            }
          }
        },
        scales: {
          y: { 
            beginAtZero: true, 
            max: 100,
            ticks: {
              callback: function(value) {
                return value + '%';
              }
            }
          }
        }
      }
    });
  }

  function buildStatusSet(payload) {
    const set = new Set();
    (payload.overall?.by_status || []).forEach(r => set.add(r.status));
    const preferred = ["AUTORIZADO", "AUTORIZADO_MATERIAL_PEND", "PEND_COMERCIAL_PRESUPUESTO", "PENDIENTE_PRESTADOR", "PENDIENTE_MEDICO", "PENDIENTE_PACIENTE", "PENDIENTE_ENVIO_PRESTADOR", "RECHAZO_COBERTURA", "REPROGRAMADO", "CANCELA_MEDICO", "CANCELA_PTE"];
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

  function populateMainFilters(payload) {
    const serviceSelect = document.getElementById("filter_service");
    const coverageSelect = document.getElementById("filter_coverage");
    const doctorSelect = document.getElementById("filter_doctor");
    if (!serviceSelect || !coverageSelect || !doctorSelect) return;

    if (!filtersLoaded) {
      const services = payload.all_services || [];
      services.forEach(service => {
        const option = document.createElement("option");
        option.value = service;
        option.textContent = service;
        serviceSelect.appendChild(option);
      });

      const coverages = payload.all_coverages || [];
      coverages.forEach(coverage => {
        const option = document.createElement("option");
        option.value = coverage;
        option.textContent = coverage;
        coverageSelect.appendChild(option);
      });

      serviceSelect.addEventListener("change", () => refresh().catch(console.error));
      coverageSelect.addEventListener("change", () => refresh().catch(console.error));
      doctorSelect.addEventListener("change", () => refresh().catch(console.error));
      filtersLoaded = true;
    }

    const selectedDoctor = doctorSelect.value;
    const doctors = payload.filtered_doctors || payload.all_doctors || [];
    doctorSelect.innerHTML = '<option value="">Todos los profesionales</option>';

    doctors.forEach(doctor => {
      const option = document.createElement("option");
      option.value = doctor;
      option.textContent = doctor;
      doctorSelect.appendChild(option);
    });

    if (selectedDoctor && doctors.includes(selectedDoctor)) {
      doctorSelect.value = selectedDoctor;
    }
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

  async function fetchStats(fromISO, toISO, service, coverage, doctor, dateField) {
    const params = {
      from: fromISO || "",
      to: toISO || "",
      service: service || "",
      coverage: coverage || "",
      doctor: doctor || "",
      date_field: dateField || "planned_date"
    };
    const url = "/estadisticas/data/?" + qs(params);
    const r = await fetch(url);
    if (!r.ok) throw new Error("No pude cargar estadísticas");
    return r.json();
  }

  async function refresh() {
    const from = document.getElementById("stats_from").value;
    const to = document.getElementById("stats_to").value;
    const service = document.getElementById("filter_service").value;
    const coverage = document.getElementById("filter_coverage").value;
    const doctor = document.getElementById("filter_doctor").value;
    const dateField = document.getElementById("date_field")?.value || "planned_date";
    
    const fromISO = parseDDMMYYYY(from);
    const toISO = parseDDMMYYYY(to);

    const payload = await fetchStats(fromISO, toISO, service, coverage, doctor, dateField);
    lastPayload = payload;
    populateMainFilters(payload);

    setKpis(payload);
    renderDualComparisonChart(payload);
    renderStatusChart(payload);
    renderPeriodsChart(payload);
    renderServiceChart(payload);
    renderDoctorChart(payload);
    renderCoverageChart(payload);
    
    // Nuevos gráficos de evolución temporal
    renderTimelineChart(payload);
    renderPeriodCreatedChart(payload);
    renderPeriodPlannedChart(payload);
    renderPeriodAuthorizedChart(payload);
    renderAuthorizationRateChart(payload);
    
    renderServiceStacked(payload);

    populateServiceDetailSelect(payload);
    renderServiceDetail(payload, "");
    
    // Actualizar enlaces de exportación con fechas y filtros
    updateExportLinks(fromISO, toISO, service, coverage, doctor, dateField);
  }

  document.addEventListener("DOMContentLoaded", async function () {
    const today = new Date();
    const sevenAgo = new Date(today.getTime() - 6 * 24 * 60 * 60 * 1000);

    flatpickr("#stats_from", { dateFormat: "d/m/Y", defaultDate: sevenAgo });
    flatpickr("#stats_to", { dateFormat: "d/m/Y", defaultDate: today });

    document.getElementById("stats_apply")?.addEventListener("click", () => {
      refresh().catch(console.error);
    });

    document.getElementById("date_field")?.addEventListener("change", () => {
      refresh().catch(console.error);
    });

    document.getElementById("service_detail")?.addEventListener("change", (e) => {
      const selected = e.target.value;
      renderServiceDetail(lastPayload || {}, selected);
    });

    refresh().catch(console.error);
  });
})();
