from unittest.mock import patch, MagicMock
from django.test import TestCase
from backups.tasks import calcular_checksum, backup_periodo
from backups.models import BackupLog
import tempfile
import os


class ChecksumTest(TestCase):

    def test_checksum_consistente(self):
        """El mismo archivo siempre produce el mismo hash."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'contenido de prueba')
            ruta = f.name
        try:
            from pathlib import Path
            h1 = calcular_checksum(Path(ruta))
            h2 = calcular_checksum(Path(ruta))
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 64)
        finally:
            os.unlink(ruta)


class BackupPeriodoTaskTest(TestCase):

    @patch('backups.tasks.MoodleSSHClient')
    def test_periodo_invalido_lanza_error(self, mock_ssh):
        """Un periodo malformado debe lanzar ValueError."""
        with self.assertRaises(ValueError):
            backup_periodo('99999')   # ciclo 9 no válido

    @patch('backups.tasks.MoodleSSHClient')
    def test_sin_cursos_retorna_cero(self, mock_ssh):
        """Si no hay cursos el resumen debe tener total=0."""
        mock_ssh.return_value.__enter__.return_value.listar_cursos.return_value = []
        resultado = backup_periodo('20261')
        self.assertEqual(resultado['total'], 0)
        self.assertEqual(resultado['exitosos'], 0)

    @patch('backups.tasks.transferir_archivo')
    @patch('backups.tasks.calcular_checksum')
    @patch('backups.tasks.MoodleSSHClient')
    def test_curso_exitoso_crea_log_completado(
        self, mock_ssh_cls, mock_checksum, mock_transferir
    ):
        """Un curso procesado correctamente debe quedar COMPLETADO en BD."""
        # Mock SSH
        mock_ssh = MagicMock()
        mock_ssh_cls.return_value.__enter__.return_value = mock_ssh
        mock_ssh.listar_cursos.return_value = [{
            'id': 100, 'shortname': '20261-11P240405-G01', 'fullname': 'Historia'
        }]
        mock_ssh.ejecutar_backup.return_value = (True, 'OK')
        mock_ssh.verificar_archivo.return_value = (True, 1024000)

        # Mock transferencia y checksum
        from pathlib import Path
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.mbz', delete=False) as f:
            f.write(b'fake mbz content')
            ruta_fake = Path(f.name)

        mock_transferir.return_value = ruta_fake
        mock_checksum.return_value  = 'a' * 64

        resultado = backup_periodo('20261')

        self.assertEqual(resultado['exitosos'], 1)
        self.assertEqual(resultado['fallidos'], 0)

        log = BackupLog.objects.get(curso_id=100)
        self.assertEqual(log.estado, BackupLog.Estado.COMPLETADO)
        self.assertEqual(log.checksum_sha256, 'a' * 64)

        import os
        os.unlink(ruta_fake)