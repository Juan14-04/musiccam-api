"""DTOs del panel del artista."""

from marshmallow import RAISE, Schema, fields, pre_dump, validate

from app.common.enums import EstadoRetiro, TipoCuenta
from app.common.schemas import PaginacionArgsSchema, PaginaSchema
from app.modules.eventos.schemas import EventoResumenSchema
from app.modules.usuarios.schemas import ArtistaPublicoSchema

_ZONA = "America/Bogota"


class SaldoSchema(Schema):
    disponible_cop = fields.Int(
        metadata={
            "description": "`ingresos_liberados − en_proceso − retirado` (RN-14).",
            "example": 2900000,
        }
    )
    retenido_cop = fields.Int(
        metadata={
            "description": "Neto de eventos `PROGRAMADO`/`EN_VIVO`; se libera al finalizar.",
            "example": 400000,
        }
    )
    en_proceso_cop = fields.Int(
        metadata={"description": "Suma de retiros `PENDIENTE`.", "example": 300000}
    )
    retirado_cop = fields.Int(
        metadata={"description": "Suma de retiros `PAGADO`.", "example": 216000}
    )


class VentaDiariaSchema(Schema):
    fecha = fields.Date(metadata={"example": "2026-11-01"})
    entradas = fields.Int(metadata={"example": 12})
    ingresos_netos_cop = fields.Int(metadata={"example": 216000})


_DESCRIPCION_VENTAS_POR_DIA = (
    f"Solo los días con ventas, por fecha en la zona horaria {_ZONA} (UTC−5), de la más "
    "antigua a la más reciente. Cuenta compras `APROBADA` según su fecha de aprobación."
)


class ResumenSchema(Schema):
    eventos_por_estado = fields.Dict(
        keys=fields.Str(),
        values=fields.Int(),
        metadata={
            "description": "Siempre trae los 5 estados, con 0 si no hay eventos.",
            "example": {
                "BORRADOR": 1,
                "PROGRAMADO": 2,
                "EN_VIVO": 0,
                "FINALIZADO": 4,
                "CANCELADO": 0,
            },
        },
    )
    entradas_vendidas = fields.Int(metadata={"example": 212})
    ingresos_brutos_cop = fields.Int(metadata={"example": 4240000})
    comision_plataforma_cop = fields.Int(metadata={"example": 424000})
    ingresos_netos_cop = fields.Int(metadata={"example": 3816000})
    seguidores = fields.Int(metadata={"description": "Conteo real de seguidores.", "example": 87})
    pico_espectadores_promedio = fields.Float(
        metadata={
            "description": "Promedio del pico solo de eventos transmitidos (único decimal).",
            "example": 41.5,
        }
    )


class EstadisticasSchema(Schema):
    resumen = fields.Nested(ResumenSchema)
    saldo = fields.Nested(SaldoSchema)
    ventas_por_dia = fields.List(
        fields.Nested(VentaDiariaSchema), metadata={"description": _DESCRIPCION_VENTAS_POR_DIA}
    )


class EstadisticasEventoSchema(Schema):
    evento_id = fields.UUID()
    entradas_vendidas = fields.Int(metadata={"example": 40})
    ingresos_brutos_cop = fields.Int(metadata={"example": 800000})
    ingresos_netos_cop = fields.Int(metadata={"example": 720000})
    pico_espectadores = fields.Int(metadata={"example": 57})
    vistas_vivo = fields.Int(metadata={"example": 63})
    vistas_replay = fields.Int(metadata={"example": 12})
    duracion_real_min = fields.Int(
        allow_none=True,
        metadata={
            "description": "`fin_real − inicio_real`; `null` si no se transmitió o no terminó.",
            "example": 92,
        },
    )
    favoritos = fields.Int(metadata={"description": "Veces marcado como favorito.", "example": 25})
    ventas_por_dia = fields.List(
        fields.Nested(VentaDiariaSchema), metadata={"description": _DESCRIPCION_VENTAS_POR_DIA}
    )


class RetiroCrearSchema(Schema):
    """Nunca se acepta el número de cuenta completo: solo los últimos 4 dígitos."""

    class Meta:
        unknown = RAISE

    monto_cop = fields.Int(
        strict=True,
        required=True,
        metadata={
            "description": "COP enteros, entre el mínimo y tu disponible.",
            "example": 100000,
        },
    )
    banco = fields.Str(
        required=True, validate=validate.Length(min=1, max=60), metadata={"example": "Bancolombia"}
    )
    tipo_cuenta = fields.Enum(
        TipoCuenta, by_value=True, required=True, metadata={"example": "AHORROS"}
    )
    cuenta_ultimos4 = fields.Str(
        required=True,
        validate=validate.Regexp(
            r"^[0-9]{4}\Z", error="Deben ser exactamente 4 dígitos (solo los últimos 4)."
        ),
        metadata={"description": "Exactamente 4 dígitos, como texto.", "example": "0042"},
    )


class RetiroSchema(Schema):
    id = fields.UUID(dump_only=True)
    monto_cop = fields.Int(dump_only=True, metadata={"example": 100000})
    banco = fields.Str(dump_only=True, metadata={"example": "Bancolombia"})
    tipo_cuenta = fields.Enum(TipoCuenta, by_value=True, dump_only=True)
    cuenta_ultimos4 = fields.Str(dump_only=True, metadata={"example": "0042"})
    estado = fields.Enum(
        EstadoRetiro,
        by_value=True,
        dump_only=True,
        metadata={"description": "`PAGADO` de inmediato si `RETIRO_AUTO_APROBAR=true`."},
    )
    referencia_pago = fields.Str(
        dump_only=True, allow_none=True, metadata={"example": "sim-payout-5b1e..."}
    )
    motivo_rechazo = fields.Str(dump_only=True, allow_none=True)
    solicitado_en = fields.AwareDateTime(dump_only=True)
    resuelto_en = fields.AwareDateTime(dump_only=True, allow_none=True)


class RetirosFiltrosSchema(PaginacionArgsSchema):
    class Meta:
        unknown = RAISE

    estado = fields.Enum(EstadoRetiro, by_value=True)


class ArtistasFiltrosSchema(PaginacionArgsSchema):
    class Meta:
        unknown = RAISE

    q = fields.Str(
        validate=validate.Length(min=1, max=100),
        metadata={"description": "Busca dentro del nombre artístico (sin distinguir mayúsculas)."},
    )


class ArtistaPerfilSchema(ArtistaPublicoSchema):
    """Perfil público: sin email, rol, estado ni datos financieros."""

    seguidores = fields.Int(
        dump_only=True,
        metadata={"description": "Solo el conteo, nunca la lista.", "example": 87},
    )
    eventos_proximos = fields.List(
        fields.Nested(EventoResumenSchema),
        dump_only=True,
        metadata={
            "description": "Eventos visibles `PROGRAMADO` o `EN_VIVO`, por fecha (hasta 20)."
        },
    )
    siguiendo = fields.Bool(
        dump_only=True,
        metadata={"description": "Solo si la petición trae sesión: ¿sigo a este artista?"},
    )

    @pre_dump
    def _combinar(self, perfil, **kwargs):
        datos = {
            nombre: getattr(perfil.artista, nombre) for nombre in ArtistaPublicoSchema().fields
        }
        datos["seguidores"] = perfil.seguidores
        datos["eventos_proximos"] = perfil.eventos_proximos
        if perfil.siguiendo is not None:
            datos["siguiendo"] = perfil.siguiendo
        return datos


class RetiroPaginaSchema(PaginaSchema):
    items = fields.List(fields.Nested(RetiroSchema))
