"""Panel del artista (§8.4).

Panel: todo cuelga de `/artistas/me/...`. Público: `/artistas` y `/artistas/<uuid:artista_id>`;
no chocan, porque el conversor `uuid` nunca coincide con la palabra `me`.
"""

import uuid

from flask.views import MethodView
from flask_smorest import Blueprint
from marshmallow import RAISE

from app.common.enums import Rol
from app.common.schemas import documentar_errores
from app.common.security import BEARER, requiere_roles, usuario_actual, usuario_opcional
from app.modules.artistas.schemas import (
    ArtistaPerfilSchema,
    ArtistasFiltrosSchema,
    EstadisticasEventoSchema,
    EstadisticasSchema,
    RetiroCrearSchema,
    RetiroPaginaSchema,
    RetiroSchema,
    RetirosFiltrosSchema,
    SaldoSchema,
)
from app.modules.artistas.service import ArtistasService
from app.modules.eventos.schemas import EventoPaginaSchema, MisEventosFiltrosSchema
from app.modules.eventos.service import EventoService
from app.modules.usuarios.schemas import ArtistaPublicoPaginaSchema

blp = Blueprint(
    "Artista (panel)",
    __name__,
    url_prefix="/api/v1/artistas/me",
    description="Gestor de transmisión del artista: eventos, estadísticas, saldo y retiros.",
)


blp_publico = Blueprint(
    "Artistas",
    __name__,
    url_prefix="/api/v1/artistas",
    description="Perfiles públicos de artistas.",
)


@blp_publico.route("")
class Artistas(MethodView):
    @blp_publico.doc(
        summary="Lista de artistas",
        description=(
            "Pública y paginada, solo artistas `ACTIVO` (su catálogo respeta RN-21), por nombre "
            "artístico. "
            "Filtro `q`. "
            "Solo se muestran datos públicos (nunca el email)."
        ),
    )
    @blp_publico.arguments(ArtistasFiltrosSchema, location="query", unknown=RAISE)
    @blp_publico.response(200, ArtistaPublicoPaginaSchema)
    @documentar_errores(blp_publico, 422)
    def get(self, filtros):
        return ArtistasService().listar_publicos(**filtros)


@blp_publico.route("/<uuid:artista_id>")
class ArtistaPublico(MethodView):
    @blp_publico.doc(
        security=[{}, *BEARER],
        summary="Perfil público de un artista",
        description=(
            "Nombre artístico, foto, biografía, `seguidores` (solo el conteo) y "
            "`eventos_proximos` (visibles, `PROGRAMADO` o `EN_VIVO`). `siguiendo` aparece solo si "
            "hay sesión. Los eventos próximos son los visibles según RN-21. Un artista suspendido, "
            "inexistente o que no es `ARTISTA` responde 404. "
            "Las rutas `/artistas/me/...` del panel no chocan con esta: `uuid` nunca coincide "
            "con `me`."
        ),
    )
    @blp_publico.response(200, ArtistaPerfilSchema)
    @documentar_errores(blp_publico, 401, 403, 404)
    def get(self, artista_id: uuid.UUID):
        return ArtistasService().perfil_publico(artista_id, usuario_opcional())


@blp.route("/eventos")
class MisEventos(MethodView):
    @requiere_roles(Rol.ARTISTA)
    @blp.doc(
        security=BEARER,
        summary="Mis eventos",
        description="Todos los eventos propios en cualquier estado; filtro opcional `estado`.",
    )
    @blp.arguments(MisEventosFiltrosSchema, location="query", unknown=RAISE)
    @blp.response(200, EventoPaginaSchema)
    @documentar_errores(blp, 401, 403, 422)
    def get(self, filtros):
        return EventoService().listar_de_artista(usuario_actual(), **filtros)


@blp.route("/estadisticas", methods=["GET"])
@requiere_roles(Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Estadísticas globales",
    description=(
        "Resumen del artista (§8.4.1) y su saldo (RN-14). Solo cuentan compras `APROBADA`; las "
        "`REEMBOLSADA`, "
        "`ANULADA`, `RECHAZADA` y `PENDIENTE` nunca suman. `ventas_por_dia` agrupa por fecha "
        "en la zona horaria **America/Bogota** e incluye solo los días con ventas."
    ),
)
@blp.response(200, EstadisticasSchema)
@documentar_errores(blp, 401, 403)
def estadisticas():
    return ArtistasService().estadisticas(usuario_actual())


@blp.route("/eventos/<uuid:evento_id>/estadisticas", methods=["GET"])
@requiere_roles(Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Estadísticas de un evento",
    description=(
        "Solo el dueño (un evento ajeno responde 403 `NO_ES_DUENO`). Ingresos según RN-14: solo "
        "cuentan compras "
        "`APROBADA`. `duracion_real_min` es `null` si el evento no se transmitió o no terminó. "
        "`ventas_por_dia` usa la zona horaria **America/Bogota** y solo los días con ventas."
    ),
)
@blp.response(200, EstadisticasEventoSchema)
@documentar_errores(blp, 401, 403, 404)
def estadisticas_de_evento(evento_id: uuid.UUID):
    return ArtistasService().estadisticas_de_evento(usuario_actual(), evento_id)


@blp.route("/saldo", methods=["GET"])
@requiere_roles(Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Saldo del artista",
    description=(
        "RN-14. El saldo no se guarda: se calcula. `disponible = ingresos liberados (eventos "
        "`FINALIZADO`) − retiros `PENDIENTE` − retiros `PAGADO``; los `RECHAZADO` no restan. "
        "`retenido` es el neto de eventos `PROGRAMADO`/`EN_VIVO`."
    ),
)
@blp.response(200, SaldoSchema)
@documentar_errores(blp, 401, 403)
def saldo():
    return ArtistasService().saldo(usuario_actual())


@blp.route("/retiros", methods=["POST"])
@requiere_roles(Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Solicitar un retiro",
    description=(
        "RN-15: `monto_cop` ≥ mínimo (`RETIRO_MIN_COP`, 422 `MONTO_FUERA_DE_RANGO`) y ≤ "
        "disponible (422 `SALDO_INSUFICIENTE`). Se bloquea la fila del artista mientras se "
        "calcula y se inserta, así que dos retiros simultáneos no pueden exceder el saldo. "
        "Solo se guardan banco, tipo de cuenta y **los últimos 4 dígitos**. Nace `PENDIENTE`, o "
        "`PAGADO` con `referencia_pago` simulada si `RETIRO_AUTO_APROBAR=true`."
    ),
)
@blp.arguments(RetiroCrearSchema)
@blp.response(201, RetiroSchema)
@documentar_errores(blp, 401, 403, 422)
def solicitar_retiro(datos):
    return ArtistasService().solicitar_retiro(usuario_actual(), **datos)


@blp.route("/retiros", methods=["GET"])
@requiere_roles(Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Historial de retiros",
    description=(
        "Paginado, del más reciente al más antiguo; filtro opcional `estado`. Los retiros los "
        "resuelve un `ADMIN` (RN-16)."
    ),
)
@blp.arguments(RetirosFiltrosSchema, location="query", unknown=RAISE)
@blp.response(200, RetiroPaginaSchema)
@documentar_errores(blp, 401, 403, 422)
def listar_retiros(filtros):
    return ArtistasService().listar_retiros(usuario_actual(), **filtros)
