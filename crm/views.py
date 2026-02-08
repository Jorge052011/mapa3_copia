# crm/views.py
from decimal import Decimal
from datetime import timedelta, date
import json
import logging
from calendar import monthrange

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import (
    Sum, Count, Max, Min, Value, DecimalField, Q, DateField, F,
    ExpressionWrapper, Prefetch, OuterRef, Subquery,
)
from django.db.models.functions import Coalesce, TruncMonth, TruncDate
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .models import Cliente, Venta, VentaItem, Producto, Importacion, GastoOperacional
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

    qs = Venta.objects.select_related("cliente").all()

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
            desde = inicio_mes - timedelta(days=180)
            hasta = hoy
    elif desde_param and hasta_param:
        desde = parse_date(desde_param) if desde_param else None
        hasta = parse_date(hasta_param) if hasta_param else None

        if not desde or not hasta:
            inicio_mes = hoy.replace(day=1)
            desde = inicio_mes - timedelta(days=180)
            hasta = hoy
        elif desde > hasta:
            desde, hasta = hasta, desde
    else:
        inicio_mes = hoy.replace(day=1)
        desde = inicio_mes - timedelta(days=180)
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
        F("cantidad") * F("producto__peso_kg"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    top_productos_qs = (
        VentaItem.objects
        .filter(venta__fecha__date__gte=desde, venta__fecha__date__lte=hasta)
        .exclude(venta__tipo_documento=Venta.TipoDocumento.NOTA_CREDITO)
        .select_related('producto')
        .values("producto__nombre")
        .annotate(kilos=Sum(kilos_expr))
        .order_by("-kilos")[:10]
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
        fecha_mes = hoy - timedelta(days=30 * i)
        primer_dia = fecha_mes.replace(day=1)
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
    default_desde = inicio_mes - timedelta(days=180)
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
        neto_real = ventas_netas

        costo = (kilos * (costo_por_kg or Decimal("0"))).quantize(Decimal("0.01"))
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
        },
    )


# -------------------------
# INVENTARIO
# -------------------------
@login_required
def inventario(request):
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
    from .services_inventario import consumo_bolsas

    desde_str = request.GET.get("desde")
    hasta_str = request.GET.get("hasta")

    desde = parse_date(desde_str) if desde_str else None
    hasta = parse_date(hasta_str) if hasta_str else None

    data = consumo_bolsas(desde=desde, hasta=hasta)

    return render(request, "crm/consumo_bolsas.html", {
        "data": data,
        "desde": desde_str or "",
        "hasta": hasta_str or "",
    })
