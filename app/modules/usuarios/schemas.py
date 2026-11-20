"""DTOs de usuarios."""

from flask_smorest.fields import Upload
from marshmallow import RAISE, Schema, fields, validate

from app.common.enums import EstadoUsuario, Rol
from app.common.schemas import PaginaSchema


class UsuarioSchema(Schema):
    """Perfil de usuario. Nunca incluye `password_hash`."""

    id = fields.UUID(dump_only=True)
    email = fields.Email(dump_only=True, metadata={"example": "ana@example.com"})
    nombre = fields.Str(dump_only=True, metadata={"example": "Ana"})
    apellido = fields.Str(dump_only=True, allow_none=True, metadata={"example": "Gómez"})
    rol = fields.Enum(Rol, by_value=True, dump_only=True)
    estado = fields.Enum(EstadoUsuario, by_value=True, dump_only=True)
    foto_url = fields.Str(
        dump_only=True,
        allow_none=True,
        metadata={"example": "/uploads/perfiles/3f2a.../1b9c....webp"},
    )
    nombre_artistico = fields.Str(
        dump_only=True, allow_none=True, metadata={"example": "Los Cumbieros del Río"}
    )
    biografia = fields.Str(dump_only=True, allow_none=True)
    consentimiento_datos_en = fields.AwareDateTime(dump_only=True)
    version_politica_datos = fields.Str(dump_only=True, metadata={"example": "v1"})
    creado_en = fields.AwareDateTime(dump_only=True)


class ArtistaPublicoSchema(Schema):
    """Lo único que se muestra de un artista a terceros: nunca email, rol ni estado."""

    id = fields.UUID(dump_only=True)
    nombre_artistico = fields.Str(dump_only=True, metadata={"example": "Los Cumbieros"})
    foto_url = fields.Str(
        dump_only=True, allow_none=True, metadata={"example": "/uploads/perfiles/3f2a.../1b9c.webp"}
    )
    biografia = fields.Str(
        dump_only=True,
        allow_none=True,
        metadata={"example": "Cumbia y vallenato desde Barranquilla."},
    )


class ArtistaPublicoPaginaSchema(PaginaSchema):
    items = fields.List(fields.Nested(ArtistaPublicoSchema))


class UsuarioActualizarSchema(Schema):
    """Edición parcial del perfil propio."""

    class Meta:
        unknown = RAISE

    nombre = fields.Str(validate=validate.Length(min=1, max=100), metadata={"example": "Ana"})
    apellido = fields.Str(validate=validate.Length(min=1, max=100), metadata={"example": "Gómez"})
    biografia = fields.Str(
        allow_none=True,
        validate=validate.Length(max=1000),
        metadata={"description": "Máximo 1000 caracteres."},
    )
    nombre_artistico = fields.Str(
        validate=validate.Length(min=1, max=100),
        metadata={"description": "Solo artistas. Único (RN-02)."},
    )


class FotoSubirSchema(Schema):
    foto = Upload(
        required=True,
        metadata={"description": "Imagen JPEG, PNG o WebP de hasta 2 MB (RN-18)."},
    )
