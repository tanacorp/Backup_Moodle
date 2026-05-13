from django.urls import path
from . import views

app_name = 'backups'

urlpatterns = [
    path('',                        views.DashboardView.as_view(),      name='dashboard'),
    path('backups/',                views.BackupListView.as_view(),      name='lista'),
    path('backups/<int:pk>/',       views.BackupDetailView.as_view(),    name='detalle'),
    path('backups/<int:pk>/estado/',views.BackupEstadoView.as_view(),    name='estado'),
    path('backups/<int:pk>/descargar/', views.DescargarBackupView.as_view(), name='descargar'),
    path('confirmar/',                  views.ConfirmarRebackupView.as_view(), name='confirmar'),
    path('iniciar/',                views.IniciarBackupView.as_view(),   name='iniciar'),
    path('cancelar/',               views.CancelarBackupView.as_view(), name='cancelar'),
    path('buscar/',                 views.BuscarCursoView.as_view(),    name='buscar'),
]