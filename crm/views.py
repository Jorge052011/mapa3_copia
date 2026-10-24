# crm/views.py
from decimal import Decimal
from datetime import timedelta, date
from dateutil.relativedelta import relativedelta
import json
import logging
from calendar import monthrange

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db import transaction
from django.db.models import (
    Sum, Count, Max, Min, Value, DecimalField, Q, DateField, F,
    ExpressionWrapper, Prefetch, OuterRef, Subquery,
)
from django.db.models.functions import Coalesce, TruncMonth, TruncDate
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .models import Cliente, Venta, VentaItem, Producto, Importacion, GastoOperacional, InventarioBolsasAjuste, DepositoEdicion, Deposito
from .constants import TIPOS_BOLSA, MOTIVOS_AJUSTE
from .forms import ClienteForm, VentaForm, VentaItemForm

logger = logging.getLogger(__name__)


# -------------------------
# Helpers
# -------------------------
def mes_key(value):
    """Normaliza cualquier fecha/datetime al primer día del mes (date)"""
    if value is None:
        return None
    if hasattr(value, "date"):
        value = value.date()
    return value.replace(day=1)


# -------------------------
# CLIENTES
# -------------------------
@login_required
def clientes_list(request):
    segmento = request.GET.get("segmento", "").strip()
    comuna = request.GET.get("comuna", "").strip()
    min_kilos = request.GET.get("min_kilos", "").strip()
    orden = request.GET.get("orden", "").strip()
    buscar = request.GET.get("buscar", "").strip()

    qs = (
        Cliente.objects.all()
        .prefetch_related(
            Prefetch(
                'ventas',
                queryset=Venta.objects.only('fecha', 'kilos_total', 'monto_total', 'cliente_id')
            )
        )
        .annotate(
            kilos_acumulados=Coalesce(
                Sum("ventas__kilos_total"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            gasto_total=Coalesce(
                Sum("ventas__monto_total"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            ultima=Max("ventas__fecha"),
            freq=Coalesce(Count("ventas"), Value(0)),
        )
    )

    if buscar:
        qs = qs.filter(nombre__icontains=buscar)

    if comuna:
        qs = qs.filter(comuna=comuna)

    if min_kilos:
        try:
            mk = Decimal(min_kilos)
            qs = qs.filter(kilos_acumulados__gte=mk)
        except Exception:
            pass

    if orden == "kilos_desc":
        qs = qs.order_by("-kilos_acumulados", "-id")
    elif orden == "kilos_asc":
        qs = qs.order_by("kilos_acumulados", "id")
    elif orden == "gasto_desc":
        qs = qs.order_by("-gasto_total", "-id")
    elif orden == "gasto_asc":
        qs = qs.order_by("gasto_total", "id")
    else:
        qs = qs.order_by("id")

    clientes = list(qs)

    if segmento:
        clientes = [c for c in clientes if getattr(c, "segmento", "") == segmento]

    paginator = Paginator(clientes, 25)
    page_number = request.GET.get('page', 1)

    try:
        clientes_paginados = paginator.page(page_number)
    except PageNotAnInteger:
        clientes_paginados = paginator.page(1)
    except EmptyPage:
        clientes_paginados = paginator.page(paginator.num_pages)

    comunas = (
        Cliente.objects.exclude(comuna="")
        .values_list("comuna", flat=True)
        .distinct()
        .order_by("comuna")
    )

    context = {
        "clientes": clientes_paginados,
        "comunas": comunas,
        "f": {
            "segmento": segmento,
            "comuna": comuna,
            "min_kilos": min_kilos,
            "orden": orden,
            "buscar": buscar,
        },
    }
    return render(request, "crm/clientes_list.html", context)


@login_required
def crear_cliente(request):
    if request.method == "POST":
        form = ClienteForm(request.POST)
        if form.is_valid():
            cliente = form.save()
            logger.info(f"Cliente #{cliente.id} creado por {request.user.username}: {cliente.nombre}")
            messages.success(request, "Cliente creado correctamente.")
            return redirect("crm:clientes_list")
    else:
        form = ClienteForm()
    return render(request, "crm/cliente_form.html", {"form": form, "modo": "crear"})


@login_required
def editar_cliente(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    if request.method == "POST":
        form = ClienteForm(request.POST, instance=cliente)
        if form.is_valid():
            form.save()
            logger.info(f"Cliente #{cliente.id} editado por {request.user.username}")
            messages.success(request, "Cliente actualizado correctamente.")
            return redirect("crm:clientes_list")
    else:
        form = ClienteForm(instance=cliente)

    return render(
        request,
        "crm/cliente_form.html",
        {"form": form, "modo": "editar", "cliente": cliente},
    )


@login_required
@require_POST
def borrar_cliente(request, cliente_id):
    cliente = get_object_or_404(Cliente, id=cliente_id)
    logger.warning(f"Cliente #{cliente.id} borrado por {request.user.username}: {cliente.nombre}")
    cliente.delete()
    messages.success(request, "Cliente borrado correctamente.")
    return redirect("crm:clientes_list")


# -------------------------
# VENTAS
# -------------------------
@login_required
def ventas_list(request):
    orden_id = request.GET.get("orden_id", "desc")
    tipo_documento = request.GET.get("tipo_documento", "").strip()
    canal = request.GET.get("canal", "").strip()
    min_kilos = request.GET.get("min_kilos", "").strip()
    max_kilos = request.GET.get("max_kilos", "").strip()
    buscar_cliente = request.GET.get("buscar_cliente", "").strip()
    modo_pago = request.GET.get("modo_pago", "").strip()
    deposito_estado = request.GET.get("deposito_estado", "").strip()
    repartidor = request.GET.get("repartidor", "").strip()

    qs = Venta.objects.select_related("cliente").all()

    if repartidor in ("jorge", "diego"):
        qs = qs.filter(repartidor=repartidor)

    if buscar_cliente:
        qs = qs.filter(cliente__nombre__icontains=buscar_cliente)

    if tipo_documento:
        qs = qs.filter(tipo_documento=tipo_documento)

    if canal:
        qs = qs.filter(canal=canal)

    if min_kilos:
        try:
            qs = qs.filter(kilos_total__gte=Decimal(min_kilos))
        except Exception:
            pass

    if max_kilos:
        try:
            qs = qs.filter(kilos_total__lte=Decimal(max_kilos))
        except Exception:
            pass

    # ✅ NUEVO: Filtro por modo de pago
    if modo_pago:
        qs = qs.filter(modo_pago=modo_pago)

    # ✅ NUEVO: Filtro por estado del depósito
    if deposito_estado:
        if deposito_estado == "depositado":
            # Solo ventas en efectivo con depósito realizado
            qs = qs.filter(modo_pago="efectivo", deposito_realizado=True)
        elif deposito_estado == "pendiente":
            # Solo ventas en efectivo con depósito pendiente
            qs = qs.filter(modo_pago="efectivo", deposito_realizado=False)

    if orden_id == "asc":
        qs = qs.order_by("id")
    else:
        qs = qs.order_by("-id")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get('page', 1)

    try:
        ventas_paginadas = paginator.page(page_number)
    except PageNotAnInteger:
        ventas_paginadas = paginator.page(1)
    except EmptyPage:
        ventas_paginadas = paginator.page(paginator.num_pages)

    context = {
        "ventas": ventas_paginadas,
        "f": {
            "orden_id": orden_id,
            "tipo_documento": tipo_documento,
            "canal": canal,
            "min_kilos": min_kilos,
            "max_kilos": max_kilos,
            "buscar_cliente": buscar_cliente,
            "modo_pago": modo_pago,
            "deposito_estado": deposito_estado,
            "repartidor": repartidor,
        },
    }
    return render(request, "crm/ventas_list.html", context)


@login_required
def venta_nueva(request):
    cliente_id = request.GET.get("cliente")
    initial = {}
    if cliente_id:
        try:
            initial["cliente"] = Cliente.objects.get(id=int(cliente_id))
        except Exception:
            pass

    if request.method == "POST":
        form = VentaForm(request.POST)
        if form.is_valid():
            venta = form.save()
            logger.info(
                f"Venta #{venta.id} creada por {request.user.username} - "
                f"Cliente: {venta.cliente.nombre}, Monto: ${venta.monto_total}"
            )
            messages.success(request, "Venta creada. Ahora agrega ítems.")
            return redirect("crm:venta_detalle", venta_id=venta.id)
        else:
            logger.warning(f"Error al crear venta por {request.user.username}: {form.errors}")
    else:
        form = VentaForm(initial=initial)

    return render(request, "crm/venta_form.html", {"form": form, "modo": "crear"})


@login_required
def venta_editar(request, venta_id):
    venta = get_object_or_404(Venta, id=venta_id)

    if request.method == "POST":
        form = VentaForm(request.POST, instance=venta)
        if form.is_valid():
            form.save()
            logger.info(f"Venta #{venta.id} editada por {request.user.username}")
            messages.success(request, "Venta actualizada correctamente.")
            return redirect("crm:venta_detalle", venta_id=venta.id)
    else:
        form = VentaForm(instance=venta)

    return render(
        request,
        "crm/venta_form.html",
        {"form": form, "modo": "editar", "venta": venta},
    )


@login_required
@require_POST
def venta_borrar(request, venta_id):
    venta = get_object_or_404(Venta, id=venta_id)
    logger.warning(f"Venta #{venta.id} borrada por {request.user.username}")
    venta.delete()
    messages.success(request, "Venta borrada.")
    return redirect("crm:ventas_list")


# -----------------
# VENTA DETALLE + ITEMS
# -----------------
@login_required
def venta_detalle(request, venta_id):
    venta = get_object_or_404(
        Venta.objects.select_related('cliente'),
        id=venta_id
    )
    items = venta.items.select_related("producto").all().order_by("id")
    form_item = VentaItemForm()

    productos = Producto.objects.filter(activo=True).order_by("nombre")

    return render(
        request,
        "crm/venta_detalle.html",
        {"venta": venta, "items": items, "form_item": form_item, "productos": productos},
    )


@login_required
@require_POST
def venta_item_agregar(request, venta_id):
    venta = get_object_or_404(Venta, id=venta_id)
    form = VentaItemForm(request.POST)
    if form.is_valid():
        item = form.save(commit=False)
        item.venta = venta
        item.save()
        logger.info(f"Item agregado a venta #{venta.id} por {request.user.username}")
        messages.success(request, "Ítem agregado.")
    else:
        messages.error(request, "No se pudo agregar el ítem. Revisa los campos.")
    return redirect("crm:venta_detalle", venta_id=venta.id)


@login_required
@require_POST
def venta_item_borrar(request, item_id):
    item = get_object_or_404(VentaItem, id=item_id)
    venta_id = item.venta_id
    logger.info(f"Item #{item.id} eliminado de venta #{venta_id} por {request.user.username}")
    item.delete()
    messages.success(request, "Ítem eliminado.")
    return redirect("crm:venta_detalle", venta_id=venta_id)


# -------------------------
# BUSCADORES
# -------------------------
@login_required
def buscar_cliente_telefono(request):
    cliente = None
    buscado = False

    if request.method == "POST":
        telefono = (request.POST.get("telefono") or "").strip()
        buscado = True
        if telefono:
            cliente = Cliente.objects.filter(telefono=telefono).first()

    return render(
        request,
        "crm/buscar_telefono.html",
        {"cliente": cliente, "buscado": buscado},
    )


# -------------------------
# DASHBOARD (KPIs + gráficos)
# -------------------------
@login_required
def dashboard(request):
    hoy = timezone.localdate()

    # ============================================
    # SISTEMA DE FILTROS DE PERÍODO
    # ============================================
    dias = request.GET.get('dias', None)
    desde_param = request.GET.get('desde', None)
    hasta_param = request.GET.get('hasta', None)

    if dias:
        try:
            desde = hoy - timedelta(days=int(dias))
            hasta = hoy
        except (ValueError, TypeError):
            inicio_mes = hoy.replace(day=1)
            desde = inicio_mes - relativedelta(months=12)
            hasta = hoy
    elif desde_param and hasta_param:
        desde = parse_date(desde_param) if desde_param else None
        hasta = parse_date(hasta_param) if hasta_param else None

        if not desde or not hasta:
            inicio_mes = hoy.replace(day=1)
            desde = inicio_mes - relativedelta(months=12)
            hasta = hoy
        elif desde > hasta:
            desde, hasta = hasta, desde
    else:
        inicio_mes = hoy.replace(day=1)
        desde = inicio_mes - relativedelta(months=12)
        hasta = hoy

    # ============================================
    # CONSULTAS CON EL PERÍODO FILTRADO
    # ============================================
    ventas = Venta.objects.filter(fecha__date__gte=desde, fecha__date__lte=hasta)
    ventas_normales = ventas.exclude(tipo_documento=Venta.TipoDocumento.NOTA_CREDITO)

    ingresos = ventas_normales.aggregate(s=Sum("monto_total"))["s"] or Decimal("0")
    kilos = ventas.aggregate(s=Sum("kilos_total"))["s"] or Decimal("0")
    n_ventas = ventas_normales.count()

    ticket_prom = Decimal("0")
    if n_ventas > 0:
        ticket_prom = (ingresos / Decimal(n_ventas)).quantize(Decimal("1"))

    # Serie mensual
    serie_qs = (
        ventas.annotate(mes=TruncMonth("fecha"))
        .values("mes")
        .annotate(
            ventas=Count("id"),
            kilos=Sum("kilos_total"),
            ingresos=Sum(
                "monto_total",
                filter=~Q(tipo_documento=Venta.TipoDocumento.NOTA_CREDITO),
            ),
        )
        .order_by("mes")
    )

    labels_mes, ingresos_mes, kilos_mes, ventas_mes = [], [], [], []
    for r in serie_qs:
        m = r["mes"]
        labels_mes.append(m.strftime("%m-%Y") if m else "")
        ingresos_mes.append(float(r["ingresos"] or 0))
        kilos_mes.append(float(r["kilos"] or 0))
        ventas_mes.append(int(r["ventas"] or 0))

    # Por canal
    por_canal_qs = (
        ventas.values("canal")
        .annotate(
            ventas=Count("id"),
            ingresos=Sum(
                "monto_total",
                filter=~Q(tipo_documento=Venta.TipoDocumento.NOTA_CREDITO),
            ),
        )
        .order_by("-ingresos")
    )
    canal_labels = [c["canal"] for c in por_canal_qs]
    canal_ingresos = [float(c["ingresos"] or 0) for c in por_canal_qs]

    # Top productos
    kilos_expr = ExpressionWrapper(
        F("cantidad") * Coalesce(F("producto__peso_kg"), Value(0)),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    top_productos_qs = (
        VentaItem.objects
        .filter(venta__fecha__date__gte=desde, venta__fecha__date__lte=hasta)
        .exclude(venta__tipo_documento=Venta.TipoDocumento.NOTA_CREDITO)
        .select_related('producto')
        .values("producto__id", "producto__nombre")
        .annotate(kilos=Sum(kilos_expr))
        .order_by("-kilos")[:15]
    )

    prod_labels = [p["producto__nombre"] for p in top_productos_qs]
    prod_kilos = [float(p["kilos"] or 0) for p in top_productos_qs]

    # ================================================
    # ✅ GRÁFICAS DIARIAS CON SELECTOR DE MES (ACTUAL)
    # ================================================
    mes_seleccionado_param = request.GET.get('mes_diario', None)

    if mes_seleccionado_param:
        try:
            anio, mes = mes_seleccionado_param.split('-')
            mes_seleccionado = date(int(anio), int(mes), 1)
        except (ValueError, AttributeError):
            mes_seleccionado = hoy.replace(day=1)
    else:
        mes_seleccionado = hoy.replace(day=1)

    inicio_mes_seleccionado = mes_seleccionado
    _, num_dias_mes = monthrange(mes_seleccionado.year, mes_seleccionado.month)
    fin_mes_seleccionado = mes_seleccionado.replace(day=num_dias_mes)

    # --------- (ACTUAL) ventas diarias ----------
    ventas_diarias = (
        Venta.objects
        .filter(
            fecha__date__gte=inicio_mes_seleccionado,
            fecha__date__lte=fin_mes_seleccionado
        )
        .exclude(tipo_documento=Venta.TipoDocumento.NOTA_CREDITO)
        .annotate(dia=TruncDate('fecha'))
        .values('dia')
        .annotate(
            cantidad=Count('id'),
            valor_total=Coalesce(
                Sum('monto_total'),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )
        )
        .order_by('dia')
    )

    ventas_map = {}
    for v in ventas_diarias:
        dia_num = v['dia'].day
        ventas_map[dia_num] = {
            'cantidad': v['cantidad'],
            'valor': float(v['valor_total'] or 0)
        }

    dias_labels = []
    cantidad_por_dia = []
    valor_por_dia = []

    for dia in range(1, num_dias_mes + 1):
        dias_labels.append(f"{dia:02d}/{mes_seleccionado.month:02d}")

        if dia in ventas_map:
            cantidad_por_dia.append(ventas_map[dia]['cantidad'])
            valor_por_dia.append(ventas_map[dia]['valor'])
        else:
            cantidad_por_dia.append(0)
            valor_por_dia.append(0)

    total_cantidad_mes = sum(cantidad_por_dia)
    total_valor_mes = sum(valor_por_dia)

    # ================================================
    # ✅ NUEVO: PRIMERAS COMPRAS (CLIENTES NUEVOS) DEL MES SELECCIONADO
    # ================================================
    base_mes = (
        Venta.objects
        .filter(
            fecha__date__gte=inicio_mes_seleccionado,
            fecha__date__lte=fin_mes_seleccionado
        )
        .exclude(tipo_documento=Venta.TipoDocumento.NOTA_CREDITO)
    )

    primera_fecha_subq = (
        Venta.objects
        .exclude(tipo_documento=Venta.TipoDocumento.NOTA_CREDITO)
        .filter(cliente_id=OuterRef("cliente_id"))
        .values("cliente_id")
        .annotate(p=Min("fecha"))
        .values("p")[:1]
    )

    ventas_first_time = (
        base_mes
        .annotate(primera_fecha=Subquery(primera_fecha_subq))
        .annotate(dia=TruncDate("fecha"), dia_primera=TruncDate("primera_fecha"))
        .filter(dia=F("dia_primera"))
        .values("dia")
        .annotate(
            cantidad=Count("cliente_id", distinct=True),
            valor_total=Coalesce(
                Sum("monto_total"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2)
            )
        )
        .order_by("dia")
    )

    first_map = {}
    for v in ventas_first_time:
        dia_num = v["dia"].day
        first_map[dia_num] = {
            "cantidad": v["cantidad"],
            "valor": float(v["valor_total"] or 0),
        }

    cantidad_first_por_dia = []
    valor_first_por_dia = []
    for dia in range(1, num_dias_mes + 1):
        if dia in first_map:
            cantidad_first_por_dia.append(first_map[dia]["cantidad"])
            valor_first_por_dia.append(first_map[dia]["valor"])
        else:
            cantidad_first_por_dia.append(0)
            valor_first_por_dia.append(0)

    total_first_cantidad_mes = sum(cantidad_first_por_dia)
    total_first_valor_mes = sum(valor_first_por_dia)

    # Meses disponibles (últimos 12 meses)
    meses_disponibles = []
    for i in range(12):
        primer_dia = hoy.replace(day=1) - relativedelta(months=i)
        meses_disponibles.append({
            'valor': primer_dia.strftime('%Y-%m'),
            'texto': primer_dia.strftime('%B %Y').capitalize()
        })

    context = {
        "desde": desde.strftime('%Y-%m-%d'),
        "hoy": hasta.strftime('%Y-%m-%d'),

        "kpi_ingresos": ingresos,
        "kpi_kilos": kilos,
        "kpi_n_ventas": n_ventas,
        "kpi_ticket": ticket_prom,

        "serie": list(serie_qs),
        "por_canal": list(por_canal_qs),
        "top_productos": list(top_productos_qs),

        "labels_mes_json": json.dumps(labels_mes),
        "ingresos_mes_json": json.dumps(ingresos_mes),
        "kilos_mes_json": json.dumps(kilos_mes),
        "ventas_mes_json": json.dumps(ventas_mes),

        "canal_labels_json": json.dumps(canal_labels),
        "canal_ingresos_json": json.dumps(canal_ingresos),

        "prod_labels_json": json.dumps(prod_labels),
        "prod_kilos_json": json.dumps(prod_kilos),

        # ✅ JSON diario actual
        "dias_labels_json": json.dumps(dias_labels),
        "cantidad_por_dia_json": json.dumps(cantidad_por_dia),
        "valor_por_dia_json": json.dumps(valor_por_dia),
        "mes_actual": mes_seleccionado.strftime("%B %Y"),
        "total_cantidad_mes": total_cantidad_mes,
        "total_valor_mes": int(total_valor_mes),

        # ✅ NUEVO JSON: first-time
        "cantidad_first_por_dia_json": json.dumps(cantidad_first_por_dia),
        "valor_first_por_dia_json": json.dumps(valor_first_por_dia),
        "total_first_cantidad_mes": total_first_cantidad_mes,
        "total_first_valor_mes": int(total_first_valor_mes),

        "meses_disponibles": meses_disponibles,
        "mes_seleccionado": mes_seleccionado.strftime('%Y-%m'),
    }
    return render(request, "crm/dashboard.html", context)


# -------------------------
# RESUMEN MENSUAL
# -------------------------
@login_required
def resumen_mensual(request):
    hoy = timezone.localdate()
    inicio_mes = hoy.replace(day=1)
    default_desde = inicio_mes - relativedelta(months=12)
    default_hasta = hoy

    desde_str = (request.GET.get("desde") or "").strip()
    hasta_str = (request.GET.get("hasta") or "").strip()

    desde = parse_date(desde_str) if desde_str else default_desde
    hasta = parse_date(hasta_str) if hasta_str else default_hasta

    if not desde:
        desde = default_desde
    if not hasta:
        hasta = default_hasta
    if desde > hasta:
        desde, hasta = hasta, desde

    # ─────────────────────────────────────────────────────────────────
    # Costo promedio ponderado POR MES
    # Para cada mes usamos las importaciones cuya fecha es <= ese mes,
    # ponderadas por sus kilos_ingresados (proxy del stock disponible
    # en ese período). Así meses vendidos con material a $405 no se
    # recalculan al precio nuevo de $227.
    # ─────────────────────────────────────────────────────────────────
    todas_importaciones = list(
        Importacion.objects.filter(activo=True).order_by("fecha")
        .values("fecha", "costo_por_kg", "kilos_ingresados", "merma_kg")
    )

    def costo_promedio_para_mes(mes_date):
        """
        Promedio ponderado por kilos de las importaciones
        disponibles hasta el mes dado (inclusive).
        """
        disponibles = [
            imp for imp in todas_importaciones
            if imp["fecha"] <= mes_date
        ]
        if not disponibles:
            disponibles = todas_importaciones[:1]
        if not disponibles:
            return Decimal("0")

        total_kilos = Decimal("0")
        total_valor = Decimal("0")
        for imp in disponibles:
            kilos_netos = (imp["kilos_ingresados"] or Decimal("0")) - (imp["merma_kg"] or Decimal("0"))
            costo = imp["costo_por_kg"] or Decimal("0")
            total_kilos += kilos_netos
            total_valor += kilos_netos * costo

        if total_kilos <= 0:
            return Decimal("0")
        return (total_valor / total_kilos).quantize(Decimal("0.01"))

    # Costo actual (para mostrarlo como referencia en el template)
    imp_activa = Importacion.objects.filter(activo=True).order_by("-fecha").first()
    costo_por_kg = imp_activa.costo_por_kg if imp_activa else Decimal("0")

    ventas_qs = (
        Venta.objects
        .filter(fecha__date__gte=desde, fecha__date__lte=hasta)
        .annotate(mes=TruncMonth("fecha", output_field=DateField()))
        .values("mes")
        .annotate(
            kilos=Coalesce(
                Sum("kilos_total"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            ventas_brutas=Coalesce(
                Sum("monto_total", filter=~Q(tipo_documento=Venta.TipoDocumento.NOTA_CREDITO)),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            notas_credito=Coalesce(
                Sum("monto_total", filter=Q(tipo_documento=Venta.TipoDocumento.NOTA_CREDITO)),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            cantidad_ventas=Count("id"),
        )
        .order_by("-mes")
    )

    gastos_qs = (
        GastoOperacional.objects
        .filter(fecha__gte=desde, fecha__lte=hasta)
        .annotate(mes=TruncMonth("fecha", output_field=DateField()))
        .values("mes")
        .annotate(
            gastos=Coalesce(
                Sum("monto_neto"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
    )

    gastos_map = {r["mes"]: (r["gastos"] or Decimal("0")) for r in gastos_qs}

    filas = []
    for r in ventas_qs:
        mes = r["mes"]
        kilos = r["kilos"] or Decimal("0")
        bruto = r["ventas_brutas"] or Decimal("0")
        notas = r["notas_credito"] or Decimal("0")

        ventas_netas = bruto - notas
        neto_real = (ventas_netas / Decimal("1.19")).quantize(Decimal("0.01"))

        costo_kg_mes = costo_promedio_para_mes(mes)
        costo = (kilos * costo_kg_mes).quantize(Decimal("0.01"))
        margen_bruto = neto_real - costo

        gastos = gastos_map.get(mes, Decimal("0"))
        utilidad = margen_bruto - gastos

        filas.append({
            "mes": mes,
            "kilos": kilos,
            "ventas_brutas": bruto,
            "notas_credito": notas,
            "ventas_netas": ventas_netas,
            "neto": neto_real,
            "cantidad_ventas": r["cantidad_ventas"],
            "costo": costo,
            "costo_kg_mes": costo_kg_mes,
            "margen_bruto": margen_bruto,
            "gastos": gastos,
            "utilidad": utilidad,
        })

    totales = {
        "kilos": sum((f["kilos"] for f in filas), Decimal("0")),
        "ventas_brutas": sum((f["ventas_brutas"] for f in filas), Decimal("0")),
        "notas_credito": sum((f["notas_credito"] for f in filas), Decimal("0")),
        "ventas_netas": sum((f["ventas_netas"] for f in filas), Decimal("0")),
        "neto": sum((f["neto"] for f in filas), Decimal("0")),
        "costo": sum((f["costo"] for f in filas), Decimal("0")),
        "margen_bruto": sum((f["margen_bruto"] for f in filas), Decimal("0")),
        "gastos": sum((f["gastos"] for f in filas), Decimal("0")),
        "utilidad": sum((f["utilidad"] for f in filas), Decimal("0")),
    }

    return render(
        request,
        "crm/resumen_mensual.html",
        {
            "filas": filas,
            "totales": totales,
            "desde": desde,
            "hasta": hasta,
            "hoy": hoy,
            "costo_por_kg": costo_por_kg,
            "costo_promedio_ponderado": costo_promedio_para_mes(hoy),
            "costo_minimo": min((imp["costo_por_kg"] for imp in todas_importaciones), default=Decimal("0")),
            "costo_maximo": max((imp["costo_por_kg"] for imp in todas_importaciones), default=Decimal("0")),
        },
    )


# -------------------------
# INVENTARIO
# -------------------------
@login_required
def inventario(request):
    from .services_inventario import stock_actual_post_inventario_kg
    try:
        hoy = timezone.localdate()

        try:
            dias = int(request.GET.get("dias", "30"))
            if dias <= 0:
                dias = 30
        except Exception:
            dias = 30

        dias_importacion = 90

        desde_consumo = hoy - timezone.timedelta(days=dias)

        # ✅ NUEVO: Usar kilos_restantes que ya descuenta la merma automáticamente
        importaciones_stats = Importacion.objects.aggregate(
            ingresados_bruto=Coalesce(
                Sum("kilos_ingresados"),
                Value(0),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            merma_total=Coalesce(
                Sum("merma_kg"),
                Value(0),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            disponibles=Coalesce(
                Sum("kilos_restantes"),  # ✅ Este campo ya tiene merma descontada
                Value(0),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )

        kilos_ingresados_bruto = importaciones_stats["ingresados_bruto"] or Decimal("0")
        merma_total = importaciones_stats["merma_total"] or Decimal("0")
        kilos_ingresados_neto = importaciones_stats["disponibles"] or Decimal("0")

        kilos_expr = ExpressionWrapper(
            F("cantidad") * F("producto__peso_kg"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )

        kilos_vendidos_total = (
            VentaItem.objects.exclude(venta__tipo_documento=Venta.TipoDocumento.NOTA_CREDITO)
            .aggregate(
                s=Coalesce(
                    Sum(kilos_expr),
                    Value(0),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )["s"]
            or Decimal("0")
        )

        # ✅ Stock = Kilos netos disponibles - Kilos vendidos
        stock_kg = (kilos_ingresados_neto - kilos_vendidos_total).quantize(Decimal("0.01"))

        # ✅ Stock actual post-inventario en kg, obtenido de los ajustes de bolsas
        stock_actual_post_inventario_kg = stock_actual_post_inventario_kg()

        kilos_vendidos_ventana = (
            VentaItem.objects.filter(venta__fecha__date__gte=desde_consumo)
            .exclude(venta__tipo_documento=Venta.TipoDocumento.NOTA_CREDITO)
            .aggregate(
                s=Coalesce(
                    Sum(kilos_expr),
                    Value(0),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )["s"]
            or Decimal("0")
        )

        consumo_diario = Decimal("0.00")
        if dias > 0:
            consumo_diario = (kilos_vendidos_ventana / Decimal(dias)).quantize(Decimal("0.01"))

        dias_stock = None
        fecha_reorden_estimada = None
        fecha_orden_sugerida = None
        dias_hasta_ordenar = None

        if consumo_diario > 0:
            dias_stock = (stock_kg / consumo_diario).quantize(Decimal("0.1"))
            fecha_reorden_estimada = hoy + timezone.timedelta(days=float(dias_stock))

            dias_hasta_ordenar = dias_stock - Decimal(str(dias_importacion))

            if dias_hasta_ordenar > 0:
                fecha_orden_sugerida = hoy + timezone.timedelta(days=float(dias_hasta_ordenar))
            else:
                fecha_orden_sugerida = hoy

        umbral_reorden_dias = dias_importacion
        alerta_reorden = (dias_stock is not None) and (dias_stock <= Decimal(str(umbral_reorden_dias)))

        context = {
            "hoy": hoy,
            "dias": dias,
            "desde_consumo": desde_consumo,
            "kilos_ingresados_bruto": kilos_ingresados_bruto,  # ✅ NUEVO
            "merma_total": merma_total,  # ✅ NUEVO
            "kilos_ingresados_neto": kilos_ingresados_neto,  # ✅ NUEVO
            "kilos_vendidos_total": kilos_vendidos_total,
            "stock_kg": stock_kg,  # ✅ Ahora refleja el stock real sin merma
            "stock_actual_post_inventario_kg": stock_actual_post_inventario_kg,  # ✅ Stock actual post-inventario en kg
            "kilos_vendidos_ventana": kilos_vendidos_ventana,
            "consumo_diario": consumo_diario,
            "dias_stock": dias_stock,
            "fecha_reorden_estimada": fecha_reorden_estimada,
            "dias_importacion": dias_importacion,
            "fecha_orden_sugerida": fecha_orden_sugerida,
            "dias_hasta_ordenar": dias_hasta_ordenar,
            "umbral_reorden_dias": umbral_reorden_dias,
            "alerta_reorden": alerta_reorden,
        }
        return render(request, "crm/inventario.html", context)

    except Exception as e:
        logger.error(
            f"Error crítico en inventario para usuario {request.user.username}: {e}",
            exc_info=True
        )
        messages.error(request, "Error al cargar inventario. Contacta al administrador.")
        return redirect("crm:dashboard")


def consumo_bolsas_view(request):
    from django.utils.dateparse import parse_date
    from .services_inventario import consumo_bolsas, inventario_bolsas_actual

    desde_str = request.GET.get("desde")
    hasta_str = request.GET.get("hasta")

    desde = parse_date(desde_str) if desde_str else None
    hasta = parse_date(hasta_str) if hasta_str else None

    data = consumo_bolsas(desde=desde, hasta=hasta)
    data_stock = inventario_bolsas_actual()

    for key in ["stock_actual_8_lav", "stock_actual_20_lav", "stock_actual_8_carbon",
                "stock_actual_20_carbon", "stock_actual_20_talco", "stock_actual_20_cafe",
                "stock_actual_8", "stock_actual_20"]:
        if key in data_stock:
            data[key] = data_stock[key]

    return render(request, "crm/consumo_bolsas.html", {
        "data": data,
        "desde": desde_str or "",
        "hasta": hasta_str or "",
    })


@login_required
def ajustar_inventario(request):
    """Vista para ajuste manual de inventario de bolsas."""

    TIPOS_BOLSA_DICT = {
        "8_lav": "Lavanda 8 kg",
        "20_lav": "Lavanda 20 kg",
        "8_carbon": "Carbón 8 kg",
        "20_carbon": "Carbón 20 kg",
        "20_talco": "Talco 20 kg",
        "20_cafe": "Café 20 kg",
    }

    if request.method == "POST":
        tipo_bolsa = request.POST.get("tipo_bolsa")
        stock_fisico = request.POST.get("stock_fisico")
        motivo = request.POST.get("motivo")
        observacion = request.POST.get("observacion", "").strip()

        # Validaciones que NO necesitan consultar inventario
        if tipo_bolsa not in TIPOS_BOLSA_DICT:
            messages.error(request, "Tipo de bolsa inválido.")
            return redirect("crm:consumo_bolsas")

        if motivo not in dict(MOTIVOS_AJUSTE).keys():
            messages.error(request, "Motivo inválido.")
            return redirect("crm:consumo_bolsas")

        # Validar stock_fisico entero >= 0
        try:
            stock_fisico = int(stock_fisico)
            if stock_fisico < 0:
                messages.error(request, "El stock físico no puede ser negativo.")
                return redirect("crm:consumo_bolsas")
        except (ValueError, TypeError):
            messages.error(request, "Stock físico debe ser un entero válido.")
            return redirect("crm:consumo_bolsas")

        # Cálculo y creación dentro de transacción atómica
        from .services_inventario import inventario_bolsas_actual
        with transaction.atomic():
            data = inventario_bolsas_actual()

            stocks_calculados = {
                "8_lav": data["stock_actual_8_lav"],
                "20_lav": data["stock_actual_20_lav"],
                "8_carbon": data["stock_actual_8_carbon"],
                "20_carbon": data["stock_actual_20_carbon"],
                "20_talco": data["stock_actual_20_talco"],
                "20_cafe": data["stock_actual_20_cafe"],
            }

            stock_sistema_before = stocks_calculados[tipo_bolsa]
            diferencia = stock_fisico - stock_sistema_before

            # No crear ajuste si diferencia es 0
            if diferencia == 0:
                messages.info(
                    request,
                    f"El inventario físico ({stock_fisico}) coincide con el sistema. No se requiere ajuste."
                )
                return redirect("crm:consumo_bolsas")

            InventarioBolsasAjuste.objects.create(
                usuario=request.user,
                tipo_bolsa=tipo_bolsa,
                stock_sistema_before=stock_sistema_before,
                stock_fisico=stock_fisico,
                diferencia=diferencia,
                motivo=motivo,
                observacion=observacion,
            )

        messages.success(
            request,
            f"Ajuste guardado: {TIPOS_BOLSA_DICT[tipo_bolsa]}, {stock_sistema_before} → {stock_fisico} bolsas ({diferencia})."
        )
        return redirect("crm:consumo_bolsas")

    # GET: mostrar formulario
    tipo = request.GET.get("tipo") or "20_lav"

    # Validar tipo en GET - si es inválido, redirigir
    if tipo and tipo not in TIPOS_BOLSA_DICT:
        messages.error(request, "Tipo de bolsa inválido.")
        return redirect("crm:consumo_bolsas")

    # Obtener stock para mostrar
    from .services_inventario import inventario_bolsas_actual
    data = inventario_bolsas_actual()
    stock_actual = data[f"stock_actual_{tipo}"]
    nombre_producto = TIPOS_BOLSA_DICT.get(tipo, "Lavanda 20 kg")

    context = {
        "tipo_seleccionado": tipo,
        "nombre_producto": nombre_producto,
        "stock_actual": stock_actual,
        "TIPOS_BOLSA_DICT": TIPOS_BOLSA_DICT,
        "motivo_choices": MOTIVOS_AJUSTE,
    }
    return render(request, "crm/ajustar_inventario.html", context)


# -------------------------
# EXPORTAR EXCEL DE VENTAS
# -------------------------
@login_required
def exportar_ventas_excel(request):
    import io
    from collections import defaultdict
    from django.http import HttpResponse
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    # --- Filtros opcionales por fecha (GET params: desde / hasta) ---
    desde_str = request.GET.get("desde", "").strip()
    hasta_str = request.GET.get("hasta", "").strip()

    qs = (
        Venta.objects.select_related("cliente")
        .prefetch_related("items__producto")
        .order_by("-fecha")
    )
    if desde_str:
        desde = parse_date(desde_str)
        if desde:
            qs = qs.filter(fecha__date__gte=desde)
    if hasta_str:
        hasta = parse_date(hasta_str)
        if hasta:
            qs = qs.filter(fecha__date__lte=hasta)

    # --- Estilos ---
    header_fill  = PatternFill("solid", start_color="1F4E79", end_color="1F4E79")
    alt_fill     = PatternFill("solid", start_color="EBF3FB", end_color="EBF3FB")
    header_font  = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    data_font    = Font(name="Arial", size=10)
    center       = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left         = Alignment(horizontal="left",   vertical="center", wrap_text=True)
    thin         = Side(style="thin", color="CCCCCC")
    borde        = Border(left=thin, right=thin, top=thin, bottom=thin)

    TIPO_DISPLAY = {
        "boleta":       "Boleta",
        "factura":      "Factura",
        "nota_credito": "Nota Crédito",
        "sin_doc":      "Sin documento",
    }
    CANAL_DISPLAY = {
        "instagram": "Instagram",
        "whatsapp":  "WhatsApp",
        "web":       "Web",
        "otro":      "Otro",
    }

    wb = openpyxl.Workbook()

    # ---- Hoja 1: Compras por día ----
    ws1 = wb.active
    ws1.title = "Compras por Día"

    headers1 = [
        "Fecha", "N° Boleta/Doc", "Tipo Documento",
        "Cliente", "Teléfono", "Email",
        "Dirección", "Comuna", "Canal",
        "Productos", "Kilos", "Monto Total ($)", "Observaciones",
    ]
    ws1.append(headers1)
    for cell in ws1[1]:
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = center
        cell.border    = borde

    resumen_comuna = defaultdict(lambda: {"ventas": 0, "monto": 0, "clientes": set()})
    resumen_dia    = defaultdict(lambda: {"ventas": 0, "monto": 0})

    for i, v in enumerate(qs, start=2):
        c   = v.cliente
        fecha_str    = v.fecha.strftime("%Y-%m-%d") if v.fecha else ""
        productos_str = " | ".join(
            f"{it.producto.nombre} x{it.cantidad}" for it in v.items.all()
        ) or "—"
        obs = (v.observaciones or c.observaciones or "").strip()
        monto = float(v.monto_total or 0)
        kilos = float(v.kilos_total or 0)
        comuna = c.comuna or "Sin comuna"

        ws1.append([
            fecha_str,
            v.numero_documento or "—",
            TIPO_DISPLAY.get(v.tipo_documento, v.tipo_documento),
            c.nombre,
            c.telefono or "",
            c.email or "",
            c.direccion or "",
            c.comuna or "",
            CANAL_DISPLAY.get(v.canal, v.canal),
            productos_str,
            kilos,
            monto,
            obs,
        ])
        for j, cell in enumerate(ws1[i], start=1):
            cell.font      = data_font
            cell.border    = borde
            cell.alignment = center if j in (1, 2, 3, 9, 11, 12) else left
            if i % 2 == 0:
                cell.fill = alt_fill

        # Acumulados para hojas 2 y 3
        resumen_comuna[comuna]["ventas"] += 1
        resumen_comuna[comuna]["monto"]  += monto
        resumen_comuna[comuna]["clientes"].add(c.nombre)
        resumen_dia[fecha_str]["ventas"] += 1
        resumen_dia[fecha_str]["monto"]  += monto

    # Formato moneda col 12
    for row in ws1.iter_rows(min_row=2, max_row=ws1.max_row, min_col=12, max_col=12):
        for cell in row:
            cell.number_format = "#,##0"

    col_widths1 = [12, 15, 18, 30, 14, 28, 35, 20, 12, 45, 10, 16, 40]
    for col, w in enumerate(col_widths1, start=1):
        ws1.column_dimensions[get_column_letter(col)].width = w
    ws1.row_dimensions[1].height = 30
    ws1.freeze_panes = "A2"

    # ---- Hoja 2: Resumen por comuna ----
    ws2 = wb.create_sheet("Resumen por Comuna")
    ws2.append(["Comuna", "N° Ventas", "Clientes Únicos", "Monto Total ($)"])
    for cell in ws2[1]:
        cell.font = header_font; cell.fill = header_fill
        cell.alignment = center; cell.border = borde

    for i, (comuna, d) in enumerate(sorted(resumen_comuna.items()), start=2):
        ws2.append([comuna, d["ventas"], len(d["clientes"]), d["monto"]])
        for cell in ws2[i]:
            cell.font = data_font; cell.border = borde; cell.alignment = center
            if i % 2 == 0: cell.fill = alt_fill

    for row in ws2.iter_rows(min_row=2, max_row=ws2.max_row, min_col=4, max_col=4):
        for c in row: c.number_format = "#,##0"
    for col, w in zip([1,2,3,4],[25,12,18,18]):
        ws2.column_dimensions[get_column_letter(col)].width = w
    ws2.freeze_panes = "A2"

    # ---- Hoja 3: Resumen por día ----
    ws3 = wb.create_sheet("Resumen por Día")
    ws3.append(["Fecha", "N° Ventas", "Monto Total ($)"])
    for cell in ws3[1]:
        cell.font = header_font; cell.fill = header_fill
        cell.alignment = center; cell.border = borde

    for i, (dia, d) in enumerate(sorted(resumen_dia.items(), reverse=True), start=2):
        ws3.append([dia, d["ventas"], d["monto"]])
        for cell in ws3[i]:
            cell.font = data_font; cell.border = borde; cell.alignment = center
            if i % 2 == 0: cell.fill = alt_fill

    for row in ws3.iter_rows(min_row=2, max_row=ws3.max_row, min_col=3, max_col=3):
        for c in row: c.number_format = "#,##0"
    for col, w in zip([1,2,3],[15,12,18]):
        ws3.column_dimensions[get_column_letter(col)].width = w
    ws3.freeze_panes = "A2"

    # --- Generar respuesta HTTP con el archivo ---
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    from datetime import date
    nombre_archivo = f"ventas_{date.today().strftime('%Y%m%d')}.xlsx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo}"'
    return response


@login_required
@require_POST
def deposito_registrar(request):
    """Registra un depósito y asocia ventas en efectivo pendientes."""
    ids = request.POST.getlist("ventas_ids")
    monto_texto = request.POST.get("monto_depositado", "").strip()

    try:
        monto_depositado = Decimal(monto_texto)
        if not monto_depositado.is_finite() or monto_depositado <= 0:
            raise ValueError
        monto_depositado = monto_depositado.quantize(Decimal("0.01"))
    except Exception:
        messages.error(request, "Ingresa un monto depositado válido y mayor que cero.")
        return redirect("crm:ventas_list")

    ids_limpios = []
    try:
        ids_limpios = list(dict.fromkeys(int(x) for x in ids))
    except (TypeError, ValueError):
        messages.error(request, "La selección de ventas no es válida.")
        return redirect("crm:ventas_list")

    if not ids_limpios:
        messages.error(request, "Selecciona al menos una venta pendiente en efectivo.")
        return redirect("crm:ventas_list")

    try:
        with transaction.atomic():
            ventas = list(
                Venta.objects.select_for_update()
                .filter(id__in=ids_limpios)
                .order_by("id")
            )

            if len(ventas) != len(ids_limpios):
                raise ValueError("Una o más ventas seleccionadas ya no existen.")

            for venta in ventas:
                if venta.modo_pago != "efectivo":
                    raise ValueError(f"La venta #{venta.id} no está pagada en efectivo.")
                if venta.deposito_realizado or venta.deposito_id is not None:
                    raise ValueError(f"La venta #{venta.id} ya está asociada a un depósito.")

            total_ventas = sum(
                (venta.monto_total or Decimal("0.00") for venta in ventas),
                Decimal("0.00"),
            ).quantize(Decimal("0.01"))

            deposito = Deposito.objects.create(
                monto_ventas=total_ventas,
                monto_depositado=monto_depositado,
                diferencia=(monto_depositado - total_ventas).quantize(Decimal("0.01")),
                registrado_por=request.user,
            )

            for venta in ventas:
                venta.deposito = deposito
                venta.deposito_realizado = True
                venta.save(update_fields=["deposito", "deposito_realizado"])

    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("crm:ventas_list")

    messages.success(
        request,
        f"Depósito #{deposito.id} registrado. "
        f"Ventas: ${total_ventas:,.0f}; depósito: ${monto_depositado:,.0f}; "
        f"diferencia: ${deposito.diferencia:,.0f}."
    )
    return redirect("crm:ventas_list")


@login_required
@require_POST
def venta_deposito_toggle(request, venta_id):
    """Bloquea el registro individual: ahora se usan depósitos múltiples."""
    messages.error(
        request,
        "El registro individual fue reemplazado por depósitos múltiples. "
        "Selecciona las ventas pendientes y registra el depósito desde la lista."
    )
    return redirect("crm:ventas_list")


@login_required
def depositos_list(request):
    """Lista los depósitos registrados."""
    from django.db.models import Count

    depositos = (
        Deposito.objects
        .select_related("registrado_por")
        .annotate(cantidad_ventas=Count("ventas"))
        .order_by("-fecha", "-id")
    )
    return render(
        request,
        "crm/depositos_list.html",
        {"depositos": depositos},
    )


@login_required
def deposito_editar(request, deposito_id):
    """Permite abrir y modificar un depósito con historial de cambios."""
    deposito = get_object_or_404(
        Deposito.objects.select_related("registrado_por"),
        pk=deposito_id,
    )

    if deposito.anulado:
        messages.error(request, f"El depósito #{deposito.id} está anulado y no se puede editar.")
        return redirect("crm:depositos_list")

    if request.method == "GET":
        ventas_asociadas = list(
            Venta.objects.filter(deposito_id=deposito.id)
            .select_related("cliente")
            .order_by("id")
        )
        ventas_disponibles = list(
            Venta.objects.filter(
                modo_pago="efectivo",
                deposito__isnull=True,
                deposito_realizado=False,
            )
            .select_related("cliente")
            .order_by("-fecha", "-id")
        )
        return render(
            request,
            "crm/deposito_editar.html",
            {
                "deposito": deposito,
                "ventas_asociadas": ventas_asociadas,
                "ventas_disponibles": ventas_disponibles,
            },
        )

    if request.method != "POST":
        return redirect("crm:depositos_list")

    monto_texto = request.POST.get("monto_depositado", "").strip()
    motivo = request.POST.get("motivo", "").strip()

    # Si el campo queda vacío, conservar el monto depositado actual.
    if monto_texto:
        try:
            monto_depositado = Decimal(monto_texto)
            if not monto_depositado.is_finite() or monto_depositado <= 0:
                raise ValueError
            monto_depositado = monto_depositado.quantize(Decimal("0.01"))
        except Exception:
            messages.error(request, "Ingresa un monto depositado válido y mayor que cero, o déjalo vacío para conservarlo.")
            return redirect("crm:deposito_editar", deposito_id=deposito_id)
    else:
        monto_depositado = None

    try:
        ids = list(dict.fromkeys(int(x) for x in request.POST.getlist("ventas_ids")))
        if any(x <= 0 for x in ids):
            raise ValueError
    except (TypeError, ValueError):
        messages.error(request, "La selección de ventas no es válida.")
        return redirect("crm:deposito_editar", deposito_id=deposito_id)

    if not ids:
        messages.error(request, "El depósito debe tener al menos una venta.")
        return redirect("crm:deposito_editar", deposito_id=deposito_id)

    try:
        with transaction.atomic():
            deposito = Deposito.objects.select_for_update().get(pk=deposito_id)

            if monto_depositado is None:
                monto_depositado = deposito.monto_depositado

            ventas_actuales = list(
                Venta.objects.select_for_update()
                .filter(deposito_id=deposito.id)
                .order_by("id")
            )
            ventas_seleccionadas = list(
                Venta.objects.select_for_update()
                .filter(id__in=ids)
                .order_by("id")
            )

            if len(ventas_seleccionadas) != len(ids):
                raise ValueError("Una o más ventas seleccionadas ya no existen.")

            actuales_por_id = {v.id: v for v in ventas_actuales}
            seleccionadas_por_id = {v.id: v for v in ventas_seleccionadas}

            for venta in ventas_seleccionadas:
                if venta.modo_pago != "efectivo":
                    raise ValueError(f"La venta #{venta.id} no está pagada en efectivo.")
                if venta.deposito_id not in (None, deposito.id):
                    raise ValueError(
                        f"La venta #{venta.id} ya pertenece a otro depósito."
                    )
                if venta.deposito_id is None and venta.deposito_realizado:
                    raise ValueError(
                        f"La venta #{venta.id} tiene un estado de depósito inconsistente."
                    )

            ids_actuales = set(actuales_por_id)
            ids_nuevos = set(seleccionadas_por_id)
            ids_agregados = sorted(ids_nuevos - ids_actuales)
            ids_quitados = sorted(ids_actuales - ids_nuevos)

            total_ventas = sum(
                (
                    v.monto_total or Decimal("0.00")
                    for v in ventas_seleccionadas
                ),
                Decimal("0.00"),
            ).quantize(Decimal("0.01"))
            diferencia = (monto_depositado - total_ventas).quantize(
                Decimal("0.01")
            )

            monto_ventas_anterior = deposito.monto_ventas
            monto_depositado_anterior = deposito.monto_depositado
            diferencia_anterior = deposito.diferencia

            for venta_id in ids_quitados:
                venta = actuales_por_id[venta_id]
                venta.deposito = None
                venta.deposito_realizado = False
                venta.save(update_fields=["deposito", "deposito_realizado"])

            for venta_id in ids_agregados:
                venta = seleccionadas_por_id[venta_id]
                venta.deposito = deposito
                venta.deposito_realizado = True
                venta.save(update_fields=["deposito", "deposito_realizado"])

            deposito.monto_ventas = total_ventas
            deposito.monto_depositado = monto_depositado
            deposito.diferencia = diferencia
            deposito.save(
                update_fields=["monto_ventas", "monto_depositado", "diferencia"]
            )

            hubo_cambios = (
                monto_ventas_anterior != total_ventas
                or monto_depositado_anterior != monto_depositado
                or diferencia_anterior != diferencia
                or bool(ids_agregados)
                or bool(ids_quitados)
                or bool(motivo)
            )

            if hubo_cambios:
                DepositoEdicion.objects.create(
                    deposito=deposito,
                    monto_ventas_anterior=monto_ventas_anterior,
                    monto_ventas_nuevo=total_ventas,
                    monto_depositado_anterior=monto_depositado_anterior,
                    monto_depositado_nuevo=monto_depositado,
                    diferencia_anterior=diferencia_anterior,
                    diferencia_nueva=diferencia,
                    ventas_agregadas=", ".join(map(str, ids_agregados)),
                    ventas_quitadas=", ".join(map(str, ids_quitados)),
                    motivo=motivo,
                    modificado_por=request.user,
                )

    except Deposito.DoesNotExist:
        messages.error(request, "El depósito solicitado no existe.")
        return redirect("crm:depositos_list")
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("crm:deposito_editar", deposito_id=deposito_id)

    messages.success(request, f"Depósito #{deposito.id} actualizado correctamente.")
    return redirect("crm:depositos_list")


@login_required
@require_POST
def deposito_anular(request, deposito_id):
    """Anula un depósito, libera sus ventas y conserva el historial."""
    try:
        with transaction.atomic():
            deposito = get_object_or_404(
                Deposito.objects.select_for_update(),
                pk=deposito_id,
            )

            if deposito.anulado:
                messages.warning(request, f"El depósito #{deposito.id} ya estaba anulado.")
                return redirect("crm:depositos_list")

            ventas = list(
                Venta.objects.select_for_update()
                .filter(deposito_id=deposito.id)
                .order_by("id")
            )

            ids_ventas = [venta.id for venta in ventas]

            # Liberar todas las ventas del depósito.
            for venta in ventas:
                venta.deposito = None
                venta.deposito_realizado = False
                venta.save(update_fields=["deposito", "deposito_realizado"])

            # Registrar la anulación sin borrar el depósito.
            deposito.anulado = True
            deposito.fecha_anulacion = timezone.now()
            deposito.anulado_por = request.user
            deposito.save(update_fields=[
                "anulado", "fecha_anulacion", "anulado_por"
            ])

            # Registrar el movimiento en el historial existente.
            DepositoEdicion.objects.create(
                deposito=deposito,
                monto_ventas_anterior=deposito.monto_ventas,
                monto_ventas_nuevo=deposito.monto_ventas,
                monto_depositado_anterior=deposito.monto_depositado,
                monto_depositado_nuevo=deposito.monto_depositado,
                diferencia_anterior=deposito.diferencia,
                diferencia_nueva=deposito.diferencia,
                ventas_agregadas="",
                ventas_quitadas=", ".join(map(str, ids_ventas)),
                motivo="Depósito anulado. Ventas liberadas para volver a asociarlas.",
                modificado_por=request.user,
            )

    except Exception as exc:
        from django.http import Http404
        if isinstance(exc, Http404):
            messages.error(request, "El depósito solicitado no existe.")
            return redirect("crm:depositos_list")
        raise

    messages.success(
        request,
        f"Depósito #{deposito.id} anulado. "
        f"Se liberaron {len(ids_ventas)} ventas.",
    )
    return redirect("crm:depositos_list")


@login_required
def deposito_historial(request, deposito_id):
    """Muestra el historial de cambios de un depósito."""
    deposito = get_object_or_404(Deposito, pk=deposito_id)
    historial = deposito.historial.select_related("modificado_por").all()
    return render(
        request,
        "crm/deposito_historial.html",
        {"deposito": deposito, "historial": historial},
    )
