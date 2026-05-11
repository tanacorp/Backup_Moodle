import hashlib
import logging
import subprocess
import time
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import BackupLog
from .validators import validar_periodo, validar_categoria_id, parsear_shortname, sanitizar_nombre_archivo
from .ssh_client import MoodleSSHClient, SSHClientError

logger = logging.getLogger(__name__)


def calcular_checksum(ruta: Path) -> str:
    sha = hashlib.sha256()
    with open(ruta, 'rb') as f:
        for bloque in iter(lambda: f.read(65536), b''):
            sha.update(bloque)
    return sha.hexdigest()


def transferir_archivo(ssh: MoodleSSHClient, ruta_relativa: str) -> Path:
    cfg         = settings.SGBM
    origen      = f"{ssh.user}@{ssh.host}:{ssh.temp_dir}/{ruta_relativa}"
    carpeta_dst = Path(cfg['NODO_B_BACKUP']) / Path(ruta_relativa).parent
    carpeta_dst.mkdir(parents=True, exist_ok=True)

    cmd = [
        'rsync', '-avz', '--remove-source-files',
        '-e', f"ssh -i {ssh.key_path} -o StrictHostKeyChecking=no",
        origen,
        str(carpeta_dst) + '/',
    ]

    logger.debug(f"rsync: {' '.join(cmd)}")
    resultado = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if resultado.returncode != 0:
        raise RuntimeError(
            f"rsync falló (código {resultado.returncode}): {resultado.stderr[:300]}"
        )

    archivo_local = carpeta_dst / Path(ruta_relativa).name
    if not archivo_local.exists():
        raise FileNotFoundError(
            f"rsync terminó pero el archivo no existe: {archivo_local}"
        )

    return archivo_local


def _marcar_fallido(log: BackupLog, detalle: str) -> None:
    log.estado        = BackupLog.Estado.FALLIDO
    log.fecha_fin     = timezone.now()
    log.error_detalle = detalle[:2000]
    log.save(update_fields=['estado', 'fecha_fin', 'error_detalle'])
    logger.error(f"[FALLIDO] {log.shortname}: {detalle[:200]}")


@shared_task(bind=True, name='backups.backup_periodo')
def backup_periodo(self, periodo: str) -> dict:
    logger.info(f"==== Iniciando backup periodo {periodo} ====")

    # ── 1. Validar input ──────────────────────────────────────────
    try:
        validar_periodo(periodo)
    except Exception as e:
        raise ValueError(f"Periodo inválido: {e}") from e

    cfg      = settings.SGBM
    delay    = cfg.get('DELAY_CURSOS', 3)
    exitosos = 0
    fallidos = 0
    total    = 0

    # ── 2. Todo en una sola conexión SSH ──────────────────────────
    try:
        with MoodleSSHClient() as ssh:

            cursos = ssh.listar_cursos(periodo)
            total  = len(cursos)
            logger.info(f"Cursos encontrados: {total}")

            if total == 0:
                return {
                    'periodo' : periodo,
                    'total'   : 0,
                    'exitosos': 0,
                    'fallidos': 0,
                    'mensaje' : 'No se encontraron cursos para el periodo.',
                }

            # ── 3. Loop por cada curso ────────────────────────────
            for i, curso in enumerate(cursos, start=1):

                curso_id  = curso['id']
                shortname = curso['shortname']
                fullname  = curso['fullname']

                logger.info(f"[{i}/{total}] ID={curso_id} | {shortname}")

                parsed   = parsear_shortname(shortname)
                anio     = parsed['anio']     or periodo[:4]
                per      = parsed['periodo']  or periodo
                programa = parsed['programa'] or 'SIN_PROGRAMA'

                nombre_archivo = sanitizar_nombre_archivo(f"{fullname}.mbz")
                ruta_relativa  = f"{anio}/{per}/{programa}/{nombre_archivo}"

                log = BackupLog.objects.create(
                    periodo        = periodo,
                    anio           = anio,
                    programa       = programa,
                    curso_id       = curso_id,
                    shortname      = shortname,
                    fullname       = fullname,
                    nombre_archivo = nombre_archivo,
                    estado         = BackupLog.Estado.EN_PROCESO,
                    celery_task_id = self.request.id,
                )

                # ── 3a. Backup via moosh ──────────────────────────
                exito, mensaje = ssh.ejecutar_backup(curso_id, ruta_relativa)
                if not exito:
                    _marcar_fallido(log, f"moosh error: {mensaje}")
                    fallidos += 1
                    continue

                existe, _ = ssh.verificar_archivo(ruta_relativa)
                if not existe:
                    _marcar_fallido(log, "Archivo no encontrado tras moosh.")
                    fallidos += 1
                    continue

                # ── 3b. Transferir a Nodo B ───────────────────────
                try:
                    archivo_local = transferir_archivo(ssh, ruta_relativa)
                except Exception as e:
                    _marcar_fallido(log, f"rsync error: {e}")
                    fallidos += 1
                    continue

                # ── 3c. Checksum y tamaño ─────────────────────────
                try:
                    checksum  = calcular_checksum(archivo_local)
                    tamano_mb = archivo_local.stat().st_size / (1024 * 1024)
                except Exception as e:
                    _marcar_fallido(log, f"Error checksum: {e}")
                    fallidos += 1
                    continue

                # ── 3d. Marcar COMPLETADO ─────────────────────────
                log.estado          = BackupLog.Estado.COMPLETADO
                log.fecha_fin       = timezone.now()
                log.ruta_local      = str(archivo_local)
                log.tamano_mb       = round(tamano_mb, 2)
                log.checksum_sha256 = checksum
                log.save(update_fields=[
                    'estado', 'fecha_fin', 'ruta_local',
                    'tamano_mb', 'checksum_sha256',
                ])

                exitosos += 1
                logger.info(f"  ✓ {tamano_mb:.1f} MB | {checksum[:12]}...")

                if i < total:
                    time.sleep(delay)

    except SSHClientError as e:
        raise RuntimeError(f"Error SSH: {e}") from e

    # ── 4. Resumen ────────────────────────────────────────────────
    resumen = {
        'periodo' : periodo,
        'total'   : total,
        'exitosos': exitosos,
        'fallidos': fallidos,
        'mensaje' : f"Completado: {exitosos}/{total} exitosos.",
    }
    logger.info(f"==== Fin backup {periodo} | {resumen['mensaje']} ====")
    return resumen

@shared_task(bind=True, name='backups.backup_categoria')
def backup_categoria(self, categoria_id: int,
                     categoria_nombre: str,
                     categoria_carpeta: str,
                     incluir_subcategorias: bool = False) -> dict:
    """
    Backup de todos los cursos de una categoría Moodle.
    Cursos CON nomenclatura → AÑO/PERIODO/PROGRAMA/
    Cursos SIN nomenclatura → CATEGORIA/
    """
    logger.info(f"==== Iniciando backup categoría [{categoria_id}] {categoria_nombre} ====")

    try:
        validar_categoria_id(categoria_id)
    except Exception as e:
        raise ValueError(f"Categoría inválida: {e}") from e

    cfg      = settings.SGBM
    delay    = cfg.get('DELAY_CURSOS', 3)
    exitosos = 0
    fallidos = 0
    total    = 0
    count_nomenclatura = 0
    count_fallback     = 0

    try:
        with MoodleSSHClient() as ssh:

            cursos = ssh.listar_cursos_categoria(
                categoria_id, incluir_subcategorias
            )
            total = len(cursos)
            logger.info(f"Cursos encontrados: {total}")

            if total == 0:
                return {
                    'categoria_id': categoria_id,
                    'total'   : 0,
                    'exitosos': 0,
                    'fallidos': 0,
                    'mensaje' : 'No se encontraron cursos en la categoría.',
                }

            for i, curso in enumerate(cursos, start=1):

                curso_id  = curso['id']
                shortname = curso['shortname']
                fullname  = curso['fullname']

                logger.info(f"[{i}/{total}] ID={curso_id} | {shortname}")

                # ── Determinar ruta según nomenclatura ────────
                if ssh.tiene_nomenclatura(shortname):
                    parsed   = parsear_shortname(shortname)
                    anio     = parsed['anio']
                    periodo  = parsed['periodo']
                    programa = parsed['programa']
                    ruta_relativa = f"{anio}/{periodo}/{programa}"
                    tipo_ruta     = 'nomenclatura'
                    count_nomenclatura += 1
                else:
                    anio      = ''
                    periodo   = ''
                    programa  = ''
                    ruta_relativa = categoria_carpeta
                    tipo_ruta     = 'fallback'
                    count_fallback += 1

                nombre_archivo = sanitizar_nombre_archivo(f"{fullname}.mbz")
                ruta_completa  = f"{ruta_relativa}/{nombre_archivo}"

                log = BackupLog.objects.create(
                    tipo              = BackupLog.TipoBackup.CATEGORIA,
                    categoria_id      = categoria_id,
                    categoria_nombre  = categoria_nombre,
                    categoria_carpeta = categoria_carpeta,
                    tipo_ruta         = tipo_ruta,
                    periodo           = periodo,
                    anio              = anio,
                    programa          = programa,
                    curso_id          = curso_id,
                    shortname         = shortname,
                    fullname          = fullname,
                    nombre_archivo    = nombre_archivo,
                    estado            = BackupLog.Estado.EN_PROCESO,
                    celery_task_id    = self.request.id,
                )

                # ── Backup via moosh ──────────────────────────
                exito, mensaje = ssh.ejecutar_backup(curso_id, ruta_completa)
                if not exito:
                    _marcar_fallido(log, f"moosh error: {mensaje}")
                    fallidos += 1
                    continue

                existe, _ = ssh.verificar_archivo(ruta_completa)
                if not existe:
                    _marcar_fallido(log, "Archivo no encontrado tras moosh.")
                    fallidos += 1
                    continue

                # ── Transferir ────────────────────────────────
                try:
                    archivo_local = transferir_archivo(ssh, ruta_completa)
                except Exception as e:
                    _marcar_fallido(log, f"rsync error: {e}")
                    fallidos += 1
                    continue

                # ── Checksum ──────────────────────────────────
                try:
                    checksum  = calcular_checksum(archivo_local)
                    tamano_mb = archivo_local.stat().st_size / (1024 * 1024)
                except Exception as e:
                    _marcar_fallido(log, f"Error checksum: {e}")
                    fallidos += 1
                    continue

                log.estado          = BackupLog.Estado.COMPLETADO
                log.fecha_fin       = timezone.now()
                log.ruta_local      = str(archivo_local)
                log.tamano_mb       = round(tamano_mb, 2)
                log.checksum_sha256 = checksum
                log.save(update_fields=[
                    'estado', 'fecha_fin', 'ruta_local',
                    'tamano_mb', 'checksum_sha256',
                ])

                exitosos += 1
                logger.info(f"  ✓ [{tipo_ruta}] {tamano_mb:.1f} MB")

                if i < total:
                    time.sleep(delay)

    except SSHClientError as e:
        raise RuntimeError(f"Error SSH: {e}") from e

    resumen = {
        'categoria_id'     : categoria_id,
        'total'            : total,
        'exitosos'         : exitosos,
        'fallidos'         : fallidos,
        'con_nomenclatura' : count_nomenclatura,
        'sin_nomenclatura' : count_fallback,
        'mensaje'          : f"Completado: {exitosos}/{total} exitosos.",
    }
    logger.info(f"==== Fin backup categoría {categoria_id} | {resumen['mensaje']} ====")
    return resumen


@shared_task(bind=True, name='backups.backup_curso_individual')
def backup_curso_individual(self, curso_id: int,
                             shortname: str,
                             fullname: str) -> dict:
    """Backup de un único curso por ID."""
    logger.info(f"==== Backup individual curso ID={curso_id} ====")

    cfg = settings.SGBM

    try:
        with MoodleSSHClient() as ssh:

            if ssh.tiene_nomenclatura(shortname):
                parsed   = parsear_shortname(shortname)
                anio     = parsed['anio']     or ''
                periodo  = parsed['periodo']  or ''
                programa = parsed['programa'] or 'SIN_PROGRAMA'
                ruta_relativa = f"{anio}/{periodo}/{programa}"
            else:
                anio      = ''
                periodo   = ''
                programa  = 'INDIVIDUAL'
                ruta_relativa = 'INDIVIDUAL'

            nombre_archivo = sanitizar_nombre_archivo(f"{fullname}.mbz")
            ruta_completa  = f"{ruta_relativa}/{nombre_archivo}"

            log = BackupLog.objects.create(
                tipo           = BackupLog.TipoBackup.PERIODO,
                periodo        = periodo,
                anio           = anio,
                programa       = programa,
                curso_id       = curso_id,
                shortname      = shortname,
                fullname       = fullname,
                nombre_archivo = nombre_archivo,
                estado         = BackupLog.Estado.EN_PROCESO,
                celery_task_id = self.request.id,
            )

            exito, mensaje = ssh.ejecutar_backup(curso_id, ruta_completa)
            if not exito:
                _marcar_fallido(log, f"moosh error: {mensaje}")
                return {'exitosos': 0, 'fallidos': 1}

            existe, _ = ssh.verificar_archivo(ruta_completa)
            if not existe:
                _marcar_fallido(log, "Archivo no encontrado tras moosh.")
                return {'exitosos': 0, 'fallidos': 1}

            archivo_local = transferir_archivo(ssh, ruta_completa)
            checksum      = calcular_checksum(archivo_local)
            tamano_mb     = archivo_local.stat().st_size / (1024 * 1024)

            log.estado          = BackupLog.Estado.COMPLETADO
            log.fecha_fin       = timezone.now()
            log.ruta_local      = str(archivo_local)
            log.tamano_mb       = round(tamano_mb, 2)
            log.checksum_sha256 = checksum
            log.save(update_fields=[
                'estado', 'fecha_fin', 'ruta_local',
                'tamano_mb', 'checksum_sha256',
            ])

            logger.info(f"✓ Backup individual OK — {tamano_mb:.1f} MB")
            return {
                'curso_id' : curso_id,
                'exitosos' : 1,
                'fallidos' : 0,
                'tamano_mb': round(tamano_mb, 2),
                'ruta'     : str(archivo_local),
            }

    except SSHClientError as e:
        raise RuntimeError(f"Error SSH: {e}") from e