"""DTOs de compras y pagos."""

from marshmallow import INCLUDE, RAISE, Schema, fields, validate

from app.common.enums import EstadoCompra, MetodoPago
from app.common.schemas import PaginacionArgsSchema, PaginaSchema
from app.modules.eventos.schemas import EventoResumenSchema


class CompraCrearSchema(Schema):
    """Solo el método de pago: el monto sale del evento guardado (§10.4)."""

    class Meta:
        unknown = RAISE

    metodo_pago = fields.Enum(
        MetodoPago,
        by_value=True,
        required=True,
        metadata={"description": "Medio de pago local.", "example": "NEQUI"},
    )


class CompraSchema(Schema):
    """Compra del comprador. No expone comisión ni neto del artista."""

    id = fields.UUID(dump_only=True)
    evento_id = fields.UUID(dump_only=True)
    referencia = fields.Str(
        dump_only=True,
        metadata={"description": "`reference` enviada a Wompi.", "example": "conc-3f2a9c..."},
    )
    monto_cop = fields.Int(
        dump_only=True,
        metadata={"description": "Precio congelado al comprar.", "example": 20000},
    )
    metodo_pago = fields.Enum(MetodoPago, by_value=True, dump_only=True)
    estado = fields.Enum(
        EstadoCompra,
        by_value=True,
        dump_only=True,
        metadata={"description": "`APROBADA` da acceso al evento (RN-08)."},
    )
    wompi_transaccion_id = fields.Str(dump_only=True, allow_none=True)
    expira_en = fields.AwareDateTime(dump_only=True)
    creado_en = fields.AwareDateTime(dump_only=True)
    aprobado_en = fields.AwareDateTime(dump_only=True, allow_none=True)


class MiCompraSchema(CompraSchema):
    """Una entrada propia: la compra (estado y monto pagado) más el resumen del evento."""

    evento = fields.Nested(EventoResumenSchema, dump_only=True)


class MisComprasFiltrosSchema(PaginacionArgsSchema):
    class Meta:
        unknown = RAISE

    estado = fields.Enum(EstadoCompra, by_value=True)
    proximos = fields.Bool(
        load_default=False,
        metadata={
            "description": "`true`: compras `APROBADA` de eventos `PROGRAMADO` o `EN_VIVO`, "
            "por `fecha_inicio` ascendente (equivale a «Mi calendario»)."
        },
    )


class MiCompraPaginaSchema(PaginaSchema):
    items = fields.List(fields.Nested(MiCompraSchema))


class CheckoutSchema(Schema):
    referencia = fields.Str(metadata={"example": "conc-3f2a9c..."})
    monto_en_centavos = fields.Int(metadata={"example": 2000000})
    moneda = fields.Str(metadata={"example": "COP"})
    firma_integridad = fields.Str(
        metadata={"description": "SHA256(referencia + monto_en_centavos + moneda + secreto)."}
    )
    checkout_url = fields.Str(metadata={"description": "Simulada: ruta del simulador de pago."})
    expira_en = fields.AwareDateTime()


class CompraCreadaSchema(Schema):
    compra = fields.Nested(CompraSchema)
    checkout = fields.Nested(CheckoutSchema)


class SimuladorSchema(Schema):
    class Meta:
        unknown = RAISE

    resultado = fields.Str(
        required=True,
        validate=validate.OneOf(["APPROVED", "DECLINED", "VOIDED", "ERROR"]),
        metadata={"description": "Cómo termina el checkout simulado.", "example": "APPROVED"},
    )


class WompiEventoSchema(Schema):
    """Evento `transaction.updated`. Se conserva el cuerpo íntegro: el checksum lo cubre."""

    class Meta:
        unknown = INCLUDE

    event = fields.Str(metadata={"example": "transaction.updated"})
    data = fields.Dict(
        metadata={
            "example": {
                "transaction": {
                    "id": "sim-0b1f...",
                    "reference": "conc-3f2a9c...",
                    "status": "APPROVED",
                    "amount_in_cents": 2000000,
                    "currency": "COP",
                    "payment_method_type": "NEQUI",
                }
            }
        }
    )
    environment = fields.Str(metadata={"example": "test"})
    signature = fields.Dict(
        metadata={
            "example": {
                "properties": [
                    "transaction.id",
                    "transaction.status",
                    "transaction.amount_in_cents",
                ],
                "checksum": "<sha256>",
            }
        }
    )
    timestamp = fields.Raw(metadata={"example": 1730000000})
    sent_at = fields.Str(metadata={"example": "2026-11-02T20:00:00Z"})


class WebhookRespuestaSchema(Schema):
    estado = fields.Str(metadata={"example": "recibido"})
