import re
from django.core.exceptions import ValidationError


PERIODO_RE = re.compile(r'^\d{4}[1-3]$')
# Formato: 4 dígitos de año + 1 dígito de ciclo (1, 2 o 3)
# Válidos:  20261, 20252, 20243
# Inválidos: 2026, 202610, 20264, abc


def validar_periodo(value: str) -> None:
    """
    Valida que el periodo tenga formato YYYYC.
    Lanza ValidationError si no cumple — nunca pasa strings
    sin validar a comandos shell.
    """
    if not isinstance(value, str):
        raise ValidationError("El periodo debe ser una cadena de texto.")

    value = value.strip()

    if not PERIODO_RE.match(value):
        raise ValidationError(
            f"Periodo inválido: '{value}'. "
            "Formato esperado: YYYYC (ej: 20261, 20252)."
        )


def sanitizar_nombre_archivo(nombre: str) -> str:
    """
    Elimina caracteres prohibidos en rutas de archivo.
    Equivalente Python de la función sanitizar_nombre() del bash.
    """
    # Caracteres prohibidos en rutas Windows y Linux
    nombre = re.sub(r'[\/:\\*?"<>|]', '_', nombre)
    # Colapsar espacios múltiples
    nombre = re.sub(r' {2,}', ' ', nombre)
    return nombre.strip()


def parsear_shortname(shortname: str) -> dict:
    """
    Extrae anio, periodo y programa desde el shortname del curso.
    Equivalente Python de parsear_shortname() del bash.

    Ejemplos:
      'CU-20261-11P240405-G03'  → {anio:'2026', periodo:'20261', programa:'11P240405'}
      '20261-11P240405-G03'     → {anio:'2026', periodo:'20261', programa:'11P240405'}
    """
    # Remover prefijo alfabético opcional (ej: 'CU-')
    limpio = re.sub(r'^[A-Za-z]+-', '', shortname)
    partes = limpio.split('-')

    periodo = partes[0] if len(partes) > 0 else ''
    programa = partes[1] if len(partes) > 1 else 'SIN_PROGRAMA'
    anio = periodo[:4] if len(periodo) >= 4 else ''

    return {
        'anio'    : anio,
        'periodo' : periodo,
        'programa': programa,
    }

def validar_categoria_id(value) -> None:
    """Valida que el ID de categoría sea un entero positivo."""
    try:
        val = int(value)
        if val <= 0:
            raise ValueError
    except (ValueError, TypeError):
        raise ValidationError(
            f"ID de categoría inválido: '{value}'. Debe ser un número entero positivo."
        )