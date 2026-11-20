"""Panel de administración (§8.8). Todo exige rol ADMIN, verificado contra la BD."""

import uuid

from flask_smorest import Blueprint
from marshmallow import RAISE

from app.common.enums import Rol
from app.common.schemas import documentar_errores
from app.common.security import BEARER, requiere_roles, usuario_actual
from app.modules.admin.schemas import (
    CompraAdminPaginaSchema,
    ComprasAdminFiltrosSchema,
    ConfiguracionSchema,
    DashboardSchema,
    EstadoUsuarioSchema,
    EventoAdminPaginaSchema,
    EventoAdminSchema,
    EventosAdminFiltrosSchema,
    MotivoSchema,
    RetiroAdminPaginaSchema,
    RetiroAdminSchema,
    RetirosAdminFiltrosSchema,
    RolUsuarioSchema,
    UsuarioPaginaSchema,
    UsuariosFiltrosSchema,
)
from app.modules.admin.service import AdminService
from app.modules.usuarios.schemas import UsuarioSchema

blp = Blueprint(
    "Admin",
    __name__,
    url_prefix="/api/v1/admin",
    description="Panel de administración. Todos los endpoints exigen rol `ADMIN`.",
)

# Los 401/403 aplican a todos los endpoints: sin token, o con un rol que no es ADMIN.


@blp.route("/dashboard", methods=["GET"])
@requiere_roles(Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Dashboard de métricas",
    description=(
        "Usuarios por rol y por estado, eventos por estado, entradas vendidas (compras "
        "`APROBADA`), ingresos brutos, comisiones acumuladas y retiros pendientes (cantidad y "
        "monto). Todo con consultas SQL agregadas."
    ),
)
@blp.response(200, DashboardSchema)
@documentar_errores(blp, 401, 403)
def dashboard():
    return AdminService().dashboard()


# --- Usuarios -----------------------------------------------------------------------------------


@blp.route("/usuarios", methods=["GET"])
@requiere_roles(Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Listar usuarios",
    description="Filtros `rol`, `estado` y `q` (nombre, email o nombre artístico). Paginado.",
)
@blp.arguments(UsuariosFiltrosSchema, location="query", unknown=RAISE)
@blp.response(200, UsuarioPaginaSchema)
@documentar_errores(blp, 401, 403, 422)
def listar_usuarios(filtros):
    return AdminService().listar_usuarios(**filtros)


@blp.route("/usuarios/<uuid:usuario_id>", methods=["GET"])
@requiere_roles(Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Detalle de un usuario",
    description="Nunca incluye `password_hash` ni datos financieros.",
)
@blp.response(200, UsuarioSchema)
@documentar_errores(blp, 401, 403, 404)
def detalle_usuario(usuario_id: uuid.UUID):
    return AdminService().obtener_usuario(usuario_id)


@blp.route("/usuarios/<uuid:usuario_id>/estado", methods=["PATCH"])
@requiere_roles(Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Suspender o reactivar un usuario",
    description=(
        "RN-19: un usuario `SUSPENDIDO` recibe 403 `CUENTA_SUSPENDIDA` en todo endpoint "
        "autenticado aunque su token siga vigente, y sus eventos salen del catálogo. RN-20: un "
        "admin no puede suspenderse a sí mismo (403) y nunca puede quedar el sistema sin un admin "
        "activo (409 `ULTIMO_ADMIN_ACTIVO`; se bloquean los admins activos antes de contar)."
    ),
)
@blp.arguments(EstadoUsuarioSchema)
@blp.response(200, UsuarioSchema)
@documentar_errores(blp, 401, 403, 404, 409, 422)
def cambiar_estado(datos, usuario_id: uuid.UUID):
    return AdminService().cambiar_estado(usuario_actual(), usuario_id, datos["estado"])


@blp.route("/usuarios/<uuid:usuario_id>/rol", methods=["PATCH"])
@requiere_roles(Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Cambiar el rol de un usuario",
    description=(
        "RN-20: no se puede cambiar el rol propio (403) ni dejar el sistema sin admins (409). "
        "Pasar a `ARTISTA` exige `nombre_artistico` (único). Pasar de `ARTISTA` a otro rol da "
        "409 `ROL_NO_CAMBIABLE` si tiene eventos `PROGRAMADO`/`EN_VIVO` o retiros `PENDIENTE`."
    ),
)
@blp.arguments(RolUsuarioSchema)
@blp.response(200, UsuarioSchema)
@documentar_errores(blp, 401, 403, 404, 409, 422)
def cambiar_rol(datos, usuario_id: uuid.UUID):
    return AdminService().cambiar_rol(
        usuario_actual(), usuario_id, datos["rol"], datos.get("nombre_artistico")
    )


# --- Eventos ------------------------------------------------------------------------------------


@blp.route("/eventos", methods=["GET"])
@requiere_roles(Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Listar todos los eventos",
    description=(
        "En cualquier estado, también borradores, cancelados y suspendidos. Filtros `q`, "
        "`categoria`, `estado`, `artista_id`, `suspendido`, `desde` y `hasta`."
    ),
)
@blp.arguments(EventosAdminFiltrosSchema, location="query", unknown=RAISE)
@blp.response(200, EventoAdminPaginaSchema)
@documentar_errores(blp, 401, 403, 422)
def listar_eventos(filtros):
    return AdminService().listar_eventos(**filtros)


@blp.route("/eventos/<uuid:evento_id>/suspender", methods=["POST"])
@requiere_roles(Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Suspender un evento",
    description=(
        "`suspendido_admin = true` con `motivo` obligatorio. Sale del catálogo (RN-21), no se "
        "puede comprar (RN-07), la reproducción responde 403 `EVENTO_SUSPENDIDO` (RN-10) y el "
        "artista no puede iniciar la transmisión. **No** finaliza ni cancela el evento. Si ya "
        "estaba suspendido: 409 `TRANSICION_INVALIDA`."
    ),
)
@blp.arguments(MotivoSchema)
@blp.response(200, EventoAdminSchema)
@documentar_errores(blp, 401, 403, 404, 409, 422)
def suspender_evento(datos, evento_id: uuid.UUID):
    return AdminService().suspender_evento(evento_id, datos["motivo"])


@blp.route("/eventos/<uuid:evento_id>/reactivar", methods=["POST"])
@requiere_roles(Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Reactivar un evento suspendido",
    description="Revierte la suspensión y borra el motivo. Si no estaba suspendido: 409.",
)
@blp.response(200, EventoAdminSchema)
@documentar_errores(blp, 401, 403, 404, 409)
def reactivar_evento(evento_id: uuid.UUID):
    return AdminService().reactivar_evento(evento_id)


# --- Compras y retiros --------------------------------------------------------------------------


@blp.route("/compras", methods=["GET"])
@requiere_roles(Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Listar compras",
    description=(
        "Solo lectura. Filtros `estado`, `evento_id`, `usuario_id` y rango de fechas "
        "(`desde`/`hasta` sobre `creado_en`). El comprador se muestra solo por nombre."
    ),
)
@blp.arguments(ComprasAdminFiltrosSchema, location="query", unknown=RAISE)
@blp.response(200, CompraAdminPaginaSchema)
@documentar_errores(blp, 401, 403, 422)
def listar_compras(filtros):
    return AdminService().listar_compras(**filtros)


@blp.route("/retiros", methods=["GET"])
@requiere_roles(Rol.ADMIN)
@blp.doc(security=BEARER, summary="Listar retiros", description="Filtro opcional `estado`.")
@blp.arguments(RetirosAdminFiltrosSchema, location="query", unknown=RAISE)
@blp.response(200, RetiroAdminPaginaSchema)
@documentar_errores(blp, 401, 403, 422)
def listar_retiros(filtros):
    return AdminService().listar_retiros(**filtros)


@blp.route("/retiros/<uuid:retiro_id>/aprobar", methods=["POST"])
@requiere_roles(Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Aprobar un retiro",
    description=(
        "RN-16: `PENDIENTE → PAGADO` con `referencia_pago` simulada (`sim-payout-<uuid>`), "
        "`resuelto_por` y `resuelto_en`. Un retiro ya resuelto responde 409 `RETIRO_YA_RESUELTO`; "
        "la fila se bloquea para que dos admins no lo resuelvan a la vez."
    ),
)
@blp.response(200, RetiroAdminSchema)
@documentar_errores(blp, 401, 403, 404, 409)
def aprobar_retiro(retiro_id: uuid.UUID):
    return AdminService().aprobar_retiro(usuario_actual(), retiro_id)


@blp.route("/retiros/<uuid:retiro_id>/rechazar", methods=["POST"])
@requiere_roles(Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Rechazar un retiro",
    description=(
        "RN-16: `motivo` obligatorio (422 si falta). Queda `RECHAZADO` y deja de restar del "
        "saldo del artista (RN-14). Un retiro ya resuelto responde 409 `RETIRO_YA_RESUELTO`."
    ),
)
@blp.arguments(MotivoSchema)
@blp.response(200, RetiroAdminSchema)
@documentar_errores(blp, 401, 403, 404, 409, 422)
def rechazar_retiro(datos, retiro_id: uuid.UUID):
    return AdminService().rechazar_retiro(usuario_actual(), retiro_id, datos["motivo"])


# --- Configuración ------------------------------------------------------------------------------


@blp.route("/configuracion", methods=["GET"])
@requiere_roles(Rol.ADMIN)
@blp.doc(
    security=BEARER,
    summary="Configuración vigente (solo lectura)",
    description=(
        "Comisión, mínimos y máximos, flags y modos de pasarela y streaming. Nunca incluye "
        "secretos ni credenciales."
    ),
)
@blp.response(200, ConfiguracionSchema)
@documentar_errores(blp, 401, 403)
def configuracion():
    return AdminService().configuracion()
