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

    # Export
    path('export/excel/', views.export_excel, name='export_excel'),
    path('export/pdf/', views.export_pdf, name='export_pdf'),

    # Acciones en lote
    path('bulk/change-status/', views.bulk_change_status, name='bulk_change_status'),
    path('bulk/assign-user/', views.bulk_assign_user, name='bulk_assign_user'),
    path('bulk/export-selected/', views.export_selected, name='export_selected'),

    # Gestión de usuarios (solo superadmin)
    path('usuarios/', views.user_management, name='user_management'),
    path('usuarios/actualizar/', views.update_user_permissions, name='update_user_permissions'),

    # 🔥 Seguimiento del paciente
    path('seguimiento/', views.tracking_view, name='tracking'),

    # Conciliación

]
