Cuando revises una app, aplica este checklist:

- Revisar variables de entorno y secretos
- Buscar formularios que permitan cambiar estados sensibles
- Revisar vistas públicas con datos sensibles
- Confirmar manejo de errores y nulls
- Revisar rate limiting
- Revisar autorización por rol
- Revisar carga y descarga de archivos
- Revisar envío de mails y credenciales SMTP
- Revisar logs con datos sensibles
- Revisar endpoints QR o accesos temporales

Forma de trabajo:
1. Identifica primero superficies expuestas: login, formularios, uploads, downloads, APIs, tareas automáticas y admin.
2. Recorre el flujo de autenticación y autorización extremo a extremo.
3. Valida evidencias en código antes de reportar.
4. Prioriza impacto real sobre pacientes, datos sensibles, credenciales y operaciones.

Formato esperado de salida:
- Resumen ejecutivo
- Hallazgos por severidad
- Evidencia por archivo o función
- Riesgo
- Fix recomendado