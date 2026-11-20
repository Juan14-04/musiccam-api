"""Esquemas comunes: error y paginación."""

from collections.abc import Callable
from typing import Any

from flask_smorest import Blueprint
from marshmallow import Schema, fields, validate


class ErrorSchema(Schema):
    codigo = fields.Str(
        metadata={"description": "Código estable del error.", "example": "VALIDACION"}
    )
    mensaje = fields.Str(metadata={"example": "Los datos enviados no son válidos."})
    detalles = fields.Dict(metadata={"description": "Detalle por campo, si aplica."})


class PaginaSchema(Schema):
    """Base de las respuestas paginadas; las subclases declaran `items`."""

    page = fields.Int(metadata={"example": 1})
    page_size = fields.Int(metadata={"example": 20})
    total = fields.Int(metadata={"example": 134})


class PaginacionArgsSchema(Schema):
    page = fields.Int(load_default=1, validate=validate.Range(min=1))
    page_size = fields.Int(
        load_default=20,
        validate=validate.Range(min=1),
        metadata={"description": "Máximo 100; valores mayores se recortan."},
    )


_DESCRIPCIONES = {
    401: "No autenticado.",
    403: "Sin permiso.",
    404: "No existe.",
    409: "Conflicto.",
    413: "Archivo demasiado grande.",
    422: "Validación.",
    429: "Demasiadas peticiones.",
    503: "Servicio no disponible.",
}


def documentar_errores(blp: Blueprint, *codigos: int) -> Callable[[Callable], Callable]:
    """Documenta en Swagger las respuestas de error con `ErrorSchema`."""

    def decorador(funcion: Callable[..., Any]) -> Callable[..., Any]:
        for codigo in reversed(codigos):
            alt = blp.alt_response(codigo, schema=ErrorSchema, description=_DESCRIPCIONES[codigo])
            funcion = alt(funcion)
        return funcion

    return decorador
