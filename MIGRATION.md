# Migracion a Servidor Nuevo

Esta guia asume Ubuntu Server 22.04/24.04 o Docker en Linux. En Windows Server los pasos son equivalentes usando PowerShell y el servicio que se prefiera.

## 1. Preparar Servidor

```bash
sudo apt update
sudo apt install -y git python3.12 python3.12-venv python3-pip postgresql postgresql-contrib nginx
```

Si se usara Docker:

```bash
docker --version
docker compose version
```

## 2. Copiar Codigo

```bash
git clone <repo-url> /opt/panelova
cd /opt/panelova
```

No copiar entornos virtuales, `staticfiles/`, caches, logs ni backups viejos como codigo.

## 3. Configurar Variables

```bash
cp .env.example .env
nano .env
```

Completar como minimo:

```env
SECRET_KEY=<clave-segura>
DEBUG=False
ALLOWED_HOSTS=panel.ejemplo.com,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://panel.ejemplo.com
PANEL_PUBLIC_BASE_URL=https://panel.ejemplo.com
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31536000
POSTGRES_DB=panel_ova
POSTGRES_USER=panel_ova_user
POSTGRES_PASSWORD=<clave-postgres>
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
```

Agregar credenciales CEMIC, SMTP y WhatsApp solo si esas integraciones se usaran.

## 4. Base de Datos PostgreSQL

```bash
sudo -u postgres psql
```

```sql
CREATE DATABASE panel_ova;
CREATE USER panel_ova_user WITH PASSWORD '<clave-postgres>';
ALTER ROLE panel_ova_user SET client_encoding TO 'utf8';
ALTER ROLE panel_ova_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE panel_ova_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE panel_ova TO panel_ova_user;
\q
```

En `.env`, usar `DATABASE_URL` o las variables `POSTGRES_*`.

## 5. Instalar Dependencias

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

## 6. Restaurar Datos

### Desde SQLite actual

En el servidor anterior:

```bash
python manage.py dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission > backup-panelova.json
```

Copiar `backup-panelova.json` al servidor nuevo y ejecutar:

```bash
python manage.py migrate
python manage.py loaddata backup-panelova.json
```

### Desde PostgreSQL actual

En origen:

```bash
pg_dump "$DATABASE_URL" > panel_ova.dump
```

En destino:

```bash
psql "$DATABASE_URL" < panel_ova.dump
python manage.py migrate
```

## 7. Restaurar Archivos Persistentes

Copiar desde el servidor anterior:

```bash
rsync -av media/ usuario@nuevo-servidor:/opt/panelova/media/
rsync -av core/agenda_saavedra_data/ usuario@nuevo-servidor:/opt/panelova/core/agenda_saavedra_data/
rsync -av core/agenda_las_heras_data/ usuario@nuevo-servidor:/opt/panelova/core/agenda_las_heras_data/
rsync -av core/agenda_pombo_data/ usuario@nuevo-servidor:/opt/panelova/core/agenda_pombo_data/
```

Copiar `.automation/whatsapp_profile/` solo si se mantiene WhatsApp Web con sesion persistente.

## 8. Inicializar App

```bash
python manage.py check
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py test core.tests
```

Smoke test local:

```bash
gunicorn panel_ova.wsgi:application --bind 127.0.0.1:5000
```

Abrir `http://127.0.0.1:5000/accounts/login/`.

## 9. Servicio systemd

Crear `/etc/systemd/system/panelova.service`:

```ini
[Unit]
Description=Panel OVA Django
After=network.target postgresql.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/panelova
EnvironmentFile=/opt/panelova/.env
ExecStart=/opt/panelova/.venv/bin/gunicorn panel_ova.wsgi:application --bind 127.0.0.1:5000 --workers 3 --timeout 120
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo chown -R www-data:www-data /opt/panelova/media /opt/panelova/core/agenda_*_data
sudo systemctl daemon-reload
sudo systemctl enable --now panelova
sudo systemctl status panelova
```

## 10. Agenda Updater

Opcion recomendada: proceso separado.

Crear `/etc/systemd/system/panelova-agenda.service`:

```ini
[Unit]
Description=Panel OVA Agenda Updater
After=network.target panelova.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/panelova
EnvironmentFile=/opt/panelova/.env
Environment=AUTO_REFRESH_AGENDAS=0
ExecStart=/opt/panelova/.venv/bin/python manage.py refresh_agendas --loop --interval-minutes 20
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now panelova-agenda
```

## 11. Nginx

Crear `/etc/nginx/sites-available/panelova`:

```nginx
server {
    listen 80;
    server_name panel.ejemplo.com;

    client_max_body_size 25M;

    location /static/ {
        alias /opt/panelova/staticfiles/;
    }

    location /media/ {
        alias /opt/panelova/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/panelova /etc/nginx/sites-enabled/panelova
sudo nginx -t
sudo systemctl reload nginx
```

Configurar SSL:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d panel.ejemplo.com
```

## 12. Backups

Base PostgreSQL:

```bash
pg_dump "$DATABASE_URL" > backups/panelova-$(date +%F).sql
```

Archivos:

```bash
tar -czf backups/panelova-media-agendas-$(date +%F).tar.gz media core/agenda_*_data
```

## 13. Verificacion Final

```bash
python manage.py check --deploy
python manage.py migrate --check
python manage.py test
curl -I http://127.0.0.1:5000/accounts/login/
sudo systemctl status panelova panelova-agenda
```

Revisar logs:

```bash
journalctl -u panelova -f
journalctl -u panelova-agenda -f
```
