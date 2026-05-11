import os
import logging
import paramiko
from django.conf import settings

logger = logging.getLogger(__name__)


class SSHClientError(Exception):
    """Error controlado de conexión o ejecución SSH."""
    pass


class MoodleSSHClient:
    """
    Cliente SSH reutilizable para ejecutar comandos en el Nodo A.
    Uso recomendado con context manager (with):

        with MoodleSSHClient() as ssh:
            stdout, stderr, code = ssh.ejecutar("ls /tmp")
    """

    def __init__(self):
        cfg = settings.SGBM
        self.host     = cfg['NODO_A_HOST']
        self.user     = cfg['NODO_A_USER']
        self.key_path = cfg['NODO_A_KEY']
        self.moodle   = cfg['NODO_A_MOODLE']
        self.moosh    = cfg['NODO_A_MOOSH']
        self.temp_dir = cfg['NODO_A_TEMP']
        self._client  = None

    # ── Conexión ──────────────────────────────────

    def conectar(self) -> None:
        try:
            self._client = paramiko.SSHClient()

            # Cargar known_hosts con ruta absoluta
            known_hosts = os.path.expanduser('~/.ssh/known_hosts')
            if os.path.exists(known_hosts):
                self._client.load_host_keys(known_hosts)

            self._client.set_missing_host_key_policy(paramiko.RejectPolicy())
            self._client.connect(
                hostname      = self.host,
                username      = self.user,
                key_filename  = self.key_path,
                look_for_keys = False,
                allow_agent   = False,
                timeout       = 15,
            )
            logger.info(f"SSH conectado → {self.user}@{self.host}")
        except paramiko.AuthenticationException as e:
            raise SSHClientError(f"Autenticación SSH fallida: {e}")
        except paramiko.SSHException as e:
            raise SSHClientError(f"Error SSH: {e}")
        except Exception as e:
            raise SSHClientError(f"No se pudo conectar a {self.host}: {e}")

    def cerrar(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            logger.info("SSH desconectado.")

    # ── Context manager ───────────────────────────

    def __enter__(self):
        self.conectar()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cerrar()
        return False

    # ── Ejecución de comandos ─────────────────────

    def ejecutar(self, comando: str, timeout: int = 300) -> tuple[str, str, int]:
        if not self._client:
            raise SSHClientError("No hay conexión SSH activa.")

        logger.debug(f"SSH exec: {comando}")

        stdin, stdout, stderr = self._client.exec_command(
            comando, timeout=timeout
        )
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode('utf-8', errors='replace').strip()
        err = stderr.read().decode('utf-8', errors='replace').strip()

        if exit_code != 0:
            logger.warning(f"Comando retornó {exit_code}: {err[:200]}")

        return out, err, exit_code

    # ── Comandos de alto nivel ────────────────────

    def listar_cursos(self, periodo: str) -> list[dict]:
        cmd = (
            f"cd {self.moodle} && "
            f"{self.moosh} course-list "
            f"| grep -E '\"(CU-)?{periodo}-[^\"]*\"'"
        )
        out, err, code = self.ejecutar(cmd, timeout=60)

        if code != 0 and not out:
            raise SSHClientError(f"Error listando cursos: {err}")

        cursos = []
        for linea in out.splitlines():
            partes = linea.split('","')
            if len(partes) < 4:
                continue
            curso_id = partes[0].strip().strip('"')
            if not curso_id.isdigit():
                continue
            cursos.append({
                'id'       : int(curso_id),
                'shortname': partes[2].strip().strip('"'),
                'fullname' : partes[3].strip().strip('"'),
            })

        logger.info(f"Cursos encontrados para {periodo}: {len(cursos)}")
        return cursos

    def ejecutar_backup(self, curso_id: int, ruta_destino: str) -> tuple[bool, str]:
        carpeta       = f"{self.temp_dir}/{'/'.join(ruta_destino.split('/')[:-1])}"
        ruta_completa = f"{self.temp_dir}/{ruta_destino}"

        self.ejecutar(f"mkdir -p '{carpeta}'")
        self.ejecutar(f"rm -f '{ruta_completa}'")

        cmd = (
            f"cd {self.moodle} && "
            f"{self.moosh} course-backup "
            f"-f '{ruta_completa}' {curso_id} 2>&1"  # ← comillas simples
        )
        out, err, code = self.ejecutar(cmd, timeout=600)

        mensaje = out or err or "Sin mensaje de moosh"

        if code == 0:
            return True, mensaje
        return False, mensaje


    def verificar_archivo(self, ruta_remota: str) -> tuple[bool, int]:
        cmd = f"stat -c%s '{self.temp_dir}/{ruta_remota}' 2>/dev/null"
        out, _, code = self.ejecutar(cmd, timeout=10)

        if code == 0 and out.isdigit():
            return True, int(out)
        return False, 0

    def eliminar_temporal(self, ruta_remota: str) -> None:
        self.ejecutar(f"rm -f {self.temp_dir}/{ruta_remota}", timeout=10)