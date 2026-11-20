"""Gateway Wompi simulado: firma de integridad y checksum de eventos (§10.3)."""

import copy
import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from types import SimpleNamespace

import pytest

from app import create_app
from app.common.clock import fijar_ahora
from app.infrastructure.payments import FABRICAS, obtener_gateway
from app.infrastructure.payments.base import FirmaInvalida
from app.infrastructure.payments.wompi_simulado import WompiSimuladoGateway

INTEGRIDAD = "secreto-integridad"
EVENTOS = "secreto-eventos"
AHORA = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)


class _Metodo(StrEnum):
    NEQUI = "NEQUI"


@pytest.fixture
def gateway():
    return WompiSimuladoGateway(INTEGRIDAD, EVENTOS)


@pytest.fixture
def compra():
    return SimpleNamespace(
        referencia="conc-abc",
        monto_cop=20_000,
        expira_en=AHORA,
        metodo_pago=_Metodo.NEQUI,
        wompi_transaccion_id=None,
    )


def sha(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def test_firma_de_integridad_del_checkout(gateway, compra):
    """SHA256(referencia + monto_en_centavos + moneda + secreto)."""
    checkout = gateway.crear_checkout(compra)
    assert checkout.monto_en_centavos == 2_000_000 and checkout.moneda == "COP"
    assert checkout.firma_integridad == sha("conc-abc" + "2000000" + "COP" + INTEGRIDAD)
    assert checkout.referencia == "conc-abc" and checkout.expira_en == AHORA


def test_checksum_del_evento_es_el_del_documento(gateway, compra):
    """checksum = SHA256(id + status + amount + timestamp + secreto de eventos)."""
    fijar_ahora(AHORA)
    evento = gateway.construir_evento_simulado(compra, "APPROVED")
    transaccion = evento["data"]["transaction"]
    esperado = sha(transaccion["id"] + "APPROVED" + "2000000" + str(evento["timestamp"]) + EVENTOS)
    assert evento["signature"]["checksum"] == esperado
    assert evento["signature"]["properties"] == [
        "transaction.id",
        "transaction.status",
        "transaction.amount_in_cents",
    ]
    assert evento["event"] == "transaction.updated" and transaccion["id"].startswith("sim-")
    assert (transaccion["reference"], transaccion["currency"]) == ("conc-abc", "COP")
    assert evento["sent_at"] == "2026-11-02T20:00:00Z"


def test_verificar_devuelve_la_transaccion(gateway, compra):
    evento = gateway.construir_evento_simulado(compra, "DECLINED")
    transaccion = gateway.verificar_evento(evento, None)
    assert (transaccion.referencia, transaccion.estado) == ("conc-abc", "DECLINED")
    assert (transaccion.monto_en_centavos, transaccion.moneda) == (2_000_000, "COP")


@pytest.mark.parametrize("resultado", ["APPROVED", "DECLINED", "VOIDED", "ERROR", "PENDING"])
def test_todos_los_estados_de_wompi_se_firman_y_verifican(gateway, compra, resultado):
    evento = gateway.construir_evento_simulado(compra, resultado)
    assert gateway.verificar_evento(evento, None).estado == resultado


def test_resultado_desconocido_no_se_simula(gateway, compra):
    with pytest.raises(ValueError, match="desconocido"):
        gateway.construir_evento_simulado(compra, "PAGADO")


@pytest.mark.parametrize(
    "alterar",
    [
        lambda e: e["data"]["transaction"].update(status="APPROVED"),
        lambda e: e["data"]["transaction"].update(amount_in_cents=100),
        lambda e: e["data"]["transaction"].update(id="sim-otro"),
        lambda e: e.update(timestamp=e["timestamp"] + 1),
        lambda e: e["signature"].update(checksum="0" * 64),
        lambda e: e["signature"].update(checksum=""),
        lambda e: e["signature"].update(properties=["transaction.id", "transaction.status"]),
        lambda e: e.update(event="nequi_token.updated"),
        lambda e: e.pop("signature"),
        lambda e: e.pop("data"),
        lambda e: e.pop("timestamp"),
        lambda e: e["signature"].update(checksum=12345),
        lambda e: e.update(data="no-es-un-objeto"),
    ],
    ids=[
        "status",
        "monto",
        "id",
        "timestamp",
        "checksum-falso",
        "checksum-vacio",
        "propiedades-sin-monto",
        "otro-tipo-de-evento",
        "sin-firma",
        "sin-data",
        "sin-timestamp",
        "checksum-no-texto",
        "data-invalida",
    ],
)
def test_evento_alterado_o_malformado_es_firma_invalida(gateway, compra, alterar):
    evento = gateway.construir_evento_simulado(compra, "DECLINED")
    alterar(evento)
    with pytest.raises(FirmaInvalida) as error:
        gateway.verificar_evento(evento, None)
    assert error.value.codigo == "FIRMA_INVALIDA"


def test_secreto_distinto_es_firma_invalida(gateway, compra):
    evento = WompiSimuladoGateway(INTEGRIDAD, "otro-secreto").construir_evento_simulado(
        compra, "APPROVED"
    )
    with pytest.raises(FirmaInvalida):
        gateway.verificar_evento(evento, None)


def test_la_cabecera_tiene_prioridad_sobre_el_checksum_del_cuerpo(gateway, compra):
    evento = gateway.construir_evento_simulado(compra, "APPROVED")
    correcto = evento["signature"]["checksum"]
    evento["signature"]["checksum"] = "0" * 64
    assert gateway.verificar_evento(evento, correcto).estado == "APPROVED"
    evento["signature"]["checksum"] = correcto
    with pytest.raises(FirmaInvalida):
        gateway.verificar_evento(evento, "0" * 64)


def test_verificar_no_modifica_el_payload(gateway, compra):
    evento = gateway.construir_evento_simulado(compra, "APPROVED")
    copia = copy.deepcopy(evento)
    gateway.verificar_evento(evento, None)
    assert evento == copia


def test_reutiliza_el_id_de_transaccion_de_la_compra(gateway, compra):
    compra.wompi_transaccion_id = "sim-fijo"
    evento = gateway.construir_evento_simulado(compra, "APPROVED")
    assert evento["data"]["transaction"]["id"] == "sim-fijo"


def test_seleccion_del_gateway_por_configuracion(app):
    with app.app_context():
        assert isinstance(obtener_gateway(), WompiSimuladoGateway)
        assert obtener_gateway().soporta_simulacion is True
    assert "wompi_simulado" in FABRICAS


def test_gateway_desconocido_impide_arrancar():
    with pytest.raises(RuntimeError, match="PAYMENT_GATEWAY desconocido"):
        create_app("testing", {"PAYMENT_GATEWAY": "stripe"})
