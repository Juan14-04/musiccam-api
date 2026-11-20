"""Endpoints de autenticación (§8.1)."""

from flask_jwt_extended import jwt_required
from flask_smorest import Blueprint

from app.common.schemas import documentar_errores
from app.extensions import limiter
from app.modules.auth.schemas import AccessTokenSchema, LoginSchema, RegistroSchema, TokensSchema
from app.modules.auth.service import AuthService
from app.modules.usuarios.schemas import UsuarioSchema

blp = Blueprint(
    "Auth", __name__, url_prefix="/api/v1/auth", description="Registro e inicio de sesión."
)


@blp.route("/registro", methods=["POST"])
@limiter.limit("10/hour")
@blp.doc(
    summary="Registrar usuario o artista",
    description=(
        "RN-01: exige `acepta_tratamiento_datos=true` y guarda fecha y versión de la política. "
        "RN-02: el artista se da de alta sin verificación manual. RN-03: política de contraseña."
    ),
)
@blp.arguments(RegistroSchema)
@blp.response(201, UsuarioSchema)
@documentar_errores(blp, 409, 422, 429)
def registro(datos):
    return AuthService().registrar(**datos)


@blp.route("/login", methods=["POST"])
@limiter.limit("5/minute")
@blp.doc(
    summary="Iniciar sesión",
    description=(
        "Mensaje genérico `CREDENCIALES_INVALIDAS` para no revelar si el correo existe (RN-03)."
    ),
)
@blp.arguments(LoginSchema)
@blp.response(200, TokensSchema)
@documentar_errores(blp, 401, 403, 422, 429)
def login(datos):
    return AuthService().login(**datos)


@blp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
@blp.doc(
    security=[{"bearerAuth": []}],
    summary="Renovar access token",
    description=(
        "Enviar el **refresh token** como Bearer. Verifica que la cuenta siga activa (RN-19)."
    ),
)
@blp.response(200, AccessTokenSchema)
@documentar_errores(blp, 401, 403)
def refresh():
    return AuthService().refrescar()
