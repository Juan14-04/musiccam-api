"""Endpoints de perfil propio (§8.2)."""

from flask.views import MethodView
from flask_smorest import Blueprint

from app.common.schemas import documentar_errores
from app.common.security import BEARER, requiere_roles, usuario_actual
from app.modules.usuarios.schemas import FotoSubirSchema, UsuarioActualizarSchema, UsuarioSchema
from app.modules.usuarios.service import UsuarioService

blp = Blueprint(
    "Usuarios", __name__, url_prefix="/api/v1/usuarios", description="Perfil del usuario."
)


@blp.route("/me")
class PerfilPropio(MethodView):
    @requiere_roles()
    @blp.doc(
        security=BEARER,
        summary="Perfil propio",
        description=(
            "Devuelve el perfil del usuario autenticado. El rol y el estado se leen de la BD en "
            "cada petición (RN-19). Nunca incluye `password_hash`."
        ),
    )
    @blp.response(200, UsuarioSchema)
    @documentar_errores(blp, 401, 403)
    def get(self):
        """Devuelve el perfil del usuario autenticado."""
        return usuario_actual()

    @requiere_roles()
    @blp.doc(
        security=BEARER,
        summary="Editar perfil propio",
        description="Edita `nombre`, `biografia` y `nombre_artistico` (solo artistas, único).",
    )
    @blp.arguments(UsuarioActualizarSchema)
    @blp.response(200, UsuarioSchema)
    @documentar_errores(blp, 401, 403, 409, 422)
    def patch(self, datos):
        return UsuarioService().actualizar_perfil(usuario_actual(), datos)


@blp.route("/me/foto")
class FotoPropia(MethodView):
    @requiere_roles()
    @blp.doc(
        security=BEARER,
        summary="Subir foto de perfil",
        description=(
            "RN-18: JPEG/PNG/WebP de hasta 2 MB. Se valida por contenido, se corrige la "
            "orientación, se elimina el EXIF y se guarda como WebP 512×512. Reemplaza la anterior."
        ),
    )
    @blp.arguments(FotoSubirSchema, location="files", content_type="multipart/form-data")
    @blp.response(200, UsuarioSchema)
    @documentar_errores(blp, 401, 403, 422)
    def put(self, datos):
        return UsuarioService().subir_foto(usuario_actual(), datos["foto"].read())

    @requiere_roles()
    @blp.doc(
        security=BEARER,
        summary="Quitar foto de perfil",
        description="RN-18: borra el archivo guardado y deja `foto_url` en `null`. Idempotente.",
    )
    @blp.response(200, UsuarioSchema)
    @documentar_errores(blp, 401, 403)
    def delete(self):
        return UsuarioService().eliminar_foto(usuario_actual())
