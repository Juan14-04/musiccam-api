"""Endpoints de compras y pagos con Wompi simulado (§8.7)."""

import uuid

from flask import request
from flask_smorest import Blueprint
from marshmallow import RAISE

from app.common.enums import Rol
from app.common.schemas import documentar_errores
from app.common.security import BEARER, requiere_roles, usuario_actual
from app.extensions import limiter
from app.modules.compras.schemas import (
    CompraCreadaSchema,
    CompraCrearSchema,
    CompraSchema,
    MiCompraPaginaSchema,
    MisComprasFiltrosSchema,
    SimuladorSchema,
    WebhookRespuestaSchema,
    WompiEventoSchema,
)
from app.modules.compras.service import ComprasService, PagosService

blp = Blueprint("Compras", __name__, url_prefix="/api/v1", description="Compra de entradas.")


@blp.route("/eventos/<uuid:evento_id>/compras", methods=["POST"])
@requiere_roles(Rol.USUARIO, Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Comprar entrada",
    description=(
        "RN-07: `USUARIO` o `ARTISTA` (no el dueño del evento) sobre un evento a la venta. "
        "Crea una compra `PENDIENTE` con precio y comisión congelados (RN-06) y devuelve el "
        "checkout simulado. Un pago aprobado da acceso automático (RN-08)."
    ),
)
@blp.arguments(CompraCrearSchema)
@blp.response(201, CompraCreadaSchema)
@documentar_errores(blp, 401, 403, 404, 409, 422)
def comprar(datos, evento_id: uuid.UUID):
    compra, checkout = ComprasService().crear(usuario_actual(), evento_id, datos["metodo_pago"])
    return {"compra": compra, "checkout": checkout}


@blp.route("/usuarios/me/compras", methods=["GET"], tags=["Usuarios"])
@requiere_roles(Rol.USUARIO, Rol.ARTISTA)
@blp.doc(
    security=BEARER,
    summary="Mis entradas",
    description=(
        "Compras del propio usuario (una `APROBADA` es el acceso al evento, RN-08) con el "
        "resumen del "
        "evento, el estado de la compra y el monto "
        "pagado. Filtros: `estado` y `proximos=true` («Mis eventos / Mi calendario»: compras "
        "`APROBADA` de eventos `PROGRAMADO` o `EN_VIVO`, por `fecha_inicio` ascendente). Sin "
        "`proximos`, de la más reciente a la más antigua. Un `ADMIN` recibe 403."
    ),
)
@blp.arguments(MisComprasFiltrosSchema, location="query", unknown=RAISE)
@blp.response(200, MiCompraPaginaSchema)
@documentar_errores(blp, 401, 403, 422)
def mis_compras(filtros):
    return ComprasService().listar_de_usuario(usuario_actual(), **filtros)


@blp.route("/compras/<uuid:compra_id>", methods=["GET"])
@requiere_roles()
@blp.doc(
    security=BEARER,
    summary="Detalle de una compra",
    description=(
        "Solo su dueño o un `ADMIN`. El frontend la consulta hasta que deje de ser `PENDIENTE`."
    ),
)
@blp.response(200, CompraSchema)
@documentar_errores(blp, 401, 403, 404)
def detalle_compra(compra_id: uuid.UUID):
    return ComprasService().obtener(usuario_actual(), compra_id)


@blp.route("/pagos/wompi/simulador/<string:referencia>", methods=["POST"])
@requiere_roles()
@blp.doc(
    security=BEARER,
    summary="Simular el resultado del checkout",
    description=(
        "Solo con `PAYMENT_GATEWAY=wompi_simulado`. Genera un evento `transaction.updated` "
        "firmado y lo procesa por el mismo camino que `POST /webhooks/wompi` (RN-09). "
        "Solo el dueño de la compra."
    ),
)
@blp.arguments(SimuladorSchema)
@blp.response(200, CompraSchema)
@documentar_errores(blp, 401, 403, 404, 422)
def simular_pago(datos, referencia: str):
    return PagosService().simular(usuario_actual(), referencia, datos["resultado"])


@blp.route("/webhooks/wompi", methods=["POST"])
@limiter.limit("120/minute")
@blp.doc(
    summary="Webhook de Wompi",
    description=(
        "Recibe `transaction.updated`. Verifica el checksum (cabecera `X-Event-Checksum` o "
        "`signature.checksum`), compara el monto con el de la compra guardada y es idempotente "
        "(RN-09). Responde 200 siempre, salvo firma inválida (401)."
    ),
)
@blp.arguments(WompiEventoSchema)
@blp.response(200, WebhookRespuestaSchema)
@documentar_errores(blp, 401, 422, 429)
def webhook_wompi(payload):
    PagosService().procesar_evento_wompi(payload, request.headers.get("X-Event-Checksum"))
    return {"estado": "recibido"}
