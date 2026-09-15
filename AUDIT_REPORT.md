# Auditoria Tecnica Panel OVA

Fecha: 2026-09-15

## Resumen

Estado inicial: aplicacion Django funcional a nivel de carga, con logica principal concentrada en `core.views`, SQLite local con datos, dependencias incompletas para PostgreSQL/DATABASE_URL, documentacion parcial, datos generados dentro del arbol del proyecto y algunos defaults riesgosos para produccion.

Estado final: se corrigio un bug de persistencia de estado, se endurecio configuracion de produccion, se completo el set de dependencias reproducibles, se agrego `.dockerignore`, se mejoro `.env.example`, se crearon tests focalizados y se documento instalacion/migracion desde cero.

## Mapa de la Aplicacion

- Entrada HTTP: `panel_ova.urls` incluye admin, `accounts.urls` y `core.urls`.
- Inicializacion: `manage.py` carga `panel_ova.settings`; `core.apps.CoreConfig.ready()` registra signals y puede iniciar scheduler de agendas.
- Frontend: Django templates en `core/templates` y `accounts/templates`; JS/CSS propios en `core/static`.
- Backend: vistas server-rendered y endpoints JSON en `core.views` y `core.saavedra_portable_views`.
- Base de datos: modelos Django `Patient`, `Attachment`, `AuditLog`, `PatientHistory`, `SavedFilter`, `QuirofanoSnapshot`, `QuirofanoEntry`, `InternacionVarias`.
- Servicios externos: CEMIC quirofanos via Playwright, WhatsApp Cloud API, WhatsApp Web por Playwright, SMTP.
- Procesos en segundo plano: scheduler embebido opcional y comando `refresh_agendas --loop`.
- Persistencia adicional: `media/` y `core/agenda_*_data/` con JSON de agenda/status/movements.

## Clasificacion

- FUNCIONAL Y NECESARIA: modelos, migraciones, login/logout, dashboard/listados, seguimiento publico, carga QR/admision, agenda portable, exportaciones, comandos de importacion/agenda/thumbnail.
- FUNCIONAL PERO MEJORABLE: `core.views` monolitico, JS inline en templates grandes, scheduler embebido en proceso web.
- DUPLICADA: mensajes WhatsApp similares en Python y templates; configuracion de descarga CEMIC en `report_downloader.py` y `saavedra_portable_backend.py`.
- OBSOLETA / NO UTILIZADA: no se elimino codigo funcional sin prueba concluyente. `core.services` y `core.selectors` son placeholders de refactor incremental.
- INCOMPLETA: documentacion de migracion y variables estaba incompleta antes de esta auditoria.
- ROTA: despliegue limpio con PostgreSQL podia fallar por falta de driver; `DATABASE_URL` podia fallar por dependencia no declarada; cambio masivo de estado no persistia `status_since`.
- RIESGOSA: `SECRET_KEY` con fallback inseguro, `SERVE_MEDIA` activo por defecto, datos/logs/caches presentes en el arbol.
- REQUIERE REVISION MANUAL: decision final sobre migrar SQLite a PostgreSQL, conservar historial JSON de agendas, y reemplazar WhatsApp Web por Cloud API donde sea posible.

## Problemas Encontrados

- `requirements.txt` no declaraba `dj-database-url`, aunque `settings.py` lo importa si existe `DATABASE_URL`.
- Docker Compose incluye PostgreSQL, pero faltaba driver PostgreSQL en dependencias.
- `playwright`, `whitenoise` y `django-compressor` no estaban fijados de forma reproducible.
- `SECRET_KEY` tenia fallback `insecure-key`, peligroso si faltaba variable en produccion.
- `SERVE_MEDIA` se activaba por defecto aun con `DEBUG=False`.
- `MEDIA_ROOT`, `STATIC_ROOT` y log de consultas lentas no podian moverse limpiamente con variables de entorno.
- `bulk_change_status` no incluia `status_since` en `update_fields`.
- Hay entornos virtuales, DB SQLite, backups y logs en el workspace; estan ignorados, pero deben tratarse como datos locales, no como codigo migrable.
- `core.views` es demasiado grande y mezcla responsabilidades, lo que aumenta riesgo de cambios.
- `0019_remove_reconciliation_tables` eliminaba tablas por SQL, pero no actualizaba el estado de migraciones de Django.

## Cambios Realizados

- `bulk_change_status` ahora guarda `status_since` y `solicitado_since` junto con `status`.
- `settings.py` exige `SECRET_KEY` cuando `DEBUG=False`.
- `settings.py` permite configurar `STATIC_ROOT`, `MEDIA_ROOT`, `MEDIA_URL` y `SLOW_QUERY_LOG_PATH`.
- `settings.py` permite configurar redireccion SSL y HSTS por entorno.
- `panel_ova.urls` dejo de servir media por defecto en produccion.
- `requirements.txt` ahora incluye dependencias runtime necesarias y versiones fijadas.
- `docker-compose.yml` ya no hardcodea password PostgreSQL y pasa `DATABASE_URL` al web/updater.
- Se agrego `.dockerignore` para builds livianos y sin datos locales.
- Se agrego `requirements-dev.txt`.
- Se agregaron tests para contrato de status y normalizacion de telefono.
- Se reemplazo `README.md` y se creo `MIGRATION.md`.
- Se agrego una migracion state-only para cerrar la eliminacion legacy de reconciliacion sin tocar datos.
- Se evito que comandos administrativos como `showmigrations` puedan arrancar el scheduler de agendas.

## Archivos Eliminados

No se eliminaron archivos de negocio ni datos. Por seguridad, no se borraron bases, backups ni media. Los artefactos generados estan excluidos por `.gitignore` y `.dockerignore`.

## Dependencias Eliminadas

No se eliminaron dependencias runtime porque todas las declaradas tienen uso directo o de configuracion:

- `openpyxl` / `xlrd`: importacion y reportes Excel.
- `reportlab` / `matplotlib` / `pillow`: PDF, graficos y thumbnails.
- `playwright`: CEMIC y WhatsApp Web.
- `whitenoise` / `django-compressor`: estaticos.

## Dependencias Agregadas/Actualizadas

- `dj-database-url==3.1.0`: requerido por `DATABASE_URL`.
- `psycopg2-binary==2.9.11`: requerido para PostgreSQL.
- `playwright==1.58.0`, `whitenoise==6.11.0`, `django-compressor==4.6.0`: fijadas para reproducibilidad.

## Funcionalidades Eliminadas

Ninguna. No hubo evidencia suficiente para retirar flujos sin decision manual.

## Funcionalidades Reparadas

- Cambio masivo de estado: ahora persiste la fecha de entrada al estado actual (`status_since`) y mantiene `solicitado_since`.
- Instalacion nueva con `DATABASE_URL`/PostgreSQL: dependencias declaradas correctamente.
- Produccion sin media accidental: `SERVE_MEDIA` ahora es opt-in fuera de `DEBUG`.

## Optimizaciones

- `.dockerignore` reduce contexto Docker, evitando enviar DB, media, entornos virtuales, logs y caches.
- Configuracion movible por entorno evita rutas rigidas.
- Dependencias quedaron mas reproducibles y explicitas.

## Migracion

Seguir `MIGRATION.md`. Puntos criticos:

- Migrar base de datos con `dumpdata/loaddata` o `pg_dump`.
- Copiar `media/`.
- Copiar `core/agenda_*_data/` si contiene status/movimientos manuales.
- Crear `.env` nuevo sin copiar secretos a Git.
- Ejecutar `migrate`, `collectstatic`, checks y smoke tests.

## Riesgos Pendientes

- `core.views` sigue siendo monolitico; conviene extraer gradualmente servicios y selectors con tests.
- No hay cobertura integral de endpoints/templates.
- Las integraciones CEMIC/WhatsApp dependen de red, credenciales y navegador; requieren prueba manual en entorno real.
- SQLite actual contiene datos locales y backups; no se modificaron ni borraron.
- `manage.py check --deploy` puede seguir marcando advertencias segun cookies/HSTS/SSL definitivos del servidor.

## Revision Manual

- Confirmar si produccion final sera PostgreSQL o SQLite.
- Definir dominio definitivo y valores de `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`.
- Decidir si el scheduler debe vivir solo como servicio separado.
- Confirmar retencion de logs y backups.
- Confirmar si WhatsApp Web debe mantenerse o migrarse por completo a WhatsApp Cloud API.

## Validacion Ejecutada

- `python manage.py check`: OK.
- `python manage.py test`: OK, 3 tests.
- `python manage.py makemigrations --check --dry-run`: OK, sin cambios pendientes.
- `python manage.py migrate --check`: OK.
- `python manage.py collectstatic --noinput --clear`: OK, 133 archivos copiados y 399 post-procesados.
- Instalacion limpia en venv temporal desde `requirements.txt`: OK.
- Migraciones desde cero contra SQLite temporal: OK.
- Tests desde venv limpio: OK.
- `python manage.py check --deploy` con variables fuertes de produccion simuladas: OK.
- `python -m playwright --version` en venv limpio: OK, 1.58.0.
