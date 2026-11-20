"""DTOs del panel de administración."""

from marshmallow import RAISE, Schema, ValidationError, fields, validate

from app.common.enums import (
    CategoriaEvento,
    EstadoCompra,
    EstadoEvento,
    EstadoRetiro,
    EstadoUsuario,
    MetodoPago,
    Rol,
    TipoCuenta,
)
from app.common.schemas import PaginacionArgsSchema, PaginaSchema
from app.modules.eventos.schemas import ArtistaResumenSchema, EventoResumenSchema
from app.modules.usuarios.schemas import UsuarioSchema


def _no_vacio(valor: str) -> None:
    if not valor.strip():
        raise ValidationError("No puede estar vacío.")


class _Estricto(Schema):
    class Meta:
        unknown = RAISE


class _FiltrosEstrictos(PaginacionArgsSchema):
    class Meta:
        unknown = RAISE


# --- Dashboard ----------------------------------------------------------------------------------


class UsuariosAgregadosSchema(Schema):
    por_rol = fields.Dict(
        keys=fields.Str(),
        values=fields.Int(),
        metadata={"example": {"USUARIO": 120, "ARTISTA": 14, "ADMIN": 2}},
    )
    por_estado = fields.Dict(
        keys=fields.Str(),
        values=fields.Int(),
        metadata={"example": {"ACTIVO": 134, "SUSPENDIDO": 2}},
    )


class RetirosPendientesSchema(Schema):
    cantidad = fields.Int(metadata={"example": 3})
    monto_cop = fields.Int(metadata={"example": 450000})


class DashboardSchema(Schema):
    usuarios = fields.Nested(UsuariosAgregadosSchema)
    eventos_por_estado = fields.Dict(
        keys=fields.Str(),
        values=fields.Int(),
        metadata={
            "example": {
                "BORRADOR": 4,
                "PROGRAMADO": 9,
                "EN_VIVO": 1,
                "FINALIZADO": 22,
                "CANCELADO": 2,
            }
        },
    )
    entradas_vendidas = fields.Int(metadata={"description": "Compras `APROBADA`.", "example": 812})
    ingresos_brutos_cop = fields.Int(metadata={"example": 16240000})
    comisiones_acumuladas_cop = fields.Int(metadata={"example": 1624000})
    retiros_pendientes = fields.Nested(RetirosPendientesSchema)


# --- Usuarios -----------------------------------------------------------------------------------


class UsuariosFiltrosSchema(_FiltrosEstrictos):
    rol = fields.Enum(Rol, by_value=True)
    estado = fields.Enum(EstadoUsuario, by_value=True)
    q = fields.Str(
        validate=validate.Length(min=1, max=100),
        metadata={"description": "Busca en nombre, email y nombre artístico."},
    )


class UsuarioPaginaSchema(PaginaSchema):
    items = fields.List(fields.Nested(UsuarioSchema))


class EstadoUsuarioSchema(_Estricto):
    estado = fields.Enum(
        EstadoUsuario,
        by_value=True,
        required=True,
        metadata={
            "description": "`SUSPENDIDO` corta el acceso de inmediato (RN-19).",
            "example": "SUSPENDIDO",
        },
    )


class RolUsuarioSchema(_Estricto):
    rol = fields.Enum(
        Rol,
        by_value=True,
        required=True,
        validate=validate.OneOf([Rol.USUARIO.value, Rol.ARTISTA.value]),
        metadata={
            "description": (
                "Solo permite `USUARIO` o `ARTISTA`; un `ADMIN` se crea en la base de datos."
            ),
            "example": "ARTISTA",
        },
    )
    nombre_artistico = fields.Str(
        validate=validate.Length(min=1, max=100),
        metadata={"description": "Obligatorio al pasar a `ARTISTA`.", "example": "Los Cumbieros"},
    )


# --- Eventos ------------------------------------------------------------------------------------


class EventoAdminSchema(EventoResumenSchema):
    suspendido_admin = fields.Bool(dump_only=True)
    motivo_suspension = fields.Str(
        dump_only=True, allow_none=True, metadata={"example": "Contenido no permitido"}
    )
    creado_en = fields.AwareDateTime(dump_only=True)


class EventosAdminFiltrosSchema(_FiltrosEstrictos):
    q = fields.Str(validate=validate.Length(min=1, max=100))
    categoria = fields.Enum(CategoriaEvento, by_value=True)
    estado = fields.Enum(EstadoEvento, by_value=True)
    artista_id = fields.UUID()
    suspendido = fields.Bool(metadata={"description": "`true`: solo los suspendidos."})
    desde = fields.AwareDateTime(metadata={"description": "`fecha_inicio` mayor o igual."})
    hasta = fields.AwareDateTime(metadata={"description": "`fecha_inicio` menor o igual."})


class EventoAdminPaginaSchema(PaginaSchema):
    items = fields.List(fields.Nested(EventoAdminSchema))


class MotivoSchema(_Estricto):
    motivo = fields.Str(
        required=True,
        validate=[validate.Length(min=1, max=255), _no_vacio],
        metadata={"description": "Obligatorio.", "example": "Incumple las normas de la plataforma"},
    )


# --- Compras ------------------------------------------------------------------------------------


class UsuarioResumenSchema(Schema):
    """Solo el nombre: nunca el correo completo."""

    id = fields.UUID(dump_only=True)
    nombre = fields.Str(dump_only=True, metadata={"example": "Ana Gómez"})


class EventoMinimoSchema(Schema):
    id = fields.UUID(dump_only=True)
    titulo = fields.Str(dump_only=True)
    estado = fields.Enum(EstadoEvento, by_value=True, dump_only=True)
    fecha_inicio = fields.AwareDateTime(dump_only=True)


class CompraAdminSchema(Schema):
    id = fields.UUID(dump_only=True)
    referencia = fields.Str(dump_only=True, metadata={"example": "conc-3f2a9c..."})
    estado = fields.Enum(EstadoCompra, by_value=True, dump_only=True)
    metodo_pago = fields.Enum(MetodoPago, by_value=True, dump_only=True)
    monto_cop = fields.Int(dump_only=True, metadata={"example": 20000})
    comision_cop = fields.Int(dump_only=True, metadata={"example": 2000})
    neto_artista_cop = fields.Int(dump_only=True, metadata={"example": 18000})
    wompi_transaccion_id = fields.Str(dump_only=True, allow_none=True)
    creado_en = fields.AwareDateTime(dump_only=True)
    aprobado_en = fields.AwareDateTime(dump_only=True, allow_none=True)
    usuario = fields.Nested(UsuarioResumenSchema, dump_only=True)
    evento = fields.Nested(EventoMinimoSchema, dump_only=True)


class ComprasAdminFiltrosSchema(_FiltrosEstrictos):
    estado = fields.Enum(EstadoCompra, by_value=True)
    evento_id = fields.UUID()
    usuario_id = fields.UUID()
    desde = fields.AwareDateTime(metadata={"description": "`creado_en` mayor o igual."})
    hasta = fields.AwareDateTime(metadata={"description": "`creado_en` menor o igual."})


class CompraAdminPaginaSchema(PaginaSchema):
    items = fields.List(fields.Nested(CompraAdminSchema))


# --- Retiros ------------------------------------------------------------------------------------


class RetiroAdminSchema(Schema):
    id = fields.UUID(dump_only=True)
    artista = fields.Nested(ArtistaResumenSchema, dump_only=True)
    monto_cop = fields.Int(dump_only=True, metadata={"example": 100000})
    banco = fields.Str(dump_only=True, metadata={"example": "Bancolombia"})
    tipo_cuenta = fields.Enum(TipoCuenta, by_value=True, dump_only=True)
    cuenta_ultimos4 = fields.Str(dump_only=True, metadata={"example": "0042"})
    estado = fields.Enum(EstadoRetiro, by_value=True, dump_only=True)
    referencia_pago = fields.Str(
        dump_only=True, allow_none=True, metadata={"example": "sim-payout-5b1e..."}
    )
    motivo_rechazo = fields.Str(dump_only=True, allow_none=True)
    resuelto_por = fields.UUID(dump_only=True, allow_none=True)
    solicitado_en = fields.AwareDateTime(dump_only=True)
    resuelto_en = fields.AwareDateTime(dump_only=True, allow_none=True)


class RetirosAdminFiltrosSchema(_FiltrosEstrictos):
    estado = fields.Enum(EstadoRetiro, by_value=True)


class RetiroAdminPaginaSchema(PaginaSchema):
    items = fields.List(fields.Nested(RetiroAdminSchema))


# --- Configuración ------------------------------------------------------------------------------


class ConfiguracionSchema(Schema):
    """Solo lectura. Nunca incluye secretos ni credenciales."""

    entorno = fields.Str(metadata={"example": "production"})
    comision_plataforma_pct = fields.Float(metadata={"example": 10.0})
    precio_min_cop = fields.Int(metadata={"example": 5000})
    precio_max_cop = fields.Int(metadata={"example": 500000})
    retiro_min_cop = fields.Int(metadata={"example": 50000})
    retiro_auto_aprobar = fields.Bool(metadata={"example": False})
    compra_pendiente_ttl_min = fields.Int(metadata={"example": 30})
    max_eventos_programados_nuevo = fields.Int(metadata={"example": 3})
    politica_datos_version = fields.Str(metadata={"example": "v1"})
    pasarela_pagos = fields.Str(metadata={"example": "wompi_simulado"})
    proveedor_streaming = fields.Str(metadata={"example": "simulado"})
    enforce_start_window = fields.Bool(metadata={"example": True})
    start_window_min = fields.Int(metadata={"example": 30})
    playback_token_ttl_min = fields.Int(metadata={"example": 10})
    jwt_access_ttl_min = fields.Int(metadata={"example": 15})
    jwt_refresh_ttl_dias = fields.Int(metadata={"example": 7})
    max_upload_mb = fields.Int(metadata={"example": 2})
    enable_dev_endpoints = fields.Bool(metadata={"example": False})
    media_base_url = fields.Str(metadata={"example": "http://localhost:8888"})
    rtmp_base_url = fields.Str(metadata={"example": "rtmp://localhost:1935/live"})
