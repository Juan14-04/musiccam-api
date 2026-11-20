"""Wompi simulado: misma forma de checkout, firma y evento que el real (§10.3)."""

import hashlib
import hmac
import uuid
from typing import Any

from app.common.clock import ahora_utc
from app.infrastructure.payments.base import (
    ESTADOS_WOMPI,
    MONEDA,
    CheckoutDTO,
    CompraPagable,
    FirmaInvalida,
    PaymentGateway,
    TransaccionDTO,
)

EVENTO_TRANSACCION = "transaction.updated"
# Propiedades que toda firma debe cubrir: sin ellas el monto o el estado irían sin firmar.
PROPIEDADES_OBLIGATORIAS = (
    "transaction.id",
    "transaction.status",
    "transaction.amount_in_cents",
)


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def _valor(datos: Any, ruta: str) -> str:
    """Resuelve `transaction.id` dentro de `data`, como hace Wompi."""
    actual = datos
    for parte in ruta.split("."):
        if not isinstance(actual, dict) or parte not in actual:
            raise FirmaInvalida
        actual = actual[parte]
    return str(actual)


class WompiSimuladoGateway(PaymentGateway):
    soporta_simulacion = True

    def __init__(self, integrity_secret: str, events_secret: str) -> None:
        self._integrity_secret = integrity_secret
        self._events_secret = events_secret

    # --- Checkout --------------------------------------------------------------------------

    def crear_checkout(self, compra: CompraPagable) -> CheckoutDTO:
        centavos = compra.monto_cop * 100
        firma = _sha256(f"{compra.referencia}{centavos}{MONEDA}{self._integrity_secret}")
        return CheckoutDTO(
            referencia=compra.referencia,
            monto_en_centavos=centavos,
            moneda=MONEDA,
            firma_integridad=firma,
            checkout_url=f"/api/v1/pagos/wompi/simulador/{compra.referencia}",
            expira_en=compra.expira_en,
        )

    # --- Eventos ---------------------------------------------------------------------------

    def _checksum(self, data: Any, propiedades: list[str], timestamp: Any) -> str:
        valores = "".join(_valor(data, propiedad) for propiedad in propiedades)
        return _sha256(f"{valores}{timestamp}{self._events_secret}")

    def verificar_evento(self, payload: dict, checksum_header: str | None) -> TransaccionDTO:
        try:
            firma = payload["signature"]
            propiedades = list(firma["properties"])
            recibido = checksum_header or firma["checksum"]
            data = payload["data"]
            timestamp = payload["timestamp"]
            if payload.get("event") != EVENTO_TRANSACCION or not isinstance(recibido, str):
                raise FirmaInvalida
            if not set(PROPIEDADES_OBLIGATORIAS) <= set(propiedades):
                raise FirmaInvalida
            esperado = self._checksum(data, propiedades, timestamp)
            if not hmac.compare_digest(esperado.encode(), recibido.encode()):
                raise FirmaInvalida
            transaccion = data["transaction"]
            return TransaccionDTO(
                id=str(transaccion["id"]),
                referencia=str(transaccion["reference"]),
                estado=str(transaccion["status"]),
                monto_en_centavos=int(transaccion["amount_in_cents"]),
                moneda=str(transaccion.get("currency", MONEDA)),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise FirmaInvalida from exc

    def construir_evento_simulado(self, compra: CompraPagable, resultado: str) -> dict:
        if resultado not in ESTADOS_WOMPI:
            raise ValueError(f"Resultado de Wompi desconocido: {resultado}")
        ahora = ahora_utc()
        timestamp = int(ahora.timestamp())
        data = {
            "transaction": {
                "id": compra.wompi_transaccion_id or f"sim-{uuid.uuid4()}",
                "reference": compra.referencia,
                "status": resultado,
                "amount_in_cents": compra.monto_cop * 100,
                "currency": MONEDA,
                "payment_method_type": compra.metodo_pago.value,
            }
        }
        propiedades = list(PROPIEDADES_OBLIGATORIAS)
        return {
            "event": EVENTO_TRANSACCION,
            "data": data,
            "environment": "test",
            "signature": {
                "properties": propiedades,
                "checksum": self._checksum(data, propiedades, timestamp),
            },
            "timestamp": timestamp,
            "sent_at": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
