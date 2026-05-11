from django.contrib import admin
from .models import BackupLog


@admin.register(BackupLog)
class BackupLogAdmin(admin.ModelAdmin):
    list_display  = ['periodo', 'shortname', 'estado', 'tamano_mb',
                     'fecha_inicio', 'fecha_fin']
    list_filter   = ['estado', 'periodo', 'anio']
    search_fields = ['shortname', 'fullname', 'periodo', 'programa']
    readonly_fields = [
        'celery_task_id', 'checksum_sha256', 'tamano_mb',
        'ruta_local', 'fecha_inicio', 'fecha_fin', 'error_detalle',
    ]
    ordering = ['-fecha_inicio']