Analiza este repositorio con foco en APIs, endpoints JSON y flujos cliente-servidor.

Quiero:
1. Inventario rápido de endpoints y contratos
2. Riesgos de autenticación y autorización
3. Riesgos de validación, errores 500 y exposición de datos
4. Problemas de consistencia entre frontend y backend
5. Fallos de mantenibilidad en serializers, views o helpers
6. Lista priorizada de correcciones

Busca especialmente:
- endpoints sin protección
- métodos HTTP mal restringidos
- errores no manejados
- respuestas con datos sensibles
- problemas de CSRF, CORS o session handling
- validaciones faltantes en parámetros y payloads
- inconsistencias entre rutas, templates y JavaScript

Entrega en formato:
- Resumen ejecutivo
- Tabla de hallazgos con severidad
- Evidencia por endpoint y archivo
- Fix recomendado