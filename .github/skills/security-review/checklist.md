# Security Review Checklist

- Secretos, credenciales y variables de entorno expuestas
- Configuración insegura en settings, middleware y cookies
- Endpoints sin autenticación o con autorización insuficiente
- Formularios que actualizan estados sensibles sin validación fuerte
- Carga y descarga de archivos con validación incompleta
- Logs, mensajes de error o respuestas con PII o datos internos
- Integraciones de mail, terceros o tareas programadas con credenciales frágiles
- Controles faltantes de CSRF, sesiones, rate limiting o expiración
- Rutas públicas, QR o accesos temporales con alcance excesivo
- Manejo insuficiente de errores, nulls o excepciones operativas