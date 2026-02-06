from django import forms
from .models import Cliente, Venta, VentaItem, Producto


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
        ]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ✅ Orden descendente por ID (clientes más recientes primero)
        self.fields['cliente'].queryset = Cliente.objects.all().order_by('-id')

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
    # ✅ Campo explícito: aquí cargas los productos activos sí o sí
    producto = forms.ModelChoiceField(
        queryset=Producto.objects.filter(activo=True).order_by("nombre"),
        empty_label="Seleccione producto...",
        required=True,
    )

    class Meta:
        model = VentaItem
        fields = ("producto", "cantidad", "precio_unitario")
