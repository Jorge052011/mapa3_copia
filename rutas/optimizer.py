# rutas/optimizer.py
import requests
import json
from django.conf import settings
import itertools
import time


# --- PARTE 1: Obtener Distancias/Tiempos de Google Maps ---
def get_distance_matrix(points, origin_coords, api_key, dest_coords=None):
    """
    Obtiene la matriz de distancias entre:
    - origen
    - todos los puntos de entrega
    - (opcional) destino
    
    CORREGIDO: Divide la solicitud en bloques para no exceder 
    el límite de 100 elementos de Google Maps API
    """
    all_points_coords = [f"{origin_coords['latitud']},{origin_coords['longitud']}"]

    for p in points:
        all_points_coords.append(f"{p.latitud},{p.longitud}")

    if dest_coords is not None:
        all_points_coords.append(f"{dest_coords['latitud']},{dest_coords['longitud']}")

    n = len(all_points_coords)
    
    # Inicializar matriz con infinitos
    distance_matrix = [[float('inf')] * n for _ in range(n)]
    
    # Diagonal = 0 (distancia a sí mismo)
    for i in range(n):
        distance_matrix[i][i] = 0.0
    
    # LÍMITE DE GOOGLE MAPS: 100 elementos por solicitud
    # Con origins×destinations, si tenemos N puntos:
    # Para no exceder 100, dividimos en bloques de máx 10×10 = 100
    MAX_LOCATIONS_PER_CALL = 10
    
    url = "https://maps.googleapis.com/maps/api/distancematrix/json"
    
    # Dividir en bloques
    for i in range(0, n, MAX_LOCATIONS_PER_CALL):
        for j in range(0, n, MAX_LOCATIONS_PER_CALL):
            # Bloque de orígenes
            origins_block = all_points_coords[i:i + MAX_LOCATIONS_PER_CALL]
            # Bloque de destinos
            destinations_block = all_points_coords[j:j + MAX_LOCATIONS_PER_CALL]
            
            origins_str = "|".join(origins_block)
            destinations_str = "|".join(destinations_block)
            
            params = {
                "origins": origins_str,
                "destinations": destinations_str,
                "mode": "driving",
                "key": api_key
            }
            
            try:
                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()
                
                if data['status'] == 'OK':
                    # Procesar resultados del bloque
                    for row_idx, row_data in enumerate(data['rows']):
                        origin_global_idx = i + row_idx
                        for col_idx, element in enumerate(row_data['elements']):
                            dest_global_idx = j + col_idx
                            
                            if element['status'] == 'OK':
                                # Convertir metros a kilómetros
                                distance_km = element['distance']['value'] / 1000.0
                                distance_matrix[origin_global_idx][dest_global_idx] = distance_km
                            else:
                                print(f"⚠️ Error en elemento [{origin_global_idx}][{dest_global_idx}]: {element['status']}")
                                distance_matrix[origin_global_idx][dest_global_idx] = float('inf')
                else:
                    error_msg = data.get('error_message', 'Sin mensaje de error')
                    print(f"❌ Error en Distance Matrix API: {data['status']} - {error_msg}")
                    return None
                
                # Pausa entre solicitudes para evitar rate limiting
                time.sleep(0.2)
                
            except requests.exceptions.Timeout:
                print(f"⏱️ Timeout en solicitud de bloque [{i}:{i+MAX_LOCATIONS_PER_CALL}] x [{j}:{j+MAX_LOCATIONS_PER_CALL}]")
                return None
            except requests.exceptions.RequestException as e:
                print(f"❌ Error de conexión con Google Maps API: {e}")
                return None
            except json.JSONDecodeError as e:
                print(f"❌ Error al decodificar JSON: {e}")
                return None
    
    return distance_matrix


# --- PARTE 2: TSP Solver con Nearest Neighbor + 2-opt ---

def solve_tsp(distance_matrix, num_points_entrega, start_index=0, end_index=None):
    """
    Resuelve el TSP con algoritmo híbrido:
    - Fuerza bruta para <= 9 puntos (rápido y óptimo)
    - Nearest Neighbor + 2-opt para >= 10 puntos (rápido pero aproximado)
    
    Args:
        distance_matrix: matriz de distancias
        num_points_entrega: cantidad de puntos de entrega
        start_index: índice del origen
        end_index: índice del destino (None = ciclo cerrado)
    
    Returns:
        (ruta_optima, distancia_total)
    """
    if not distance_matrix or num_points_entrega == 0:
        return [], 0.0

    delivery_indices = list(range(1, num_points_entrega + 1))

    # ✅ Fuerza bruta para <= 9 puntos (óptimo garantizado)
    if num_points_entrega <= 9:
        return _solve_tsp_bruteforce(
            distance_matrix, delivery_indices, start_index, end_index
        )
    
    # ✅ Nearest Neighbor + 2-opt para >= 10 puntos (heurística)
    return _solve_tsp_heuristic(
        distance_matrix, delivery_indices, start_index, end_index
    )


def _solve_tsp_bruteforce(distance_matrix, delivery_indices, start_index, end_index):
    """Fuerza bruta - O(n!) pero óptimo garantizado"""
    min_distance = float('inf')
    best_route = []

    for permutation in itertools.permutations(delivery_indices):
        if end_index is None:
            current_route_indices = [start_index] + list(permutation) + [start_index]
        else:
            current_route_indices = [start_index] + list(permutation) + [end_index]

        current_distance = 0.0

        for i in range(len(current_route_indices) - 1):
            origin_idx = current_route_indices[i]
            dest_idx = current_route_indices[i + 1]
            segment_distance = distance_matrix[origin_idx][dest_idx]
            if segment_distance == float('inf'):
                current_distance = float('inf')
                break
            current_distance += segment_distance

        if current_distance < min_distance:
            min_distance = current_distance
            best_route = current_route_indices

    return best_route, min_distance


def _solve_tsp_heuristic(distance_matrix, delivery_indices, start_index, end_index):
    """
    Nearest Neighbor + 2-opt - O(n²) mucho más rápido para n grande
    """
    # 1) Construir ruta inicial con Nearest Neighbor
    unvisited = set(delivery_indices)
    route = [start_index]
    current = start_index

    while unvisited:
        nearest = min(
            unvisited,
            key=lambda x: distance_matrix[current][x]
        )
        route.append(nearest)
        unvisited.remove(nearest)
        current = nearest

    # Agregar punto final
    if end_index is None:
        route.append(start_index)
    else:
        route.append(end_index)

    # 2) Mejorar con 2-opt
    route = _two_opt(distance_matrix, route)

    # 3) Calcular distancia total
    total_distance = 0.0
    for i in range(len(route) - 1):
        segment = distance_matrix[route[i]][route[i + 1]]
        if segment == float('inf'):
            return route, float('inf')
        total_distance += segment

    return route, total_distance


def _two_opt(distance_matrix, route):
    """
    Optimización local 2-opt: invierte segmentos de la ruta
    para reducir cruces y mejorar la distancia total.
    """
    improved = True
    best_route = route[:]

    while improved:
        improved = False
        for i in range(1, len(best_route) - 2):
            for j in range(i + 1, len(best_route)):
                if j - i == 1:
                    continue

                new_route = best_route[:]
                # Invertir segmento [i:j]
                new_route[i:j] = reversed(best_route[i:j])

                # Calcular distancia
                old_dist = _route_distance(distance_matrix, best_route)
                new_dist = _route_distance(distance_matrix, new_route)

                if new_dist < old_dist:
                    best_route = new_route
                    improved = True
                    break
            if improved:
                break

    return best_route


def _route_distance(distance_matrix, route):
    """Calcula distancia total de una ruta"""
    total = 0.0
    for i in range(len(route) - 1):
        d = distance_matrix[route[i]][route[i + 1]]
        if d == float('inf'):
            return float('inf')
        total += d
    return total


# --- PARTE 3: Cálculos de Consumo ---
AUTO_RENDIMIENTO_KM_POR_LITRO = 12  # valor por defecto


def calculate_fuel_cost(total_distance_km, rendimiento_km_por_litro=AUTO_RENDIMIENTO_KM_POR_LITRO):
    """
    Calcula los litros de combustible consumidos.
    """
    if total_distance_km == float('inf'):
        return float('inf')
    if rendimiento_km_por_litro <= 0:
        return float('inf')
    return total_distance_km / rendimiento_km_por_litro


def calculate_fuel_consumption(total_distance_km, rendimiento_km_por_litro=AUTO_RENDIMIENTO_KM_POR_LITRO):
    """Alias para compatibilidad"""
    return calculate_fuel_cost(total_distance_km, rendimiento_km_por_litro)