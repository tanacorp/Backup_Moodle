import os
import logging
import paramiko
import re
from django.conf import settings

logger = logging.getLogger(__name__)

class SSHClientError(Exception):
    """Error controlado de conexión o ejecución SSH."""
    pass

class MoodleSSHClient:
    """
    Cliente SSH reutilizable para ejecutar comandos en el Nodo A.
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

    def conectar(self) -> None:
        try:
            self._client = paramiko.SSHClient()
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
        except Exception as e:
            raise SSHClientError(f"No se pudo conectar a {self.host}: {e}")

    def cerrar(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            logger.info("SSH desconectado.")

    def __enter__(self):
        self.conectar()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cerrar()
        return False

    def ejecutar(self, comando: str, timeout: int = 300) -> tuple[str, str, int]:
        if not self._client:
            raise SSHClientError("No hay conexión SSH activa.")
        
        stdin, stdout, stderr = self._client.exec_command(comando, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode('utf-8', errors='replace').strip()
        err = stderr.read().decode('utf-8', errors='replace').strip()
        
        if exit_code != 0:
            logger.warning(f"Comando retornó {exit_code}: {err[:200]}")
        return out, err, exit_code

    def listar_cursos(self, periodo: str) -> list[dict]:
        cmd = f"cd {self.moodle} && {self.moosh} course-list | grep -E '\"(CU-)?{periodo}-[^\"]*\"'"
        out, err, code = self.ejecutar(cmd, timeout=60)
        
        cursos = []
        for linea in out.splitlines():
            partes = linea.split('","')
            if len(partes) < 4: continue
            curso_id = partes[0].strip().strip('"')
            if not curso_id.isdigit(): continue
            cursos.append({
                'id': int(curso_id),
                'shortname': partes[2].strip().strip('"'),
                'fullname': partes[3].strip().strip('"'),
            })
        return cursos

    def ejecutar_backup(self, curso_id: int, ruta_destino: str) -> tuple[bool, str]:
        carpeta = f"{self.temp_dir}/{'/'.join(ruta_destino.split('/')[:-1])}"
        ruta_completa = f"{self.temp_dir}/{ruta_destino}"
        self.ejecutar(f"mkdir -p '{carpeta}'")
        
        cmd = f"cd {self.moodle} && {self.moosh} course-backup -f '{ruta_completa}' {curso_id} 2>&1"
        out, err, code = self.ejecutar(cmd, timeout=600)
        return (code == 0), (out or err)

    def verificar_archivo(self, ruta_remota: str) -> tuple[bool, int]:
        cmd = f"stat -c%s '{self.temp_dir}/{ruta_remota}' 2>/dev/null"
        out, _, code = self.ejecutar(cmd, timeout=10)
        if code == 0 and out.isdigit():
            return True, int(out)
        return False, 0

    def eliminar_temporal(self, ruta_remota: str) -> None:
        self.ejecutar(f"rm -f {self.temp_dir}/{ruta_remota}", timeout=10)

    def listar_categorias(self) -> list[dict]:
        import re
        cmd = f"cd {self.moodle} && {self.moosh} category-list 2>&1"
        out, err, code = self.ejecutar(cmd, timeout=60)

        if not out:
            raise SSHClientError(f"Error listando categorías: {err}")

        lineas = out.splitlines()
        if not lineas:
            return []

        # Calcular posiciones desde la cabecera
        cabecera = lineas[0]
        cols = {}
        for nombre_col in ['id', 'name', 'idnumber', 'description', 'parent', 'visible']:
            pos = cabecera.find(nombre_col)
            if pos >= 0:
                cols[nombre_col] = pos

        def extraer(linea, col_inicio, col_siguiente=None):
            if col_siguiente:
                return linea[col_inicio:col_siguiente].strip()
            return linea[col_inicio:].strip()

        # Orden de columnas para saber dónde termina cada una
        orden = sorted(cols.items(), key=lambda x: x[1])

        categorias = []
        for linea in lineas[1:]:
            if not linea.strip():
                continue

            # Extraer cada campo usando posiciones de cabecera
            valores = {}
            for i, (nombre, inicio) in enumerate(orden):
                siguiente = orden[i+1][1] if i+1 < len(orden) else None
                valores[nombre] = extraer(linea, inicio, siguiente)

            cat_id = valores.get('id', '').split()[0] if valores.get('id') else ''
            if not cat_id.isdigit():
                continue

            # nombre e idnumber pueden estar pegados — separar por patrón CC-NNN
            nombre_raw   = valores.get('name', '')
            idnumber_raw = valores.get('idnumber', '')
            nombre_completo = (nombre_raw + idnumber_raw).strip()

            match = re.search(r'(CC-\w+)', nombre_completo)
            if match:
                idnumber_real = match.group(1)
                nombre_real   = nombre_completo[:match.start()].strip()
            else:
                idnumber_real = idnumber_raw.strip()
                nombre_real   = nombre_raw.strip()

            padre = valores.get('parent', 'Top').strip()

            categorias.append({
                'id'      : int(cat_id),
                'nombre'  : nombre_real,
                'idnumber': idnumber_real,
                'padre'   : 0 if padre == 'Top' else (int(padre) if padre.isdigit() else 0),
            })

        logger.info(f"Categorías encontradas: {len(categorias)}")
        return categorias

    def listar_cursos_categoria(self, categoria_id: int, incluir_subcategorias: bool = False) -> list[dict]:
        def _cursos_de_cat(cid: int) -> list[dict]:
            cmd = (
                f"cd {self.moodle} && "
                f"{self.moosh} course-list -c {cid} 2>&1"
                f" | grep -E '\"[0-9]+\",'"
            )
            out, _, _ = self.ejecutar(cmd, timeout=60)
            res = []
            for linea in out.splitlines():
                partes = linea.split('","')
                if len(partes) < 4:
                    continue
                curso_id = partes[0].strip().strip('"')
                if not curso_id.isdigit():
                    continue
                res.append({
                    'id'       : int(curso_id),
                    'shortname': partes[2].strip().strip('"'),
                    'fullname' : partes[3].strip().strip('"'),
                })
            return res

        cursos = _cursos_de_cat(categoria_id)

        if incluir_subcategorias:
            todas_cats = self.listar_categorias()
            subcats = [c for c in todas_cats if c['padre'] == categoria_id]
            for s in subcats:
                cursos.extend(_cursos_de_cat(s['id']))

        # Deduplicar por ID
        vistos = set()
        return [c for c in cursos if not (c['id'] in vistos or vistos.add(c['id']))]

    def tiene_nomenclatura(self, shortname: str) -> bool:
        from .validators import parsear_shortname
        parsed = parsear_shortname(shortname)
        return (
            bool(re.match(r'^20\d{2}$', parsed.get('anio', ''))) and
            bool(re.match(r'^\d{5}$', parsed.get('periodo', ''))) and
            bool(parsed.get('programa', ''))
        )