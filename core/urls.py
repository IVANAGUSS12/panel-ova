from django.urls import path
from . import views
from . import saavedra_portable_views

app_name = 'core'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    # QR público
    path('qr/carga/', views.qr_patient_create, name='qr_patient_create'),
    path('admision/carga/', views.admission_patient_create, name='admission_patient_create'),

    # Servicio / listado
    path('servicio/', views.patient_list, name='patient_list'),
    path('servicio/<int:pk>/', views.patient_detail, name='patient_detail'),

    # Calendario portable Saavedra
    path('calendario/', saavedra_portable_views.calendar_portable_view, name='calendar'),
    path('calendario/api/<str:agenda>/surgeries', saavedra_portable_views.api_surgeries, name='calendar_portable_api_surgeries'),
    path('calendario/api/<str:agenda>/meta', saavedra_portable_views.api_meta, name='calendar_portable_api_meta'),
    path('calendario/api/<str:agenda>/versions', saavedra_portable_views.api_versions, name='calendar_portable_api_versions'),
    path('calendario/api/<str:agenda>/movements', saavedra_portable_views.api_movements, name='calendar_portable_api_movements'),
    path('calendario/api/<str:agenda>/status', saavedra_portable_views.api_status, name='calendar_portable_api_status'),
    path('calendario/api/<str:agenda>/internaciones-varias', saavedra_portable_views.api_internaciones_varias, name='calendar_portable_api_internaciones_varias'),
    path('calendario/api/<str:agenda>/internaciones-varias/<int:item_id>', saavedra_portable_views.api_internacion_varias_detail, name='calendar_portable_api_internacion_varias_detail'),
    path('calendario/api/<str:agenda>/whatsapp-autosend', saavedra_portable_views.api_whatsapp_autosend, name='calendar_portable_api_whatsapp_autosend'),
    path('calendario/api/<str:agenda>/refresh', saavedra_portable_views.api_refresh, name='calendar_portable_api_refresh'),
    path('calendario/api/<str:agenda>/upload', saavedra_portable_views.api_upload, name='calendar_portable_api_upload'),
    path('calendario/saavedra/imprimir', saavedra_portable_views.print_programacion_saavedra, name='calendar_saavedra_print'),
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

    # Seguimiento público del paciente
    path('tracking/', views.tracking_view, name='tracking'),

    # Quirófano
    path('quirofano/', saavedra_portable_views.quirofano_redirect_view, name='quirofano_view'),
    path('quirofano/gestion/', views.quirofano_view, name='quirofano_management'),
    path('quirofano/entrada/<int:pk>/estado/', views.quirofano_entry_status_update, name='quirofano_entry_status_update'),
    path('quirofano/whatsapp/autosend/', views.quirofano_whatsapp_autosend, name='quirofano_whatsapp_autosend'),
]
