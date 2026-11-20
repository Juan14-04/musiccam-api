"""Selección del gateway de pagos según `PAYMENT_GATEWAY`."""

from collections.abc import Callable, Mapping
from typing import Any

from flask import current_app

from app.infrastructure.payments.base import PaymentGateway
from app.infrastructure.payments.wompi_simulado import WompiSimuladoGateway

# Futuro: "wompi" → WompiGateway (sandbox/real). Solo cambia este registro.
FABRICAS: dict[str, Callable[[Mapping[str, Any]], PaymentGateway]] = {
    "wompi_simulado": lambda cfg: WompiSimuladoGateway(
        cfg["WOMPI_INTEGRITY_SECRET"], cfg["WOMPI_EVENTS_SECRET"]
    ),
}


def validar_configuracion(config: Mapping[str, Any]) -> None:
    nombre = config["PAYMENT_GATEWAY"]
    if nombre not in FABRICAS:
        raise RuntimeError(f"PAYMENT_GATEWAY desconocido: {nombre!r} (válidos: {sorted(FABRICAS)})")


def obtener_gateway() -> PaymentGateway:
    return FABRICAS[current_app.config["PAYMENT_GATEWAY"]](current_app.config)
