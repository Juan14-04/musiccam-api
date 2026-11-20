"""Favoritos y artistas seguidos (§8.2). Salen en Swagger bajo el tag `Usuarios`."""

import uuid

from flask.views import MethodView
from flask_smorest import Blueprint
from marshmallow import RAISE

from app.common.enums import Rol
from app.common.schemas import PaginacionArgsSchema, documentar_errores
from app.common.security import BEARER, requiere_roles, usuario_actual
from app.modules.eventos.schemas import EventoPaginaSchema
from app.modules.social.service import SocialService
from app.modules.usuarios.schemas import ArtistaPublicoPaginaSchema

# El tag de cada ruta es `Usuarios` (§8.2); el nombre del blueprint solo evita colisiones.
TAGS = ["Usuarios"]
ROLES = (Rol.USUARIO, Rol.ARTISTA)

blp = Blueprint("Social", __name__, url_prefix="/api/v1/usuarios/me")


class _PaginacionEstricta(PaginacionArgsSchema):
    class Meta:
        unknown = RAISE


@blp.route("/favoritos", tags=TAGS)
class Favoritos(MethodView):
    @requiere_roles(*ROLES)
    @blp.doc(
        security=BEARER,
        summary="Mis eventos favoritos",
        description=(
            "Resumen de los eventos que marqué, del más reciente al más antiguo. Los eventos que "
            "dejaron de ser visibles (RN-21: cancelados, suspendidos…) no aparecen. `ADMIN`: 403."
        ),
    )
    @blp.arguments(_PaginacionEstricta, location="query", unknown=RAISE)
    @blp.response(200, EventoPaginaSchema)
    @documentar_errores(blp, 401, 403, 422)
    def get(self, paginacion):
        return SocialService().listar_favoritos(usuario_actual(), **paginacion)


@blp.route("/favoritos/<uuid:evento_id>", tags=TAGS)
class Favorito(MethodView):
    @requiere_roles(*ROLES)
    @blp.doc(
        security=BEARER,
        summary="Marcar un evento como favorito",
        description=(
            "RN-17: idempotente (repetirlo da 204 igual). Solo eventos visibles (RN-21); un "
            "borrador, cancelado o suspendido responde 404 `EVENTO_NO_ENCONTRADO`."
        ),
    )
    @blp.response(204)
    @documentar_errores(blp, 401, 403, 404)
    def put(self, evento_id: uuid.UUID):
        SocialService().agregar_favorito(usuario_actual(), evento_id)

    @requiere_roles(*ROLES)
    @blp.doc(
        security=BEARER,
        summary="Quitar un favorito",
        description="RN-17: idempotente; quitar algo que no estaba marcado también da 204.",
    )
    @blp.response(204)
    @documentar_errores(blp, 401, 403)
    def delete(self, evento_id: uuid.UUID):
        SocialService().quitar_favorito(usuario_actual(), evento_id)


@blp.route("/seguidos", tags=TAGS)
class Seguidos(MethodView):
    @requiere_roles(*ROLES)
    @blp.doc(
        security=BEARER,
        summary="Artistas que sigo",
        description=(
            "RN-17: solo `nombre_artistico`, `foto_url` y `biografia`; nunca el email ni otros "
            "datos "
            "privados. No incluye artistas suspendidos. `ADMIN`: 403."
        ),
    )
    @blp.arguments(_PaginacionEstricta, location="query", unknown=RAISE)
    @blp.response(200, ArtistaPublicoPaginaSchema)
    @documentar_errores(blp, 401, 403, 422)
    def get(self, paginacion):
        return SocialService().listar_seguidos(usuario_actual(), **paginacion)


@blp.route("/seguidos/<uuid:artista_id>", tags=TAGS)
class Seguido(MethodView):
    @requiere_roles(*ROLES)
    @blp.doc(
        security=BEARER,
        summary="Seguir a un artista",
        description=(
            "RN-17: idempotente. Solo a un usuario con rol `ARTISTA` y `ACTIVO` (si no existe, "
            "no es artista o está suspendido: 404 `USUARIO_NO_ENCONTRADO`); a uno mismo: 422."
        ),
    )
    @blp.response(204)
    @documentar_errores(blp, 401, 403, 404, 422)
    def put(self, artista_id: uuid.UUID):
        SocialService().seguir(usuario_actual(), artista_id)

    @requiere_roles(*ROLES)
    @blp.doc(
        security=BEARER,
        summary="Dejar de seguir a un artista",
        description="RN-17: idempotente; dejar de seguir a quien no seguía también da 204.",
    )
    @blp.response(204)
    @documentar_errores(blp, 401, 403)
    def delete(self, artista_id: uuid.UUID):
        SocialService().dejar_de_seguir(usuario_actual(), artista_id)
