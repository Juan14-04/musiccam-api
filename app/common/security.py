"""Autorización: `requiere_roles` recarga siempre el usuario desde la BD (§4, RN-19)."""

import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import current_app, g
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request

from app.common.enums import EstadoUsuario, Rol
from app.common.errors import NoAutorizado, NoEncontrado, Prohibido
from app.modules.usuarios.models import Usuario
from app.modules.usuarios.repository import UsuarioRepository

# Para `@blp.doc(security=BEARER)` en los endpoints protegidos.
BEARER = [{"bearerAuth": []}]


def _cargar_usuario() -> Usuario:
    try:
        usuario_id = uuid.UUID(str(get_jwt_identity()))
    except ValueError as exc:
        raise NoAutorizado() from exc
    usuario = UsuarioRepository().obtener_por_id(usuario_id)
    if usuario is None:
        raise NoAutorizado()
    return usuario


def verificar_usuario_activo(usuario: Usuario) -> None:
    if usuario.estado != EstadoUsuario.ACTIVO:
        raise Prohibido("CUENTA_SUSPENDIDA", "Tu cuenta está suspendida.")


def requiere_roles(*roles: Rol) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Exige un access token válido y, si se indican, alguno de los roles dados.

    Sin argumentos basta con estar autenticado. El rol del token es solo una pista: se
    compara contra el que está en la BD.
    """

    def decorador(funcion: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(funcion)
        def envoltura(*args: Any, **kwargs: Any) -> Any:
            verify_jwt_in_request()
            usuario = _cargar_usuario()
            verificar_usuario_activo(usuario)
            if roles and usuario.rol not in roles:
                raise Prohibido("ROL_NO_PERMITIDO", "Tu rol no permite esta acción.")
            g.usuario_actual = usuario
            return funcion(*args, **kwargs)

        return envoltura

    return decorador


def usuario_opcional() -> Usuario | None:
    """Usuario autenticado si la petición trae token; `None` si no trae ninguno.

    Un token presente pero inválido sigue siendo 401, y una cuenta suspendida 403 (RN-19).
    """
    verify_jwt_in_request(optional=True)
    if get_jwt_identity() is None:
        return None
    usuario = _cargar_usuario()
    verificar_usuario_activo(usuario)
    g.usuario_actual = usuario
    return usuario


def solo_desarrollo(funcion: Callable[..., Any]) -> Callable[..., Any]:
    """Responde 404 si `ENABLE_DEV_ENDPOINTS` es falso. Va por fuera de `requiere_roles`."""

    @wraps(funcion)
    def envoltura(*args: Any, **kwargs: Any) -> Any:
        if not current_app.config["ENABLE_DEV_ENDPOINTS"]:
            raise NoEncontrado("RECURSO_NO_ENCONTRADO", "El recurso no existe.")
        return funcion(*args, **kwargs)

    return envoltura


def usuario_actual() -> Usuario:
    """Usuario autenticado de la petición (solo dentro de `@requiere_roles`)."""
    return g.usuario_actual
