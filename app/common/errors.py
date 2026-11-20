"""Errores de negocio unificados (§13) y manejadores globales."""

import logging
from typing import Any

from flask import Flask, jsonify
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Error base. Los servicios lo lanzan sin conocer códigos HTTP."""

    status = 500
    codigo_defecto = "ERROR_INTERNO"
    mensaje_defecto = "Error interno del servidor."

    def __init__(
        self,
        codigo: str | None = None,
        mensaje: str | None = None,
        detalles: dict[str, Any] | None = None,
    ) -> None:
        self.codigo = codigo or self.codigo_defecto
        self.mensaje = mensaje or self.mensaje_defecto
        self.detalles = detalles or {}
        super().__init__(self.mensaje)


class PeticionInvalida(AppError):
    status = 400
    codigo_defecto = "PETICION_INVALIDA"
    mensaje_defecto = "La petición está mal formada."


class NoAutorizado(AppError):
    status = 401
    codigo_defecto = "NO_AUTENTICADO"
    mensaje_defecto = "Debes iniciar sesión."


class Prohibido(AppError):
    status = 403
    codigo_defecto = "ROL_NO_PERMITIDO"
    mensaje_defecto = "No tienes permiso para esta acción."


class NoEncontrado(AppError):
    status = 404
    codigo_defecto = "RECURSO_NO_ENCONTRADO"
    mensaje_defecto = "El recurso no existe."


class Conflicto(AppError):
    status = 409
    codigo_defecto = "CONFLICTO"
    mensaje_defecto = "La operación entra en conflicto con el estado actual."


class ReglaDeNegocio(AppError):
    status = 422
    codigo_defecto = "VALIDACION"
    mensaje_defecto = "Los datos enviados no son válidos."


class ServicioNoDisponible(AppError):
    status = 503
    codigo_defecto = "BASE_DE_DATOS_NO_DISPONIBLE"
    mensaje_defecto = "El servicio no está disponible."


_HTTP_ESTANDAR: dict[int, tuple[str, str]] = {
    400: ("PETICION_INVALIDA", "La petición está mal formada."),
    401: ("NO_AUTENTICADO", "Debes iniciar sesión."),
    403: ("ROL_NO_PERMITIDO", "No tienes permiso para esta acción."),
    404: ("RECURSO_NO_ENCONTRADO", "El recurso no existe."),
    405: ("METODO_NO_PERMITIDO", "Método HTTP no permitido para esta ruta."),
    413: ("ARCHIVO_DEMASIADO_GRANDE", "La petición supera el tamaño máximo permitido."),
    415: ("TIPO_MEDIO_NO_SOPORTADO", "Tipo de contenido no soportado."),
    422: ("VALIDACION", "Los datos enviados no son válidos."),
    429: ("DEMASIADAS_PETICIONES", "Demasiadas peticiones. Intenta más tarde."),
}

_UBICACIONES = {"json", "query", "form", "files", "headers", "cookies"}


def respuesta_error(
    status: int, codigo: str, mensaje: str, detalles: dict[str, Any] | None = None
) -> ResponseReturnValue:
    cuerpo = {"codigo": codigo, "mensaje": mensaje, "detalles": detalles or {}}
    return jsonify(cuerpo), status


def _aplanar_mensajes(mensajes: Any) -> dict[str, Any]:
    """Convierte `{"json": {"campo": [...]}}` de smorest en `{"campo": [...]}`."""
    if not isinstance(mensajes, dict):
        return {}
    plano: dict[str, Any] = {}
    for clave, valor in mensajes.items():
        if clave in _UBICACIONES and isinstance(valor, dict):
            plano.update(valor)
        else:
            plano[clave] = valor
    return plano


def registrar_manejadores_errores(app: Flask) -> None:
    @app.errorhandler(AppError)
    def _app_error(exc: AppError) -> ResponseReturnValue:
        return respuesta_error(exc.status, exc.codigo, exc.mensaje, exc.detalles)

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException) -> ResponseReturnValue:
        status = exc.code or 500
        codigo, mensaje = _HTTP_ESTANDAR.get(status, ("ERROR_HTTP", exc.name))
        datos = getattr(exc, "data", None) or {}
        detalles = _aplanar_mensajes(datos.get("messages"))
        respuesta = app.make_response(respuesta_error(status, codigo, mensaje, detalles))
        for clave, valor in (getattr(exc, "headers", None) or {}).items():
            respuesta.headers[clave] = valor
        return respuesta

    @app.errorhandler(Exception)
    def _error_inesperado(exc: Exception) -> ResponseReturnValue:
        logger.exception("Error no controlado")
        return respuesta_error(500, "ERROR_INTERNO", "Error interno del servidor.")
