// core/static/core/js/calendar.js

document.addEventListener('DOMContentLoaded', function () {
  const calendarEl = document.getElementById('calendar');
  if (!calendarEl) {
    console.error('No se encontró el div #calendar');
    return;
  }

  try {
    const calendar = new FullCalendar.Calendar(calendarEl, {
      initialView: 'dayGridMonth',
      // si el locale da error, mejor no lo usamos por ahora
      // locale: 'es',
      height: 'auto',
      editable: true,

      events: {
        url: '/calendario/eventos/',
        method: 'GET',
        failure: function (error) {
          console.error('Error cargando eventos', error);
          alert('No se pudieron cargar los eventos del calendario.');
        },
      },

      eventDrop: function (info) {
        const newDate = info.event.startStr;

        fetch(`/calendario/mover/${info.event.id}/`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-CSRFToken': getCookie('csrftoken'),
          },
          body: `date=${encodeURIComponent(newDate)}`,
        })
          .then((r) => {
            if (!r.ok) throw new Error('Error en la reprogramación');
          })
          .catch((err) => {
            console.error(err);
            alert('Error reprogramando evento');
            info.revert();
          });
      },

      eventClick: function (info) {
        alert(info.event.title);
      },
    });

    calendar.render();
  } catch (e) {
    console.error('Error inicializando FullCalendar', e);
    alert('Error inicializando el calendario');
  }

  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
      const cookies = document.cookie.split(';');
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === name + '=') {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }
});
