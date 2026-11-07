# Fase 2.1 - Endurecimiento y validación

Fecha: 2026-11-06

Entorno de implementación y validación: `/tmp/mapa3_copia_impl_20261106T213329Z`.

Referencia de comparación no modificada: `/workspace/mapa3_copia-dev`.

## Alcance

- Ejecutar la suite completa de pruebas de `crm` con los cambios de Fase 2.
- Añadir pruebas de regresión para los casos de borde detectados en la auditoría.
- Reproducir una migración fresca desde la base previa a Fase 2.
- Documentar los agregados legacy de 8 kg y 20 kg.
- Generar un parche unificado versionado de Fase 2.
- No modificar producción, desarrollo original, bases originales ni respaldos.

## Pruebas

Comando ejecutado:

```bash
cd /tmp/mapa3_copia_impl_20261106T213329Z
PYTHONPATH=$PWD/venv/lib/python3.12/site-packages \
  /tmp/opencode/py/musl/python/bin/python manage.py test crm -v 1
```

Resultado final: **52 pruebas OK**, 0 fallos, 0 errores.

La clase `Fase21EndurecimientoTestCase` añade 14 pruebas:

1. Eliminar una receta recalcula o limpia el peso del producto.
2. Cambiar el peso de un material recalcula todos los productos afectados.
3. Rechazar una receta nueva con material inactivo.
4. Rechazar un ítem de venta nuevo con producto inactivo.
5. Rechazar un ítem de venta nuevo con receta que contiene material inactivo.
6. Rechazar un ítem de venta nuevo con peso inválido.
7. Editar un ítem histórico sin revalidar producto/material inactivo.
8. Editar un ingreso histórico con material inactivo.
9. Registrar un ajuste de inventario de un material activo.
10. Rechazar un ajuste de inventario de un material inactivo.
11. No crear ajuste cuando la diferencia es cero.
12. Los formularios filtran inactivos en altas y conservan históricos.
13. El admin de ingresos filtra inactivos y conserva históricos.
14. El inline de recetas rechaza material inactivo y conserva históricos.

Evidencia: `/tmp/test_output_fase21_final.txt`.

## Migración fresca

Origen: `db.sqlite3.pre-fase2`

SHA-256 del origen:

```text
b3af4f97dfdac46204cf5d720790ec41802af946df05a7b2d65e84343ef89cf1
```

Se copió la base previa y se aplicaron las migraciones con el código actual:

```bash
cd /tmp/mapa3_copia_impl_20261106T213329Z
cp -p db.sqlite3.pre-fase2 /tmp/opencode/fase21_fresh.sqlite3

PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=$PWD/venv/lib/python3.12/site-packages \
/tmp/opencode/py/musl/python/bin/python - <<'PY'
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'DistribucionApp.settings')
django.setup()
from django.conf import settings
settings.DATABASES['default']['NAME'] = '/tmp/opencode/fase21_fresh.sqlite3'
from django.core.management import call_command
call_command('migrate', interactive=False, verbosity=2)
PY
```

Resultado:

- Se aplicaron correctamente `crm.0023_material_productomaterial` y `crm.0024_tipo_bolsa_material_fk`.
- SHA-256 de la base fresca: `64845185bebf1f216e567f037b2279dd2f5be34346ea1b4068e78a3aeb4d91f8`.
- `PRAGMA integrity_check`: `ok`.
- `PRAGMA foreign_key_check`: sin filas.
- Productos: 20.
- Materiales: 6.
- Filas de `ProductoMaterial`: 22.
- Suma de `ProductoMaterial.cantidad`: 46 unidades.
- Productos con receta: 20.
- No se eliminaron tablas.
- Tablas nuevas: `crm_material`, `crm_productomaterial`.
- Las 12 tablas históricas comunes no presentaron diferencias.
- Los 20 pesos de producto se conservaron sin cambios.
- No se detectaron discrepancias entre `Producto.peso_kg` y la suma de sus recetas.

Evidencia: `/tmp/fase21_migracion_fresca.log` y `/tmp/fase21_validacion_fresca.txt`.

## Agregados legacy 8 kg / 20 kg

### Finalidad

Se conservan las claves agregadas:

- `consumo_8`, `consumo_20`
- `stock_inicial_8`, `stock_inicial_20`
- `inventario_8`, `inventario_20`
- `stock_actual_8`, `stock_actual_20`

Son una interfaz histórica para vistas, plantillas, pruebas o consumidores externos que trabajaban con dos grupos de bolsas: 8 kg y 20 kg.

### Implementación actual

- `consumo_bolsas()` calcula `consumo_<peso>` sumando el consumo de todos los materiales cuyo `Material.peso_kg` es exactamente 8 o 20.
- `stock_inicial_<peso>` suma los ingresos históricos de esos mismos materiales.
- `inventario_<peso>` es `stock_inicial_<peso> - consumo_<peso>`.
- `inventario_bolsas_actual()` añade los ajustes de inventario y calcula `stock_actual_<peso>`.
- La agrupación se obtiene con `_materiales_por_peso()` y `stock_inicial_por_tipo()`.

### Dependencias

- `crm/services_inventario.py`: `consumo_bolsas()`, `inventario_bolsas_actual()`, `_materiales_por_peso()`, `stock_inicial_por_tipo()`, `get_ajustes_bolsas()`.
- Los consumidores deben seguir usando las claves `consumo_8`, `consumo_20`, `stock_inicial_8`, `stock_inicial_20`, `inventario_8`, `inventario_20`, `stock_actual_8` y `stock_actual_20`.
- La interfaz nueva y principal es dinámica: `materiales`, `consumo_<codigo>`, `stock_inicial_<codigo>`, `inventario_<codigo>` y `stock_actual_<codigo>`.

### Riesgos

- Un material con peso distinto de 8 kg o 20 kg no se incluye en los agregados legacy, aunque sí aparece en las claves dinámicas por código.
- Cambiar el peso de un material altera la pertenencia al grupo 8/20 y, por tanto, el significado histórico de esos agregados.
- Los materiales inactivos se conservan en el histórico de recetas; no deben eliminarse ni reescribirse para “limpiar” los agregados.
- Eliminar o renombrar las claves legacy puede romper consumidores externos no detectados en este repositorio.
- En el código actual no se encontraron referencias internas directas a `consumo_8`, `consumo_20`, `stock_actual_8` o `stock_actual_20`; la dependencia principal es de compatibilidad externa.

### Decisión

No se eliminan ni se cambia su comportamiento en Fase 2.1. Una retirada futura requiere inventario de consumidores externos, ventana de compatibilidad, pruebas de regresión y un plan de despliegue aprobado.

## Observaciones de aceptación

- El diseño funcional habla de “46 registros”, pero la implementación materializa 22 filas de `ProductoMaterial` que suman 46 unidades. La validación confirma que no hay pérdida funcional; la diferencia es semántica y requiere aceptación formal.
- La migración añade dos tablas y metadatos estándar de Django (content types/permisos). No se modifican datos históricos.
- El repositorio no tiene metadata Git válida; el parche se entrega como diff unificado, sin commits.
- No se realizó despliegue ni cambio en producción.

## Veredicto

**APTO CON OBSERVACIONES**, sujeto a la aceptación formal de la diferencia entre “46 registros” y “22 filas / 46 unidades” y al uso controlado de los agregados legacy.
