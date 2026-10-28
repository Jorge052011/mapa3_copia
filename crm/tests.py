from datetime import date, datetime
from uuid import uuid4
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import resolve, reverse
from django.utils import timezone

from .models import (
    Cliente,
    Importacion,
    IngresoMaterial,
    MovimientoBolsasVenta,
    Producto,
    Venta,
    VentaItem,
)
from .services_inventario import (
    consumo_bolsas,
    inventario_bolsas_actual,
    stock_actual_post_inventario_kg,
)
from .services_trazabilidad import (
    asignar_bolsas,
    fecha_inicio_control,
    importaciones_recepcionables,
    pendiente_item,
    registrar_devolucion_fisica,
    revertir_movimiento,
    saldo_contenedor,
    ventas_con_trazabilidad_pendiente,
)


class ClienteTestCase(TestCase):
    def test_crear_cliente(self):
        cliente = Cliente.objects.create(
            nombre="Test Cliente",
            telefono="912345678"
        )
        self.assertEqual(cliente.nombre, "Test Cliente")

    def test_segmento_nuevo(self):
        cliente = Cliente.objects.create(nombre="Nuevo")
        self.assertEqual(cliente.segmento, "Ocasional")


class TrazabilidadBaseTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tester",
            password="test-pass",
        )
        self.cliente = Cliente.objects.create(nombre="Cliente trazabilidad")
        self.producto = Producto.objects.create(
            sku="3",
            nombre="Lavanda 20 kg",
            peso_kg=Decimal("20.00"),
        )
        self.fecha_venta = timezone.make_aware(
            datetime(2026, 10, 28, 12, 0, 0),
            timezone.get_current_timezone(),
        )

        self.importacion_a = self._crear_importacion(
            "2026-10-29", "Contenedor A", 100
        )
        self.importacion_b = self._crear_importacion(
            "2026-10-30", "Contenedor B", 100
        )

    def _crear_importacion(self, fecha, descripcion, cantidad):
        importacion = Importacion.objects.create(
            fecha=date.fromisoformat(fecha),
            descripcion=descripcion,
            kilos_ingresados=Decimal("1000.00"),
            merma_kg=Decimal("0.00"),
            costo_total=Decimal("1000.00"),
        )
        IngresoMaterial.objects.create(
            fecha=importacion.fecha,
            importacion=importacion,
            tipo_bolsa="20_lav",
            cantidad=cantidad,
            control_contenedor=True,
            observacion="Ingreso de prueba",
        )
        return importacion

    def _crear_venta(self, cantidad=50):
        venta = Venta.objects.create(
            cliente=self.cliente,
            fecha=self.fecha_venta,
            kilos_total=Decimal("1000.00"),
        )
        item = VentaItem.objects.create(
            venta=venta,
            producto=self.producto,
            cantidad=cantidad,
            precio_unitario=Decimal("1000.00"),
        )
        return venta, item


class InventarioVentasTestCase(TrazabilidadBaseTestCase):
    def test_venta_normal_posterior_al_corte_local_se_descuenta(self):
        stock_antes = inventario_bolsas_actual()["stock_actual_20_lav"]
        kg_antes = stock_actual_post_inventario_kg()

        self._crear_venta(cantidad=5)

        data = consumo_bolsas()
        stock_despues = inventario_bolsas_actual()["stock_actual_20_lav"]
        kg_despues = stock_actual_post_inventario_kg()

        self.assertEqual(data["consumo_20_lav"], 5)
        self.assertEqual(data["inventario_20_lav"], stock_antes - 5)
        self.assertEqual(stock_despues, stock_antes - 5)
        self.assertEqual(kg_despues, kg_antes - 100)

    def test_filtro_hasta_explicito_sigue_limitando_el_consumo(self):
        self._crear_venta(cantidad=5)

        antes_del_corte = consumo_bolsas(hasta=date(2026, 10, 27))
        incluyendo_venta = consumo_bolsas(hasta=date(2026, 10, 28))

        self.assertEqual(antes_del_corte["consumo_20_lav"], 0)
        self.assertEqual(incluyendo_venta["consumo_20_lav"], 5)


class SeparacionInventarioContenedorTestCase(TrazabilidadBaseTestCase):
    def test_asignar_no_descuenta_dos_veces_el_inventario_general(self):
        _, item = self._crear_venta(cantidad=50)
        antes = inventario_bolsas_actual()["stock_actual_20_lav"]

        asignar_bolsas(
            item, self.importacion_a, "20_lav", 30,
            usuario=self.user, observacion="Parte A",
        )
        asignar_bolsas(
            item, self.importacion_b, "20_lav", 20,
            usuario=self.user, observacion="Parte B",
        )

        despues = inventario_bolsas_actual()["stock_actual_20_lav"]

        self.assertEqual(antes, despues)
        self.assertEqual(saldo_contenedor(self.importacion_a, "20_lav"), 70)
        self.assertEqual(saldo_contenedor(self.importacion_b, "20_lav"), 80)
        self.assertEqual(pendiente_item(item)["20_lav"], 0)
        self.assertEqual(
            MovimientoBolsasVenta.objects.filter(venta_item=item).count(),
            2,
        )

    def test_asignacion_parcial_muestra_pendiente_sin_alterar_bodega(self):
        _, item = self._crear_venta(cantidad=50)
        antes = inventario_bolsas_actual()["stock_actual_20_lav"]

        asignar_bolsas(
            item, self.importacion_a, "20_lav", 30,
            usuario=self.user,
        )

        self.assertEqual(
            inventario_bolsas_actual()["stock_actual_20_lav"],
            antes,
        )
        self.assertEqual(pendiente_item(item)["20_lav"], 20)
        pendientes = ventas_con_trazabilidad_pendiente()
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(pendientes[0]["pendiente"]["20_lav"], 20)

    def test_devolucion_fisica_restituye_solo_el_contenedor(self):
        _, item = self._crear_venta(cantidad=50)
        antes = inventario_bolsas_actual()["stock_actual_20_lav"]
        asignacion = asignar_bolsas(
            item, self.importacion_a, "20_lav", 30,
            usuario=self.user,
        )

        registrar_devolucion_fisica(
            item, self.importacion_a, "20_lav", 10,
            usuario=self.user,
        )

        self.assertEqual(saldo_contenedor(self.importacion_a, "20_lav"), 80)
        self.assertEqual(pendiente_item(item)["20_lav"], 30)
        self.assertEqual(
            inventario_bolsas_actual()["stock_actual_20_lav"],
            antes,
        )
        self.assertEqual(asignacion.cantidad, 30)

    def test_reversion_de_asignacion_restituye_contenedor(self):
        _, item = self._crear_venta(cantidad=50)
        antes = inventario_bolsas_actual()["stock_actual_20_lav"]
        asignacion = asignar_bolsas(
            item, self.importacion_a, "20_lav", 30,
            usuario=self.user,
        )

        revertir_movimiento(asignacion, usuario=self.user)

        self.assertEqual(saldo_contenedor(self.importacion_a, "20_lav"), 100)
        self.assertEqual(pendiente_item(item)["20_lav"], 50)
        self.assertEqual(
            inventario_bolsas_actual()["stock_actual_20_lav"],
            antes,
        )

    def test_rechaza_asignacion_que_excede_lo_vendido(self):
        _, item = self._crear_venta(cantidad=10)

        with self.assertRaises(ValidationError):
            asignar_bolsas(
                item, self.importacion_a, "20_lav", 11,
                usuario=self.user,
            )

    def test_rechaza_asignacion_que_excede_saldo_del_contenedor(self):
        _, item = self._crear_venta(cantidad=50)
        self.importacion_a.ingresos_material.all().delete()
        IngresoMaterial.objects.create(
            fecha=self.importacion_a.fecha,
            importacion=self.importacion_a,
            tipo_bolsa="20_lav",
            cantidad=5,
            control_contenedor=True,
        )
        self.assertEqual(saldo_contenedor(self.importacion_a, "20_lav"), 5)

        with self.assertRaises(ValidationError):
            asignar_bolsas(
                item, self.importacion_a, "20_lav", 6,
                usuario=self.user,
            )


class RecepcionContenedorTestCase(TrazabilidadBaseTestCase):
    def test_recepcion_suma_bodega_y_contenedor_una_sola_vez(self):
        importacion = Importacion.objects.create(
            fecha=date(2026, 11, 1),
            descripcion="Contenedor C",
            kilos_ingresados=Decimal("1000.00"),
            merma_kg=Decimal("0.00"),
            costo_total=Decimal("1000.00"),
        )
        antes = inventario_bolsas_actual()["stock_actual_20_lav"]

        from .services_trazabilidad import crear_recepcion

        creados = crear_recepcion(
            importacion,
            date(2026, 11, 1),
            [("20_lav", 15)],
            usuario=self.user,
        )

        self.assertEqual(len(creados), 1)
        self.assertEqual(
            inventario_bolsas_actual()["stock_actual_20_lav"],
            antes + 15,
        )
        self.assertEqual(saldo_contenedor(importacion, "20_lav"), 15)

    def test_importaciones_anteriores_no_son_recepcionables(self):
        anterior = Importacion.objects.create(
            fecha=date(2026, 10, 27),
            descripcion="Anterior al control",
            kilos_ingresados=Decimal("1000.00"),
            merma_kg=Decimal("0.00"),
            costo_total=Decimal("1000.00"),
        )
        self.assertNotIn(anterior, importaciones_recepcionables())
        self.assertIn(self.importacion_a, importaciones_recepcionables())

        with self.assertRaises(ValidationError):
            IngresoMaterial.objects.create(
                fecha=anterior.fecha,
                importacion=anterior,
                tipo_bolsa="20_lav",
                cantidad=3,
                control_contenedor=True,
            )


class NotaCreditoTestCase(TrazabilidadBaseTestCase):
    def test_nota_credito_sin_devolucion_fisica_no_mueve_bolsas(self):
        antes = inventario_bolsas_actual()["stock_actual_20_lav"]
        venta = Venta.objects.create(
            cliente=self.cliente,
            fecha=timezone.now(),
            tipo_documento=Venta.TipoDocumento.NOTA_CREDITO,
            devolucion_fisica=False,
            kilos_total=Decimal("200.00"),
        )
        VentaItem.objects.create(
            venta=venta,
            producto=self.producto,
            cantidad=10,
            precio_unitario=Decimal("1000.00"),
        )

        self.assertEqual(
            inventario_bolsas_actual()["stock_actual_20_lav"],
            antes,
        )

        venta.devolucion_fisica = True
        venta.save(update_fields=["devolucion_fisica"])

        self.assertEqual(
            inventario_bolsas_actual()["stock_actual_20_lav"],
            antes + 10,
        )

    def test_fecha_inicio_del_control_es_2026_10_28(self):
        self.assertEqual(str(fecha_inicio_control()), "2026-10-28")


class VistasTrazabilidadTestCase(TrazabilidadBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def _crear_asignacion(self, cantidad=4):
        venta, item = self._crear_venta(cantidad=10)
        movimiento = asignar_bolsas(
            item,
            self.importacion_a,
            "20_lav",
            cantidad,
            usuario=self.user,
        )
        return venta, item, movimiento

    def test_paginas_de_contenedor_y_conciliacion_cargan(self):
        respuesta = self.client.get("/crm/inventario/contenedores/")
        self.assertEqual(respuesta.status_code, 200)

        respuesta = self.client.get(
            f"/crm/inventario/contenedores/{self.importacion_a.id}/"
        )
        self.assertEqual(respuesta.status_code, 200)

        respuesta = self.client.get("/crm/inventario/conciliacion/")
        self.assertEqual(respuesta.status_code, 200)

    def test_formulario_de_recepcion_carga(self):
        respuesta = self.client.get("/crm/inventario/ingresos/")
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Líneas recibidas")

    def test_detalle_de_venta_muestra_trazabilidad(self):
        venta, _ = self._crear_venta(cantidad=1)
        respuesta = self.client.get(f"/crm/ventas/{venta.id}/")
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Trazabilidad por contenedor")

    def test_post_recepcion_multilinea(self):
        importacion = Importacion.objects.create(
            fecha=date(2026, 11, 2),
            descripcion="Contenedor D",
            kilos_ingresados=Decimal("1000.00"),
            merma_kg=Decimal("0.00"),
            costo_total=Decimal("1000.00"),
        )
        datos = {
            "recepcion_key": str(uuid4()),
            "fecha": "2026-11-02",
            "importacion": importacion.id,
            "observacion": "Recepción de prueba",
            "lineas-TOTAL_FORMS": "1",
            "lineas-INITIAL_FORMS": "0",
            "lineas-MIN_NUM_FORMS": "1",
            "lineas-MAX_NUM_FORMS": "1000",
            "lineas-0-tipo_bolsa": "20_lav",
            "lineas-0-cantidad": "7",
        }
        respuesta = self.client.post("/crm/inventario/ingresos/", datos)
        self.assertEqual(respuesta.status_code, 302)
        respuesta = self.client.post("/crm/inventario/ingresos/", datos)
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(saldo_contenedor(importacion, "20_lav"), 7)
        self.assertEqual(
            IngresoMaterial.objects.filter(
                importacion=importacion,
                control_contenedor=True,
                recepcion_key__isnull=False,
            ).count(),
            1,
        )

    def test_post_asignacion_desde_vista(self):
        venta, item = self._crear_venta(cantidad=10)
        respuesta = self.client.post("/crm/ventas/trazabilidad/asignar/", {
            "item_id": item.id,
            "importacion_id": self.importacion_a.id,
            "tipo_bolsa": "20_lav",
            "cantidad": "4",
            "idempotency_key": str(uuid4()),
        })
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(
            MovimientoBolsasVenta.objects.filter(venta_item=item).count(),
            1,
        )
        self.assertEqual(saldo_contenedor(self.importacion_a, "20_lav"), 96)

    def test_urls_de_trazabilidad_resuelven_con_movimiento_id(self):
        _, _, movimiento = self._crear_asignacion()

        for nombre in (
            "revertir_trazabilidad",
            "devolucion_trazabilidad",
        ):
            url = reverse(f"crm:{nombre}", args=[movimiento.id])
            resuelta = resolve(url)

            self.assertEqual(resuelta.url_name, nombre)
            self.assertEqual(
                resuelta.kwargs,
                {"movimiento_id": movimiento.id},
            )

    def test_devolucion_valida_desde_vista(self):
        venta, item, movimiento = self._crear_asignacion()
        inventario_antes = inventario_bolsas_actual()["stock_actual_20_lav"]

        respuesta = self.client.post(
            reverse("crm:devolucion_trazabilidad", args=[movimiento.id]),
            {
                "venta_id": venta.id,
                "cantidad": "2",
                "idempotency_key": str(uuid4()),
            },
        )

        self.assertEqual(respuesta.status_code, 302)
        devoluciones = MovimientoBolsasVenta.objects.filter(
            venta_item=item,
            tipo_movimiento=MovimientoBolsasVenta.TipoMovimiento.DEVOLUCION,
        )
        self.assertEqual(devoluciones.count(), 1)
        self.assertEqual(devoluciones.get().cantidad, -2)
        self.assertEqual(saldo_contenedor(self.importacion_a, "20_lav"), 98)
        self.assertEqual(pendiente_item(item)["20_lav"], 8)
        self.assertEqual(
            inventario_bolsas_actual()["stock_actual_20_lav"],
            inventario_antes,
        )

    def test_reversion_valida_desde_vista(self):
        venta, item, movimiento = self._crear_asignacion()
        inventario_antes = inventario_bolsas_actual()["stock_actual_20_lav"]

        respuesta = self.client.post(
            reverse("crm:revertir_trazabilidad", args=[movimiento.id]),
            {
                "venta_id": venta.id,
                "idempotency_key": str(uuid4()),
            },
        )

        self.assertEqual(respuesta.status_code, 302)
        reversiones = MovimientoBolsasVenta.objects.filter(
            venta_item=item,
            tipo_movimiento=MovimientoBolsasVenta.TipoMovimiento.REVERSION,
        )
        self.assertEqual(reversiones.count(), 1)
        self.assertEqual(reversiones.get().cantidad, -4)
        self.assertEqual(saldo_contenedor(self.importacion_a, "20_lav"), 100)
        self.assertEqual(pendiente_item(item)["20_lav"], 10)
        self.assertEqual(
            inventario_bolsas_actual()["stock_actual_20_lav"],
            inventario_antes,
        )

    def test_devolucion_duplicada_no_crea_movimiento_extra(self):
        venta, item, movimiento = self._crear_asignacion()
        inventario_antes = inventario_bolsas_actual()["stock_actual_20_lav"]
        datos = {
            "venta_id": venta.id,
            "cantidad": "2",
            "idempotency_key": str(uuid4()),
        }
        url = reverse("crm:devolucion_trazabilidad", args=[movimiento.id])

        self.assertEqual(self.client.post(url, datos).status_code, 302)
        self.assertEqual(self.client.post(url, datos).status_code, 302)

        self.assertEqual(
            MovimientoBolsasVenta.objects.filter(
                venta_item=item,
                tipo_movimiento=MovimientoBolsasVenta.TipoMovimiento.DEVOLUCION,
            ).count(),
            1,
        )
        self.assertEqual(saldo_contenedor(self.importacion_a, "20_lav"), 98)
        self.assertEqual(
            inventario_bolsas_actual()["stock_actual_20_lav"],
            inventario_antes,
        )

    def test_reversion_duplicada_no_crea_movimiento_extra(self):
        venta, item, movimiento = self._crear_asignacion()
        inventario_antes = inventario_bolsas_actual()["stock_actual_20_lav"]
        url = reverse("crm:revertir_trazabilidad", args=[movimiento.id])

        self.assertEqual(self.client.post(url, {
            "venta_id": venta.id,
            "idempotency_key": str(uuid4()),
        }).status_code, 302)
        self.assertEqual(self.client.post(url, {
            "venta_id": venta.id,
            "idempotency_key": str(uuid4()),
        }).status_code, 302)

        self.assertEqual(
            MovimientoBolsasVenta.objects.filter(
                venta_item=item,
                tipo_movimiento=MovimientoBolsasVenta.TipoMovimiento.REVERSION,
            ).count(),
            1,
        )
        self.assertEqual(saldo_contenedor(self.importacion_a, "20_lav"), 100)
        self.assertEqual(
            inventario_bolsas_actual()["stock_actual_20_lav"],
            inventario_antes,
        )

    def test_devolucion_invalida_no_modifica_saldos(self):
        venta, item, movimiento = self._crear_asignacion()
        inventario_antes = inventario_bolsas_actual()["stock_actual_20_lav"]

        respuesta = self.client.post(
            reverse("crm:devolucion_trazabilidad", args=[movimiento.id]),
            {
                "venta_id": venta.id,
                "cantidad": "5",
                "idempotency_key": str(uuid4()),
            },
        )

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(
            MovimientoBolsasVenta.objects.filter(
                venta_item=item,
                tipo_movimiento=MovimientoBolsasVenta.TipoMovimiento.DEVOLUCION,
            ).count(),
            0,
        )
        self.assertEqual(saldo_contenedor(self.importacion_a, "20_lav"), 96)
        self.assertEqual(
            inventario_bolsas_actual()["stock_actual_20_lav"],
            inventario_antes,
        )

    def test_trazabilidad_solo_acepta_post_y_requiere_login(self):
        _, item, movimiento = self._crear_asignacion()
        urls = [
            reverse("crm:revertir_trazabilidad", args=[movimiento.id]),
            reverse("crm:devolucion_trazabilidad", args=[movimiento.id]),
        ]

        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 405)

        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.post(url, {}).status_code, 302)

        self.assertEqual(
            MovimientoBolsasVenta.objects.filter(venta_item=item).count(),
            1,
        )

    def test_movimiento_inexistente_devuelve_404(self):
        for nombre in (
            "revertir_trazabilidad",
            "devolucion_trazabilidad",
        ):
            respuesta = self.client.post(
                reverse(f"crm:{nombre}", args=[999999]),
                {},
            )
            self.assertEqual(respuesta.status_code, 404)
