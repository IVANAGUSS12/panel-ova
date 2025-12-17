document.addEventListener('DOMContentLoaded', function () {
  fetch('/estadisticas/data/')
    .then(r => r.json())
    .then(data => {
      const s = data.by_service || [];
      const c = data.by_coverage || [];

      const ctxService = document.getElementById('chartService');
      if (ctxService) {
        new Chart(ctxService, {
          type: 'bar',
          data: {
            labels: s.map(x => x.service || 'Sin servicio'),
            datasets: [{
              label: 'Cantidad',
              data: s.map(x => x.count),
            }]
          }
        });
      }

      const ctxCoverage = document.getElementById('chartCoverage');
      if (ctxCoverage) {
        new Chart(ctxCoverage, {
          type: 'bar',
          data: {
            labels: c.map(x => x.coverage || 'Sin cobertura'),
            datasets: [{
              label: 'Cantidad',
              data: c.map(x => x.count),
            }]
          }
        });
      }
    });
});
