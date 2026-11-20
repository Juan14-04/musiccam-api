"""Puerto de pagos (§10.1). Los servicios dependen solo de `PaymentGateway`."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.common.errors import NoAutorizado

MONEDA = "COP"

# Estados de transacción de Wompi que se contemplan.
APPROVED = "APPROVED"
DECLINED = "DECLINED"
VOIDED = "VOIDED"
ERROR = "ERROR"
PENDING = "PENDING"
ESTADOS_WOMPI = {APPROVED, DECLINED, VOIDED, ERROR, PENDING}


class FirmaInvalida(NoAutorizado):
    """El evento no trae una firma válida (o no se puede verificar)."""

    codigo_defecto = "FIRMA_INVALIDA"
    mensaje_defecto = "La firma del evento no es válida."


class CompraPagable(Protocol):
    """Lo mínimo que el gateway necesita saber de una compra."""

    referencia: str
    monto_cop: int
    expira_en: datetime
    metodo_pago: Any
    wompi_transaccion_id: str | None


@dataclass(frozen=True)
class CheckoutDTO:
    referencia: str
    monto_en_centavos: int
    moneda: str
    firma_integridad: str
    checkout_url: str
    expira_en: datetime


@dataclass(frozen=True)
class TransaccionDTO:
    id: str
    referencia: str
    estado: str
    monto_en_centavos: int
    moneda: str


class PaymentGateway(ABC):
    # Solo el simulador sabe fabricar eventos (§8.7: `POST /pagos/wompi/simulador/...`).
    soporta_simulacion = False

    @abstractmethod
    def crear_checkout(self, compra: CompraPagable) -> CheckoutDTO: ...

    @abstractmethod
    def verificar_evento(self, payload: dict, checksum_header: str | None) -> TransaccionDTO:
        """Verifica la firma y devuelve la transacción. Lanza `FirmaInvalida`."""

    def construir_evento_simulado(self, compra: CompraPagable, resultado: str) -> dict:
        raise NotImplementedError("Este gateway no simula eventos.")
