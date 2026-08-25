Cuando revises arquitectura, evaluá cómo está organizada la aplicación y dónde puede degradarse con el tiempo.

Checklist base:
- Identificar módulos principales y límites entre responsabilidades
- Detectar lógica duplicada entre views, helpers, services y templates
- Revisar acoplamiento entre frontend, backend y configuración
- Señalar dependencias frágiles o implícitas
- Detectar flujos críticos sin tests o sin contratos claros
- Buscar nombres ambiguos, funciones largas y módulos sobrecargados
- Revisar consistencia entre modelos, formularios, URLs y vistas
- Priorizar mejoras de bajo esfuerzo con alto impacto

Forma de trabajo:
1. Construye un mapa rápido de arquitectura.
2. Identifica flujos críticos del negocio.
3. Detecta zonas con acoplamiento, duplicación o complejidad accidental.
4. Propone fixes incrementales antes que reescrituras amplias.

Formato esperado de salida:
- Resumen ejecutivo
- Hallazgos por severidad
- Riesgos estructurales
- Archivos afectados
- Recomendaciones concretas