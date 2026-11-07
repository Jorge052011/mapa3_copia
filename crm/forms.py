from uuid import uuid4

from django import forms
from django.forms import formset_factory
from django.db import models

from .models import (
    Cliente,
    Venta,
    VentaItem,
    Producto,
    Importacion,
    IngresoMaterial,
    Material,
    productos_vendibles_qs,
)
from .services_trazabilidad import importaciones_recepcionables


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ["nombre", "telefono", "email", "comuna", "direccion", "observaciones"]

    def clean_telefono(self):
        telefono = (self.cleaned_data.get("telefono") or "").strip()

        if not telefono:
            return telefono

        qs = Cliente.objects.filter(telefono=telefono)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise forms.ValidationError("Ya existe un cliente con ese teléfono.")

        return telefono


class VentaForm(forms.ModelForm):
    class Meta:
        model = Venta
        fields = [
            "cliente",
            "fecha",
            "tipo_documento",
            "numero_documento",
            "canal",
            "kilos_total",      # ✅ kilos (antes era total)
            "observaciones",
            "repartidor",       # ✅ Nuevo campo: Repartidor
            "modo_pago",        # ✅ Nuevo campo: Modo de pago
            "venta_origen",
            "devolucion_fisica",
        ]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Orden descendente por ID (clientes más recientes primero)
        self.fields['cliente'].queryset = Cliente.objects.all().order_by('-id')
        self.fields['venta_origen'].queryset = (
            Venta.objects.exclude(tipo_documento=Venta.TipoDocumento.NOTA_CREDITO)
            .order_by('-fecha', '-id')
        )
        self.fields['venta_origen'].required = False

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get("tipo_documento")
        numero = (cleaned_data.get("numero_documento") or "").strip()

        # ✅ si NO es sin_doc, exige número
        if tipo != Venta.TipoDocumento.SIN_DOC and not numero:
            self.add_error(
                "numero_documento",
                "Debes ingresar el número de la factura o boleta."
            )

        # Limpia el número (por si venía con espacios)
        cleaned_data["numero_documento"] = numero

        return cleaned_data


class VentaItemForm(forms.ModelForm):
    producto = forms.ModelChoiceField(
        queryset=Producto.objects.none(),
        empty_label="Seleccione producto...",
        required=True,
    )

    class Meta:
        model = VentaItem
        fields = ("producto", "item_origen", "cantidad", "precio_unitario")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        productos = productos_vendibles_qs().order_by("nombre")
        if self.instance and self.instance.pk:
            productos = Producto.objects.filter(
                models.Q(pk=self.instance.producto_id) | models.Q(pk__in=productos)
            ).distinct().order_by("nombre")
        self.fields["producto"].queryset = productos
        self.fields["item_origen"].required = False
        self.fields["item_origen"].queryset = (
            VentaItem.objects.select_related("venta", "producto")
            .order_by("-id")
        )


class IngresoMaterialForm(forms.ModelForm):
    class Meta:
        model = IngresoMaterial
        fields = (
            "fecha",
            "importacion",
            "tipo_bolsa",
            "cantidad",
            "observacion",
        )
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "observacion": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["importacion"].required = True
        self.fields["importacion"].queryset = importaciones_recepcionables()
        materiales = Material.objects.filter(activo=True).order_by("nombre")
        if self.instance and self.instance.pk:
            materiales = Material.objects.filter(
                models.Q(codigo=self.instance.tipo_bolsa_id)
                | models.Q(codigo__in=materiales.values("codigo"))
            ).order_by("nombre")
        self.fields["tipo_bolsa"].queryset = materiales


class RecepcionMaterialForm(forms.Form):
    recepcion_key = forms.UUIDField(
        required=False,
        widget=forms.HiddenInput,
    )
    fecha = forms.DateField(
        label="Fecha de recepción",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    importacion = forms.ModelChoiceField(
        label="Importación",
        queryset=Importacion.objects.none(),
        empty_label="Seleccione importación...",
    )
    observacion = forms.CharField(
        label="Observación",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["importacion"].queryset = importaciones_recepcionables()
        if not self.is_bound and not self.initial.get("recepcion_key"):
            self.initial["recepcion_key"] = uuid4()


class IngresoLineaForm(forms.Form):
    tipo_bolsa = forms.ModelChoiceField(
        label="Tipo de bolsa",
        queryset=Material.objects.none(),
        to_field_name="codigo",
        empty_label="Seleccione material...",
        required=False,
    )
    cantidad = forms.IntegerField(
        label="Cantidad",
        min_value=0,
        initial=0,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tipo_bolsa"].queryset = Material.objects.filter(
            activo=True
        ).order_by("nombre")


RecepcionLineaFormSet = formset_factory(
    IngresoLineaForm,
    extra=5,
    min_num=1,
    validate_min=True,
    can_delete=False,
)
