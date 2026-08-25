"""Consultas reutilizables de la app core.

Este módulo debe concentrar queries compartidas por dashboard, listados,
reportes, quirófano y estadísticas.

Regla de uso:
- si una función solo consulta datos, vive acá;
- si modifica datos o dispara acciones, vive en ``services.py``.

El archivo arranca deliberadamente sin lógica para evitar cambios funcionales
en la primera etapa de reestructuración.
"""

