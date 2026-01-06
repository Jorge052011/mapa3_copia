# rutas/views.py
import json
import requests
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie

from .models import PuntoEntrega
from . import optimizer

logger = logging.getLogger(__name__)

DEFAULT_FUEL_PRICE = 1250
DEFAULT_RENDIMIENTO = getattr(optimizer, 'AUTO_RENDIMIENTO_KM_POR_LITRO', 12)


@login_required
@ensure_csrf_cookie
def mapa_view(request):
    """
    Muestra el mapa, la lista de puntos, el formulario de origen/destino
    y los resultados de la última optimización.
    - En el MAPA solo se muestran los puntos seleccionados en la última optimización.
    - En el LISTADO/FORM aparecen todos los puntos para poder elegir nuevos subconjuntos.
    """
    puntos_entrega = PuntoEntrega.objects.all().order_by('orden_optimo', 'id')

    selected_ids = request.session.get('selected_ids')

    if selected_ids:
        puntos_para_mapa = PuntoEntrega.objects.filter(
            id__in=selected_ids
        ).order_by('orden_optimo', 'id')
    else:
        puntos_para_mapa = puntos_entrega

    puntos_json = json.dumps([
        {
            'id': p.id,
            'nombre': p.nombre,
            'direccion': p.direccion,
            'latitud': float(p.latitud),
            'longitud': float(p.longitud),
            'orden_optimo': p.orden_optimo,
        }
        for p in puntos_para_mapa
    ])

    context = {
        'google_maps_api_key': settings.GOOGLE_MAPS_API_KEY,
        'puntos_entrega_json': puntos_json,
        'puntos_entrega': puntos_entrega,

        'total_distance_km': request.session.pop('total_distance_km', None),
        'fuel_consumed_liters': request.session.pop('fuel_consumed_liters', None),
        'fuel_cost_clp': request.session.pop('fuel_cost_clp', None),
        'precio_bencina': request.session.pop('precio_bencina', DEFAULT_FUEL_PRICE),

        'rendimiento_vehiculo': request.session.pop('rendimiento_vehiculo', DEFAULT_RENDIMIENTO),

        'direccion_origen': request.session.pop('direccion_origen', ''),
        'direccion_destino': request.session.pop('direccion_destino', ''),

        'origen_lat': request.session.pop('origen_lat', None),
        'origen_lng': request.session.pop('origen_lng', None),
        'destino_lat': request.session.pop('destino_lat', None),
        'destino_lng': request.session.pop('destino_lng', None),

        'error_message': request.session.pop('error_message', None),

        'selected_ids': selected_ids,
    }

    return render(request, "rutas/mapa.html", context)


@login_required
def agregar_punto(request):
    """
    Agrega un punto de entrega. Si no vienen lat/lng, geocodifica la dirección.
    """
    if request.method != 'POST':
        return redirect('mapa')

    nombre = request.POST.get('nombre')
    direccion = request.POST.get('direccion')

    if not nombre or not direccion:
        request.session['error_message'] = 'Nombre y dirección son obligatorios.'
        return redirect('mapa')

    latitud = request.POST.get('latitud')
    longitud = request.POST.get('longitud')

    # Geocodificación si no se proporcionan lat/lng
    if not latitud or not longitud:
        try:
            geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "address": direccion,
                "key": settings.GOOGLE_MAPS_API_KEY
            }
            response = requests.get(geocode_url, params=params)
            data = response.json()
            if data['status'] == 'OK' and data['results']:
                location = data['results'][0]['geometry']['location']
                latitud = location['lat']
                longitud = location['lng']
            else:
                request.session['error_message'] = (
                    f"No se pudo geocodificar la dirección: {direccion}. "
                    f"Estado: {data.get('status')}"
                )
                return redirect('mapa')
        except requests.exceptions.RequestException as e:
            logger.error(f"Error geocodificando dirección para {request.user.username}: {e}")
            request.session['error_message'] = f"Error de conexión con la API de geocodificación: {e}"
            return redirect('mapa')
        except Exception as e:
            logger.error(f"Error inesperado geocodificando: {e}", exc_info=True)
            request.session['error_message'] = f"Error inesperado al geocodificar: {e}"
            return redirect('mapa')

    # Convertir a float
    try:
        latitud = float(latitud)
        longitud = float(longitud)
    except ValueError:
        request.session['error_message'] = 'Latitud o Longitud con formato incorrecto.'
        return redirect('mapa')

    punto = PuntoEntrega.objects.create(
        nombre=nombre,
        direccion=direccion,
        latitud=latitud,
        longitud=longitud,
    )
    
    logger.info(f"Punto #{punto.id} agregado por {request.user.username}: {nombre}")
    return redirect('mapa')


@login_required
def optimizar_ruta(request):
    """
    Toma los puntos de entrega seleccionados, el origen/destino,
    construye la matriz de distancias, resuelve el TSP y guarda
    el orden óptimo + métricas de consumo/costo en sesión.
    """
    if request.method != 'POST':
        return redirect('mapa')

    # 0) LEER PUNTOS SELECCIONADOS DEL FORMULARIO
    selected_ids = request.POST.getlist('puntos_seleccionados')
    
    logger.info(
        f"Usuario {request.user.username} optimizando ruta con "
        f"{len(selected_ids)} puntos seleccionados"
    )

    if not selected_ids:
        logger.warning(f"Usuario {request.user.username} intentó optimizar sin puntos")
        request.session['error_message'] = (
            'Debes seleccionar al menos un punto de entrega para optimizar la ruta.'
        )
        return redirect('mapa')

    # Guardar selección en sesión
    request.session['selected_ids'] = selected_ids

    # Obtener sólo los puntos seleccionados
    puntos_entrega_db = list(
        PuntoEntrega.objects.filter(id__in=selected_ids).order_by('id')
    )

    if not puntos_entrega_db:
        request.session['error_message'] = (
            'Los puntos seleccionados no existen o fueron eliminados.'
        )
        return redirect('mapa')

    # 1) ORIGEN
    origen_predef = request.POST.get('origen_predefinido', '').strip()
    origen_custom = request.POST.get('origen_custom', '').strip()

    if origen_predef == 'custom':
        direccion_origen = origen_custom
    elif origen_predef:
        direccion_origen = origen_predef
    else:
        request.session['error_message'] = 'Debes seleccionar o escribir una dirección de origen.'
        return redirect('mapa')

    if not direccion_origen:
        request.session['error_message'] = 'La dirección de origen no puede estar vacía.'
        return redirect('mapa')

    # 2) DESTINO
    destino_predef = request.POST.get('destino_predefinido', '').strip()
    destino_custom = request.POST.get('destino_custom', '').strip()

    if destino_predef == 'custom':
        direccion_destino = destino_custom
    elif destino_predef == 'same_origin' or not destino_predef:
        direccion_destino = direccion_origen
    else:
        direccion_destino = destino_predef

    if not direccion_destino:
        request.session['error_message'] = 'La dirección de destino no puede estar vacía.'
        return redirect('mapa')

    # 3) GEOCODIFICAR ORIGEN
    geocode_url = "https://maps.googleapis.com/maps/api/geocode/json"
    try:
        params_origen = {
            "address": direccion_origen,
            "key": settings.GOOGLE_MAPS_API_KEY
        }
        resp_o = requests.get(geocode_url, params=params_origen)
        data_o = resp_o.json()

        if data_o['status'] == 'OK' and data_o['results']:
            loc_o = data_o['results'][0]['geometry']['location']
            lat_inicio = float(loc_o['lat'])
            lng_inicio = float(loc_o['lng'])
            punto_inicio_coords = {'latitud': lat_inicio, 'longitud': lng_inicio}
            request.session['origen_lat'] = lat_inicio
            request.session['origen_lng'] = lng_inicio
        else:
            request.session['error_message'] = (
                f"No se pudo geocodificar la dirección de origen: {direccion_origen} "
                f"(estado: {data_o.get('status')})."
            )
            return redirect('mapa')
    except Exception as e:
        logger.error(f"Error geocodificando origen: {e}", exc_info=True)
        request.session['error_message'] = f"Error al geocodificar la dirección de origen: {e}"
        return redirect('mapa')

    # 4) GEOCODIFICAR DESTINO
    destino_coords = None
    try:
        if direccion_destino == direccion_origen:
            destino_coords = {'latitud': lat_inicio, 'longitud': lng_inicio}
            request.session['destino_lat'] = lat_inicio
            request.session['destino_lng'] = lng_inicio
        else:
            params_dest = {
                "address": direccion_destino,
                "key": settings.GOOGLE_MAPS_API_KEY
            }
            resp_d = requests.get(geocode_url, params=params_dest)
            data_d = resp_d.json()
            if data_d['status'] == 'OK' and data_d['results']:
                loc_d = data_d['results'][0]['geometry']['location']
                lat_dest = float(loc_d['lat'])
                lng_dest = float(loc_d['lng'])
                destino_coords = {'latitud': lat_dest, 'longitud': lng_dest}
                request.session['destino_lat'] = lat_dest
                request.session['destino_lng'] = lng_dest
            else:
                request.session['error_message'] = (
                    f"No se pudo geocodificar la dirección destino: {direccion_destino} "
                    f"(estado: {data_d.get('status')})."
                )
                return redirect('mapa')
    except Exception as e:
        logger.error(f"Error geocodificando destino: {e}", exc_info=True)
        request.session['error_message'] = f"Error al geocodificar la dirección destino: {e}"
        return redirect('mapa')

    # 5) MATRIZ DE DISTANCIAS
    distance_matrix = optimizer.get_distance_matrix(
        puntos_entrega_db,
        punto_inicio_coords,
        settings.GOOGLE_MAPS_API_KEY,
        dest_coords=destino_coords,
    )

    if distance_matrix is None:
        request.session['error_message'] = (
            'No se pudo obtener la matriz de distancias. '
            'Revisa la clave API o la conexión.'
        )
        return redirect('mapa')

    num_delivery_points = len(puntos_entrega_db)
    end_index = num_delivery_points + 1 if destino_coords is not None else None

    # 6) OPTIMIZAR RUTA
    optimized_route_indices, total_distance_km = optimizer.solve_tsp(
        distance_matrix,
        num_delivery_points,
        start_index=0,
        end_index=end_index,
    )

    if not optimized_route_indices:
        request.session['error_message'] = (
            'No se pudo optimizar la ruta. Verifica los puntos o el algoritmo.'
        )
        return redirect('mapa')

    # 7) GUARDAR ORDEN ÓPTIMO
    for i, matrix_idx in enumerate(optimized_route_indices[1:-1]):
        if 1 <= matrix_idx <= num_delivery_points:
            punto = puntos_entrega_db[matrix_idx - 1]
            punto.orden_optimo = i + 1
            punto.save()

    # 8) CONSUMO Y COSTO
    rendimiento_str = request.POST.get('rendimiento_vehiculo', '').strip()
    try:
        if rendimiento_str:
            rendimiento_vehiculo = float(rendimiento_str)
        else:
            rendimiento_vehiculo = DEFAULT_RENDIMIENTO
    except ValueError:
        rendimiento_vehiculo = DEFAULT_RENDIMIENTO

    fuel_consumed = optimizer.calculate_fuel_cost(total_distance_km, rendimiento_vehiculo)

    precio_bencina_str = request.POST.get('precio_bencina', '').strip()
    try:
        precio_bencina = float(precio_bencina_str) if precio_bencina_str else DEFAULT_FUEL_PRICE
    except ValueError:
        precio_bencina = DEFAULT_FUEL_PRICE

    fuel_cost = fuel_consumed * precio_bencina

    # Guardar en sesión
    request.session['total_distance_km'] = round(total_distance_km, 2)
    request.session['fuel_consumed_liters'] = round(fuel_consumed, 2)
    request.session['fuel_cost_clp'] = round(fuel_cost, 0)
    request.session['precio_bencina'] = precio_bencina
    request.session['rendimiento_vehiculo'] = rendimiento_vehiculo
    request.session['direccion_origen'] = direccion_origen
    request.session['direccion_destino'] = direccion_destino

    logger.info(
        f"Ruta optimizada por {request.user.username}: {total_distance_km:.2f} km, "
        f"{fuel_consumed:.2f} L, ${fuel_cost:.0f} CLP"
    )

    return redirect('mapa')


@login_required
def borrar_puntos(request):
    """
    Borra todos los puntos de entrega.
    """
    if request.method == "POST":
        count = PuntoEntrega.objects.count()
        PuntoEntrega.objects.all().delete()
        logger.warning(f"{count} puntos borrados por {request.user.username}")
        
        # Limpiar selección si borras todos
        if 'selected_ids' in request.session:
            del request.session['selected_ids']
    return redirect('mapa')


@login_required
@require_POST
def borrar_punto(request, punto_id):
    """
    Borra un solo punto de entrega (usado por el fetch JS).
    Devuelve JSON para que el frontend sepa si fue OK.
    """
    try:
        punto = get_object_or_404(PuntoEntrega, id=punto_id)
        nombre = punto.nombre
        punto.delete()
        
        logger.info(f"Punto #{punto_id} ({nombre}) borrado por {request.user.username}")

        # Actualizar la selección en sesión si existía
        selected_ids = request.session.get('selected_ids')
        if selected_ids:
            pid_str = str(punto_id)
            selected_ids = [sid for sid in selected_ids if sid != pid_str]
            request.session['selected_ids'] = selected_ids

        return JsonResponse({"ok": True})
    except Exception as e:
        logger.error(f"Error borrando punto #{punto_id}: {e}", exc_info=True)
        return JsonResponse({"ok": False, "error": str(e)}, status=500)