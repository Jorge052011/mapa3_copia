// static/js/main.js

let map;
let markers = [];
let directionsService = null;
let directionsRenderer = null;

function initMap() {
    console.log("initMap llamado");

    let center = { lat: -36.827, lng: -73.050 }; // Concepción

    if (typeof origen_coords !== "undefined" && origen_coords && origen_coords.lat && origen_coords.lng) {
        center = { lat: origen_coords.lat, lng: origen_coords.lng };
    } else if (Array.isArray(puntos_entrega_data) && puntos_entrega_data.length > 0) {
        center = {
            lat: puntos_entrega_data[0].latitud,
            lng: puntos_entrega_data[0].longitud
        };
    }

    map = new google.maps.Map(document.getElementById("map"), {
        center: center,
        zoom: 12
    });

    directionsService = new google.maps.DirectionsService();
    directionsRenderer = new google.maps.DirectionsRenderer({
        suppressMarkers: true
    });
    directionsRenderer.setMap(map);

    renderPuntosEntrega();
}

function renderPuntosEntrega() {
    clearMap();

    const bounds = new google.maps.LatLngBounds();
    const path = [];

    // ORIGEN
    if (typeof origen_coords !== "undefined" && origen_coords && origen_coords.lat && origen_coords.lng) {
        const originPos = { lat: origen_coords.lat, lng: origen_coords.lng };

        const originMarker = new google.maps.Marker({
            position: originPos,
            map: map,
            label: "O",
            title: "Origen del recorrido"
        });

        markers.push(originMarker);
        path.push(originPos);
        bounds.extend(originPos);
    }

    // PUNTOS DE ENTREGA
    if (Array.isArray(puntos_entrega_data) && puntos_entrega_data.length > 0) {
        const anyOrden = puntos_entrega_data.some(
            (p) => p.orden_optimo !== null && p.orden_optimo !== undefined
        );

        const puntos = [...puntos_entrega_data];

        if (anyOrden) {
            puntos.sort((a, b) => {
                if (a.orden_optimo == null) return 1;
                if (b.orden_optimo == null) return -1;
                return a.orden_optimo - b.orden_optimo;
            });
        }

        puntos.forEach((p, index) => {
            const position = { lat: p.latitud, lng: p.longitud };

            const labelText = (anyOrden && p.orden_optimo)
                ? String(p.orden_optimo)
                : String(index + 1);

            const marker = new google.maps.Marker({
                position: position,
                map: map,
                label: labelText,
                title: `${p.nombre} - ${p.direccion}`
            });

            markers.push(marker);
            path.push(position);
            bounds.extend(position);
        });
    }

    // DESTINO
    if (typeof destino_coords !== "undefined" && destino_coords && destino_coords.lat && destino_coords.lng) {
        const destPos = { lat: destino_coords.lat, lng: destino_coords.lng };

        const destMarker = new google.maps.Marker({
            position: destPos,
            map: map,
            label: "F",
            title: "Fin del recorrido"
        });

        markers.push(destMarker);
        path.push(destPos);
        bounds.extend(destPos);
    }

    if (!bounds.isEmpty()) {
        map.fitBounds(bounds);
    }

    if (path.length > 1) {
        drawRouteWithDirectionsAPI(path);
    }
}

function drawRouteWithDirectionsAPI(path) {
    if (!directionsService || !directionsRenderer) return;
    if (!Array.isArray(path) || path.length < 2) return;

    const origin = path[0];
    const destination = path[path.length - 1];

    const waypoints = path.slice(1, -1).map((pos) => ({
        location: pos,
        stopover: true
    }));

    const request = {
        origin: origin,
        destination: destination,
        waypoints: waypoints,
        travelMode: google.maps.TravelMode.DRIVING,
        optimizeWaypoints: false
    };

    directionsService.route(request, (result, status) => {
        console.log("Directions status:", status);
        if (status === "OK" || status === google.maps.DirectionsStatus.OK) {
            directionsRenderer.setDirections(result);
        } else {
            console.error("Error en Directions API:", status);
        }
    });
}

function clearMap() {
    if (markers.length > 0) {
        markers.forEach((m) => m.setMap(null));
        markers = [];
    }

    if (directionsRenderer) {
        directionsRenderer.set("directions", null);
    }
}

function toggleOrigenCustom() {
    const select = document.getElementById("origen_predefinido");
    const wrapper = document.getElementById("origen_custom_wrapper");

    if (!select || !wrapper) return;

    wrapper.style.display = (select.value === "custom") ? "block" : "none";
}

function toggleDestinoCustom() {
    const select = document.getElementById("destino_predefinido");
    const wrapper = document.getElementById("destino_custom_wrapper");

    if (!select || !wrapper) return;

    wrapper.style.display = (select.value === "custom") ? "block" : "none";
}

// ===================== CSRF =====================
// ===================== CSRF (ROBUSTO EN CODESPACES) =====================
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
        const cookies = document.cookie.split(";");
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + "=")) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

// 1) Intenta leer CSRF desde <meta name="csrf-token" ...> (más confiable)
// 2) Si no existe, cae a cookie "csrftoken"
function getCSRFToken() {
    // 1) token inyectado por template (lo más confiable)
    if (window.CSRF_TOKEN && window.CSRF_TOKEN !== "NOTPROVIDED") {
        return window.CSRF_TOKEN;
    }

    // 2) meta tag
    const meta = document.querySelector('meta[name="csrf-token"]');
    const metaToken = meta ? meta.getAttribute("content") : null;
    if (metaToken && metaToken !== "NOTPROVIDED") {
        return metaToken;
    }

    // 3) fallback cookie
    return getCookie("csrftoken");
}

// ===================== MODAL REUTILIZABLE =====================
let pendingAction = null;

function openConfirmModal({ title, text, confirmText, onConfirm }) {
    const modal = document.getElementById("confirmModal");
    const titleEl = document.getElementById("confirmTitle");
    const textEl = document.getElementById("confirmText");
    const btnConfirm = document.getElementById("btnConfirm");

    // Si no existe el modal, fallback a confirm (no rompe el mapa)
    if (!modal || !titleEl || !textEl || !btnConfirm) {
        if (confirm(text || "¿Seguro?")) onConfirm();
        return;
    }

    titleEl.textContent = title || "Confirmar";
    textEl.textContent = text || "¿Seguro que deseas continuar?";
    btnConfirm.textContent = confirmText || "Aceptar";

    pendingAction = onConfirm;

    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
}

function closeConfirmModal() {
    const modal = document.getElementById("confirmModal");
    if (!modal) return;
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
    pendingAction = null;
}

document.addEventListener("DOMContentLoaded", () => {
    const modal = document.getElementById("confirmModal");
    const btnCancel = document.getElementById("btnCancel");
    const btnConfirm = document.getElementById("btnConfirm");

    if (btnCancel) btnCancel.addEventListener("click", closeConfirmModal);

    if (btnConfirm) {
        btnConfirm.addEventListener("click", () => {
            if (typeof pendingAction === "function") pendingAction();
        });
    }

    // click fuera para cerrar
    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) closeConfirmModal();
        });
    }

    // ESC para cerrar
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeConfirmModal();
    });
});

// ===================== ELIMINAR 1 PUNTO (MODAL) =====================
// ===================== ELIMINAR 1 PUNTO (MODAL) =====================
function eliminarPunto(url) {
    openConfirmModal({
        title: "Eliminar punto",
        text: "¿Seguro que quieres eliminar este punto de entrega?",
        confirmText: "Eliminar",
        onConfirm: () => {
            const csrftoken = getCSRFToken();
            console.log("CSRF usado:", csrftoken);

            fetch(url, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "X-CSRFToken": csrftoken,
                    "X-Requested-With": "XMLHttpRequest",
                },
            })
            .then((response) => {
                if (!response.ok) {
                    alert("No se pudo borrar. Status: " + response.status);
                    throw new Error("HTTP " + response.status);
                }
                return response.json();
            })
            .then((data) => {
                if (data.ok) {
                    location.reload();
                } else {
                    alert("El servidor respondió, pero no confirmó el borrado.");
                    console.error("Detalle error:", data.error);
                }
            })
            .catch((err) => {
                console.error(err);
                alert("No se pudo borrar el punto");
            })
            .finally(() => closeConfirmModal());
        }
    });
}

// ===================== BORRAR TODOS (MODAL) =====================
function borrarTodosPuntos(url) {
    openConfirmModal({
        title: "Borrar todos",
        text: "¿Seguro que quieres borrar TODOS los puntos de entrega?",
        confirmText: "Borrar todo",
        onConfirm: () => {
            const csrftoken = getCSRFToken();
            console.log("csrftoken:", csrftoken);

            fetch(url, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "X-CSRFToken": csrftoken,
                    "X-Requested-With": "XMLHttpRequest",
                },
            })
            .then((response) => {
                if (!response.ok) {
                    console.error("Error al borrar todos. Status:", response.status);
                    throw new Error("Error al borrar todos los puntos");
                }
                return response.json().catch(() => ({}));
            })
            .then((data) => {
                if (!data.ok && data.ok !== undefined) {
                    alert("El servidor respondió, pero no confirmó el borrado total.");
                    console.error("Detalle error:", data.error);
                    return;
                }
                location.reload();
            })
            .catch((err) => {
                console.error(err);
                alert("No se pudieron borrar todos los puntos");
            })
            .finally(() => closeConfirmModal());
        }
    });
}


// Exponer funciones globales
window.initMap = initMap;
window.clearMap = clearMap;
window.toggleOrigenCustom = toggleOrigenCustom;
window.toggleDestinoCustom = toggleDestinoCustom;
window.eliminarPunto = eliminarPunto;
window.borrarTodosPuntos = borrarTodosPuntos;
