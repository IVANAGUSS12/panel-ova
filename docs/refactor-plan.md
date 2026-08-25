# Plan de reestructuración por etapas

## Etapa 1 — Base segura

Objetivo: dejar una guía clara y preparar archivos de arquitectura sin cambiar comportamiento.

Estado:

- [x] Crear `docs/`.
- [x] Crear documentación inicial de arquitectura.
- [x] Crear plan de refactor.
- [x] Crear puntos de entrada `services.py` y `selectors.py`.
- [ ] Auditar duplicación de templates/includes.
- [ ] Auditar CSS duplicado.
- [ ] Auditar JS inline por pantalla.

## Etapa 2 — Backend incremental

Extraer desde `core/views.py` sin cambiar URLs:

1. Selectores de dashboard y listado de pacientes.
2. Servicios de WhatsApp/mensajería.
3. Servicios de email de admisión.
4. Servicios de exportación.
5. Selectores y servicios de quirófano.

Cada extracción debe terminar con:

```bash
python manage.py check
```

## Etapa 3 — Templates e includes

Objetivo: reducir HTML duplicado.

Primeros candidatos:

- Mensajes flash.
- Paginación.
- Modales de confirmación.
- Encabezados de página.
- Barras de filtros.

## Etapa 4 — CSS

Objetivo: pasar de “capas de override” a archivos por responsabilidad.

Orden recomendado:

1. Variables y base.
2. Layout.
3. Componentes.
4. Formularios.
5. Tablas.
6. Módulos específicos.

Mientras la app esté viva, mantener `app-refresh.css` como capa final hasta completar la transición.

## Etapa 5 — JavaScript

Objetivo: sacar JS inline de templates grandes.

Primeros candidatos:

- Acciones masivas de `patient_list.html`.
- Visor de archivos de `patient_detail.html`.
- Gestión de quirófano.
- Agenda portable.

## Etapa 6 — Documentación operativa

- Completar setup local.
- Completar Docker.
- Documentar variables `.env.example`.
- Documentar comandos habituales.
- Documentar reglas de mantenimiento.
