# Arquitectura de la aplicación

Este documento resume la estructura objetivo para ordenar la app Django sin cortar funcionalidad existente.

## Principios

- Mantener la app funcionando mientras se refactoriza.
- Evitar cambios masivos en una sola etapa.
- No tocar modelos, migraciones, base de datos ni `.env` sin confirmación.
- Mover lógica de forma incremental, con validación después de cada paso.
- Reutilizar componentes visuales y comportamientos comunes.

## Diagnóstico actual

La app concentra gran parte de la lógica en `core`:

- `core/views.py` tiene vistas, helpers, consultas, exportaciones, email, WhatsApp, estadísticas y quirófano.
- Algunos templates son grandes y contienen JavaScript inline.
- El CSS está dividido entre `styles.css`, `app-refresh.css` y estilos específicos de quirófano portable.
- La pantalla de agenda portable usa HTML/CSS/JS propios.
- `staticfiles/` existe como salida de `collectstatic`; no debe editarse a mano.

## Organización objetivo

```text
core/
  selectors.py      consultas reutilizables
  services.py       lógica de negocio y acciones
  views.py          coordinación request/response
  forms.py          validación de formularios
  templates/core/   presentación
  static/core/      CSS/JS fuente editable

accounts/
  services.py       lógica de usuarios/permisos si crece
  selectors.py      consultas de usuarios si crece
```

## Criterio para mover código

- Solo consulta datos: `selectors.py`.
- Modifica datos o ejecuta una acción: `services.py`.
- Valida input de usuario: `forms.py`.
- Renderiza pantalla o devuelve response: `views.py`.
- Reutilización visual: include/template o CSS central.

## No hacer todavía

- No partir `core` en muchas apps nuevas hasta estabilizar límites.
- No mover modelos sin una etapa de migración explícita.
- No cambiar URLs públicas si no es estrictamente necesario.
- No borrar archivos legacy hasta que no haya reemplazo probado.
