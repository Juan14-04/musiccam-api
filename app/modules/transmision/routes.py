"""Endpoints de transmisión y reproducción simuladas (§8.6)."""

import uuid

from flask_smorest import Blueprint

from app.common.enums import Rol
from app.common.schemas import documentar_errores
from app.common.security import BEARER, requiere_roles, solo_desarrollo, usuario_actual
from app.modules.transmision.schemas import (
    AudienciaSimuladaSchema,
    CredencialesSchema,
    EstadoTransmisionSchema,
    IniciarTransmisionSchema,
    ReproduccionSchema,
)
from app.modules.transmision.service import TransmisionService

blp = Blueprint(
    "Transmision",
    __name__,
    url_prefix="/api/v1",
    description="Emisión y reproducción simuladas (en vivo y replay).",
)


@blp.route("/eventos/<uuid:evento_id>/transmision/credenciales", methods=["POST"])
@requiere_roles(Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Generar o rotar la clave de emisión",
    description=(
        "Solo el dueño y con el evento `PROGRAMADO`. RN-11: la clave es aleatoria, se guarda "
        "solo su hash SHA-256 y se muestra **una única vez**. Pedirla de nuevo rota la clave y "
        "la anterior deja de valer."
    ),
)
@blp.response(200, CredencialesSchema)
@documentar_errores(blp, 401, 403, 404, 409)
def credenciales(evento_id: uuid.UUID):
    return TransmisionService().generar_credenciales(usuario_actual(), evento_id)


@blp.route("/eventos/<uuid:evento_id>/transmision/iniciar", methods=["POST"])
@requiere_roles(Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Iniciar la transmisión",
    description=(
        "`PROGRAMADO → EN_VIVO` (RN-05) solo con la clave de emisión válida (RQ6, RN-11). Un "
        "artista no puede tener dos eventos `EN_VIVO`. Con `ENFORCE_START_WINDOW=true` solo "
        "desde `fecha_inicio − START_WINDOW_MIN`."
    ),
)
@blp.arguments(IniciarTransmisionSchema)
@blp.response(200, EstadoTransmisionSchema)
@documentar_errores(blp, 401, 403, 404, 409, 422)
def iniciar(datos, evento_id: uuid.UUID):
    return TransmisionService().iniciar(usuario_actual(), evento_id, datos["stream_key"])


@blp.route("/eventos/<uuid:evento_id>/transmision/finalizar", methods=["POST"])
@requiere_roles(Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Finalizar la transmisión",
    description=(
        "`EN_VIVO → FINALIZADO` (RN-12): fija `fin_real`, pone `espectadores_actuales` en 0 y "
        "`replay_disponible = replay_habilitado`."
    ),
)
@blp.response(200, EstadoTransmisionSchema)
@documentar_errores(blp, 401, 403, 404, 409)
def finalizar(evento_id: uuid.UUID):
    return TransmisionService().finalizar(usuario_actual(), evento_id)


@blp.route("/eventos/<uuid:evento_id>/transmision", methods=["GET"])
@requiere_roles(Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Estado de la transmisión",
    description=(
        "Solo el dueño: estado, audiencia actual, pico y minutos transcurridos desde `inicio_real` "
        "(hasta `fin_real` al finalizar, RN-12)."
    ),
)
@blp.response(200, EstadoTransmisionSchema)
@documentar_errores(blp, 401, 403, 404)
def estado_transmision(evento_id: uuid.UUID):
    return TransmisionService().estado(usuario_actual(), evento_id)


@blp.route("/eventos/<uuid:evento_id>/reproduccion/entrar", methods=["POST"])
@requiere_roles(Rol.USUARIO, Rol.ARTISTA, Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Entrar a ver el evento (vivo o replay)",
    description=(
        "RN-10 (C4): evento suspendido → 403 `EVENTO_SUSPENDIDO`. Se entrega URL solo si el "
        "evento está `EN_VIVO` (`VIVO`) o `FINALIZADO` con `replay_disponible` (`REPLAY`) y el "
        "solicitante es comprador con compra `APROBADA`, el artista dueño o un `ADMIN`; si no, "
        "403 `ACCESO_NO_AUTORIZADO`. Cuenta la entrada con un `UPDATE` atómico."
    ),
)
@blp.response(200, ReproduccionSchema)
@documentar_errores(blp, 401, 403, 404)
def entrar(evento_id: uuid.UUID):
    return TransmisionService().entrar(usuario_actual(), evento_id)


@blp.route("/eventos/<uuid:evento_id>/reproduccion/salir", methods=["POST"])
@requiere_roles(Rol.USUARIO, Rol.ARTISTA, Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Salir de la reproducción",
    description=(
        "Descuenta un espectador de `espectadores_actuales` (nunca por debajo de 0). Exige "
        "(RN-10) "
        "tener derecho de acceso al evento, pero no depende de su estado actual."
    ),
)
@blp.response(204)
@documentar_errores(blp, 401, 403, 404)
def salir(evento_id: uuid.UUID):
    TransmisionService().salir(usuario_actual(), evento_id)


@blp.route("/dev/eventos/<uuid:evento_id>/audiencia-simulada", methods=["POST"])
@solo_desarrollo
@requiere_roles(Rol.ARTISTA, Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Fijar audiencia simulada (solo desarrollo)",
    description=(
        "Responde 404 si `ENABLE_DEV_ENDPOINTS` es falso. Dueño o `ADMIN`. Fija "
        "`espectadores_actuales` y sube `pico_espectadores` para poblar estadísticas en demos."
    ),
)
@blp.arguments(AudienciaSimuladaSchema)
@blp.response(200, EstadoTransmisionSchema)
@documentar_errores(blp, 401, 403, 404, 422)
def audiencia_simulada(datos, evento_id: uuid.UUID):
    return TransmisionService().audiencia_simulada(
        usuario_actual(), evento_id, datos["espectadores"]
    )
