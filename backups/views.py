import logging
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView

from .models import BackupLog
from django.core.exceptions import ValidationError

from .ssh_client import MoodleSSHClient, SSHClientError
from .validators import validar_periodo, validar_categoria_id, sanitizar_nombre_archivo
from .tasks import backup_periodo, backup_categoria

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
    template_name = 'backups/iniciar_backup.html'

    def get(self, request):
        from django.shortcuts import render

        # Cargar categorías desde Moodle via SSH
        categorias = []
        try:
            with MoodleSSHClient() as ssh:
                categorias = ssh.listar_categorias()
        except Exception as e:
            messages.warning(request, f"No se pudieron cargar las categorías: {e}")

        periodos = (
            BackupLog.objects.values_list('periodo', flat=True)
            .exclude(periodo='')
            .distinct().order_by('-periodo')[:10]
        )
        return render(request, self.template_name, {
            'periodos'  : periodos,
            'categorias': categorias,
        })

    def post(self, request):
        from django.urls import reverse
        tipo       = request.POST.get('tipo', 'periodo')
        confirmado = request.POST.get('confirmado', '0')

        if tipo == 'periodo':
            return self._iniciar_periodo(request, confirmado)
        else:
            return self._iniciar_categoria(request)

    def _iniciar_periodo(self, request, confirmado):
        from django.urls import reverse
        periodo = request.POST.get('periodo', '').strip()

        try:
            validar_periodo(periodo)
        except ValidationError as e:
            messages.error(request, f"Periodo inválido: {e.message}")
            return redirect('backups:iniciar')

        en_proceso = BackupLog.objects.filter(
            periodo=periodo,
            estado=BackupLog.Estado.EN_PROCESO,
        ).exists()
        if en_proceso:
            messages.warning(request, f"Ya hay un backup en proceso para {periodo}.")
            return redirect('backups:lista')

        tiene_previos = BackupLog.objects.filter(periodo=periodo).exists()
        if tiene_previos and confirmado != '1':
            url = reverse('backups:confirmar') + f'?periodo={periodo}&tipo=periodo'
            return redirect(url)

        eliminados, _ = BackupLog.objects.filter(periodo=periodo).delete()
        if eliminados:
            messages.info(request, f"Eliminados {eliminados} registros anteriores.")

        tarea = backup_periodo.delay(periodo)
        messages.success(request, f"Backup periodo {periodo} iniciado. ID: {tarea.id}")
        return redirect('backups:lista')

    def _iniciar_categoria(self, request):
        from django.urls import reverse
        from .tasks import backup_categoria

        cat_id               = request.POST.get('categoria_id', '').strip()
        cat_nombre           = request.POST.get('categoria_nombre', '').strip()
        cat_idnumber         = request.POST.get('categoria_idnumber', '').strip()
        incluir_subcategorias = request.POST.get('incluir_subcategorias') == '1'
        confirmado           = request.POST.get('confirmado', '0')

        try:
            validar_categoria_id(cat_id)
        except ValidationError as e:
            messages.error(request, f"Categoría inválida: {e.message}")
            return redirect('backups:iniciar')

        cat_id = int(cat_id)

        # Carpeta fallback: idnumber si existe, sino nombre sanitizado
        cat_carpeta = sanitizar_nombre_archivo(cat_idnumber or cat_nombre)

        en_proceso = BackupLog.objects.filter(
            categoria_id=cat_id,
            estado=BackupLog.Estado.EN_PROCESO,
        ).exists()
        if en_proceso:
            messages.warning(request, f"Ya hay un backup en proceso para la categoría {cat_nombre}.")
            return redirect('backups:lista')

        tiene_previos = BackupLog.objects.filter(categoria_id=cat_id).exists()
        if tiene_previos and confirmado != '1':
            url = (reverse('backups:confirmar') +
                   f'?categoria_id={cat_id}&categoria_nombre={cat_nombre}&tipo=categoria')
            return redirect(url)

        eliminados, _ = BackupLog.objects.filter(categoria_id=cat_id).delete()
        if eliminados:
            messages.info(request, f"Eliminados {eliminados} registros anteriores.")

        tarea = backup_categoria.delay(
            cat_id, cat_nombre, cat_carpeta, incluir_subcategorias
        )
        messages.success(
            request,
            f"Backup categoría '{cat_nombre}' iniciado. ID: {tarea.id}"
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


class BuscarCursoView(LoginRequiredMixin, View):
    template_name = 'backups/buscar_curso.html'

    def get(self, request):
        from django.shortcuts import render
        return render(request, self.template_name)

    def post(self, request):
        from django.http import JsonResponse
        from .tasks import backup_curso_individual

        action = request.GET.get('action', 'buscar')

        # ── Disparar backup de un curso ───────────
        if action == 'backup':
            curso_id  = request.POST.get('curso_id', '').strip()
            shortname = request.POST.get('shortname', '').strip()
            fullname  = request.POST.get('fullname', '').strip()

            if not curso_id.isdigit():
                return JsonResponse({'error': 'ID inválido'}, status=400)

            tarea = backup_curso_individual.delay(
                int(curso_id), shortname, fullname
            )
            return JsonResponse({'task_id': tarea.id, 'curso_id': curso_id})

        # ── Búsqueda ──────────────────────────────
        q = request.POST.get('q', '').strip()
        if len(q) < 2:
            return JsonResponse({'cursos': []})

        try:
            with MoodleSSHClient() as ssh:
                cmd = (
                    f"cd {ssh.moodle} && "
                    f"{ssh.moosh} course-list 2>&1"
                    f" | grep -i '{q}'"
                )
                out, err, code = ssh.ejecutar(cmd, timeout=30)

                cursos = []
                q_lower = q.lower()
                for linea in out.splitlines():
                    partes = linea.split('","')
                    if len(partes) < 4:
                        continue
                    cid = partes[0].strip().strip('"')
                    if not cid.isdigit():
                        continue
                    shortname = partes[2].strip().strip('"')
                    fullname  = partes[3].strip().strip('"')

                    if q_lower in shortname.lower() or q_lower in fullname.lower():
                        cursos.append({
                            'id'       : int(cid),
                            'shortname': shortname,
                            'fullname' : fullname,
                        })

            return JsonResponse({'cursos': cursos[:50]})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

class ConfirmarRebackupView(LoginRequiredMixin, View):
    def get(self, request):
        from django.shortcuts import render

        tipo         = request.GET.get('tipo', 'periodo')
        periodo      = request.GET.get('periodo', '')
        categoria_id = request.GET.get('categoria_id', '')
        cat_nombre   = request.GET.get('categoria_nombre', '')

        if tipo == 'periodo':
            existentes  = BackupLog.objects.filter(periodo=periodo)
            total       = existentes.count()
            completados = existentes.filter(estado=BackupLog.Estado.COMPLETADO).count()
            descripcion = f"Periodo {periodo}"
        else:
            existentes  = BackupLog.objects.filter(categoria_id=categoria_id)
            total       = existentes.count()
            completados = existentes.filter(estado=BackupLog.Estado.COMPLETADO).count()
            descripcion = f"Categoría {cat_nombre}"

        return render(request, 'backups/confirmar_rebackup.html', {
            'tipo'        : tipo,
            'periodo'     : periodo,
            'categoria_id': categoria_id,
            'cat_nombre'  : cat_nombre,
            'descripcion' : descripcion,
            'total'       : total,
            'completados' : completados,
        })