# crm/models.py
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Sum, F, DecimalField, ExpressionWrapper
from django.utils import timezone
from django.core.exceptions import ValidationError


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
    peso_kg = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

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

    tipo_documento = models.CharField(
        max_length=20,
        choices=TipoDocumento.choices,
        default=TipoDocumento.SIN_DOC,
        db_index=True,
    )
    numero_documento = models.CharField(max_length=30, blank=True, db_index=True)

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


class VentaItem(models.Model):
    venta = models.ForeignKey(Venta, on_delete=models.CASCADE, related_name="items")
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
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