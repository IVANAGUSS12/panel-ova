from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    # QR público
    path('qr/carga/', views.qr_patient_create, name='qr_patient_create'),

    # Servicio / listado
    path('servicio/', views.patient_list, name='patient_list'),
    path('servicio/<int:pk>/', views.patient_detail, name='patient_detail'),

    # Calendario
    path('calendario/', views.calendar_view, name='calendar'),
    path('calendario/dia/', views.calendar_day_view, name='calendar_day'),
    path('calendario/eventos/', views.calendar_events, name='calendar_events'),
    path('calendario/mover/<int:pk>/', views.calendar_move_event, name='calendar_move_event'),

    # Estadísticas
    path('estadisticas/', views.stats_view, name='stats_view'),
    path('estadisticas/data/', views.stats_data, name='stats_data'),

    # Alias (por compatibilidad si en algún lado usás {% url 'core:stats' %})
    path('estadisticas/', views.stats_view, name='stats'),

    # Export
    path('export/excel/', views.export_excel, name='export_excel'),
    path('export/pdf/', views.export_pdf, name='export_pdf'),

    # 🔥 Seguimiento del paciente
    path('seguimiento/', views.tracking_view, name='tracking'),
]
