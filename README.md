# Panel CEMIC / Panel OVA

Sistema interno Django para gestión administrativa quirúrgica: solicitudes de pacientes, autorizaciones, agendas de quirófano, reportes, documentación y mensajería operativa.

## Estado del proyecto

La aplicación está en una etapa de ordenamiento progresivo. La regla principal es mejorar estructura y mantenibilidad sin romper flujos existentes ni bajar la app en uso.

## Estructura actual resumida

```text
accounts/        autenticación, login/logout y usuarios
core/            pacientes, autorizaciones, dashboard, reportes, quirófano y lógica operativa
panel_ova/       configuración Django
media/           archivos subidos/generados por la app
staticfiles/     salida de collectstatic, no editar manualmente
docs/            documentación técnica y decisiones de arquitectura
```

## Comandos útiles

```bash
python manage.py check
python manage.py migrate --check
python manage.py collectstatic --noinput
```

## Levantar con Cloudflare (sin cloudflared en PATH)

Si necesitás URL publica por Cloudflare y en Windows te falla porque `cloudflared` no está en PATH, podés usar el contenedor incluido en Docker Compose.

1. Levantar app + db + agenda updater:

```bash
docker compose up --build
```

2. Levantar también el túnel de Cloudflare (perfil opcional `tunnel`):

```bash
docker compose --profile tunnel up --build
```

3. Ver la URL pública generada por Cloudflare:

```bash
docker compose logs -f cloudflared
```

En los logs vas a ver una URL `https://...trycloudflare.com` para compartir acceso externo.

## Reglas de trabajo

- No editar `.env` real.
- No editar manualmente `staticfiles/`.
- No modificar modelos/base de datos sin plan y confirmación.
- Separar vistas, consultas y lógica de negocio de forma gradual.
- Mantener estética institucional CEMIC.
- Validar después de cada etapa.

Ver más en `docs/architecture.md` y `docs/refactor-plan.md`.
