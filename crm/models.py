# crm/models.py
from decimal import Decimal

from django.conf import settings
from django.db import models, transaction
from django.db.models import Sum, F, DecimalField, ExpressionWrapper
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator

from .constants import MOTIVOS_AJUSTE


class Cliente(models.Model):
    nombre = models.CharField(max_length=120)
    telefono = models.CharField(max_length=30, blank=True, db_index=True)
    email = models.EmailField(blank=True, db_index=True)
    comuna = models.CharField(max_length=80, blank=True)
    direccion = models.CharField(max_length=200, blank=True)
    observaciones = models.TextField(blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    # ✅ NUEVO: Meta con índices
    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        indexes = [
            models.Index(fields=['telefono']),
            models.Index(fields=['email']),
            models.Index(fields=['nombre']),
            models.Index(fields=['-creado_en']),
        ]

    @property
    def segmento(self):
        from .services import segmentar_cliente
        return segmentar_cliente(self)[0]

    @property
    def segmento_color(self):
        from .services import segmentar_cliente
        return segmentar_cliente(self)[1]

    def __str__(self):
        return f"{self.nombre} ({self.telefono or self.email or 'sin contacto'})"


class Producto(models.Model):
    sku = models.CharField(max_length=40, unique=True)
    nombre = models.CharField(max_length=140)
    peso_kg = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        editable=False,
        help_text="Peso calculado desde la receta de materiales.",
    )

    precio_sugerido = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Precio sugerido de venta (opcional)."
    )

    activo = models.BooleanField(default=True)

    # ✅ NUEVO: Meta con índices
    class Meta:
        verbose_name = "Producto"
        verbose_name_plural = "Productos"
        indexes = [
            models.Index(fields=['sku']),
            models.Index(fields=['activo', 'nombre']),
        ]

    def __str__(self):
        return f"{self.nombre} [{self.sku}]"

    def recalcular_peso(self, guardar=True):
        """Recalcula el peso desde la receta y lo mantiene como caché."""
        from django.db.models import F, Sum, DecimalField, ExpressionWrapper

        if not self.pk:
            return None

        expresion = ExpressionWrapper(
            F("cantidad") * F("material__peso_kg"),
            output_field=DecimalField(max_digits=12, decimal_places=4),
        )
        total = self.componentes.aggregate(total=Sum(expresion))["total"]
        nuevo = (
            total.quantize(Decimal("0.01")) if total is not None else None
        )
        self.peso_kg = nuevo
        if guardar and nuevo != Producto.objects.filter(pk=self.pk).values_list(
            "peso_kg", flat=True
        ).first():
            self.save(update_fields=["peso_kg"])
        return nuevo


class Material(models.Model):
    """Insumo físico (bolsa) que compone los productos."""

    codigo = models.CharField(max_length=20, unique=True, verbose_name="Código")
    nombre = models.CharField(max_length=100, verbose_name="Nombre")
    peso_kg = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        verbose_name="Peso por bolsa (kg)",
    )
    activo = models.BooleanField(default=True, verbose_name="Activo")

    class Meta:
        ordering = ["nombre", "codigo"]
        verbose_name = "Material"
        verbose_name_plural = "Materiales"
        constraints = [
            models.CheckConstraint(
                check=models.Q(peso_kg__gt=0),
                name="material_peso_kg_positivo",
            ),
        ]

    def __str__(self):
        return f"{self.nombre} ({self.peso_kg:g} kg)"

    def clean(self):
        super().clean()
        if self.peso_kg is not None and self.peso_kg <= 0:
            raise ValidationError(
                {"peso_kg": "El peso por bolsa debe ser mayor a 0."}
            )
        if self.pk:
            anterior = Material.objects.filter(pk=self.pk).values_list(
                "codigo", flat=True
            ).first()
            if anterior is not None and anterior != self.codigo:
                raise ValidationError(
                    {"codigo": "El código de un material no se puede modificar."}
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ProductoMaterial(models.Model):
    """Receta: cantidad de bolsas de un material que usa cada producto."""

    producto = models.ForeignKey(
        Producto,
        on_delete=models.CASCADE,
        related_name="componentes",
    )
    material = models.ForeignKey(
        Material,
        to_field="codigo",
        db_column="material_codigo",
        on_delete=models.PROTECT,
        related_name="productos",
    )
    cantidad = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Cantidad de bolsas de este material por producto.",
    )

    class Meta:
        ordering = ["producto", "material"]
        verbose_name = "Material del producto"
        verbose_name_plural = "Materiales del producto"
        constraints = [
            models.UniqueConstraint(
                fields=["producto", "material"],
                name="uniq_producto_material",
            ),
            models.CheckConstraint(
                check=models.Q(cantidad__gte=1),
                name="pm_cantidad_min_1",
            ),
        ]

    def __str__(self):
        return f"{self.producto.sku}: {self.cantidad} x {self.material.codigo}"

    def clean(self):
        super().clean()
        if self.cantidad is not None and self.cantidad < 1:
            raise ValidationError(
                {"cantidad": "La cantidad debe ser al menos 1."}
            )
        if self.material_id and not self.pk and not self.material.activo:
            raise ValidationError(
                {"material": "No se pueden agregar materiales inactivos a una receta."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


def productos_vendibles_qs():
    """Productos activos, con receta y todos sus materiales activos."""
    return (
        Producto.objects.filter(activo=True, peso_kg__gt=0)
        .filter(componentes__isnull=False)
        .exclude(componentes__material__activo=False)
        .distinct()
    )


class Venta(models.Model):
    class Canal(models.TextChoices):
        INSTAGRAM = "instagram", "Instagram"
        WHATSAPP = "whatsapp", "WhatsApp"
        WEB = "web", "Web"
        OTRO = "otro", "Otro"

    class TipoDocumento(models.TextChoices):
        SIN_DOC = "sin_doc", "Sin documento"
        BOLETA = "boleta", "Boleta"
        FACTURA = "factura", "Factura"
        NOTA_CREDITO = "nota_credito", "Nota crédito"

    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="ventas")
    fecha = models.DateTimeField(default=timezone.now, db_index=True)
    canal = models.CharField(max_length=20, choices=Canal.choices, default=Canal.OTRO)

    kilos_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    monto_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    observaciones = models.TextField(blank=True)

    REPARTIDOR_CHOICES = [
        ("jorge", "Jorge"),
        ("diego", "Diego"),
    ]

    MODO_PAGO_CHOICES = [
        ("tarjeta", "Tarjeta"),
        ("transferencia", "Transferencia"),
        ("efectivo", "Efectivo"),
        ("pago_web", "Pago web"),
        ("link_pago", "Link de pago"),
    ]

    repartidor = models.CharField(
        "Repartidor",
        max_length=20,
        choices=REPARTIDOR_CHOICES,
        blank=True,
        null=True,
        help_text="Seleccione el repartidor de la venta",
    )

    modo_pago = models.CharField(
        "Modo de pago",
        max_length=20,
        choices=MODO_PAGO_CHOICES,
        blank=True,
        null=True,
        help_text="Seleccione el modo de pago de la venta",
    )

    deposito_realizado = models.BooleanField(
        "Depósito realizado",
        default=False,
        help_text="Indica si el depósito en efectivo ha sido realizado",
    )

    deposito = models.ForeignKey(
        "Deposito",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ventas",
        verbose_name="Depósito asociado",
    )

    tipo_documento = models.CharField(
        max_length=20,
        choices=TipoDocumento.choices,
        default=TipoDocumento.SIN_DOC,
        db_index=True,
    )
    numero_documento = models.CharField(max_length=30, blank=True, db_index=True)

    venta_origen = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="notas_credito",
        verbose_name="Venta de origen",
        help_text="Venta original cuando este documento es una nota de crédito.",
    )
    devolucion_fisica = models.BooleanField(
        default=False,
        verbose_name="Devolución física",
        help_text="Solo una nota de crédito con devolución física restituye bolsas al inventario.",
    )

    # ✅ NUEVO: Meta con índices
    class Meta:
        verbose_name = "Venta"
        verbose_name_plural = "Ventas"
        constraints = [
            models.UniqueConstraint(
                fields=["tipo_documento", "numero_documento"],
                name="uniq_tipo_numero_documento",
                condition=~models.Q(numero_documento=""),
            )
        ]
        indexes = [
            models.Index(fields=['-fecha', 'cliente']),
            models.Index(fields=['tipo_documento', 'fecha']),
            models.Index(fields=['-fecha']),
            models.Index(fields=['cliente', '-fecha']),
        ]

    def __str__(self):
        return f"Venta #{self.id} - {self.cliente}"

    def recalcular_monto_total(self, guardar=True):
        expr = ExpressionWrapper(
            F("cantidad") * F("precio_unitario"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        total_items = self.items.aggregate(s=Sum(expr))["s"] or Decimal("0.00")
        self.monto_total = total_items
        if guardar:
            self.save(update_fields=["monto_total"])
        return self.monto_total

    @property
    def monto_neto(self):
        total = self.monto_total or Decimal("0")
        return (total / Decimal("1.19")).quantize(Decimal("0.01"))

    @property
    def iva(self):
        return (self.monto_total - self.monto_neto).quantize(Decimal("0.01"))

    @property
    def costo_estimado(self):
        from .services import costo_promedio_kg
        cpk = costo_promedio_kg() or Decimal("0")
        kilos = self.kilos_total or Decimal("0")
        return (kilos * cpk).quantize(Decimal("0.01"))

    @property
    def margen(self):
        return (self.monto_neto - self.costo_estimado).quantize(Decimal("0.01"))

    @property
    def margen_pct(self):
        neto = self.monto_neto
        if neto <= 0:
            return Decimal("0.00")
        return ((self.margen / neto) * Decimal("100")).quantize(Decimal("0.01"))

    @property
    def kilos_calculados(self):
        total = Decimal("0.00")
        for it in self.items.select_related("producto"):
            peso = it.producto.peso_kg or Decimal("0")
            total += it.cantidad * peso
        return total


class Deposito(models.Model):
    fecha = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        verbose_name="Fecha del depósito",
    )
    monto_ventas = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Total de ventas asociadas",
    )
    monto_depositado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Monto efectivamente depositado",
    )
    diferencia = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Diferencia del depósito",
    )
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="depositos_ventas",
        verbose_name="Registrado por",
    )

    anulado = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Depósito anulado",
    )
    fecha_anulacion = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fecha de anulación",
    )
    anulado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="depositos_anulados",
        null=True,
        blank=True,
        verbose_name="Anulado por",
    )

    class Meta:
        ordering = ["-fecha", "-id"]
        verbose_name = "Depósito"
        verbose_name_plural = "Depósitos"

    def __str__(self):
        return f"Depósito #{self.pk} - ${self.monto_depositado}"


class VentaItem(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name="items")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    item_origen = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="items_devolucion",
        verbose_name="Ítem de origen",
        help_text="Ítem original cuando esta línea pertenece a una devolución física.",
    )
    cantidad = models.PositiveIntegerField(default=1)
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)

    # ✅ NUEVO: Meta con índices
    class Meta:
        verbose_name = "Item de Venta"
        verbose_name_plural = "Items de Venta"
        indexes = [
            models.Index(fields=['venta', 'producto']),
            models.Index(fields=['producto']),
        ]

    @property
    def subtotal(self):
        return self.cantidad * self.precio_unitario

    def clean(self):
        super().clean()
        if not self._state.adding:
            return
        producto = self.producto
        if producto is None:
            raise ValidationError({"producto": "Debe seleccionar un producto."})
        if not producto.activo:
            raise ValidationError({"producto": "El producto está inactivo."})
        componentes = list(producto.componentes.select_related("material"))
        if not componentes:
            raise ValidationError(
                {"producto": "El producto no tiene una receta de bolsas definida."}
            )
        inactivos = [
            c.material.codigo for c in componentes if not c.material.activo
        ]
        if inactivos:
            raise ValidationError(
                {"producto": "La receta usa materiales inactivos: " + ", ".join(inactivos)}
            )
        if producto.peso_kg is None or producto.peso_kg <= 0:
            raise ValidationError(
                {"producto": "El producto no tiene un peso calculado válido."}
            )

    def save(self, *args, **kwargs):
        if self._state.adding:
            self.full_clean()
        super().save(*args, **kwargs)


class Importacion(models.Model):
    fecha = models.DateField(default=timezone.localdate)

    descripcion = models.CharField(
        max_length=200,
        blank=True,
        help_text="Ej: Contenedor Tianjin Sep-2025"
    )

    kilos_ingresados = models.DecimalField(max_digits=12, decimal_places=2)

    merma_kg = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Merma total en kilos (roturas, humedad, etc.)"
    )

    costo_total = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Costo total CIF + gastos asociados (SIN IVA)"
    )

    costo_por_kg = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        editable=False
    )
    
    kilos_restantes = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        editable=False
    )

    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    # ✅ NUEVO: Meta con índices
    class Meta:
        ordering = ["-fecha"]
        verbose_name = "Importación"
        verbose_name_plural = "Importaciones"
        indexes = [
            models.Index(fields=['activo', '-fecha']),
        ]

    def __str__(self):
        return f"{self.fecha} - {self.descripcion or 'Importación'} ({self.kilos_ingresados} kg)"

    def clean(self):
        """✅ Validaciones que Django ejecuta automáticamente en forms y admin."""
        super().clean()
        
        if self.kilos_ingresados is None or self.kilos_ingresados <= 0:
            raise ValidationError({
                'kilos_ingresados': 'Debe ser mayor a 0.'
            })

        if self.merma_kg is None or self.merma_kg < 0:
            raise ValidationError({
                'merma_kg': 'No puede ser negativa.'
            })

        if self.merma_kg >= self.kilos_ingresados:
            raise ValidationError({
                'merma_kg': f'No puede ser mayor o igual a kilos ingresados ({self.kilos_ingresados} kg).'
            })

        if self.costo_total is None or self.costo_total < 0:
            raise ValidationError({
                'costo_total': 'No puede ser negativo.'
            })

    def save(self, *args, **kwargs):
        """✅ Calcula automáticamente costo_por_kg y kilos_restantes"""
        self.full_clean()
        
        kilos_netos = self.kilos_ingresados - self.merma_kg
        
        if kilos_netos <= 0:
            raise ValidationError(
                f"Los kilos netos ({kilos_netos} kg) deben ser mayores a 0. "
                f"Revisa los kilos ingresados ({self.kilos_ingresados} kg) y la merma ({self.merma_kg} kg)."
            )
        
        self.costo_por_kg = (self.costo_total / kilos_netos).quantize(Decimal("0.01"))
        
        # ✅ SIMPLIFICADO: kilos_restantes siempre es kilos_netos
        # (el descuento de ventas se hace a nivel agregado en la vista)
        if not self.pk:
            # Nueva importación
            self.kilos_restantes = kilos_netos
        else:
            # Edición: recalcular desde el estado anterior
            try:
                anterior = Importacion.objects.get(pk=self.pk)
                kilos_netos_anterior = anterior.kilos_ingresados - anterior.merma_kg
                kilos_vendidos = kilos_netos_anterior - anterior.kilos_restantes
                
                nuevo_restante = kilos_netos - kilos_vendidos
                
                if nuevo_restante < 0:
                    raise ValidationError(
                        f"No puedes reducir el stock a {nuevo_restante} kg. "
                        f"Ya se han vendido {kilos_vendidos} kg de esta importación."
                    )
                
                self.kilos_restantes = nuevo_restante
            except Importacion.DoesNotExist:
                self.kilos_restantes = kilos_netos
        
        super().save(*args, **kwargs)


class IngresoMaterial(models.Model):
    """Ingreso de bolsas asociado a una importación real."""

    fecha = models.DateField(default=timezone.localdate, db_index=True)
    importacion = models.ForeignKey(
        Importacion,
        on_delete=models.PROTECT,
        related_name="ingresos_material",
        null=True,
        blank=True,
        verbose_name="Importación",
        help_text="Obligatoria para ingresos nuevos.",
    )
    tipo_bolsa = models.ForeignKey(
        Material,
        to_field="codigo",
        db_column="tipo_bolsa",
        on_delete=models.PROTECT,
        related_name="ingresos_material",
        verbose_name="Tipo de bolsa",
    )
    cantidad = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Cantidad de bolsas recibidas (mayor a 0).",
    )
    control_contenedor = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Control por contenedor",
        help_text="Incluye este ingreso en el stock operativo de su importación.",
    )
    recepcion_key = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Clave de recepción",
        help_text="Agrupa las líneas de una misma recepción e impide duplicados.",
    )
    observacion = models.TextField(blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ingresos_material",
        verbose_name="Registrado por",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        verbose_name = "Ingreso de material"
        verbose_name_plural = "Ingresos de material"
        constraints = [
            models.UniqueConstraint(
                fields=["recepcion_key", "tipo_bolsa"],
                condition=models.Q(recepcion_key__isnull=False),
                name="uniq_recepcion_key_tipo_bolsa",
            ),
        ]
        indexes = [
            models.Index(fields=["importacion", "tipo_bolsa", "control_contenedor"]),
        ]

    def __str__(self):
        return (
            f"{self.fecha} - {self.tipo_bolsa.nombre} "
            f"({self.cantidad} bolsas)"
        )

    def clean(self):
        super().clean()

        if self.cantidad is not None and self.cantidad <= 0:
            raise ValidationError({
                "cantidad": "La cantidad debe ser mayor a 0."
            })

        if not self.pk and self.tipo_bolsa_id and not self.tipo_bolsa.activo:
            raise ValidationError({
                "tipo_bolsa": "No se puede recibir un material inactivo."
            })

        if self.control_contenedor and self.importacion_id is None:
            raise ValidationError({
                "importacion": "Un ingreso del control por contenedor debe tener importación."
            })

        if (
            not self.pk
            and self.control_contenedor
            and self.importacion_id
        ):
            control = ControlTrazabilidad.objects.filter(pk=1).first()
            if (
                control
                and self.importacion.fecha < control.fecha_inicio
            ):
                raise ValidationError({
                    "importacion": (
                        "La importación es anterior al inicio del control y está "
                        "cerrada para nuevas recepciones."
                    )
                })

        if self.importacion_id is None:
            if not self.pk:
                raise ValidationError({
                    "importacion": "Debe seleccionar una importación real."
                })

            # Solo los registros históricos ya existentes pueden quedar sin
            # importación. Un ingreso real no puede perder su importación.
            if not IngresoMaterial.objects.filter(
                pk=self.pk, importacion__isnull=True
            ).exists():
                raise ValidationError({
                    "importacion": "Debe seleccionar una importación real."
                })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ControlTrazabilidad(models.Model):
    """Configuración única del control operativo por contenedor."""

    fecha_inicio = models.DateField(
        default=timezone.localdate,
        verbose_name="Fecha de inicio",
    )
    activo = models.BooleanField(default=True)
    nota = models.TextField(blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="controles_trazabilidad",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Control de trazabilidad"
        verbose_name_plural = "Control de trazabilidad"

    def __str__(self):
        return f"Control desde {self.fecha_inicio} ({'activo' if self.activo else 'inactivo'})"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        return cls.objects.filter(pk=1).first()


class MovimientoBolsasVenta(models.Model):
    """Libro append-only de asignaciones, devoluciones y reversiones por contenedor."""

    class TipoMovimiento(models.TextChoices):
        ASIGNACION = "asignacion", "Asignación a venta"
        DEVOLUCION = "devolucion", "Devolución física"
        REVERSION = "reversion", "Reversión"

    venta_item = models.ForeignKey(
        VentaItem,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movimientos_bolsas",
    )
    venta_item_ref = models.IntegerField(null=True, blank=True)
    venta_ref = models.IntegerField(null=True, blank=True)
    sku = models.CharField(max_length=40, blank=True)
    importacion = models.ForeignKey(
        Importacion,
        on_delete=models.PROTECT,
        related_name="movimientos_bolsas",
    )
    tipo_bolsa = models.ForeignKey(
        Material,
        to_field="codigo",
        db_column="tipo_bolsa",
        on_delete=models.PROTECT,
        related_name="movimientos_venta",
        verbose_name="Tipo de bolsa",
    )
    cantidad = models.IntegerField(
        help_text="Positiva para asignaciones; negativa para devoluciones o reversiones."
    )
    tipo_movimiento = models.CharField(
        max_length=20,
        choices=TipoMovimiento.choices,
        default=TipoMovimiento.ASIGNACION,
        db_index=True,
    )
    movimiento_origen = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversiones",
    )
    idempotency_key = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        help_text="Evita registrar dos veces el mismo movimiento.",
    )
    observacion = models.TextField(blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movimientos_bolsas",
    )
    creado_en = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-creado_en", "-id"]
        verbose_name = "Movimiento de bolsas por contenedor"
        verbose_name_plural = "Movimientos de bolsas por contenedor"
        constraints = [
            models.CheckConstraint(
                check=~models.Q(cantidad=0),
                name="movimiento_bolsas_cantidad_no_cero",
            ),
        ]
        indexes = [
            models.Index(fields=["importacion", "tipo_bolsa"]),
            models.Index(fields=["venta_item", "tipo_bolsa"]),
        ]

    def __str__(self):
        return (
            f"{self.get_tipo_movimiento_display()} {self.cantidad:+d} "
            f"{self.tipo_bolsa.nombre} - {self.importacion}"
        )

    def clean(self):
        super().clean()
        if self.tipo_movimiento == self.TipoMovimiento.ASIGNACION and self.cantidad <= 0:
            raise ValidationError({"cantidad": "Una asignación debe ser positiva."})
        if self.tipo_movimiento == self.TipoMovimiento.DEVOLUCION and self.cantidad >= 0:
            raise ValidationError({"cantidad": "Una devolución debe ser negativa."})
        if self.cantidad == 0:
            raise ValidationError({"cantidad": "La cantidad no puede ser cero."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class GastoOperacional(models.Model):
    class Tipo(models.TextChoices):
        ARRIENDO = "arriendo", "Arriendo"
        BENCINA = "bencina", "Bencina"
        TRANSPORTE = "transporte", "Transporte / despacho"
        SERVICIOS = "servicios", "Servicios"
        CONTADOR = "contador", "Contador"
        MARKETING = "marketing", "Marketing"
        INSUMOS = "insumos", "Insumos"
        OTRO = "otro", "Otro"

    fecha = models.DateField(db_index=True)
    tipo = models.CharField(max_length=20, choices=Tipo.choices, db_index=True)
    descripcion = models.CharField(max_length=200, blank=True)

    monto_neto = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Monto NETO (SIN IVA)"
    )

    aplica_iva = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    # ✅ NUEVO: Meta con índices
    class Meta:
        verbose_name = "Gasto Operacional"
        verbose_name_plural = "Gastos Operacionales"
        indexes = [
            models.Index(fields=['-fecha']),
            models.Index(fields=['tipo', 'fecha']),
            models.Index(fields=['fecha']),
        ]

    @property
    def iva(self):
        if not self.aplica_iva:
            return Decimal("0.00")
        return (self.monto_neto * Decimal("0.19")).quantize(Decimal("0.01"))

    @property
    def total_con_iva(self):
        return (self.monto_neto + self.iva).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.fecha} - {self.get_tipo_display()} - ${self.monto_neto}"


class InventarioBolsasAjuste(models.Model):
    class MotivoChoices(models.TextChoices):
        INVENTARIO_FISICO = "inf", "Inventario físico"
        ERROR_DETECTADO = "ed", "Error detectado"
        MERMA_PERDA = "mp", "Merma / pérdida"
        DEVOLUCION = "dv", "Devolución / recuperación"
        OTRO = "otro", "Otro"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ajustes_inventario_bolsas",
    )

    fecha = models.DateTimeField(auto_now_add=True)

    tipo_bolsa = models.ForeignKey(
        Material,
        to_field="codigo",
        db_column="tipo_bolsa",
        on_delete=models.PROTECT,
        related_name="ajustes_inventario",
        verbose_name="Tipo de bolsa",
    )

    stock_sistema_before = models.IntegerField(
        help_text="Stock calculado por sistema antes del ajuste"
    )
    stock_fisico = models.IntegerField(
        help_text="Conteo físico ingresado por usuario (entero >= 0)"
    )
    diferencia = models.IntegerField(
        editable=False,
        help_text="Automático: stock_fisico - stock_sistema_before"
    )

    motivo = models.CharField(
        max_length=10,
        choices=MOTIVOS_AJUSTE,
    )
    observacion = models.TextField(blank=True)

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "Ajuste Inventario Bolsas"
        verbose_name_plural = "Ajustes Inventario Bolsas"

    def __str__(self):
        return f"{self.tipo_bolsa.nombre} | {self.fecha:%d/%m/%Y} | S:{self.stock_sistema_before} F:{self.stock_fisico} A:{self.diferencia}"

    def save(self, *args, **kwargs):
        self.diferencia = self.stock_fisico - self.stock_sistema_before
        super().save(*args, **kwargs)


class DepositoEdicion(models.Model):
    """Historial de modificaciones realizadas a un depósito."""

    deposito = models.ForeignKey(
        Deposito,
        on_delete=models.PROTECT,
        related_name="historial",
        verbose_name="Depósito",
    )
    fecha = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        verbose_name="Fecha de modificación",
    )
    monto_ventas_anterior = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Total anterior de ventas",
    )
    monto_ventas_nuevo = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Total nuevo de ventas",
    )
    monto_depositado_anterior = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Monto depositado anterior",
    )
    monto_depositado_nuevo = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Monto depositado nuevo",
    )
    diferencia_anterior = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Diferencia anterior",
    )
    diferencia_nueva = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Diferencia nueva",
    )
    ventas_agregadas = models.TextField(
        blank=True,
        verbose_name="Ventas agregadas",
    )
    ventas_quitadas = models.TextField(
        blank=True,
        verbose_name="Ventas quitadas",
    )
    motivo = models.TextField(
        blank=True,
        verbose_name="Motivo de la modificación",
    )
    modificado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ediciones_depositos",
        verbose_name="Modificado por",
    )

    class Meta:
        ordering = ["-fecha", "-id"]
        verbose_name = "Modificación de depósito"
        verbose_name_plural = "Historial de modificaciones de depósitos"

    def __str__(self):
        return f"Edición del depósito #{self.deposito_id} - {self.fecha:%d/%m/%Y %H:%M}"


class CostoMensual(models.Model):
    """Costo manual por kg de material para un mes específico."""

    mes = models.DateField(
        unique=True,
        verbose_name="Mes",
        help_text="Se normaliza siempre al primer día del mes.",
    )
    costo_por_kg = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Costo por kg (sin IVA)",
    )
    observacion = models.CharField(max_length=200, blank=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="costos_mensuales_actualizados",
        verbose_name="Actualizado por",
    )

    class Meta:
        ordering = ["-mes"]
        verbose_name = "Costo mensual"
        verbose_name_plural = "Costos mensuales"

    def __str__(self):
        return f"{self.mes:%m-%Y}: ${self.costo_por_kg}/kg"

    def clean(self):
        super().clean()
        if self.mes:
            self.mes = self.mes.replace(day=1)
        if self.costo_por_kg is not None and self.costo_por_kg < 0:
            raise ValidationError(
                {"costo_por_kg": "El costo por kg no puede ser negativo."}
            )

    def save(self, *args, **kwargs):
        if self.mes:
            self.mes = self.mes.replace(day=1)
        self.full_clean()
        super().save(*args, **kwargs)
