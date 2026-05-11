from django.db import models


class BackupLog(models.Model):

    class Estado(models.TextChoices):
        PENDIENTE  = 'PENDIENTE',  'Pendiente'
        EN_PROCESO = 'EN_PROCESO', 'En proceso'
        COMPLETADO = 'COMPLETADO', 'Completado'
        FALLIDO    = 'FALLIDO',    'Fallido'

    # Identificación académica
    periodo  = models.CharField(max_length=10, db_index=True)
    anio     = models.CharField(max_length=4)
    programa = models.CharField(max_length=30)

    # Datos del curso en Moodle
    curso_id  = models.IntegerField()
    shortname = models.CharField(max_length=120)
    fullname  = models.CharField(max_length=255)

    # Estado del proceso
    estado         = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
        db_index=True,
    )
    celery_task_id = models.CharField(max_length=255, null=True, blank=True)
    error_detalle  = models.TextField(null=True, blank=True)

    # Timestamps
    fecha_inicio = models.DateTimeField(auto_now_add=True)
    fecha_fin    = models.DateTimeField(null=True, blank=True)

    # Archivo resultante
    nombre_archivo   = models.CharField(max_length=255, null=True, blank=True)
    ruta_local       = models.CharField(max_length=512, null=True, blank=True)
    tamano_mb        = models.FloatField(null=True, blank=True)
    checksum_sha256  = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        ordering = ['-fecha_inicio']
        verbose_name     = 'Backup'
        verbose_name_plural = 'Backups'
        indexes = [
            models.Index(fields=['periodo', 'estado']),
            models.Index(fields=['curso_id']),
        ]

    def __str__(self):
        return f"[{self.estado}] {self.periodo} — {self.shortname}"

    @property
    def duracion_segundos(self):
        if self.fecha_inicio and self.fecha_fin:
            return (self.fecha_fin - self.fecha_inicio).seconds
        return None

    @property
    def archivo_disponible(self):
        return self.estado == self.Estado.COMPLETADO and bool(self.ruta_local)