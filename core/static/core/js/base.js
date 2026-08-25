/* ================================
   PANEL OVA — base.js
   Funcionalidades globales de UI
   ================================ */

(function () {
  'use strict';

  /* -----------------------------------------------
     CARGA GLOBAL — navegación, filtros y formularios
  ----------------------------------------------- */
  const loadingState = {
    visible: false,
    timer: null,
    startedAt: 0,
    root: null,
    messageNode: null,
  };

  function ensureLoadingUi() {
    if (loadingState.root) return;

    const bar = document.createElement('div');
    bar.className = 'app-loading-bar';
    bar.setAttribute('aria-hidden', 'true');

    const toast = document.createElement('div');
    toast.className = 'app-loading-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.innerHTML =
      '<span class="app-loading-spinner" aria-hidden="true"></span>' +
      '<span class="app-loading-message">Cargando…</span>';

    document.body.appendChild(bar);
    document.body.appendChild(toast);

    loadingState.root = toast;
    loadingState.messageNode = toast.querySelector('.app-loading-message');
  }

  function showGlobalLoading(message, delay) {
    ensureLoadingUi();
    window.clearTimeout(loadingState.timer);
    loadingState.startedAt = Date.now();
    if (loadingState.messageNode) {
      loadingState.messageNode.textContent = message || 'Procesando solicitud…';
    }
    loadingState.timer = window.setTimeout(function () {
      document.documentElement.classList.add('app-is-loading');
      loadingState.visible = true;
    }, typeof delay === 'number' ? delay : 120);
  }

  function hideGlobalLoading() {
    window.clearTimeout(loadingState.timer);
    const elapsed = Date.now() - loadingState.startedAt;
    const wait = loadingState.visible ? Math.max(0, 240 - elapsed) : 0;
    window.setTimeout(function () {
      document.documentElement.classList.remove('app-is-loading');
      loadingState.visible = false;
    }, wait);
  }

  window.CemicLoading = {
    show: showGlobalLoading,
    hide: hideGlobalLoading,
    setMessage: function (message) {
      ensureLoadingUi();
      if (loadingState.messageNode) loadingState.messageNode.textContent = message;
    }
  };

  /* -----------------------------------------------
     MENÚ HAMBURGUESA (mobile)
  ----------------------------------------------- */
  const toggleBtn = document.querySelector('.topbar-menu-toggle');
  const mobileNav = document.getElementById('mobileNav');

  if (toggleBtn && mobileNav) {
    toggleBtn.addEventListener('click', function () {
      const isOpen = mobileNav.classList.toggle('is-open');
      toggleBtn.setAttribute('aria-expanded', String(isOpen));
      mobileNav.setAttribute('aria-hidden', String(!isOpen));
    });

    // Cerrar al hacer click fuera del menú
    document.addEventListener('click', function (e) {
      if (
        mobileNav.classList.contains('is-open') &&
        !mobileNav.contains(e.target) &&
        !toggleBtn.contains(e.target)
      ) {
        mobileNav.classList.remove('is-open');
        toggleBtn.setAttribute('aria-expanded', 'false');
        mobileNav.setAttribute('aria-hidden', 'true');
      }
    });

    // Cerrar con Escape
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && mobileNav.classList.contains('is-open')) {
        mobileNav.classList.remove('is-open');
        toggleBtn.setAttribute('aria-expanded', 'false');
        mobileNav.setAttribute('aria-hidden', 'true');
        toggleBtn.focus();
      }
    });
  }

  /* -----------------------------------------------
     SKIP LINK — accesibilidad teclado
  ----------------------------------------------- */
  const skipLink = document.getElementById('skip-to-content');
  if (skipLink) {
    skipLink.addEventListener('click', function (e) {
      e.preventDefault();
      const target = document.getElementById('main-content');
      if (target) {
        target.setAttribute('tabindex', '-1');
        target.focus();
      }
    });
  }

  /* -----------------------------------------------
     DISMISS automático de mensajes flash
  ----------------------------------------------- */
  const alerts = document.querySelectorAll('.alert-message');
  alerts.forEach(function (alert) {
    // Agrega botón de cierre a cada alerta
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'alert-close';
    closeBtn.setAttribute('aria-label', 'Cerrar mensaje');
    closeBtn.innerHTML = '&times;';
    closeBtn.addEventListener('click', function () {
      alert.style.opacity = '0';
      alert.style.transform = 'translateY(-4px)';
      setTimeout(function () { alert.remove(); }, 250);
    });
    alert.appendChild(closeBtn);

    // Auto-dismiss después de 6 segundos para mensajes informativos
    if (!alert.classList.contains('alert-message-error')) {
      setTimeout(function () {
        if (alert.parentNode) {
          alert.style.opacity = '0';
          alert.style.transform = 'translateY(-4px)';
          setTimeout(function () { if (alert.parentNode) alert.remove(); }, 250);
        }
      }, 6000);
    }
  });

  /* -----------------------------------------------
     TOGGLE DE FILTROS (patient list)
  ----------------------------------------------- */
  const toggleFiltersBtn = document.getElementById('toggleFilters');
  const filtersPanel = document.getElementById('filtersPanel');

  if (toggleFiltersBtn && filtersPanel) {
    // Si hay parámetros de filtro activos en la URL, abrir el panel por defecto
    const params = new URLSearchParams(window.location.search);
    const filterKeys = ['q', 'status', 'coverage', 'doctor', 'service', 'assigned_to',
                        'date_from', 'date_to', 'created_from', 'created_to', 'order_by'];
    const hasActiveFilters = filterKeys.some(function (k) { return params.get(k); });

    if (hasActiveFilters) {
      filtersPanel.removeAttribute('hidden');
      toggleFiltersBtn.setAttribute('aria-expanded', 'true');
    }

    toggleFiltersBtn.addEventListener('click', function () {
      const isHidden = filtersPanel.hasAttribute('hidden');
      if (isHidden) {
        filtersPanel.removeAttribute('hidden');
        toggleFiltersBtn.setAttribute('aria-expanded', 'true');
      } else {
        filtersPanel.setAttribute('hidden', '');
        toggleFiltersBtn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  document.addEventListener('submit', function (e) {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.dataset.noLoading === 'true') return;
    if (form.target && form.target !== '_self') return;

    const submitter = e.submitter;
    const method = (form.getAttribute('method') || 'get').toLowerCase();
    const message =
      (submitter && submitter.dataset && submitter.dataset.loadingMessage) ||
      form.dataset.loadingMessage ||
      (method === 'get' ? 'Aplicando filtros…' : 'Guardando cambios…');

    showGlobalLoading(message, 80);
    form.classList.add('is-submitting');

    if (submitter && !submitter.disabled) {
      submitter.classList.add('is-loading');
      window.setTimeout(function () { submitter.disabled = true; }, 0);
    }
  }, true);

  document.addEventListener('click', function (e) {
    const link = e.target.closest && e.target.closest('a[href]');
    if (!link) return;
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (link.target && link.target !== '_self') return;
    if (link.hasAttribute('download')) return;
    if (link.dataset.noLoading === 'true') return;

    const href = link.getAttribute('href') || '';
    if (!href || href.startsWith('#') || href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:')) return;

    try {
      const targetUrl = new URL(href, window.location.href);
      if (targetUrl.origin !== window.location.origin) return;
      showGlobalLoading(link.dataset.loadingMessage || 'Cargando pantalla…', 140);
    } catch (_) {
      // Si no se puede interpretar la URL, no bloqueamos la navegación.
    }
  }, true);

})();
