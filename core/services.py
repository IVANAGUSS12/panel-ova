"""Servicios de negocio compartidos de la app core.

Este módulo es el punto de entrada para lógica que modifica estado o ejecuta
acciones operativas: mensajería, emails, exportaciones, procesamiento de
archivos, cambios masivos y reglas de autorización.

Regla de uso:
- las vistas coordinan request/response;
- los services ejecutan acciones de negocio;
- los selectors concentran consultas reutilizables.

El archivo arranca deliberadamente liviano para permitir una extracción
incremental desde ``core.views`` sin cambiar comportamiento.
"""

