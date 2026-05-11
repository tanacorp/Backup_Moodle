import logging
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView

from .models import BackupLog
from .tasks import backup_periodo
from .validators import validar_periodo
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


class DashboardView(LoginRequiredMixin, TemplateView):
    """Vista principal — métricas y acceso rápido."""
    template_name = 'backups/dashboard.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        qs = BackupLog.objects.all()
        ctx['total']      = qs.count()
        ctx['exitosos']   = qs.filter(estado=BackupLog.Estado.COMPLETADO).count()
        ctx['fallidos']   = qs.filter(estado=BackupLog.Estado.FALLIDO).count()
        ctx['en_proceso'] = qs.filter(estado=BackupLog.Estado.EN_PROCESO).count()

        # Últimos 10 backups
        ctx['recientes'] = qs.select_related()[:10]

        # Periodos únicos registrados
        ctx['periodos'] = (
            qs.values_list('periodo', flat=True)
            .distinct()
            .order_by('-periodo')[:10]
        )
        return ctx


class BackupListView(LoginRequiredMixin, ListView):
    """Lista paginada de todos los BackupLog con filtros."""
    model               = BackupLog
    template_name       = 'backups/backup_list.html'
    context_object_name = 'backups'
    paginate_by         = 50

    def get_queryset(self):
        qs = BackupLog.objects.all()

        periodo = self.request.GET.get('periodo', '').strip()
        estado  = self.request.GET.get('estado', '').strip()
        q       = self.request.GET.get('q', '').strip()

        if periodo:
            qs = qs.filter(periodo=periodo)
        if estado:
            qs = qs.filter(estado=estado)
        if q:
            qs = qs.filter(fullname__icontains=q) | qs.filter(shortname__icontains=q)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['estados']  = BackupLog.Estado.choices
        ctx['periodos'] = (
            BackupLog.objects.values_list('periodo', flat=True)
            .distinct().order_by('-periodo')
        )
        ctx['filtros'] = {
            'periodo': self.request.GET.get('periodo', ''),
            'estado' : self.request.GET.get('estado', ''),
            'q'      : self.request.GET.get('q', ''),
        }
        # Total de registros con filtros aplicados
        ctx['total_filtrado'] = self.get_queryset().count()
        return ctx


class BackupDetailView(LoginRequiredMixin, DetailView):
    """Detalle de un BackupLog individual."""
    model               = BackupLog
    template_name       = 'backups/backup_detail.html'
    context_object_name = 'backup'


class IniciarBackupView(LoginRequiredMixin, View):
    """
    POST → dispara la tarea Celery para un periodo.
    GET  → muestra el formulario de selección.
    """
    template_name = 'backups/iniciar_backup.html'

    def get(self, request):
        from django.shortcuts import render
        periodos = (
            BackupLog.objects.values_list('periodo', flat=True)
            .distinct().order_by('-periodo')
        )
        return render(request, self.template_name, {'periodos': periodos})

    def post(self, request):
        periodo = request.POST.get('periodo', '').strip()

        # Validar antes de enviar a Celery
        try:
            validar_periodo(periodo)
        except ValidationError as e:
            messages.error(request, f"Periodo inválido: {e.message}")
            return redirect('backups:iniciar')

        # Verificar que no haya un backup en proceso para ese periodo
        en_proceso = BackupLog.objects.filter(
            periodo=periodo,
            estado=BackupLog.Estado.EN_PROCESO,
        ).exists()

        if en_proceso:
            messages.warning(
                request,
                f"Ya existe un backup en proceso para el periodo {periodo}."
            )
            return redirect('backups:lista')

        # Disparar tarea Celery
        tarea = backup_periodo.delay(periodo)
        logger.info(f"Backup disparado — periodo={periodo} task_id={tarea.id}")

        messages.success(
            request,
            f"Backup del periodo {periodo} iniciado. "
            f"ID de tarea: {tarea.id}"
        )
        return redirect('backups:lista')


class BackupEstadoView(LoginRequiredMixin, View):
    """
    JSON endpoint para polling del estado de un backup.
    Usado por el frontend para actualizar el estado sin recargar.
    """
    def get(self, request, pk):
        from django.http import JsonResponse
        try:
            log = BackupLog.objects.get(pk=pk)
        except BackupLog.DoesNotExist:
            return JsonResponse({'error': 'No encontrado'}, status=404)

        return JsonResponse({
            'id'      : log.pk,
            'estado'  : log.estado,
            'tamano'  : log.tamano_mb,
            'checksum': log.checksum_sha256,
            'fecha_fin': log.fecha_fin.isoformat() if log.fecha_fin else None,
            'error'   : log.error_detalle,
        })


class DescargarBackupView(LoginRequiredMixin, View):
    """Descarga directa del archivo .mbz desde Nodo B."""
    def get(self, request, pk):
        try:
            log = BackupLog.objects.get(pk=pk)
        except BackupLog.DoesNotExist:
            raise Http404

        if not log.archivo_disponible:
            messages.error(request, "El archivo no está disponible para descarga.")
            return redirect('backups:detalle', pk=pk)

        try:
            response = FileResponse(
                open(log.ruta_local, 'rb'),
                content_type='application/octet-stream',
            )
            response['Content-Disposition'] = (
                f'attachment; filename="{log.nombre_archivo}"'
            )
            return response
        except FileNotFoundError:
            messages.error(request, "Archivo no encontrado en disco.")
            return redirect('backups:detalle', pk=pk)

class CancelarBackupView(LoginRequiredMixin, View):
    def post(self, request):
        from config.celery import app as celery_app

        # Revocar todas las tareas activas
        inspect = celery_app.control.inspect()
        activas = inspect.active() or {}

        revocadas = 0
        for worker, tareas in activas.items():
            for tarea in tareas:
                celery_app.control.revoke(
                    tarea['id'],
                    terminate=True,
                    signal='SIGKILL'
                )
                revocadas += 1

        # Limpiar cola pendiente
        celery_app.control.purge()

        # Marcar EN_PROCESO como FALLIDO en BD
        BackupLog.objects.filter(
            estado=BackupLog.Estado.EN_PROCESO
        ).update(
            estado=BackupLog.Estado.FALLIDO,
            error_detalle='Cancelado manualmente.'
        )

        messages.warning(
            request,
            f"Backup cancelado. {revocadas} tarea(s) revocadas."
        )
        return redirect('backups:lista')