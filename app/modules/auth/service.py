"""Casos de uso de autenticación: registro (RN-01), login y refresh."""

import uuid
from typing import Any

from flask import current_app
from flask_jwt_extended import create_access_token, create_refresh_token, get_jwt_identity
from werkzeug.security import check_password_hash, generate_password_hash

from app.common.enums import Rol
from app.common.errors import NoAutorizado, ReglaDeNegocio
from app.common.security import verificar_usuario_activo
from app.modules.usuarios.models import Usuario
from app.modules.usuarios.service import UsuarioService

# Hash de relleno: login con correo inexistente cuesta lo mismo que con uno existente.
_HASH_RELLENO = generate_password_hash("relleno-no-valido-1")

_CREDENCIALES_INVALIDAS = NoAutorizado("CREDENCIALES_INVALIDAS", "Correo o contraseña incorrectos.")


class AuthService:
    """Gestiona el registro, el inicio de sesión y la renovación de credenciales."""

    def __init__(self, usuarios: UsuarioService | None = None) -> None:
        self.usuarios = usuarios or UsuarioService()

    def registrar(
        self,
        *,
        nombre: str,
        apellido: str | None = None,
        email: str,
        password: str,
        rol: Rol,
        acepta_tratamiento_datos: bool,
        nombre_artistico: str | None = None,
    ) -> Usuario:
        """Crea una cuenta permitida y registra la aceptación de la política de datos."""
        if not acepta_tratamiento_datos:
            raise ReglaDeNegocio(
                "CONSENTIMIENTO_REQUERIDO",
                "Debes aceptar el tratamiento de datos personales para registrarte.",
            )
        if rol not in (Rol.USUARIO, Rol.ARTISTA):
            raise ReglaDeNegocio(detalles={"rol": ["Solo USUARIO o ARTISTA."]})
        return self.usuarios.crear(
            nombre=nombre,
            apellido=apellido,
            email=email,
            password_hash=generate_password_hash(password),
            rol=rol,
            nombre_artistico=nombre_artistico,
            version_politica=current_app.config["POLITICA_DATOS_VERSION"],
        )

    def login(self, *, email: str, password: str) -> dict[str, Any]:
        """Verifica las credenciales y devuelve tokens junto con el usuario autenticado."""
        usuario = self.usuarios.obtener_por_email(email)
        hash_guardado = usuario.password_hash if usuario else _HASH_RELLENO
        if not check_password_hash(hash_guardado, password) or usuario is None:
            raise _CREDENCIALES_INVALIDAS
        verificar_usuario_activo(usuario)
        return {
            "access_token": create_access_token(
                identity=str(usuario.id), additional_claims={"rol": usuario.rol.value}
            ),
            "refresh_token": create_refresh_token(identity=str(usuario.id)),
            "usuario": usuario,
        }

    def refrescar(self) -> dict[str, str]:
        """Emite un access token nuevo; recarga al usuario para respetar suspensiones."""
        try:
            usuario = self.usuarios.obtener(uuid.UUID(str(get_jwt_identity())))
        except Exception as exc:
            raise NoAutorizado() from exc
        verificar_usuario_activo(usuario)
        return {
            "access_token": create_access_token(
                identity=str(usuario.id), additional_claims={"rol": usuario.rol.value}
            )
        }
