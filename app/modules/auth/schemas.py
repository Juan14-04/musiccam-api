"""DTOs de autenticación."""

from marshmallow import RAISE, Schema, ValidationError, fields, validate, validates_schema

from app.common.enums import Rol
from app.modules.usuarios.schemas import UsuarioSchema


def _validar_password(valor: str) -> None:
    """RN-03: al menos una letra y un dígito."""
    if not any(c.isalpha() for c in valor) or not any(c.isdigit() for c in valor):
        raise ValidationError("Debe contener al menos una letra y un dígito.")


class RegistroSchema(Schema):
    class Meta:
        unknown = RAISE

    nombre = fields.Str(
        required=True, validate=validate.Length(min=1, max=100), metadata={"example": "Ana"}
    )
    apellido = fields.Str(
        required=True, validate=validate.Length(min=1, max=100), metadata={"example": "Gómez"}
    )
    email = fields.Email(
        required=True, validate=validate.Length(max=254), metadata={"example": "ana@example.com"}
    )
    password = fields.Str(
        required=True,
        load_only=True,
        validate=[validate.Length(min=8, max=128), _validar_password],
        metadata={
            "description": "8–128 caracteres, con letra y dígito (RN-03).",
            "example": "Clave1234",
        },
    )
    rol = fields.Enum(
        Rol,
        by_value=True,
        required=True,
        validate=validate.OneOf([Rol.USUARIO, Rol.ARTISTA]),
        metadata={"description": "USUARIO o ARTISTA. ADMIN no se auto-registra (RN-01)."},
    )
    nombre_artistico = fields.Str(
        validate=validate.Length(min=1, max=100),
        metadata={"description": "Obligatorio si `rol=ARTISTA`.", "example": "Los Cumbieros"},
    )
    acepta_tratamiento_datos = fields.Bool(
        required=True,
        metadata={"description": "Debe ser `true` (Ley 1581 de 2012, RN-01).", "example": True},
    )

    @validates_schema
    def _artista_exige_nombre_artistico(self, datos, **kwargs):
        if datos.get("rol") == Rol.ARTISTA and not datos.get("nombre_artistico"):
            raise ValidationError("Obligatorio para artistas.", "nombre_artistico")


class LoginSchema(Schema):
    class Meta:
        unknown = RAISE

    email = fields.Str(required=True, metadata={"example": "ana@example.com"})
    password = fields.Str(required=True, load_only=True, metadata={"example": "Clave1234"})


class TokensSchema(Schema):
    access_token = fields.Str(metadata={"description": "JWT de acceso (15 min)."})
    refresh_token = fields.Str(metadata={"description": "JWT de refresco (7 días)."})
    usuario = fields.Nested(UsuarioSchema)


class AccessTokenSchema(Schema):
    access_token = fields.Str(metadata={"description": "JWT de acceso nuevo."})
