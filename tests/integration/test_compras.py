"""Compras y pagos (Fase 3): RN-06..RN-09, RN-13, C3 y la matriz de §15 para esta fase."""

import hashlib
import logging
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.common.clock import fijar_ahora
from app.common.enums import EstadoCompra, EstadoEvento, EstadoUsuario, Rol
from app.extensions import db, limiter
from app.infrastructure.payments import FABRICAS
from app.infrastructure.payments.wompi_simulado import WompiSimuladoGateway
from app.modules.compras.models import Compra
from app.modules.compras.repository import ComprasRepository
from app.modules.compras.service import ComprasService
from app.modules.eventos.models import Evento
from app.modules.usuarios.models import Usuario
from tests.factories import (
    crear_compra,
    crear_evento,
    crear_usuario,
    encabezados,
    iniciar_sesion,
)

WEBHOOK = "/api/v1/webhooks/wompi"
SECRETO_EVENTOS = "sim-events-secret"
SECRETO_INTEGRIDAD = "sim-integrity-secret"
AHORA = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


def comprar_url(evento_id) -> str:
    return f"/api/v1/eventos/{evento_id}/compras"


def simulador_url(referencia) -> str:
    return f"/api/v1/pagos/wompi/simulador/{referencia}"


def entrar(client, email):
    return encabezados(iniciar_sesion(client, email)["access_token"])


# --- Firma de eventos, reimplementada aquí a propósito (independiente del gateway) -------------


def checksum(data, propiedades, timestamp, secreto=SECRETO_EVENTOS) -> str:
    valores = ""
    for propiedad in propiedades:
        valor = data
        for parte in propiedad.split("."):
            valor = valor[parte]
        valores += str(valor)
    return hashlib.sha256(f"{valores}{timestamp}{secreto}".encode()).hexdigest()


def evento_wompi(referencia, monto_cop, estado="APPROVED", tx="sim-test-1", timestamp=1730000000):
    data = {
        "transaction": {
            "id": tx,
            "reference": referencia,
            "status": estado,
            "amount_in_cents": monto_cop * 100,
            "currency": "COP",
            "payment_method_type": "NEQUI",
        }
    }
    propiedades = ["transaction.id", "transaction.status", "transaction.amount_in_cents"]
    return {
        "event": "transaction.updated",
        "data": data,
        "environment": "test",
        "signature": {
            "properties": propiedades,
            "checksum": checksum(data, propiedades, timestamp),
        },
        "timestamp": timestamp,
        "sent_at": "2026-11-02T20:00:00Z",
    }


# --- Fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def artista(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("luz@example.com", Rol.ARTISTA, "Luz Band").id)


@pytest.fixture
def evento(app, artista):
    with app.app_context():
        return SimpleNamespace(id=str(crear_evento(artista, precio_cop=20_000).id), precio=20_000)


@pytest.fixture
def comprador(client, app):
    with app.app_context():
        crear_usuario("ana@example.com")
    return entrar(client, "ana@example.com")


@pytest.fixture
def otro_comprador(client, app):
    with app.app_context():
        crear_usuario("beto@example.com")
    return entrar(client, "beto@example.com")


@pytest.fixture
def sesion_artista(client, artista):
    return entrar(client, "luz@example.com")


def comprar(client, sesion, evento_id, metodo="NEQUI"):
    return client.post(comprar_url(evento_id), headers=sesion, json={"metodo_pago": metodo})


def nueva_compra(client, sesion, evento_id) -> dict:
    respuesta = comprar(client, sesion, evento_id)
    assert respuesta.status_code == 201, respuesta.get_json()
    return respuesta.get_json()["compra"]


def simular(client, sesion, referencia, resultado):
    return client.post(simulador_url(referencia), headers=sesion, json={"resultado": resultado})


def en_bd(app, referencia) -> SimpleNamespace:
    with app.app_context():
        compra = db.session.scalar(select(Compra).where(Compra.referencia == referencia))
        return SimpleNamespace(
            **{c.name: getattr(compra, c.name) for c in Compra.__table__.columns}
        )


def usuario_id(app, email) -> str:
    with app.app_context():
        return db.session.scalar(select(Usuario.id).where(Usuario.email == email))


# --- RN-06: comisión congelada -----------------------------------------------------------------


def test_comprar_crea_pendiente_con_precio_y_comision_congelados(app, client, evento, comprador):
    """★ RN-06: 20.000 COP al 10 % → comisión 2.000 / neto 18.000; monto = comisión + neto."""
    fijar_ahora(AHORA)
    respuesta = comprar(client, comprador, evento.id, "PSE")

    assert respuesta.status_code == 201
    cuerpo = respuesta.get_json()
    compra = cuerpo["compra"]
    assert (compra["estado"], compra["monto_cop"], compra["metodo_pago"]) == (
        "PENDIENTE",
        20_000,
        "PSE",
    )
    assert compra["referencia"].startswith("conc-") and len(compra["referencia"]) <= 64
    assert compra["expira_en"].startswith("2026-11-02T15:30:00")  # ahora + 30 min (§10.2)
    assert compra["aprobado_en"] is None and compra["wompi_transaccion_id"] is None

    fila = en_bd(app, compra["referencia"])
    assert (fila.monto_cop, fila.comision_cop, fila.neto_artista_cop) == (20_000, 2_000, 18_000)
    assert fila.monto_cop == fila.comision_cop + fila.neto_artista_cop
    assert fila.comision_pct == Decimal("10.00")


def test_la_respuesta_no_expone_comision_ni_neto_del_artista(client, evento, comprador):
    compra = comprar(client, comprador, evento.id).get_json()["compra"]
    assert not {"comision_cop", "neto_artista_cop", "comision_pct", "usuario_id"} & set(compra)


def test_la_comision_usa_redondeo_half_up(app, client, artista, comprador):
    with app.app_context():
        otro = str(crear_evento(artista, precio_cop=12_345).id)
    referencia = nueva_compra(client, comprador, otro)["referencia"]
    fila = en_bd(app, referencia)
    assert (fila.comision_cop, fila.neto_artista_cop) == (1_235, 11_110)  # 1.234,5 → 1.235


def test_checkout_simulado_con_firma_de_integridad(client, evento, comprador):
    checkout = comprar(client, comprador, evento.id).get_json()["checkout"]
    referencia = checkout["referencia"]
    assert checkout["monto_en_centavos"] == 2_000_000 and checkout["moneda"] == "COP"
    esperado = hashlib.sha256(f"{referencia}2000000COP{SECRETO_INTEGRIDAD}".encode()).hexdigest()
    assert checkout["firma_integridad"] == esperado
    assert checkout["checkout_url"].endswith(f"/pagos/wompi/simulador/{referencia}")
    assert checkout["expira_en"]


@pytest.mark.parametrize("metodo", ["CARD", "PSE", "NEQUI"])
def test_metodos_de_pago_locales(client, evento, comprador, metodo):
    assert comprar(client, comprador, evento.id, metodo).status_code == 201


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"metodo_pago": "BITCOIN"},
        {"metodo_pago": "nequi"},
        {"metodo_pago": "NEQUI", "monto_cop": 1},
        {"metodo_pago": "NEQUI", "usuario_id": "x"},
        {"metodo_pago": "NEQUI", "estado": "APROBADA"},
    ],
)
def test_el_cliente_no_puede_mandar_monto_estado_ni_usuario(client, evento, comprador, cuerpo):
    respuesta = client.post(comprar_url(evento.id), headers=comprador, json=cuerpo)
    assert respuesta.status_code == 422 and respuesta.get_json()["codigo"] == "VALIDACION"


# --- RN-09: estados del pago vía simulador -----------------------------------------------------


@pytest.mark.parametrize(
    ("resultado", "esperado"),
    [
        ("APPROVED", "APROBADA"),
        ("DECLINED", "RECHAZADA"),
        ("ERROR", "RECHAZADA"),
        ("VOIDED", "ANULADA"),
    ],
)
def test_el_simulador_lleva_la_compra_al_estado_correspondiente(
    app, client, evento, comprador, resultado, esperado
):
    """★ RN-09."""
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    respuesta = simular(client, comprador, referencia, resultado)

    assert respuesta.status_code == 200
    assert respuesta.get_json()["estado"] == esperado
    fila = en_bd(app, referencia)
    assert fila.estado.value == esperado
    assert fila.wompi_transaccion_id.startswith("sim-")
    assert (fila.aprobado_en is not None) == (esperado == "APROBADA")


def test_pago_aprobado_da_acceso_sin_pasos_extra(app, client, evento, comprador):
    """★ C3/RN-08: sin intervención de nadie, ni siquiera existe un ADMIN."""
    comprado_id = usuario_id(app, "ana@example.com")
    with app.app_context():
        assert ComprasService().tiene_acceso(comprado_id, evento.id) is False

    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    with app.app_context():  # PENDIENTE todavía no da acceso
        assert ComprasService().tiene_acceso(comprado_id, evento.id) is False
    simular(client, comprador, referencia, "APPROVED")

    with app.app_context():
        assert ComprasService().tiene_acceso(comprado_id, evento.id) is True
        assert db.session.scalar(select(func.count()).where(Usuario.rol == Rol.ADMIN)) == 0


@pytest.mark.parametrize("resultado", ["DECLINED", "ERROR", "VOIDED"])
def test_pagos_fallidos_no_dan_acceso_y_permiten_reintentar(
    app, client, evento, comprador, resultado
):
    comprado_id = usuario_id(app, "ana@example.com")
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    simular(client, comprador, referencia, resultado)

    with app.app_context():
        assert ComprasService().tiene_acceso(comprado_id, evento.id) is False
    assert comprar(client, comprador, evento.id).status_code == 201  # el índice parcial lo permite


def test_tiene_acceso_solo_con_compra_aprobada_del_usuario_y_del_evento(
    app, client, artista, evento, comprador, otro_comprador
):
    with app.app_context():
        otro_evento = str(crear_evento(artista).id)
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    simular(client, comprador, referencia, "APPROVED")
    ana, beto = usuario_id(app, "ana@example.com"), usuario_id(app, "beto@example.com")
    with app.app_context():
        servicio = ComprasService()
        assert servicio.tiene_acceso(ana, evento.id) is True
        assert servicio.tiene_acceso(beto, evento.id) is False
        assert servicio.tiene_acceso(ana, otro_evento) is False


def test_reembolsada_y_anulada_no_dan_acceso(app, client, artista, evento, comprador):
    ana = usuario_id(app, "ana@example.com")
    with app.app_context():
        for estado in (EstadoCompra.REEMBOLSADA, EstadoCompra.ANULADA, EstadoCompra.RECHAZADA):
            crear_compra(ana, evento.id, estado=estado)
        assert ComprasService().tiene_acceso(ana, evento.id) is False


# --- RN-09: webhook ----------------------------------------------------------------------------


def test_webhook_aprueba_la_compra(app, client, evento, comprador):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    respuesta = client.post(WEBHOOK, json=evento_wompi(referencia, evento.precio))
    assert respuesta.status_code == 200 and respuesta.get_json() == {"estado": "recibido"}
    fila = en_bd(app, referencia)
    assert fila.estado == EstadoCompra.APROBADA and fila.wompi_transaccion_id == "sim-test-1"


@pytest.mark.parametrize(
    ("estado_wompi", "esperado"),
    [("DECLINED", "RECHAZADA"), ("ERROR", "RECHAZADA"), ("VOIDED", "ANULADA")],
)
def test_webhook_mapea_los_estados_de_wompi(app, client, evento, comprador, estado_wompi, esperado):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    client.post(WEBHOOK, json=evento_wompi(referencia, evento.precio, estado_wompi))
    assert en_bd(app, referencia).estado.value == esperado


def test_webhook_pending_no_cambia_nada(app, client, evento, comprador):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    respuesta = client.post(WEBHOOK, json=evento_wompi(referencia, evento.precio, "PENDING"))
    assert respuesta.status_code == 200
    fila = en_bd(app, referencia)
    assert fila.estado == EstadoCompra.PENDIENTE
    assert fila.wompi_transaccion_id is None and fila.aprobado_en is None


def test_webhook_con_estado_desconocido_no_cambia_nada(app, client, evento, comprador):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    client.post(WEBHOOK, json=evento_wompi(referencia, evento.precio, "EXPLOTADO"))
    assert en_bd(app, referencia).estado == EstadoCompra.PENDIENTE


def test_webhook_repetido_tiene_un_solo_efecto(app, client, evento, comprador, caplog):
    """★ RN-09: idempotente. Wompi reintenta y el segundo envío no hace nada."""
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    payload = evento_wompi(referencia, evento.precio)
    with caplog.at_level(logging.INFO):
        primero = client.post(WEBHOOK, json=payload)
        despues_del_primero = en_bd(app, referencia)
        segundo = client.post(WEBHOOK, json=payload)
        # Mismo estado pero otro id/timestamp (otro envío del mismo evento).
        tercero = client.post(WEBHOOK, json=evento_wompi(referencia, evento.precio, tx="sim-2"))

    assert primero.status_code == segundo.status_code == tercero.status_code == 200
    fila = en_bd(app, referencia)
    assert fila.estado == EstadoCompra.APROBADA
    assert fila.aprobado_en == despues_del_primero.aprobado_en
    assert fila.wompi_transaccion_id == "sim-test-1"
    assert len([r for r in caplog.records if "APROBADA" in r.getMessage()]) == 1


@pytest.mark.parametrize(
    ("primero", "tardio", "final"),
    [
        ("APPROVED", "DECLINED", "APROBADA"),
        ("APPROVED", "VOIDED", "APROBADA"),
        ("APPROVED", "ERROR", "APROBADA"),
        ("DECLINED", "APPROVED", "RECHAZADA"),
        ("VOIDED", "APPROVED", "ANULADA"),
        ("VOIDED", "DECLINED", "ANULADA"),
    ],
)
def test_webhook_tardio_no_retrocede_un_estado_terminal(
    app, client, evento, comprador, primero, tardio, final
):
    """★ RN-09 / §6."""
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    client.post(WEBHOOK, json=evento_wompi(referencia, evento.precio, primero))
    respuesta = client.post(WEBHOOK, json=evento_wompi(referencia, evento.precio, tardio, "sim-9"))
    assert respuesta.status_code == 200
    assert en_bd(app, referencia).estado.value == final


def test_el_simulador_tampoco_retrocede_un_estado_terminal(app, client, evento, comprador):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    simular(client, comprador, referencia, "APPROVED")
    respuesta = simular(client, comprador, referencia, "DECLINED")
    assert respuesta.status_code == 200 and respuesta.get_json()["estado"] == "APROBADA"


def test_webhooks_simultaneos_del_mismo_evento_tienen_un_solo_efecto(
    app, client, evento, comprador, caplog
):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    payload = evento_wompi(referencia, evento.precio)
    codigos = []

    def enviar():
        codigos.append(app.test_client().post(WEBHOOK, json=payload).status_code)

    with caplog.at_level(logging.INFO):
        hilos = [threading.Thread(target=enviar) for _ in range(4)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(20)

    assert codigos == [200] * 4
    assert en_bd(app, referencia).estado == EstadoCompra.APROBADA
    assert len([r for r in caplog.records if "APROBADA" in r.getMessage()]) == 1


# --- RN-09: firma y monto ----------------------------------------------------------------------


def test_webhook_con_firma_invalida_es_401_y_no_aprueba(app, client, evento, comprador):
    """★ RN-09."""
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    payload = evento_wompi(referencia, evento.precio)
    payload["signature"]["checksum"] = "0" * 64

    respuesta = client.post(WEBHOOK, json=payload)

    assert respuesta.status_code == 401
    assert respuesta.get_json()["codigo"] == "FIRMA_INVALIDA"
    assert en_bd(app, referencia).estado == EstadoCompra.PENDIENTE


def test_webhook_firmado_con_otro_secreto_es_401(app, client, evento, comprador):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    payload = evento_wompi(referencia, evento.precio)
    payload["signature"]["checksum"] = checksum(
        payload["data"], payload["signature"]["properties"], payload["timestamp"], "otro-secreto"
    )
    assert client.post(WEBHOOK, json=payload).status_code == 401


def test_webhook_con_estado_alterado_despues_de_firmar_es_401(app, client, evento, comprador):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    payload = evento_wompi(referencia, evento.precio, "DECLINED")
    payload["data"]["transaction"]["status"] = "APPROVED"  # el atacante lo cambia a mano
    assert client.post(WEBHOOK, json=payload).status_code == 401
    assert en_bd(app, referencia).estado == EstadoCompra.PENDIENTE


def test_webhook_con_monto_alterado_despues_de_firmar_es_401(app, client, evento, comprador):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    payload = evento_wompi(referencia, evento.precio)
    payload["data"]["transaction"]["amount_in_cents"] = 100
    assert client.post(WEBHOOK, json=payload).status_code == 401


@pytest.mark.parametrize(
    "cuerpo",
    [{}, {"event": "transaction.updated"}, {"signature": {}, "data": {}}, {"data": {"x": 1}}],
)
def test_webhook_sin_firma_o_malformado_es_401(client, cuerpo):
    respuesta = client.post(WEBHOOK, json=cuerpo)
    assert respuesta.status_code == 401 and respuesta.get_json()["codigo"] == "FIRMA_INVALIDA"


def test_webhook_la_cabecera_de_checksum_se_verifica(app, client, evento, comprador):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    payload = evento_wompi(referencia, evento.precio)
    correcto = payload["signature"]["checksum"]

    mala = client.post(WEBHOOK, json=payload, headers={"X-Event-Checksum": "0" * 64})
    assert mala.status_code == 401 and en_bd(app, referencia).estado == EstadoCompra.PENDIENTE

    payload["signature"]["checksum"] = "0" * 64  # el del cuerpo es inútil si la cabecera es válida
    buena = client.post(WEBHOOK, json=payload, headers={"X-Event-Checksum": correcto})
    assert buena.status_code == 200 and en_bd(app, referencia).estado == EstadoCompra.APROBADA


def test_webhook_con_monto_distinto_al_de_la_compra_no_aprueba(app, client, evento, comprador):
    """★ RN-09: bien firmado, pero 1.000 COP no es lo que cuesta la entrada."""
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    respuesta = client.post(WEBHOOK, json=evento_wompi(referencia, 1_000))
    assert respuesta.status_code == 200
    fila = en_bd(app, referencia)
    assert fila.estado == EstadoCompra.PENDIENTE and fila.aprobado_en is None


@pytest.mark.parametrize("monto", [19_999, 20_001, 200_000])
def test_solo_el_monto_exacto_aprueba(app, client, evento, comprador, monto):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    client.post(WEBHOOK, json=evento_wompi(referencia, monto))
    assert en_bd(app, referencia).estado == EstadoCompra.PENDIENTE


def test_webhook_con_otra_moneda_no_aprueba(app, client, evento, comprador):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    payload = evento_wompi(referencia, evento.precio)
    payload["data"]["transaction"]["currency"] = "USD"  # no está firmada: no debe contar
    client.post(WEBHOOK, json=payload)
    assert en_bd(app, referencia).estado == EstadoCompra.PENDIENTE


def test_webhook_de_referencia_desconocida_responde_200(client):
    respuesta = client.post(WEBHOOK, json=evento_wompi("conc-no-existe", 20_000))
    assert respuesta.status_code == 200


def test_los_logs_no_contienen_secretos_ni_checksums(client, evento, comprador, caplog):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    payload = evento_wompi(referencia, evento.precio)
    with caplog.at_level(logging.DEBUG):
        client.post(WEBHOOK, json=payload)
        malo = evento_wompi(referencia, evento.precio)
        malo["signature"]["checksum"] = "f" * 64
        client.post(WEBHOOK, json=malo)
        client.post(WEBHOOK, json=evento_wompi(referencia, 1))
    texto = caplog.text
    for secreto in (
        SECRETO_EVENTOS,
        SECRETO_INTEGRIDAD,
        payload["signature"]["checksum"],
        "f" * 64,
    ):
        assert secreto not in texto


def test_webhook_tiene_rate_limit_de_120_por_minuto(client, monkeypatch):
    monkeypatch.setattr(limiter, "enabled", True)
    limiter.reset()
    try:
        codigos = [client.post(WEBHOOK, json={}).status_code for _ in range(121)]
        assert set(codigos[:120]) == {401}
        assert codigos[120] == 429
        assert client.post(WEBHOOK, json={}).get_json()["codigo"] == "DEMASIADAS_PETICIONES"
    finally:
        limiter.reset()


# --- RN-07: doble compra -----------------------------------------------------------------------


def test_doble_compra_secuencial_es_409(client, evento, comprador):
    """★ RN-07: con la primera PENDIENTE y también con la primera APROBADA."""
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    pendiente = comprar(client, comprador, evento.id)
    assert pendiente.status_code == 409
    assert pendiente.get_json()["codigo"] == "COMPRA_DUPLICADA"

    simular(client, comprador, referencia, "APPROVED")
    aprobada = comprar(client, comprador, evento.id)
    assert aprobada.status_code == 409 and aprobada.get_json()["codigo"] == "COMPRA_DUPLICADA"


def test_doble_compra_concurrente_deja_exactamente_una(app, client, evento, comprador, monkeypatch):
    """★ RN-07 / índice parcial: dos peticiones que pasan la verificación a la vez."""
    barrera = threading.Barrier(2, timeout=15)
    original = ComprasRepository.existe_activa

    def existe_activa_sincronizada(self, usuario_id, evento_id):
        resultado = original(self, usuario_id, evento_id)
        barrera.wait()  # ambos hilos ya comprobaron "no hay compra" antes de insertar
        return resultado

    monkeypatch.setattr(ComprasRepository, "existe_activa", existe_activa_sincronizada)
    resultados = []

    def intentar():
        respuesta = comprar(app.test_client(), comprador, evento.id)
        resultados.append((respuesta.status_code, respuesta.get_json().get("codigo")))

    hilos = [threading.Thread(target=intentar) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(30)

    assert sorted(codigo for codigo, _ in resultados) == [201, 409]
    assert (409, "COMPRA_DUPLICADA") in resultados
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Compra)) == 1


def test_compras_simultaneas_de_usuarios_distintos_no_se_bloquean(app, client, evento):
    with app.app_context():
        for i in range(3):
            crear_usuario(f"u{i}@example.com")
    sesiones = [entrar(client, f"u{i}@example.com") for i in range(3)]
    codigos = []

    def intentar(sesion):
        codigos.append(comprar(app.test_client(), sesion, evento.id).status_code)

    hilos = [threading.Thread(target=intentar, args=(s,)) for s in sesiones]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(30)
    assert codigos == [201, 201, 201]


def test_el_indice_parcial_existe_en_la_bd(app, evento):
    """El respaldo real contra la carrera es la BD, no el código."""
    ana = None
    with app.app_context():
        ana = crear_usuario("ana@example.com").id
        crear_compra(ana, evento.id)
        with pytest.raises(IntegrityError):
            crear_compra(ana, evento.id)
        db.session.rollback()
        # Una compra terminada no bloquea una nueva.
        crear_compra(ana, evento.id, estado=EstadoCompra.RECHAZADA)
        crear_compra(ana, evento.id, estado=EstadoCompra.ANULADA)


def test_check_monto_igual_a_comision_mas_neto(app, evento):
    with app.app_context():
        ana = crear_usuario("ana@example.com").id
        with pytest.raises(IntegrityError):
            crear_compra(ana, evento.id, comision_cop=2_500)
        db.session.rollback()
        with pytest.raises(IntegrityError):
            crear_compra(ana, evento.id, monto_cop=0, comision_cop=0, neto_artista_cop=0)
        db.session.rollback()


def test_referencia_unica(app, evento):
    with app.app_context():
        ana = crear_usuario("ana@example.com").id
        beto = crear_usuario("beto@example.com").id
        crear_compra(ana, evento.id, referencia="conc-fija")
        with pytest.raises(IntegrityError):
            crear_compra(beto, evento.id, referencia="conc-fija")
        db.session.rollback()


# --- Expiración perezosa (RN-07) ---------------------------------------------------------------


def test_pendiente_vencida_se_anula_al_intentar_comprar_de_nuevo(app, client, evento, comprador):
    fijar_ahora(AHORA)
    vieja = nueva_compra(client, comprador, evento.id)["referencia"]

    fijar_ahora(AHORA + timedelta(minutes=29, seconds=59))
    assert comprar(client, comprador, evento.id).status_code == 409  # aún vigente
    assert en_bd(app, vieja).estado == EstadoCompra.PENDIENTE

    fijar_ahora(AHORA + timedelta(minutes=30))  # expira_en <= ahora
    nueva = comprar(client, comprador, evento.id)
    assert nueva.status_code == 201
    assert nueva.get_json()["compra"]["referencia"] != vieja
    assert en_bd(app, vieja).estado == EstadoCompra.ANULADA


def test_la_expiracion_solo_afecta_al_usuario_y_evento_de_la_compra(
    app, client, artista, evento, comprador, otro_comprador
):
    with app.app_context():
        otro_evento = str(crear_evento(artista).id)
    fijar_ahora(AHORA)
    de_ana = nueva_compra(client, comprador, evento.id)["referencia"]
    de_beto = nueva_compra(client, otro_comprador, evento.id)["referencia"]
    de_ana_otro = nueva_compra(client, comprador, otro_evento)["referencia"]

    fijar_ahora(AHORA + timedelta(hours=1))
    assert comprar(client, comprador, evento.id).status_code == 201

    assert en_bd(app, de_ana).estado == EstadoCompra.ANULADA
    assert en_bd(app, de_beto).estado == EstadoCompra.PENDIENTE
    assert en_bd(app, de_ana_otro).estado == EstadoCompra.PENDIENTE


def test_una_compra_aprobada_no_se_anula_por_vencimiento(app, client, evento, comprador):
    fijar_ahora(AHORA)
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    simular(client, comprador, referencia, "APPROVED")
    fijar_ahora(AHORA + timedelta(days=2))
    assert comprar(client, comprador, evento.id).status_code == 409
    assert en_bd(app, referencia).estado == EstadoCompra.APROBADA


# --- RN-07: quién puede comprar y qué ----------------------------------------------------------


def test_el_dueno_del_evento_no_puede_comprarlo(client, evento, sesion_artista):
    respuesta = comprar(client, sesion_artista, evento.id)
    assert respuesta.status_code == 403
    assert respuesta.get_json()["codigo"] == "COMPRA_PROPIO_EVENTO"


def test_un_artista_si_puede_comprar_el_evento_de_otro(app, client, evento, otro_comprador):
    with app.app_context():
        crear_usuario("otro@example.com", Rol.ARTISTA, "Otro Grupo")
    otro_artista = entrar(client, "otro@example.com")
    assert comprar(client, otro_artista, evento.id).status_code == 201


def test_un_admin_no_puede_comprar(app, client, evento):
    with app.app_context():
        crear_usuario("admin@example.com", Rol.ADMIN)
    respuesta = comprar(client, entrar(client, "admin@example.com"), evento.id)
    assert respuesta.status_code == 403 and respuesta.get_json()["codigo"] == "ROL_NO_PERMITIDO"


@pytest.mark.parametrize(
    ("campos", "codigo_http", "codigo"),
    [
        ({"estado": EstadoEvento.CANCELADO}, 409, "EVENTO_NO_DISPONIBLE"),
        ({"estado": EstadoEvento.FINALIZADO}, 409, "EVENTO_NO_DISPONIBLE"),
        ({"estado": EstadoEvento.BORRADOR}, 404, "EVENTO_NO_ENCONTRADO"),
        ({"suspendido_admin": True}, 403, "EVENTO_SUSPENDIDO"),
        (
            {"estado": EstadoEvento.EN_VIVO, "suspendido_admin": True},
            403,
            "EVENTO_SUSPENDIDO",
        ),
    ],
    ids=["cancelado", "finalizado-sin-replay", "borrador", "suspendido", "en-vivo-suspendido"],
)
def test_eventos_no_comprables_son_rechazados(
    app, client, artista, comprador, campos, codigo_http, codigo
):
    with app.app_context():
        evento_id = str(crear_evento(artista, **campos).id)
    respuesta = comprar(client, comprador, evento_id)
    assert respuesta.status_code == codigo_http
    assert respuesta.get_json()["codigo"] == codigo
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Compra)) == 0


@pytest.mark.parametrize(
    "campos",
    [
        {"estado": EstadoEvento.PROGRAMADO},
        {"estado": EstadoEvento.EN_VIVO},
        {"estado": EstadoEvento.FINALIZADO, "replay_disponible": True},
    ],
    ids=["programado", "en-vivo", "finalizado-con-replay"],
)
def test_eventos_a_la_venta(app, client, artista, comprador, campos):
    with app.app_context():
        evento_id = str(crear_evento(artista, **campos).id)
    assert comprar(client, comprador, evento_id).status_code == 201


def test_no_se_compra_el_evento_de_un_artista_suspendido(app, client, artista, evento, comprador):
    with app.app_context():
        db.session.get(Usuario, artista.id).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    respuesta = comprar(client, comprador, evento.id)
    assert respuesta.status_code == 404 and respuesta.get_json()["codigo"] == "EVENTO_NO_ENCONTRADO"


def test_evento_inexistente_es_404(client, comprador):
    respuesta = comprar(client, comprador, "00000000-0000-4000-8000-000000000000")
    assert respuesta.status_code == 404 and respuesta.get_json()["codigo"] == "EVENTO_NO_ENCONTRADO"


def test_usuario_suspendido_no_puede_comprar(app, client, evento, comprador):
    with app.app_context():
        usuario = db.session.scalar(select(Usuario).where(Usuario.email == "ana@example.com"))
        usuario.estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    respuesta = comprar(client, comprador, evento.id)
    assert respuesta.status_code == 403 and respuesta.get_json()["codigo"] == "CUENTA_SUSPENDIDA"


# --- Autenticación y propiedad (anti-IDOR) -----------------------------------------------------


def test_endpoints_autenticados_sin_token_son_401(client, evento):
    uuid_cualquiera = "00000000-0000-4000-8000-000000000000"
    assert client.post(comprar_url(evento.id), json={"metodo_pago": "NEQUI"}).status_code == 401
    assert client.get(f"/api/v1/compras/{uuid_cualquiera}").status_code == 401
    assert client.post(simulador_url("x"), json={"resultado": "APPROVED"}).status_code == 401


def test_un_usuario_no_ve_la_compra_de_otro(client, evento, comprador, otro_comprador):
    compra = nueva_compra(client, comprador, evento.id)
    ruta = f"/api/v1/compras/{compra['id']}"
    ajeno = client.get(ruta, headers=otro_comprador)
    assert ajeno.status_code == 403 and ajeno.get_json()["codigo"] == "NO_ES_DUENO"
    assert client.get(ruta, headers=comprador).status_code == 200


def test_un_usuario_no_simula_la_compra_de_otro(app, client, evento, comprador, otro_comprador):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    respuesta = simular(client, otro_comprador, referencia, "APPROVED")
    assert respuesta.status_code == 403 and respuesta.get_json()["codigo"] == "NO_ES_DUENO"
    assert en_bd(app, referencia).estado == EstadoCompra.PENDIENTE


def test_el_artista_dueno_del_evento_no_ve_ni_simula_compras_ajenas(
    client, evento, comprador, sesion_artista
):
    compra = nueva_compra(client, comprador, evento.id)
    assert client.get(f"/api/v1/compras/{compra['id']}", headers=sesion_artista).status_code == 403
    assert simular(client, sesion_artista, compra["referencia"], "APPROVED").status_code == 403


def test_un_admin_puede_ver_pero_no_simular_una_compra(app, client, evento, comprador):
    with app.app_context():
        crear_usuario("admin@example.com", Rol.ADMIN)
    admin = entrar(client, "admin@example.com")
    compra = nueva_compra(client, comprador, evento.id)
    assert client.get(f"/api/v1/compras/{compra['id']}", headers=admin).status_code == 200
    assert simular(client, admin, compra["referencia"], "APPROVED").status_code == 403


def test_compra_inexistente_es_404(client, comprador):
    uuid_cualquiera = "00000000-0000-4000-8000-000000000000"
    respuesta = client.get(f"/api/v1/compras/{uuid_cualquiera}", headers=comprador)
    assert respuesta.status_code == 404 and respuesta.get_json()["codigo"] == "COMPRA_NO_ENCONTRADA"
    sim = simular(client, comprador, "conc-no-existe", "APPROVED")
    assert sim.status_code == 404 and sim.get_json()["codigo"] == "COMPRA_NO_ENCONTRADA"


def test_get_compra_refleja_el_estado_actual(client, evento, comprador):
    compra = nueva_compra(client, comprador, evento.id)
    ruta = f"/api/v1/compras/{compra['id']}"
    assert client.get(ruta, headers=comprador).get_json()["estado"] == "PENDIENTE"
    simular(client, comprador, compra["referencia"], "APPROVED")
    cuerpo = client.get(ruta, headers=comprador).get_json()
    assert cuerpo["estado"] == "APROBADA" and cuerpo["aprobado_en"] is not None


@pytest.mark.parametrize("resultado", ["PENDING", "PAGADO", "", None])
def test_el_simulador_solo_acepta_los_cuatro_resultados(client, evento, comprador, resultado):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    respuesta = client.post(
        simulador_url(referencia), headers=comprador, json={"resultado": resultado}
    )
    assert respuesta.status_code == 422


def test_el_simulador_se_deshabilita_con_otro_gateway(client, evento, comprador, monkeypatch):
    class SinSimulacion(WompiSimuladoGateway):
        soporta_simulacion = False

    monkeypatch.setitem(
        FABRICAS, "wompi_simulado", lambda cfg: SinSimulacion(SECRETO_INTEGRIDAD, SECRETO_EVENTOS)
    )
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    assert simular(client, comprador, referencia, "APPROVED").status_code == 404
    # El webhook real sigue funcionando.
    assert client.post(WEBHOOK, json=evento_wompi(referencia, evento.precio)).status_code == 200


# --- Precio congelado --------------------------------------------------------------------------


def test_cambiar_el_precio_despues_de_comprar_no_altera_la_compra(
    app, client, evento, comprador, sesion_artista
):
    """★ RN-06: el monto se congela; el webhook se compara contra el congelado."""
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    cambio = client.patch(
        f"/api/v1/eventos/{evento.id}", headers=sesion_artista, json={"precio_cop": 50_000}
    )
    assert cambio.status_code == 200 and cambio.get_json()["precio_cop"] == 50_000

    fila = en_bd(app, referencia)
    assert (fila.monto_cop, fila.comision_cop, fila.neto_artista_cop) == (20_000, 2_000, 18_000)
    # Un pago por el precio nuevo no coincide con lo congelado: no aprueba.
    client.post(WEBHOOK, json=evento_wompi(referencia, 50_000))
    assert en_bd(app, referencia).estado == EstadoCompra.PENDIENTE
    # El precio congelado sí aprueba.
    client.post(WEBHOOK, json=evento_wompi(referencia, 20_000, tx="sim-2"))
    assert en_bd(app, referencia).estado == EstadoCompra.APROBADA


def test_la_nueva_compra_usa_el_precio_vigente(
    client, app, evento, comprador, otro_comprador, sesion_artista
):
    primera = nueva_compra(client, comprador, evento.id)["referencia"]
    client.patch(
        f"/api/v1/eventos/{evento.id}", headers=sesion_artista, json={"precio_cop": 50_000}
    )
    segunda = nueva_compra(client, otro_comprador, evento.id)["referencia"]
    assert en_bd(app, primera).monto_cop == 20_000
    fila = en_bd(app, segunda)
    assert (fila.monto_cop, fila.comision_cop, fila.neto_artista_cop) == (50_000, 5_000, 45_000)


def test_con_una_compra_aprobada_ya_no_se_edita_precio_fecha_ni_replay(
    client, evento, comprador, sesion_artista
):
    """RN-04 con datos reales (antes era un stub que devolvía False)."""
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    simular(client, comprador, referencia, "APPROVED")
    ruta = f"/api/v1/eventos/{evento.id}"
    for restringido in (
        {"precio_cop": 30_000},
        {"fecha_inicio": "2031-01-01T20:00:00+00:00"},
        {"replay_habilitado": False},
    ):
        respuesta = client.patch(ruta, headers=sesion_artista, json=restringido)
        assert respuesta.status_code == 409
        assert respuesta.get_json()["codigo"] == "EVENTO_NO_EDITABLE"
    assert client.patch(ruta, headers=sesion_artista, json={"titulo": "Nuevo"}).status_code == 200


def test_una_compra_pendiente_o_rechazada_no_bloquea_la_edicion_de_precio(
    client, evento, comprador, sesion_artista
):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    simular(client, comprador, referencia, "DECLINED")
    respuesta = client.patch(
        f"/api/v1/eventos/{evento.id}", headers=sesion_artista, json={"precio_cop": 30_000}
    )
    assert respuesta.status_code == 200


# --- RN-13: cancelar el evento -----------------------------------------------------------------


def test_cancelar_con_compras_aprobadas_las_reembolsa(
    app, client, evento, comprador, otro_comprador, sesion_artista
):
    """★ RN-13: APROBADA → REEMBOLSADA y PENDIENTE → ANULADA, en la misma transacción."""
    with app.app_context():
        crear_usuario("carla@example.com")
    tercero = entrar(client, "carla@example.com")
    aprobada = nueva_compra(client, comprador, evento.id)["referencia"]
    simular(client, comprador, aprobada, "APPROVED")
    pendiente = nueva_compra(client, otro_comprador, evento.id)["referencia"]
    rechazada = nueva_compra(client, tercero, evento.id)["referencia"]
    simular(client, tercero, rechazada, "DECLINED")

    respuesta = client.post(f"/api/v1/eventos/{evento.id}/cancelar", headers=sesion_artista)

    assert respuesta.status_code == 200 and respuesta.get_json()["estado"] == "CANCELADO"
    assert en_bd(app, aprobada).estado == EstadoCompra.REEMBOLSADA
    assert en_bd(app, pendiente).estado == EstadoCompra.ANULADA
    assert en_bd(app, rechazada).estado == EstadoCompra.RECHAZADA  # lo terminal no se toca
    with app.app_context():
        ana = usuario_id(app, "ana@example.com")
        assert ComprasService().tiene_acceso(ana, evento.id) is False  # el reembolso quita acceso


def test_pago_tardio_de_una_compra_anulada_por_cancelacion_no_la_revive(
    app, client, evento, comprador, sesion_artista
):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    client.post(f"/api/v1/eventos/{evento.id}/cancelar", headers=sesion_artista)
    client.post(WEBHOOK, json=evento_wompi(referencia, evento.precio))
    assert en_bd(app, referencia).estado == EstadoCompra.ANULADA


def test_no_se_compra_un_evento_cancelado(client, evento, comprador, sesion_artista):
    client.post(f"/api/v1/eventos/{evento.id}/cancelar", headers=sesion_artista)
    respuesta = comprar(client, comprador, evento.id)
    assert respuesta.status_code == 409 and respuesta.get_json()["codigo"] == "EVENTO_NO_DISPONIBLE"


def test_si_el_reembolso_falla_no_se_cancela_nada(
    app, client, evento, comprador, sesion_artista, monkeypatch
):
    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    simular(client, comprador, referencia, "APPROVED")

    def falla(self, evento_id):
        raise RuntimeError("fallo del reembolso")

    monkeypatch.setattr(ComprasService, "reembolsar_por_evento", falla)
    respuesta = client.post(f"/api/v1/eventos/{evento.id}/cancelar", headers=sesion_artista)
    monkeypatch.undo()

    assert respuesta.status_code == 500
    assert en_bd(app, referencia).estado == EstadoCompra.APROBADA
    with app.app_context():
        assert db.session.get(Evento, evento.id).estado == EstadoEvento.PROGRAMADO


def test_cancelar_espera_a_una_compra_en_curso_y_la_anula(
    app, client, evento, comprador, sesion_artista, monkeypatch
):
    """Carrera compra ↔ cancelación: la compra que empezó antes no queda sin anular."""
    dentro = threading.Event()
    seguir = threading.Event()
    original = ComprasRepository.existe_activa

    def lenta(self, usuario_id, evento_id):
        resultado = original(self, usuario_id, evento_id)
        dentro.set()  # la compra ya tomó FOR SHARE sobre el evento
        seguir.wait(15)
        return resultado

    monkeypatch.setattr(ComprasRepository, "existe_activa", lenta)
    resultado_compra = []
    hilo_compra = threading.Thread(
        target=lambda: resultado_compra.append(
            comprar(app.test_client(), comprador, evento.id).status_code
        )
    )
    hilo_compra.start()
    assert dentro.wait(15)

    resultado_cancelar = []
    hilo_cancelar = threading.Thread(
        target=lambda: resultado_cancelar.append(
            app.test_client()
            .post(f"/api/v1/eventos/{evento.id}/cancelar", headers=sesion_artista)
            .status_code
        )
    )
    hilo_cancelar.start()
    hilo_cancelar.join(1.5)
    assert hilo_cancelar.is_alive()  # FOR UPDATE del evento espera al FOR SHARE de la compra

    seguir.set()
    hilo_compra.join(20)
    hilo_cancelar.join(20)

    assert resultado_compra == [201] and resultado_cancelar == [200]
    with app.app_context():
        compra = db.session.scalar(select(Compra))
        assert compra.estado == EstadoCompra.ANULADA  # la cancelación vio la compra


# --- Detalle del evento: `comprado` con datos reales -------------------------------------------


def test_detalle_marca_comprado_solo_con_compra_aprobada(client, evento, comprador, otro_comprador):
    ruta = f"/api/v1/eventos/{evento.id}"
    assert client.get(ruta, headers=comprador).get_json()["comprado"] is False

    referencia = nueva_compra(client, comprador, evento.id)["referencia"]
    assert client.get(ruta, headers=comprador).get_json()["comprado"] is False  # PENDIENTE

    simular(client, comprador, referencia, "APPROVED")
    assert client.get(ruta, headers=comprador).get_json()["comprado"] is True
    assert client.get(ruta, headers=otro_comprador).get_json()["comprado"] is False
    assert "comprado" not in client.get(ruta).get_json()  # sin sesión se omite


# --- Swagger -----------------------------------------------------------------------------------


def test_swagger_documenta_compras_y_pagos(client):
    spec = client.get("/api/openapi.json").get_json()
    assert "Compras" in {t["name"] for t in spec["tags"]}
    esperados = [
        ("/api/v1/eventos/{evento_id}/compras", "post", {"201", "401", "403", "404", "409", "422"}),
        ("/api/v1/compras/{compra_id}", "get", {"200", "401", "403", "404"}),
        ("/api/v1/pagos/wompi/simulador/{referencia}", "post", {"200", "401", "403", "404", "422"}),
        ("/api/v1/webhooks/wompi", "post", {"200", "401", "429"}),
    ]
    for ruta, metodo, codigos in esperados:
        operacion = spec["paths"][ruta][metodo]
        assert operacion["tags"] == ["Compras"] and operacion["summary"]
        assert codigos <= set(operacion["responses"]), (ruta, set(operacion["responses"]))
    assert "security" not in spec["paths"]["/api/v1/webhooks/wompi"]["post"]
    assert spec["paths"]["/api/v1/compras/{compra_id}"]["get"]["security"]
    esquemas = spec["components"]["schemas"]
    assert "example" in esquemas["CompraCrear"]["properties"]["metodo_pago"]
    assert "example" in esquemas["Simulador"]["properties"]["resultado"]
    assert "example" in esquemas["WompiEvento"]["properties"]["data"]
