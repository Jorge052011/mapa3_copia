from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import (
    ControlTrazabilidad,
    Importacion,
    IngresoMaterial,
    Material,
    MovimientoBolsasVenta,
    Venta,
    VentaItem,
)
from .services_inventario import inventario_bolsas_actual

FECHA_INICIO_DEFAULT = date(2026, 10, 28)


def _codigo(tipo):
    return getattr(tipo, "codigo", tipo)


def _material(tipo, activo=None):
    qs = Material.objects.filter(codigo=_codigo(tipo))
    if activo is not None:
        qs = qs.filter(activo=activo)
    return qs.first()


def _materiales():
    return list(Material.objects.all().order_by("peso_kg", "nombre", "codigo"))


def _vacío():
    return {material.codigo: 0 for material in _materiales()}


def get_control():
    return ControlTrazabilidad.get_solo()


def fecha_inicio_control():
    control = get_control()
    return control.fecha_inicio if control else FECHA_INICIO_DEFAULT


def control_activo():
    control = get_control()
    return bool(control and control.activo)


def _validar_control_activo():
    if not control_activo():
        raise ValidationError("El control de trazabilidad no está activo.")


def _texto_tipo(tipo):
    material = _material(tipo)
    return material.nombre if material else str(_codigo(tipo))


def recibido_contenedor(importacion, tipo_bolsa=None):
    qs = IngresoMaterial.objects.filter(
        importacion=importacion,
        control_contenedor=True,
    )
    if tipo_bolsa:
        return qs.filter(tipo_bolsa__codigo=_codigo(tipo_bolsa)).aggregate(
            total=Sum("cantidad")
        )["total"] or 0

    resultado = _vacío()
    for fila in qs.values("tipo_bolsa__codigo").annotate(total=Sum("cantidad")):
        codigo = fila["tipo_bolsa__codigo"]
        if codigo in resultado:
            resultado[codigo] = fila["total"] or 0
    return resultado


def resumen_contenedor(importacion):
    recibido = recibido_contenedor(importacion)
    materiales = _materiales()
    asignado = _vacío()
    devuelto = _vacío()

    for mov in MovimientoBolsasVenta.objects.filter(
        importacion=importacion
    ).values("tipo_bolsa__codigo", "cantidad"):
        codigo = mov["tipo_bolsa__codigo"]
        if codigo not in recibido:
            continue
        cantidad = mov["cantidad"] or 0
        if cantidad > 0:
            asignado[codigo] += cantidad
        else:
            devuelto[codigo] += abs(cantidad)

    filas = []
    for material in materiales:
        codigo = material.codigo
        saldo = recibido[codigo] - asignado[codigo] + devuelto[codigo]
        peso_saldo = (Decimal(saldo) * material.peso_kg).quantize(Decimal("0.01"))
        filas.append({
            "tipo_bolsa": codigo,
            "material": material,
            "etiqueta": material.nombre,
            "peso_kg": material.peso_kg,
            "recibido": recibido[codigo],
            "asignado": asignado[codigo],
            "devuelto": devuelto[codigo],
            "saldo": saldo,
            "peso_saldo_kg": peso_saldo,
        })

    return {
        "importacion": importacion,
        "filas": filas,
        "recibido": recibido,
        "asignado": asignado,
        "devuelto": devuelto,
        "saldo": {
            fila["tipo_bolsa"]: fila["saldo"] for fila in filas
        },
        "total_peso_saldo_kg": sum(
            (fila["peso_saldo_kg"] for fila in filas), Decimal("0")
        ).quantize(Decimal("0.01")),
    }


def saldo_contenedor(importacion, tipo_bolsa):
    return resumen_contenedor(importacion)["saldo"].get(_codigo(tipo_bolsa), 0)


def saldos_por_contenedor():
    importaciones = (
        Importacion.objects
        .filter(ingresos_material__control_contenedor=True)
        .distinct()
        .order_by("-fecha", "-id")
    )
    return [resumen_contenedor(imp) for imp in importaciones]


def importaciones_con_saldo(tipo_bolsa):
    codigo = _codigo(tipo_bolsa)
    recibidos = {
        fila["importacion_id"]: fila["total"] or 0
        for fila in (
            IngresoMaterial.objects
            .filter(control_contenedor=True, tipo_bolsa__codigo=codigo)
            .values("importacion_id")
            .annotate(total=Sum("cantidad"))
        )
    }
    netos = {
        fila["importacion_id"]: fila["total"] or 0
        for fila in (
            MovimientoBolsasVenta.objects
            .filter(tipo_bolsa__codigo=codigo)
            .values("importacion_id")
            .annotate(total=Sum("cantidad"))
        )
    }

    disponibles = []
    for importacion in Importacion.objects.filter(
        pk__in=[pk for pk in recibidos if pk]
    ).order_by("fecha", "id"):
        saldo = recibidos.get(importacion.pk, 0) - netos.get(importacion.pk, 0)
        if saldo > 0:
            disponibles.append({
                "importacion": importacion,
                "saldo": saldo,
            })
    return disponibles


def importaciones_recepcionables():
    inicio = fecha_inicio_control()
    return (
        Importacion.objects
        .filter(
            Q(fecha__gte=inicio) |
            Q(ingresos_material__control_contenedor=True)
        )
        .distinct()
        .order_by("-fecha", "-id")
    )


def requerido_bolsas_item(item):
    requerido = {}
    for componente in (
        item.producto.componentes.select_related("material").all()
    ):
        requerido[componente.material.codigo] = (
            int(componente.cantidad) * int(item.cantidad)
        )
    return requerido


def asignado_neto_item(item, tipo_bolsa=None):
    qs = MovimientoBolsasVenta.objects.filter(venta_item=item)
    if tipo_bolsa:
        qs = qs.filter(tipo_bolsa__codigo=_codigo(tipo_bolsa))
    resultado = _vacío()
    for fila in qs.values("tipo_bolsa__codigo").annotate(total=Sum("cantidad")):
        codigo = fila["tipo_bolsa__codigo"]
        if codigo in resultado:
            resultado[codigo] = fila["total"] or 0
    return resultado if tipo_bolsa is None else resultado.get(_codigo(tipo_bolsa), 0)


def pendiente_item(item):
    requerido = requerido_bolsas_item(item)
    asignado = asignado_neto_item(item)
    return {
        tipo: requerido.get(tipo, 0) - asignado.get(tipo, 0)
        for tipo in requerido
    }


def item_requiere_trazabilidad(item):
    if item.venta.tipo_documento == Venta.TipoDocumento.NOTA_CREDITO:
        return False
    if item.venta.fecha.date() < fecha_inicio_control():
        return False
    return any(valor > 0 for valor in pendiente_item(item).values())


def detalle_trazabilidad_venta(venta):
    disponibles_por_tipo = {}
    filas = []

    for item in venta.items.select_related("producto").order_by("id"):
        requerido = requerido_bolsas_item(item)
        asignado = asignado_neto_item(item)
        tipos = []

        for tipo, cantidad_requerida in requerido.items():
            if cantidad_requerida <= 0:
                continue
            if tipo not in disponibles_por_tipo:
                disponibles_por_tipo[tipo] = importaciones_con_saldo(tipo)
            cantidad_asignada = asignado.get(tipo, 0)
            pendiente = cantidad_requerida - cantidad_asignada
            tipos.append({
                "tipo_bolsa": tipo,
                "etiqueta": _texto_tipo(tipo),
                "requerido": cantidad_requerida,
                "asignado": cantidad_asignada,
                "pendiente": pendiente,
                "importaciones": disponibles_por_tipo[tipo],
                "form_token": uuid4(),
            })

        movimientos = list(
            MovimientoBolsasVenta.objects
            .filter(venta_item=item)
            .select_related("importacion", "tipo_bolsa", "creado_por")
            .order_by("-creado_en", "-id")
        )
        for movimiento in movimientos:
            movimiento.form_token_reversion = uuid4()
            movimiento.form_token_devolucion = uuid4()

        filas.append({
            "item": item,
            "tipos": tipos,
            "tiene_pendiente": any(fila["pendiente"] > 0 for fila in tipos),
            "movimientos": movimientos,
        })

    return filas


def ventas_con_trazabilidad_pendiente():
    inicio = fecha_inicio_control()
    items = (
        VentaItem.objects
        .select_related("venta", "producto")
        .filter(
            venta__fecha__date__gte=inicio,
        )
        .exclude(venta__tipo_documento=Venta.TipoDocumento.NOTA_CREDITO)
        .order_by("-venta__fecha", "venta_id", "id")
    )

    resultado = []
    for item in items:
        pendiente = {
            tipo: valor for tipo, valor in pendiente_item(item).items()
            if valor > 0
        }
        if pendiente:
            resultado.append({
                "item": item,
                "pendiente": pendiente,
            })
    return resultado


def conciliacion_por_tipo():
    inventario = inventario_bolsas_actual()
    materiales = _materiales()

    recibido = _vacío()
    for fila in (
        IngresoMaterial.objects
        .filter(control_contenedor=True)
        .values("tipo_bolsa__codigo")
        .annotate(total=Sum("cantidad"))
    ):
        codigo = fila["tipo_bolsa__codigo"]
        if codigo in recibido:
            recibido[codigo] = fila["total"] or 0

    asignado = _vacío()
    devuelto = _vacío()
    for mov in MovimientoBolsasVenta.objects.values(
        "tipo_bolsa__codigo", "cantidad"
    ):
        codigo = mov["tipo_bolsa__codigo"]
        if codigo not in asignado:
            continue
        cantidad = mov["cantidad"] or 0
        if cantidad > 0:
            asignado[codigo] += cantidad
        else:
            devuelto[codigo] += abs(cantidad)

    filas = []
    for material in materiales:
        codigo = material.codigo
        saldo_contenedores = recibido[codigo] - asignado[codigo] + devuelto[codigo]
        stock_general = inventario[f"stock_actual_{codigo}"]
        filas.append({
            "tipo_bolsa": codigo,
            "material": material,
            "etiqueta": material.nombre,
            "peso_kg": material.peso_kg,
            "stock_general": stock_general,
            "recibido": recibido[codigo],
            "asignado": asignado[codigo],
            "devuelto": devuelto[codigo],
            "saldo_contenedores": saldo_contenedores,
            "diferencia": stock_general - saldo_contenedores,
        })

    return filas


def asignar_bolsas(
    item,
    importacion,
    tipo_bolsa,
    cantidad,
    usuario=None,
    idempotency_key=None,
    observacion="",
):
    _validar_control_activo()

    if item.venta.tipo_documento == Venta.TipoDocumento.NOTA_CREDITO:
        raise ValidationError("No se asigna una nota de crédito como salida de contenedor.")
    if item.venta.fecha.date() < fecha_inicio_control():
        raise ValidationError("La venta es anterior al inicio del control por contenedor.")

    material = _material(tipo_bolsa, activo=True)
    if material is None:
        raise ValidationError("Tipo de bolsa inválido o inactivo.")
    codigo = material.codigo

    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        raise ValidationError("La cantidad debe ser un entero.")

    if cantidad <= 0:
        raise ValidationError("La cantidad a asignar debe ser mayor a 0.")

    if idempotency_key:
        existente = MovimientoBolsasVenta.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if existente:
            return existente

    with transaction.atomic():
        importacion = Importacion.objects.select_for_update().get(pk=importacion.pk)
        requerido = requerido_bolsas_item(item).get(codigo, 0)
        if requerido <= 0:
            raise ValidationError(
                "El producto de este ítem no requiere bolsas de ese tipo."
            )

        pendiente = pendiente_item(item).get(codigo, 0)
        if cantidad > pendiente:
            raise ValidationError(
                f"La asignación excede el pendiente ({pendiente} bolsas)."
            )

        saldo = saldo_contenedor(importacion, codigo)
        if cantidad > saldo:
            raise ValidationError(
                f"El contenedor solo tiene {saldo} bolsas disponibles de ese tipo."
            )

        movimiento = MovimientoBolsasVenta(
            venta_item=item,
            venta_item_ref=item.pk,
            venta_ref=item.venta_id,
            sku=(item.producto.sku or "").strip(),
            importacion=importacion,
            tipo_bolsa=material,
            cantidad=cantidad,
            tipo_movimiento=MovimientoBolsasVenta.TipoMovimiento.ASIGNACION,
            idempotency_key=idempotency_key,
            observacion=observacion,
            creado_por=usuario,
        )
        movimiento.save()
        return movimiento


def revertir_movimiento(movimiento, usuario=None, idempotency_key=None, observacion=""):
    _validar_control_activo()

    if movimiento.tipo_movimiento == MovimientoBolsasVenta.TipoMovimiento.REVERSION:
        raise ValidationError("No se puede revertir una reversión.")
    if movimiento.reversiones.exists():
        raise ValidationError("Este movimiento ya tiene una reversión registrada.")

    if idempotency_key:
        existente = MovimientoBolsasVenta.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if existente:
            return existente

    cantidad = -movimiento.cantidad

    with transaction.atomic():
        reversal = MovimientoBolsasVenta(
            venta_item=movimiento.venta_item,
            venta_item_ref=movimiento.venta_item_ref,
            venta_ref=movimiento.venta_ref,
            sku=movimiento.sku,
            importacion=movimiento.importacion,
            tipo_bolsa=movimiento.tipo_bolsa,
            cantidad=cantidad,
            tipo_movimiento=MovimientoBolsasVenta.TipoMovimiento.REVERSION,
            movimiento_origen=movimiento,
            idempotency_key=idempotency_key,
            observacion=observacion,
            creado_por=usuario,
        )
        reversal.save()
        return reversal


def registrar_devolucion_fisica(
    item,
    importacion,
    tipo_bolsa,
    cantidad,
    usuario=None,
    idempotency_key=None,
    observacion="",
):
    _validar_control_activo()

    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        raise ValidationError("La cantidad debe ser un entero.")

    if cantidad <= 0:
        raise ValidationError("La cantidad devuelta debe ser mayor a 0.")

    material = _material(tipo_bolsa)
    if material is None:
        raise ValidationError("Tipo de bolsa inválido.")

    if idempotency_key:
        existente = MovimientoBolsasVenta.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        if existente:
            return existente

    with transaction.atomic():
        importacion = Importacion.objects.select_for_update().get(pk=importacion.pk)

        asignado = asignado_neto_item(item, material.codigo)
        if cantidad > asignado:
            raise ValidationError(
                f"La devolución excede lo asignado a este ítem ({asignado} bolsas)."
            )

        movimiento = MovimientoBolsasVenta(
            venta_item=item,
            venta_item_ref=item.pk,
            venta_ref=item.venta_id,
            sku=(item.producto.sku or "").strip(),
            importacion=importacion,
            tipo_bolsa=material,
            cantidad=-cantidad,
            tipo_movimiento=MovimientoBolsasVenta.TipoMovimiento.DEVOLUCION,
            idempotency_key=idempotency_key,
            observacion=observacion,
            creado_por=usuario,
        )
        movimiento.save()
        return movimiento


def crear_recepcion(importacion, fecha, lineas, usuario=None, recepcion_key=None):
    _validar_control_activo()

    if importacion.pk not in set(importaciones_recepcionables().values_list("pk", flat=True)):
        raise ValidationError(
            "La importación no está habilitada para nuevas recepciones."
        )

    normalizadas = []
    codigos = []
    for tipo_bolsa, cantidad in lineas:
        material = _material(tipo_bolsa, activo=True)
        if material is None:
            raise ValidationError("Tipo de bolsa inválido o inactivo.")
        codigos.append(material.codigo)
        normalizadas.append((material, cantidad))

    if len(codigos) != len(set(codigos)):
        raise ValidationError("No repitas el mismo tipo de bolsa en una recepción.")

    recepcion_key = recepcion_key or uuid4()

    existentes = list(
        IngresoMaterial.objects.filter(recepcion_key=recepcion_key)
        .select_related("importacion", "tipo_bolsa")
    )
    if existentes:
        return existentes

    creados = []

    with transaction.atomic():
        for material, cantidad in normalizadas:
            try:
                cantidad = int(cantidad)
            except (TypeError, ValueError):
                raise ValidationError("Todas las cantidades deben ser enteros.")
            if cantidad <= 0:
                raise ValidationError("Todas las cantidades deben ser mayores a 0.")

            ingreso = IngresoMaterial(
                fecha=fecha,
                importacion=importacion,
                tipo_bolsa=material,
                cantidad=cantidad,
                control_contenedor=True,
                recepcion_key=recepcion_key,
                creado_por=usuario,
            )
            ingreso.full_clean()
            ingreso.save()
            creados.append(ingreso)

    return creados
