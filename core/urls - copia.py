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
    path('estadisticas/', views.stats_view, name='stats'),
    path('estadisticas/data/', views.stats_data, name='stats_data'),

    # Export
    path('export/excel/', views.export_excel, name='export_excel'),
    path('export/pdf/', views.export_pdf, name='export_pdf'),
]
