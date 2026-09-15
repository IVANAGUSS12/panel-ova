# Panel CEMIC / Panel OVA

Aplicacion Django para gestion administrativa quirurgica: carga de solicitudes de pacientes, seguimiento publico por codigo OVA, autorizaciones, agenda de quirofanos por sede, reportes Excel/PDF, historial de cambios, emails operativos y mensajes WhatsApp.

## Arquitectura

- `panel_ova/`: configuracion Django, URLs raiz, WSGI/ASGI.
- `accounts/`: login/logout con autenticacion Django.
- `core/`: dominio principal: pacientes, adjuntos, agenda, quirofano, reportes, emails, WhatsApp, auditoria y comandos operativos.
- `core/static/` y `core/templates/`: frontend server-rendered con JavaScript propio.
- `core/agenda_*_data/`: datos runtime de agendas portables en JSON. No debe versionarse; debe migrarse como dato persistente si se usa en produccion.
- `media/`: adjuntos, PDFs y archivos generados/subidos. No debe versionarse; debe respaldarse.
- `staticfiles/`: salida de `collectstatic`. Se regenera.

La app usa Django templates, sesiones Django, ORM Django y por defecto SQLite si no se configura base externa. Para servidor nuevo se recomienda PostgreSQL.

## Requisitos

- Python 3.12 recomendado.
- PostgreSQL 15+ recomendado para produccion.
- Chromium de Playwright si se usan descarga automatica de agendas o envio WhatsApp Web.
- En Linux: paquetes de compilacion basicos y librerias de PostgreSQL si no se usa wheel binario.

## Instalacion Local

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
notepad .env
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py runserver 0.0.0.0:5000
```

Para Playwright:

```bash
python -m playwright install chromium
```

## Configuracion

Variables obligatorias en produccion:

- `SECRET_KEY`
- `DEBUG=False`
- `ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- Base de datos: `DATABASE_URL` o `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`

Variables obligatorias solo para integraciones:

- Agenda CEMIC: `CEMIC_USER`, `CEMIC_PASS`
- WhatsApp Cloud API: `WHATSAPP_CLOUD_PHONE_NUMBER_ID`, `WHATSAPP_CLOUD_ACCESS_TOKEN`
- Email SMTP: `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`

Variables utiles:

- `PANEL_PUBLIC_BASE_URL`: URL publica usada en links enviados a pacientes.
- `ANESTHESIA_INFO_URL` y `ANESTHESIA_PDF_PATH`: informacion/adjunto de anestesia.
- `AUTO_REFRESH_AGENDAS`: activa el scheduler embebido. En Docker se recomienda dejarlo en `0` para el proceso web y usar `agenda-updater`.
- `SERVE_MEDIA`: solo para desarrollo o despliegues controlados sin Nginx/Apache. En produccion debe quedar `0`.
- `SECURE_SSL_REDIRECT` y `SECURE_HSTS_SECONDS`: activar solo cuando HTTPS definitivo ya este funcionando.

## Desarrollo

```bash
python manage.py check
python manage.py test
python manage.py makemigrations --check --dry-run
python manage.py migrate --check
```

No editar manualmente `staticfiles/`, `.env`, bases SQLite, backups ni archivos de `media/`.

## Produccion Con Docker

Crear `.env` desde `.env.example`, definir `SECRET_KEY`, `POSTGRES_PASSWORD`, hosts y credenciales reales. Luego:

```bash
docker compose up --build -d db
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py collectstatic --noinput
docker compose up --build -d web agenda-updater
```

Tunel temporal de Cloudflare, si aplica:

```bash
docker compose --profile tunnel up -d cloudflared
docker compose logs -f cloudflared
```

## Datos Persistentes

Respaldar y restaurar:

- Base de datos.
- `media/`.
- `core/agenda_*_data/` si las agendas portables ya tienen estado manual relevante.
- `.automation/whatsapp_profile/` solo si se sigue usando WhatsApp Web con sesion de navegador.

## Troubleshooting

- Error `SECRET_KEY es obligatoria`: completar `SECRET_KEY` en `.env` o usar `DEBUG=True` solo en desarrollo.
- Error PostgreSQL sin driver: reinstalar con `pip install -r requirements.txt`.
- Agenda no descarga: verificar `CEMIC_USER`, `CEMIC_PASS`, conectividad y `python -m playwright install chromium`.
- Archivos media no se ven en produccion: servir `MEDIA_ROOT` con Nginx/Apache o activar `SERVE_MEDIA=1` solo de forma temporal/controlada.
- Estatica faltante: ejecutar `python manage.py collectstatic --noinput`.

Ver tambien [MIGRATION.md](MIGRATION.md) y [AUDIT_REPORT.md](AUDIT_REPORT.md).
