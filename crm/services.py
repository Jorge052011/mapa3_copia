# crm/services.py
import re

from django.utils import timezone
from django.db.models import Sum, Max, Count
from decimal import Decimal
from .models import Venta, Importacion


def normalizar_telefono(valor):
    """
    Deja un teléfono en formato consistente.

    Elimina espacios (incluidos NBSP), guiones, puntos, paréntesis y cualquier
    otro carácter que no sea dígito. Conserva un único '+' inicial si el valor
    original comenzaba con '+'.

    Ejemplos:
        "+56 9 2005 4682" -> "+56920054682"
        "9 2005 4682"     -> "920054682"
        "+ +56920054682"  -> "+56920054682"
    """
    valor = (valor or "").strip()
    if not valor:
        return ""

    tenia_mas = valor.startswith("+")
    digitos = re.sub(r"\D", "", valor)
    if not digitos:
        return ""

    return f"+{digitos}" if tenia_mas else digitos


def _ultimo_numero_numerico(tipo_documento, limite=50):
    valores = (
        Venta.objects.filter(tipo_documento=tipo_documento)
        .exclude(numero_documento="")
        .order_by("-id")
        .values_list("numero_documento", flat=True)[:limite]
    )
    for valor in valores:
        valor = (valor or "").strip()
        if valor.isdigit():
            return valor
    return None


def siguiente_numero_documento(tipo_documento):
    """
    Devuelve el siguiente número sugerido para un tipo de documento.

    Usa el último número numérico por id del mismo tipo. Si el tipo no tiene
    historial numérico, cae al último número de boleta. Convierte el valor a
    entero y suma uno, ignorando formatos no numéricos como "27828-1".
    """
    if tipo_documento not in Venta.TipoDocumento.values:
        return ""
    if tipo_documento == Venta.TipoDocumento.SIN_DOC:
        return ""

    base = _ultimo_numero_numerico(tipo_documento)
    if base is None and tipo_documento != Venta.TipoDocumento.BOLETA:
        base = _ultimo_numero_numerico(Venta.TipoDocumento.BOLETA)
    if base is None:
        return "1"
    return str(int(base) + 1)


def segmentar_cliente(c):
    """
    Segmenta clientes según RFM (Recency, Frequency, Monetary).
    
    Args:
        c: Instancia de Cliente
    
    Returns:
        tuple: (segmento_nombre: str, color_css: str)
    """
    hoy = timezone.now()

    qs = Venta.objects.filter(cliente=c)

    agg = qs.aggregate(
        ultima=Max("fecha"),
        freq=Count("id"),
        kilos=Sum("kilos_total"),
    )

    ultima_compra = agg["ultima"] or c.creado_en
    dias = (hoy - ultima_compra).days
    freq = agg["freq"] or 0
    kilos = agg["kilos"] or 0

    # 🔴 Dormido = red flag (más de 90 días sin comprar)
    if freq > 0 and dias > 90:
        return "Dormido", "red"

    # 🟨 VIP (compra reciente, frecuente y alto volumen)
    if dias <= 45 and freq >= 2 and kilos >= 30:
        return "VIP", "gold"

    # 🟦 Frecuente (compra regular)
    if dias <= 60 and freq >= 1 and kilos >= 20:
        return "Frecuente", "blue"

    # ⚪ Ocasional (resto)
    return "Ocasional", "gray"


def costo_promedio_kg():
    """
    Calcula el costo promedio ponderado por kg según importaciones ACTIVAS.
    
    Usa kilos_restantes (stock actual) para ponderar correctamente.
    Si usáramos kilos_ingresados, incluiríamos stock ya vendido.
    
    Returns:
        Decimal: Costo promedio SIN IVA por kg (ejemplo: 5250.50)
    
    Example:
        >>> costo_promedio_kg()
        Decimal('5250.50')
    """
    qs = Importacion.objects.filter(activo=True)

    total_kilos = qs.aggregate(s=Sum("kilos_restantes"))["s"] or Decimal("0")
    
    if total_kilos <= 0:
        return Decimal("0.00")

    # Valor total = suma de (kilos_restantes * costo_por_kg) de cada importación
    valor_total = Decimal("0")
    for imp in qs:
        kilos = imp.kilos_restantes or Decimal("0")
        costo = imp.costo_por_kg or Decimal("0")
        valor_total += kilos * costo

    return (valor_total / total_kilos).quantize(Decimal("0.01"))