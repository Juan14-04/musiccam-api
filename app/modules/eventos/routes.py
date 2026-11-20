"""Endpoints de eventos (§8.5)."""

import uuid

from flask.views import MethodView
from flask_smorest import Blueprint
from marshmallow import RAISE

from app.common.enums import Rol
from app.common.schemas import documentar_errores
from app.common.security import BEARER, requiere_roles, usuario_actual, usuario_opcional
from app.modules.eventos.schemas import (
    EventoActualizarSchema,
    EventoCrearSchema,
    EventoDetalleSchema,
    EventoFiltrosSchema,
    EventoPaginaSchema,
    EventoSchema,
    TrailerSubirSchema,
)
from app.modules.eventos.service import EventoService

blp = Blueprint(
    "Eventos", __name__, url_prefix="/api/v1/eventos", description="Catálogo y gestión de eventos."
)


@blp.route("")
class Eventos(MethodView):
    @blp.doc(
        summary="Catálogo público de eventos",
        description=(
            "RN-21: solo `PROGRAMADO`, `EN_VIVO` y `FINALIZADO` con replay disponible, sin "
            "suspensión de admin y de artistas activos. Ordenado por `fecha_inicio`."
        ),
    )
    @blp.arguments(EventoFiltrosSchema, location="query", unknown=RAISE)
    @blp.response(200, EventoPaginaSchema)
    @documentar_errores(blp, 422)
    def get(self, filtros):
        return EventoService().listar_catalogo(**filtros)

    @requiere_roles(Rol.ARTISTA)
    @blp.doc(
        security=BEARER,
        summary="Crear evento",
        description=(
            "RN-04: nace en `BORRADOR` y requiere un tráiler promocional de YouTube. Precio entero "
            "dentro del rango configurado. Crear y publicar no genera ningún cargo al artista (C2)."
        ),
    )
    @blp.arguments(EventoCrearSchema)
    @blp.response(201, EventoSchema)
    @documentar_errores(blp, 401, 403, 422)
    def post(self, datos):
        return EventoService().crear(usuario_actual(), datos)


@blp.route("/<uuid:evento_id>")
class EventoPorId(MethodView):
    @blp.doc(
        security=[{}, *BEARER],
        summary="Detalle de un evento",
        description=(
            "Público si cumple RN-21. Un `BORRADOR`, `CANCELADO` o suspendido solo lo ve su "
            "dueño (para los demás es 404). Con sesión añade `es_favorito` y `comprado`."
        ),
    )
    @blp.response(200, EventoDetalleSchema)
    @documentar_errores(blp, 401, 403, 404)
    def get(self, evento_id: uuid.UUID):
        return EventoService().obtener_detalle(evento_id, usuario_opcional())

    @requiere_roles(Rol.ARTISTA)
    @blp.doc(
        security=BEARER,
        summary="Editar evento propio",
        description=(
            "RN-04: en `BORRADOR` se edita todo; en `PROGRAMADO` título, descripción, portada, "
            "tráiler y categoría, y precio, fecha, duración y replay solo sin compras aprobadas. "
            "El resto de estados no se edita."
        ),
    )
    @blp.arguments(EventoActualizarSchema)
    @blp.response(200, EventoSchema)
    @documentar_errores(blp, 401, 403, 404, 409, 422)
    def patch(self, datos, evento_id: uuid.UUID):
        return EventoService().actualizar(usuario_actual(), evento_id, datos)

    @requiere_roles(Rol.ARTISTA)
    @blp.doc(
        security=BEARER,
        summary="Eliminar evento en borrador",
        description=(
            "RN-05: solo se elimina un `BORRADOR`; los demás estados responden 409 "
            "`TRANSICION_INVALIDA`."
        ),
    )
    @blp.response(204)
    @documentar_errores(blp, 401, 403, 404, 409)
    def delete(self, evento_id: uuid.UUID):
        EventoService().eliminar(usuario_actual(), evento_id)


@blp.route("/<uuid:evento_id>/publicar", methods=["POST"])
@requiere_roles(Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Publicar evento",
    description=(
        "`BORRADOR → PROGRAMADO` (RN-05). RN-04: `fecha_inicio` ≥ ahora + 30 min. RN-02: un "
        "artista sin eventos `FINALIZADO` puede tener hasta 3 `PROGRAMADO` a la vez."
    ),
)
@blp.response(200, EventoSchema)
@documentar_errores(blp, 401, 403, 404, 409, 422)
def publicar(evento_id: uuid.UUID):
    return EventoService().publicar(usuario_actual(), evento_id)


@blp.route("/<uuid:evento_id>/cancelar", methods=["POST"])
@requiere_roles(Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Cancelar evento",
    description="`BORRADOR`/`PROGRAMADO → CANCELADO` (RN-05, RN-13). `EN_VIVO` y `FINALIZADO` no.",
)
@blp.response(200, EventoSchema)
@documentar_errores(blp, 401, 403, 404, 409)
def cancelar(evento_id: uuid.UUID):
    return EventoService().cancelar(usuario_actual(), evento_id)


@blp.route("/<uuid:evento_id>/trailer")
class TrailerEvento(MethodView):
    @requiere_roles(Rol.ARTISTA)
    @blp.doc(
        security=BEARER,
        summary="Subir video del tráiler",
        description=(
            "RN-04: solo en `BORRADOR` o `PROGRAMADO`. Video MP4 o WebM (se valida por contenido) "
            "de hasta `MAX_TRAILER_MB` MB. Reemplaza el anterior y tiene prioridad sobre "
            "`youtube_url` al mostrar el tráiler."
        ),
    )
    @blp.arguments(TrailerSubirSchema, location="files", content_type="multipart/form-data")
    @blp.response(200, EventoSchema)
    @documentar_errores(blp, 401, 403, 404, 409, 413, 422)
    def put(self, datos, evento_id: uuid.UUID):
        return EventoService().subir_trailer(usuario_actual(), evento_id, datos["trailer"].read())

    @requiere_roles(Rol.ARTISTA)
    @blp.doc(
        security=BEARER,
        summary="Quitar video del tráiler",
        description=(
            "RN-04: solo en `BORRADOR` o `PROGRAMADO`. Borra el archivo y deja "
            "`trailer_video_url` en `null`. Idempotente."
        ),
    )
    @blp.response(200, EventoSchema)
    @documentar_errores(blp, 401, 403, 404, 409)
    def delete(self, evento_id: uuid.UUID):
        return EventoService().eliminar_trailer(usuario_actual(), evento_id)
