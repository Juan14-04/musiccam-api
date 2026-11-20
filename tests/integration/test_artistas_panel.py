"""Panel del artista (Fase 5): RN-14 (saldo), RN-15 (retiros) y estadísticas (§8.4)."""

import logging
import threading
import time
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.common.clock import fijar_ahora
from app.common.enums import EstadoCompra, EstadoEvento, EstadoRetiro, Rol, TipoCuenta
from app.extensions import db
from app.modules.artistas.models import Retiro
from app.modules.artistas.repository import ArtistasRepository
from tests.factories import (
    crear_compra,
    crear_evento,
    crear_retiro,
    crear_usuario,
    encabezados,
    iniciar_sesion,
)

PANEL = "/api/v1/artistas/me"
SALDO = f"{PANEL}/saldo"
RETIROS = f"{PANEL}/retiros"
ESTADISTICAS = f"{PANEL}/estadisticas"
AHORA = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)

# Instantes elegidos alrededor de la medianoche de Bogotá (UTC−5) para probar la agrupación.
T_A = datetime(2026, 11, 2, 3, 30, tzinfo=UTC)  # 2026-11-01 22:30 en Bogotá
T_B = datetime(2026, 11, 2, 4, 59, 59, tzinfo=UTC)  # 2026-11-01 23:59:59 en Bogotá
T_C = datetime(2026, 11, 2, 5, 0, 0, tzinfo=UTC)  # 2026-11-02 00:00:00 en Bogotá
T_D = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)  # 2026-11-02 10:00 en Bogotá
T_E = datetime(2026, 11, 3, 20, 0, tzinfo=UTC)  # 2026-11-03 15:00 en Bogotá
T_REEMBOLSO = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
INICIO = datetime(2026, 10, 30, 1, 0, tzinfo=UTC)


def entrar_sesion(client, email):
    return encabezados(iniciar_sesion(client, email)["access_token"])


# --- Datos de prueba ---------------------------------------------------------------------------

_contador = {"n": 0}


def comprador_nuevo():
    _contador["n"] += 1
    return crear_usuario(f"comprador{_contador['n']}@example.com").id


def compra(evento_id, monto, estado=EstadoCompra.APROBADA, aprobado_en=None):
    """Compra directa con comisión del 10 % (20.000 → 2.000 / 18.000)."""
    comision = monto // 10
    return crear_compra(
        comprador_nuevo(),
        evento_id,
        estado=estado,
        monto_cop=monto,
        comision_cop=comision,
        neto_artista_cop=monto - comision,
        aprobado_en=aprobado_en,
    )


@pytest.fixture
def artista(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("luz@example.com", Rol.ARTISTA, "Luz Band").id)


@pytest.fixture
def sesion(client, artista):
    return entrar_sesion(client, "luz@example.com")


@pytest.fixture
def otro(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("otro@example.com", Rol.ARTISTA, "Otro Grupo").id)


@pytest.fixture
def sesion_otro(client, otro):
    return entrar_sesion(client, "otro@example.com")


@pytest.fixture
def sesion_usuario(client, app):
    with app.app_context():
        crear_usuario("ana@example.com")
    return entrar_sesion(client, "ana@example.com")


@pytest.fixture
def sesion_admin(client, app):
    with app.app_context():
        crear_usuario("admin@example.com", Rol.ADMIN)
    return entrar_sesion(client, "admin@example.com")


def dar_saldo(app, artista, compras=6, monto=20_000) -> str:
    """Deja `compras × 90 %·monto` disponibles (evento FINALIZADO). 6 × 18.000 = 108.000."""
    with app.app_context():
        evento = crear_evento(
            artista,
            estado=EstadoEvento.FINALIZADO,
            inicio_real=INICIO,
            fin_real=INICIO + timedelta(hours=1),
        )
        for _ in range(compras):
            compra(evento.id, monto, aprobado_en=T_D)
        return str(evento.id)


def pedir_retiro(client, sesion, monto, **cambios):
    cuerpo = {
        "monto_cop": monto,
        "banco": "Bancolombia",
        "tipo_cuenta": "AHORROS",
        "cuenta_ultimos4": "0042",
        **cambios,
    }
    return client.post(RETIROS, headers=sesion, json=cuerpo)


def saldo_de(client, sesion) -> dict:
    return client.get(SALDO, headers=sesion).get_json()


@pytest.fixture
def escenario(app, artista, otro):
    """Datos conocidos, calculados a mano en las pruebas de abajo."""
    with app.app_context():
        e1 = crear_evento(
            artista,
            titulo="E1 finalizado",
            estado=EstadoEvento.FINALIZADO,
            inicio_real=INICIO,
            fin_real=INICIO + timedelta(minutes=90),
            pico_espectadores=40,
            vistas_vivo=55,
            vistas_replay=7,
            replay_disponible=True,
        )
        e2 = crear_evento(artista, titulo="E2 programado", precio_cop=50_000)
        e3 = crear_evento(
            artista,
            titulo="E3 en vivo",
            estado=EstadoEvento.EN_VIVO,
            precio_cop=30_000,
            inicio_real=INICIO,
            pico_espectadores=20,
        )
        e4 = crear_evento(artista, titulo="E4 cancelado", estado=EstadoEvento.CANCELADO)
        e5 = crear_evento(artista, titulo="E5 borrador", estado=EstadoEvento.BORRADOR)
        e6 = crear_evento(artista, titulo="E6 sin transmitir", pico_espectadores=99)
        e7 = crear_evento(
            artista,
            titulo="E7 finalizado sin ventas",
            estado=EstadoEvento.FINALIZADO,
            inicio_real=INICIO,
            fin_real=INICIO + timedelta(minutes=30),
            pico_espectadores=43,
        )
        ajeno = crear_evento(otro, estado=EstadoEvento.FINALIZADO, titulo="De otro artista")

        # E1: 3 aprobadas de 20.000 (neto 18.000) + 3 que nunca suman.
        compra(e1.id, 20_000, aprobado_en=T_A)
        compra(e1.id, 20_000, aprobado_en=T_B)
        compra(e1.id, 20_000, aprobado_en=T_C)
        for estado in (EstadoCompra.RECHAZADA, EstadoCompra.PENDIENTE, EstadoCompra.ANULADA):
            compra(e1.id, 20_000, estado=estado, aprobado_en=T_D)
        # E2: 2 aprobadas de 50.000 (neto 45.000). E3: 1 de 30.000 (neto 27.000).
        compra(e2.id, 50_000, aprobado_en=T_D)
        compra(e2.id, 50_000, aprobado_en=T_D)
        compra(e3.id, 30_000, aprobado_en=T_E)
        # E4 (cancelado): reembolsada y anulada, tampoco suman.
        compra(e4.id, 20_000, estado=EstadoCompra.REEMBOLSADA, aprobado_en=T_REEMBOLSO)
        compra(e4.id, 20_000, estado=EstadoCompra.ANULADA)
        # Otro artista: no debe filtrarse a las cifras de `luz`.
        compra(ajeno.id, 100_000, aprobado_en=T_D)
        crear_retiro(otro.id, monto_cop=5_000, estado=EstadoRetiro.PAGADO)

        crear_retiro(artista.id, monto_cop=20_000, estado=EstadoRetiro.PENDIENTE)
        crear_retiro(artista.id, monto_cop=14_000, estado=EstadoRetiro.PAGADO)
        crear_retiro(artista.id, monto_cop=99_999, estado=EstadoRetiro.RECHAZADO)
        return SimpleNamespace(
            **{f"e{i}": str(e.id) for i, e in enumerate((e1, e2, e3, e4, e5, e6, e7), start=1)},
            ajeno=str(ajeno.id),
        )


# --- Saldo (RN-14) -----------------------------------------------------------------------------


def test_saldo_con_datos_conocidos(client, sesion, escenario):
    """★ RN-14, calculado a mano.

    liberado = 3 × 18.000 = 54.000 · retenido = 2 × 45.000 + 27.000 = 117.000
    en_proceso = 20.000 (PENDIENTE) · retirado = 14.000 (PAGADO) · el RECHAZADO no resta
    disponible = 54.000 − 20.000 − 14.000 = 20.000
    """
    respuesta = client.get(SALDO, headers=sesion)
    assert respuesta.status_code == 200
    assert respuesta.get_json() == {
        "disponible_cop": 20_000,
        "retenido_cop": 117_000,
        "en_proceso_cop": 20_000,
        "retirado_cop": 14_000,
    }


def test_sin_actividad_todo_es_cero(client, sesion):
    assert saldo_de(client, sesion) == {
        "disponible_cop": 0,
        "retenido_cop": 0,
        "en_proceso_cop": 0,
        "retirado_cop": 0,
    }


def test_el_saldo_de_un_artista_no_incluye_al_de_otro(client, sesion, sesion_otro, escenario):
    # `otro` tiene 90.000 liberados y 5.000 pagados.
    assert saldo_de(client, sesion_otro) == {
        "disponible_cop": 85_000,
        "retenido_cop": 0,
        "en_proceso_cop": 0,
        "retirado_cop": 5_000,
    }


def test_al_finalizar_el_evento_el_neto_pasa_de_retenido_a_disponible(
    app, client, artista, sesion, sesion_usuario
):
    """★ RN-14: el dinero se libera cuando el evento termina."""
    with app.app_context():
        evento_id = str(crear_evento(artista, precio_cop=20_000).id)
    # Compra real por API: 20.000 → neto 18.000.
    referencia = client.post(
        f"/api/v1/eventos/{evento_id}/compras",
        headers=sesion_usuario,
        json={"metodo_pago": "NEQUI"},
    ).get_json()["compra"]["referencia"]
    client.post(
        f"/api/v1/pagos/wompi/simulador/{referencia}",
        headers=sesion_usuario,
        json={"resultado": "APPROVED"},
    )
    programado = saldo_de(client, sesion)
    assert (programado["retenido_cop"], programado["disponible_cop"]) == (18_000, 0)

    clave = client.post(
        f"/api/v1/eventos/{evento_id}/transmision/credenciales", headers=sesion
    ).get_json()["stream_key"]
    client.post(
        f"/api/v1/eventos/{evento_id}/transmision/iniciar",
        headers=sesion,
        json={"stream_key": clave},
    )
    en_vivo = saldo_de(client, sesion)
    assert (en_vivo["retenido_cop"], en_vivo["disponible_cop"]) == (18_000, 0)

    client.post(f"/api/v1/eventos/{evento_id}/transmision/finalizar", headers=sesion)
    final = saldo_de(client, sesion)
    assert (final["retenido_cop"], final["disponible_cop"]) == (0, 18_000)


def test_una_compra_reembolsada_por_cancelar_no_suma_en_ningun_campo(
    app, client, artista, sesion, sesion_usuario
):
    """★ RN-13/RN-14: el evento cancelado reembolsa y todo vuelve a cero."""
    with app.app_context():
        evento_id = str(crear_evento(artista).id)
    referencia = client.post(
        f"/api/v1/eventos/{evento_id}/compras",
        headers=sesion_usuario,
        json={"metodo_pago": "PSE"},
    ).get_json()["compra"]["referencia"]
    client.post(
        f"/api/v1/pagos/wompi/simulador/{referencia}",
        headers=sesion_usuario,
        json={"resultado": "APPROVED"},
    )
    assert saldo_de(client, sesion)["retenido_cop"] == 18_000
    assert client.get(ESTADISTICAS, headers=sesion).get_json()["resumen"]["entradas_vendidas"] == 1

    assert client.post(f"/api/v1/eventos/{evento_id}/cancelar", headers=sesion).status_code == 200

    assert saldo_de(client, sesion) == {
        "disponible_cop": 0,
        "retenido_cop": 0,
        "en_proceso_cop": 0,
        "retirado_cop": 0,
    }
    resumen = client.get(ESTADISTICAS, headers=sesion).get_json()
    assert resumen["resumen"]["entradas_vendidas"] == 0
    assert resumen["resumen"]["ingresos_brutos_cop"] == 0
    assert resumen["resumen"]["ingresos_netos_cop"] == 0
    assert resumen["ventas_por_dia"] == []
    por_evento = client.get(f"{PANEL}/eventos/{evento_id}/estadisticas", headers=sesion).get_json()
    assert (por_evento["entradas_vendidas"], por_evento["ingresos_netos_cop"]) == (0, 0)


@pytest.mark.parametrize(
    "estado",
    [
        EstadoCompra.PENDIENTE,
        EstadoCompra.RECHAZADA,
        EstadoCompra.ANULADA,
        EstadoCompra.REEMBOLSADA,
    ],
)
def test_solo_las_compras_aprobadas_suman(app, client, artista, sesion, estado):
    with app.app_context():
        for estado_evento in (EstadoEvento.FINALIZADO, EstadoEvento.PROGRAMADO):
            compra(crear_evento(artista, estado=estado_evento).id, 20_000, estado, aprobado_en=T_D)
    assert saldo_de(client, sesion)["disponible_cop"] == 0
    assert saldo_de(client, sesion)["retenido_cop"] == 0
    resumen = client.get(ESTADISTICAS, headers=sesion).get_json()
    assert resumen["resumen"]["entradas_vendidas"] == 0 and resumen["ventas_por_dia"] == []


def test_retiro_pendiente_reduce_el_disponible_y_el_rechazado_no(app, client, artista, sesion):
    """★ RN-14."""
    dar_saldo(app, artista)  # 108.000 disponibles
    with app.app_context():
        crear_retiro(artista.id, monto_cop=60_000, estado=EstadoRetiro.PENDIENTE)
    assert saldo_de(client, sesion)["disponible_cop"] == 48_000
    assert saldo_de(client, sesion)["en_proceso_cop"] == 60_000

    with app.app_context():
        crear_retiro(artista.id, monto_cop=50_000, estado=EstadoRetiro.RECHAZADO)
    assert saldo_de(client, sesion)["disponible_cop"] == 48_000  # el RECHAZADO no resta

    with app.app_context():
        crear_retiro(artista.id, monto_cop=8_000, estado=EstadoRetiro.PAGADO)
    final = saldo_de(client, sesion)
    assert (final["disponible_cop"], final["en_proceso_cop"], final["retirado_cop"]) == (
        40_000,
        60_000,
        8_000,
    )


def test_un_retiro_rechazado_vuelve_a_dejar_el_dinero_disponible(app, client, artista, sesion):
    dar_saldo(app, artista)
    with app.app_context():
        retiro_id = crear_retiro(artista.id, monto_cop=100_000).id
    assert saldo_de(client, sesion)["disponible_cop"] == 8_000
    with app.app_context():  # lo que hará el admin en la Fase 7
        db.session.get(Retiro, retiro_id).estado = EstadoRetiro.RECHAZADO
        db.session.commit()
    assert saldo_de(client, sesion)["disponible_cop"] == 108_000


# --- Estadísticas globales ---------------------------------------------------------------------


def test_estadisticas_con_datos_conocidos(client, sesion, escenario):
    """★ Totales calculados a mano (ver `escenario`)."""
    respuesta = client.get(ESTADISTICAS, headers=sesion)
    assert respuesta.status_code == 200
    cuerpo = respuesta.get_json()
    assert cuerpo["resumen"] == {
        "eventos_por_estado": {
            "BORRADOR": 1,
            "PROGRAMADO": 2,
            "EN_VIVO": 1,
            "FINALIZADO": 2,
            "CANCELADO": 1,
        },
        # 3 (E1) + 2 (E2) + 1 (E3) entradas aprobadas
        "entradas_vendidas": 6,
        # 3 × 20.000 + 2 × 50.000 + 30.000
        "ingresos_brutos_cop": 190_000,
        # 3 × 2.000 + 2 × 5.000 + 3.000
        "comision_plataforma_cop": 19_000,
        "ingresos_netos_cop": 171_000,
        "seguidores": 0,
        # (40 + 20 + 43) / 3 = 34,33…; E6 (pico 99) no se transmitió y no cuenta
        "pico_espectadores_promedio": 34.3,
    }
    assert cuerpo["saldo"] == {
        "disponible_cop": 20_000,
        "retenido_cop": 117_000,
        "en_proceso_cop": 20_000,
        "retirado_cop": 14_000,
    }
    resumen = cuerpo["resumen"]
    assert (
        resumen["ingresos_brutos_cop"]
        == resumen["comision_plataforma_cop"] + resumen["ingresos_netos_cop"]
    )


def test_ventas_por_dia_se_agrupan_en_hora_de_bogota(client, sesion, escenario):
    """Solo días con ventas; el límite del día es la medianoche de Bogotá (05:00 UTC)."""
    dias = client.get(ESTADISTICAS, headers=sesion).get_json()["ventas_por_dia"]
    assert dias == [
        # T_A y T_B caen antes de las 05:00 UTC: siguen siendo el 1 de noviembre en Bogotá
        {"fecha": "2026-11-01", "entradas": 2, "ingresos_netos_cop": 36_000},
        # T_C (05:00:00 UTC exacto) ya es el 2; más las 2 de E2 a las 10:00
        {"fecha": "2026-11-02", "entradas": 3, "ingresos_netos_cop": 18_000 + 2 * 45_000},
        {"fecha": "2026-11-03", "entradas": 1, "ingresos_netos_cop": 27_000},
    ]
    assert sum(d["entradas"] for d in dias) == 6
    assert sum(d["ingresos_netos_cop"] for d in dias) == 171_000
    # El 5 de noviembre solo hay una compra REEMBOLSADA: ese día no aparece.
    assert "2026-11-05" not in {d["fecha"] for d in dias}


def test_forma_exacta_de_8_4_1_y_tipos(client, sesion, escenario):
    cuerpo = client.get(ESTADISTICAS, headers=sesion).get_json()
    assert set(cuerpo) == {"resumen", "saldo", "ventas_por_dia"}
    assert set(cuerpo["resumen"]) == {
        "eventos_por_estado",
        "entradas_vendidas",
        "ingresos_brutos_cop",
        "comision_plataforma_cop",
        "ingresos_netos_cop",
        "seguidores",
        "pico_espectadores_promedio",
    }
    assert set(cuerpo["saldo"]) == {
        "disponible_cop",
        "retenido_cop",
        "en_proceso_cop",
        "retirado_cop",
    }
    assert set(cuerpo["ventas_por_dia"][0]) == {"fecha", "entradas", "ingresos_netos_cop"}
    # Dinero siempre entero; el promedio de picos es el único decimal.
    dinero = [v for k, v in cuerpo["resumen"].items() if k.endswith("_cop")]
    dinero += list(cuerpo["saldo"].values())
    assert all(type(v) is int for v in dinero)
    assert type(cuerpo["resumen"]["pico_espectadores_promedio"]) is float
    assert date.fromisoformat(cuerpo["ventas_por_dia"][0]["fecha"])


def test_estadisticas_sin_actividad(client, sesion):
    cuerpo = client.get(ESTADISTICAS, headers=sesion).get_json()
    assert cuerpo["resumen"]["eventos_por_estado"] == {
        "BORRADOR": 0,
        "PROGRAMADO": 0,
        "EN_VIVO": 0,
        "FINALIZADO": 0,
        "CANCELADO": 0,
    }
    assert cuerpo["resumen"]["pico_espectadores_promedio"] == 0.0
    assert cuerpo["ventas_por_dia"] == []


def test_el_promedio_de_picos_solo_cuenta_eventos_transmitidos(app, client, artista, sesion):
    with app.app_context():
        crear_evento(artista, pico_espectadores=10, inicio_real=INICIO)
        crear_evento(artista, pico_espectadores=15, inicio_real=INICIO)
        crear_evento(artista, pico_espectadores=500)  # nunca se transmitió
    assert (
        client.get(ESTADISTICAS, headers=sesion).get_json()["resumen"]["pico_espectadores_promedio"]
        == 12.5
    )


# --- Estadísticas por evento -------------------------------------------------------------------


def estadisticas_de(client, sesion, evento_id):
    return client.get(f"{PANEL}/eventos/{evento_id}/estadisticas", headers=sesion)


def test_estadisticas_de_un_evento_finalizado(client, sesion, escenario):
    respuesta = estadisticas_de(client, sesion, escenario.e1)
    assert respuesta.status_code == 200
    assert respuesta.get_json() == {
        "evento_id": escenario.e1,
        "entradas_vendidas": 3,
        "ingresos_brutos_cop": 60_000,
        "ingresos_netos_cop": 54_000,
        "pico_espectadores": 40,
        "vistas_vivo": 55,
        "vistas_replay": 7,
        "duracion_real_min": 90,
        "favoritos": 0,
        "ventas_por_dia": [
            {"fecha": "2026-11-01", "entradas": 2, "ingresos_netos_cop": 36_000},
            {"fecha": "2026-11-02", "entradas": 1, "ingresos_netos_cop": 18_000},
        ],
    }


def test_estadisticas_de_eventos_con_otras_ventas(client, sesion, escenario):
    programado = estadisticas_de(client, sesion, escenario.e2).get_json()
    assert (programado["entradas_vendidas"], programado["ingresos_brutos_cop"]) == (2, 100_000)
    assert programado["ingresos_netos_cop"] == 90_000
    assert programado["duracion_real_min"] is None  # no se transmitió

    en_vivo = estadisticas_de(client, sesion, escenario.e3).get_json()
    assert (en_vivo["entradas_vendidas"], en_vivo["ingresos_netos_cop"]) == (1, 27_000)
    assert en_vivo["duracion_real_min"] is None  # aún no termina
    assert en_vivo["ventas_por_dia"] == [
        {"fecha": "2026-11-03", "entradas": 1, "ingresos_netos_cop": 27_000}
    ]


@pytest.mark.parametrize("clave", ["e4", "e5", "e7"])
def test_estadisticas_de_eventos_sin_ventas_aprobadas(client, sesion, escenario, clave):
    cuerpo = estadisticas_de(client, sesion, getattr(escenario, clave)).get_json()
    assert (cuerpo["entradas_vendidas"], cuerpo["ingresos_brutos_cop"]) == (0, 0)
    assert cuerpo["ingresos_netos_cop"] == 0 and cuerpo["ventas_por_dia"] == []


def test_la_duracion_real_de_otro_evento_finalizado(client, sesion, escenario):
    assert estadisticas_de(client, sesion, escenario.e7).get_json()["duracion_real_min"] == 30


def test_estadisticas_de_un_evento_ajeno_son_403_no_es_dueno(client, sesion, escenario):
    """Un evento ajeno responde igual en todos los endpoints: 403 `NO_ES_DUENO`."""
    respuesta = estadisticas_de(client, sesion, escenario.ajeno)
    assert respuesta.status_code == 403
    assert respuesta.get_json()["codigo"] == "NO_ES_DUENO"
    assert "100000" not in respuesta.get_data(as_text=True)


def test_estadisticas_de_un_evento_inexistente_son_404(client, sesion):
    respuesta = estadisticas_de(client, sesion, "00000000-0000-4000-8000-000000000000")
    assert respuesta.status_code == 404
    assert respuesta.get_json()["codigo"] == "EVENTO_NO_ENCONTRADO"


def test_un_artista_ve_las_estadisticas_de_su_propio_evento_pero_no_las_del_otro(
    client, sesion, sesion_otro, escenario
):
    assert estadisticas_de(client, sesion_otro, escenario.ajeno).status_code == 200
    assert estadisticas_de(client, sesion_otro, escenario.e1).status_code == 403


# --- Solicitar retiro (RN-15) ------------------------------------------------------------------


def test_retiro_valido_nace_pendiente_y_reduce_el_disponible(app, client, artista, sesion):
    dar_saldo(app, artista)  # 108.000
    fijar_ahora(AHORA)
    respuesta = pedir_retiro(client, sesion, 100_000, banco="Davivienda", tipo_cuenta="CORRIENTE")

    assert respuesta.status_code == 201
    cuerpo = respuesta.get_json()
    assert cuerpo["estado"] == "PENDIENTE" and cuerpo["monto_cop"] == 100_000
    assert (cuerpo["banco"], cuerpo["tipo_cuenta"]) == ("Davivienda", "CORRIENTE")
    assert cuerpo["cuenta_ultimos4"] == "0042"
    assert cuerpo["referencia_pago"] is None and cuerpo["resuelto_en"] is None
    assert cuerpo["solicitado_en"].startswith("2026-11-10T15:00:00")
    assert "resuelto_por" not in cuerpo and "artista_id" not in cuerpo
    saldo = saldo_de(client, sesion)
    assert (saldo["disponible_cop"], saldo["en_proceso_cop"]) == (8_000, 100_000)


def test_retiro_mayor_que_el_disponible_es_422_saldo_insuficiente(app, client, artista, sesion):
    """★ RN-15."""
    dar_saldo(app, artista)
    respuesta = pedir_retiro(client, sesion, 108_001)
    assert respuesta.status_code == 422
    assert respuesta.get_json()["codigo"] == "SALDO_INSUFICIENTE"
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Retiro)) == 0


@pytest.mark.parametrize("monto", [49_999, 1, 0, -50_000])
def test_retiro_menor_al_minimo_es_422_monto_fuera_de_rango(app, client, artista, sesion, monto):
    dar_saldo(app, artista)
    respuesta = pedir_retiro(client, sesion, monto)
    assert respuesta.status_code == 422
    assert respuesta.get_json()["codigo"] == "MONTO_FUERA_DE_RANGO"


def test_el_minimo_se_valida_antes_que_el_saldo(client, sesion):
    """Sin saldo, un monto bajo sigue siendo 'fuera de rango' y uno válido 'sin saldo'."""
    assert pedir_retiro(client, sesion, 100).get_json()["codigo"] == "MONTO_FUERA_DE_RANGO"
    assert pedir_retiro(client, sesion, 50_000).get_json()["codigo"] == "SALDO_INSUFICIENTE"


def test_los_limites_exactos_del_monto(app, client, artista, sesion):
    dar_saldo(app, artista)  # 108.000
    assert pedir_retiro(client, sesion, 50_000).status_code == 201  # == RETIRO_MIN_COP
    assert pedir_retiro(client, sesion, 58_001).status_code == 422  # un peso más que lo que queda
    assert pedir_retiro(client, sesion, 58_000).status_code == 201  # == disponible restante
    assert saldo_de(client, sesion)["disponible_cop"] == 0
    assert pedir_retiro(client, sesion, 50_000).get_json()["codigo"] == "SALDO_INSUFICIENTE"


def test_no_se_retira_dinero_retenido(app, client, artista, sesion):
    """El neto de eventos que no han terminado no está disponible."""
    with app.app_context():
        compra(crear_evento(artista, estado=EstadoEvento.EN_VIVO).id, 200_000, aprobado_en=T_D)
    assert saldo_de(client, sesion)["retenido_cop"] == 180_000
    assert pedir_retiro(client, sesion, 100_000).get_json()["codigo"] == "SALDO_INSUFICIENTE"


def test_retiro_con_auto_aprobacion_nace_pagado(app, client, artista, sesion, monkeypatch):
    monkeypatch.setitem(app.config, "RETIRO_AUTO_APROBAR", True)
    dar_saldo(app, artista)
    fijar_ahora(AHORA)
    respuesta = pedir_retiro(client, sesion, 60_000)

    assert respuesta.status_code == 201
    cuerpo = respuesta.get_json()
    assert cuerpo["estado"] == "PAGADO"
    assert cuerpo["referencia_pago"].startswith("sim-payout-")
    assert len(cuerpo["referencia_pago"]) == len("sim-payout-") + 36  # sim-payout-<uuid>
    assert cuerpo["resuelto_en"].startswith("2026-11-10T15:00:00")
    saldo = saldo_de(client, sesion)
    assert (saldo["disponible_cop"], saldo["en_proceso_cop"], saldo["retirado_cop"]) == (
        48_000,
        0,
        60_000,
    )


def test_retiro_sin_auto_aprobacion_nace_pendiente(app, client, artista, sesion, monkeypatch):
    monkeypatch.setitem(app.config, "RETIRO_AUTO_APROBAR", False)
    dar_saldo(app, artista)
    cuerpo = pedir_retiro(client, sesion, 60_000).get_json()
    assert cuerpo["estado"] == "PENDIENTE"
    assert cuerpo["referencia_pago"] is None and cuerpo["resuelto_en"] is None
    with app.app_context():
        assert db.session.scalar(select(Retiro)).resuelto_por is None


def test_la_auto_aprobacion_tambien_respeta_el_saldo(app, client, sesion, monkeypatch):
    monkeypatch.setitem(app.config, "RETIRO_AUTO_APROBAR", True)
    assert pedir_retiro(client, sesion, 60_000).status_code == 422


# --- cuenta_ultimos4 y validación de entrada ---------------------------------------------------


@pytest.mark.parametrize(
    "cuenta",
    [
        "",
        "1",
        "123",
        "12345",
        "1234567890123456",
        "12a4",
        "abcd",
        " 123",
        "1234 ",
        "1234\n",
        "１２３４",
        1234,
    ],
    ids=[
        "vacia",
        "uno",
        "tres",
        "cinco",
        "numero-completo",
        "con-letra",
        "letras",
        "espacio-antes",
        "espacio-despues",
        "salto-de-linea",
        "digitos-anchos",
        "entero",
    ],
)
def test_cuenta_ultimos4_debe_ser_exactamente_4_digitos(app, client, artista, sesion, cuenta):
    """★ §5.4: nunca se guarda un número de cuenta más largo."""
    dar_saldo(app, artista)
    respuesta = pedir_retiro(client, sesion, 60_000, cuenta_ultimos4=cuenta)
    assert respuesta.status_code == 422
    assert "cuenta_ultimos4" in respuesta.get_json()["detalles"]
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Retiro)) == 0


def test_cuenta_ultimos4_conserva_los_ceros_a_la_izquierda(app, client, artista, sesion):
    dar_saldo(app, artista)
    assert (
        pedir_retiro(client, sesion, 60_000, cuenta_ultimos4="0007").get_json()["cuenta_ultimos4"]
        == "0007"
    )
    with app.app_context():
        assert db.session.scalar(select(Retiro.cuenta_ultimos4)) == "0007"


def test_el_numero_de_cuenta_completo_no_se_registra_en_los_logs(
    app, client, artista, sesion, caplog
):
    dar_saldo(app, artista)
    completo = "9876543210123456"
    with caplog.at_level(logging.DEBUG):
        rechazado = pedir_retiro(client, sesion, 60_000, cuenta_ultimos4=completo)
        otro_campo = pedir_retiro(client, sesion, 60_000, numero_cuenta=completo)
        pedir_retiro(client, sesion, 60_000, cuenta_ultimos4="3456")
    assert rechazado.status_code == 422 and otro_campo.status_code == 422
    assert completo not in caplog.text
    assert completo not in rechazado.get_data(as_text=True)
    assert completo not in otro_campo.get_data(as_text=True)


@pytest.mark.parametrize(
    "cambios",
    [
        {"numero_cuenta": "1234567890"},
        {"estado": "PAGADO"},
        {"artista_id": "00000000-0000-4000-8000-000000000000"},
        {"referencia_pago": "sim-payout-x"},
        {"resuelto_por": "00000000-0000-4000-8000-000000000000"},
        {"banco": ""},
        {"banco": "B" * 61},
        {"tipo_cuenta": "AHORRO"},
        {"tipo_cuenta": "ahorros"},
        {"monto_cop": 60_000.5},
        {"monto_cop": "60000"},
        {"monto_cop": True},
        {"monto_cop": None},
    ],
)
def test_retiro_rechaza_campos_invalidos_o_desconocidos(app, client, artista, sesion, cambios):
    dar_saldo(app, artista)
    cuerpo = {
        "monto_cop": 60_000,
        "banco": "Bancolombia",
        "tipo_cuenta": "AHORROS",
        "cuenta_ultimos4": "0042",
        **cambios,
    }
    respuesta = client.post(RETIROS, headers=sesion, json=cuerpo)
    assert respuesta.status_code == 422 and respuesta.get_json()["codigo"] == "VALIDACION"


@pytest.mark.parametrize("campo", ["monto_cop", "banco", "tipo_cuenta", "cuenta_ultimos4"])
def test_retiro_exige_todos_los_campos(client, sesion, campo):
    cuerpo = {
        "monto_cop": 60_000,
        "banco": "Bancolombia",
        "tipo_cuenta": "AHORROS",
        "cuenta_ultimos4": "0042",
    }
    del cuerpo[campo]
    respuesta = client.post(RETIROS, headers=sesion, json=cuerpo)
    assert respuesta.status_code == 422 and campo in respuesta.get_json()["detalles"]


@pytest.mark.parametrize("tipo", [t.value for t in TipoCuenta])
def test_tipos_de_cuenta_validos(app, client, artista, sesion, tipo):
    dar_saldo(app, artista)
    assert pedir_retiro(client, sesion, 50_000, tipo_cuenta=tipo).status_code == 201


def test_ddl_exige_4_digitos_y_monto_positivo(app, artista):
    with app.app_context():
        for invalido in (
            {"cuenta_ultimos4": "12345"},
            {"cuenta_ultimos4": "abcd"},
            {"monto_cop": 0},
        ):
            with pytest.raises(DBAPIError):
                crear_retiro(artista.id, **invalido)
            db.session.rollback()


# --- Concurrencia (RN-15) ----------------------------------------------------------------------


def _lanzar(app, sesion, montos) -> list[tuple[int, str | None]]:
    resultados = []

    def intentar(monto):
        respuesta = pedir_retiro(app.test_client(), sesion, monto)
        resultados.append((respuesta.status_code, respuesta.get_json().get("codigo")))

    hilos = [threading.Thread(target=intentar, args=(m,)) for m in montos]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(60)
    return resultados


@pytest.fixture
def saldo_lento(monkeypatch):
    """Alarga la ventana entre 'calcular el disponible' e 'insertar' para exponer la carrera."""
    original = ArtistasRepository.sumas_de_retiros

    def lento(self, artista_id):
        resultado = original(self, artista_id)
        time.sleep(0.5)
        return resultado

    monkeypatch.setattr(ArtistasRepository, "sumas_de_retiros", lento)


def test_dos_retiros_concurrentes_que_exceden_el_saldo_solo_dejan_pasar_uno(
    app, client, artista, sesion, saldo_lento
):
    """★ RN-15: 108.000 disponibles; dos de 60.000 a la vez."""
    dar_saldo(app, artista)
    resultados = _lanzar(app, sesion, [60_000, 60_000])

    assert sorted(codigo for codigo, _ in resultados) == [201, 422]
    assert (422, "SALDO_INSUFICIENTE") in resultados
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Retiro)) == 1
    saldo = saldo_de(client, sesion)
    assert (saldo["disponible_cop"], saldo["en_proceso_cop"]) == (48_000, 60_000)


def test_varios_retiros_concurrentes_nunca_superan_el_saldo(
    app, client, artista, sesion, saldo_lento
):
    dar_saldo(app, artista)  # 108.000 → caben 2 de 50.000 (100.000), no 3
    resultados = _lanzar(app, sesion, [50_000] * 5)
    exitosos = [r for r in resultados if r[0] == 201]
    assert len(exitosos) == 2 and len(resultados) == 5
    assert all(r == (422, "SALDO_INSUFICIENTE") for r in resultados if r[0] != 201)
    saldo = saldo_de(client, sesion)
    assert saldo["en_proceso_cop"] == 100_000 and saldo["disponible_cop"] == 8_000


def test_los_retiros_de_artistas_distintos_no_se_bloquean_entre_si(
    app, client, artista, sesion, otro, sesion_otro, saldo_lento
):
    dar_saldo(app, artista)
    dar_saldo(app, otro)
    inicio = time.monotonic()
    resultados = []

    def intentar(sesion_artista):
        resultados.append(pedir_retiro(app.test_client(), sesion_artista, 100_000).status_code)

    hilos = [threading.Thread(target=intentar, args=(s,)) for s in (sesion, sesion_otro)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(60)
    assert resultados == [201, 201]
    assert time.monotonic() - inicio < 0.95  # en paralelo (2 × 0,5 s en serie serían ≥ 1 s)


def test_el_retiro_bloquea_la_fila_del_artista_antes_de_calcular_el_disponible(
    app, client, artista, sesion
):
    dar_saldo(app, artista)
    sentencias = []

    def capturar(conexion, cursor, statement, parameters, context, executemany):
        sentencias.append(" ".join(statement.split()).lower())

    with app.app_context():
        motor = db.engine
    sa_event.listen(motor, "before_cursor_execute", capturar)
    try:
        assert pedir_retiro(client, sesion, 60_000).status_code == 201
    finally:
        sa_event.remove(motor, "before_cursor_execute", capturar)

    bloqueo = next(
        i for i, s in enumerate(sentencias) if "from usuarios" in s and "for update" in s
    )
    calculo = next(i for i, s in enumerate(sentencias) if "from compras" in s)
    insercion = next(i for i, s in enumerate(sentencias) if s.startswith("insert into retiros"))
    assert bloqueo < calculo < insercion


# --- Todo son agregaciones SQL -----------------------------------------------------------------


def test_las_consultas_sobre_compras_son_agregaciones(app, client, sesion, escenario, artista):
    sentencias = []

    def capturar(conexion, cursor, statement, parameters, context, executemany):
        sentencias.append(" ".join(statement.split()).lower())

    with app.app_context():
        motor = db.engine
    sa_event.listen(motor, "before_cursor_execute", capturar)
    try:
        client.get(ESTADISTICAS, headers=sesion)
        client.get(SALDO, headers=sesion)
        client.get(f"{PANEL}/eventos/{escenario.e1}/estadisticas", headers=sesion)
    finally:
        sa_event.remove(motor, "before_cursor_execute", capturar)

    sobre_compras = [s for s in sentencias if s.startswith("select") and "from compras" in s]
    assert sobre_compras
    for sql in sobre_compras:
        assert "sum(" in sql or "count(" in sql, sql
        assert "limit" not in sql


# --- Historial de retiros ----------------------------------------------------------------------


def test_historial_pagina_filtra_y_ordena(app, client, artista, sesion, otro, sesion_otro):
    with app.app_context():
        for dia in range(5):
            crear_retiro(
                artista.id,
                monto_cop=10_000 + dia,
                solicitado_en=AHORA + timedelta(days=dia),
                estado=EstadoRetiro.PAGADO if dia % 2 else EstadoRetiro.PENDIENTE,
            )
        crear_retiro(otro.id, monto_cop=77_777)

    pagina = client.get(RETIROS, headers=sesion, query_string={"page_size": 2}).get_json()
    assert (pagina["page"], pagina["page_size"], pagina["total"]) == (1, 2, 5)
    assert [r["monto_cop"] for r in pagina["items"]] == [10_004, 10_003]  # más reciente primero
    segunda = client.get(RETIROS, headers=sesion, query_string={"page": 3, "page_size": 2})
    assert [r["monto_cop"] for r in segunda.get_json()["items"]] == [10_000]

    pagados = client.get(RETIROS, headers=sesion, query_string={"estado": "PAGADO"}).get_json()
    assert pagados["total"] == 2 and {r["estado"] for r in pagados["items"]} == {"PAGADO"}
    todos = client.get(RETIROS, headers=sesion).get_json()["items"]
    assert 77_777 not in {r["monto_cop"] for r in todos}  # nunca los de otro artista
    assert not {"artista_id", "resuelto_por"} & set(todos[0])


@pytest.mark.parametrize(
    "params", [{"estado": "NADA"}, {"page": 0}, {"page_size": 0}, {"orden": "monto"}]
)
def test_historial_rechaza_parametros_invalidos(client, sesion, params):
    assert client.get(RETIROS, headers=sesion, query_string=params).status_code == 422


# --- Roles y autenticación ---------------------------------------------------------------------

ENDPOINTS = [
    ("get", ESTADISTICAS),
    ("get", f"{PANEL}/eventos/00000000-0000-4000-8000-000000000000/estadisticas"),
    ("get", SALDO),
    ("post", RETIROS),
    ("get", RETIROS),
    ("get", f"{PANEL}/eventos"),
]


@pytest.mark.parametrize(("metodo", "ruta"), ENDPOINTS)
def test_sin_token_es_401(client, metodo, ruta):
    assert getattr(client, metodo)(ruta).status_code == 401


@pytest.mark.parametrize(("metodo", "ruta"), ENDPOINTS)
def test_un_usuario_normal_y_un_admin_reciben_403_en_todo_el_panel(
    client, sesion_usuario, sesion_admin, metodo, ruta
):
    """★ §4."""
    for encabezado in (sesion_usuario, sesion_admin):
        respuesta = getattr(client, metodo)(ruta, headers=encabezado, json={})
        assert respuesta.status_code == 403
        assert respuesta.get_json()["codigo"] == "ROL_NO_PERMITIDO"


def test_artista_suspendido_no_usa_el_panel(app, client, artista, sesion):
    from app.common.enums import EstadoUsuario
    from app.modules.usuarios.models import Usuario

    with app.app_context():
        db.session.get(Usuario, artista.id).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    respuesta = client.get(SALDO, headers=sesion)
    assert respuesta.status_code == 403 and respuesta.get_json()["codigo"] == "CUENTA_SUSPENDIDA"


def test_me_no_choca_con_una_ruta_de_artista_por_uuid(client, sesion):
    """`/artistas/me/...` responde; `/artistas/me` no se toma como un `artista_id` uuid."""
    assert client.get(SALDO, headers=sesion).status_code == 200
    assert client.get("/api/v1/artistas/me", headers=sesion).status_code == 404


# --- Swagger -----------------------------------------------------------------------------------


def test_swagger_documenta_el_panel(client):
    spec = client.get("/api/openapi.json").get_json()
    esperados = [
        (f"{PANEL}/estadisticas", "get", {"200", "401", "403"}),
        (f"{PANEL}/eventos/{{evento_id}}/estadisticas", "get", {"200", "401", "403", "404"}),
        (f"{PANEL}/saldo", "get", {"200", "401", "403"}),
        (RETIROS, "post", {"201", "401", "403", "422"}),
        (RETIROS, "get", {"200", "401", "403", "422"}),
    ]
    for ruta, metodo, codigos in esperados:
        operacion = spec["paths"][ruta][metodo]
        assert operacion["tags"] == ["Artista (panel)"] and operacion["summary"], ruta
        assert operacion["security"], ruta
        assert codigos <= set(operacion["responses"]), (ruta, set(operacion["responses"]))

    esquemas = spec["components"]["schemas"]
    # La zona horaria de ventas_por_dia está documentada.
    assert (
        "America/Bogota" in esquemas["Estadisticas"]["properties"]["ventas_por_dia"]["description"]
    )
    assert "America/Bogota" in spec["paths"][ESTADISTICAS]["get"]["description"]
    for campo, esquema in (
        ("cuenta_ultimos4", "RetiroCrear"),
        ("monto_cop", "RetiroCrear"),
        ("disponible_cop", "Saldo"),
        ("pico_espectadores_promedio", "Resumen"),
        ("fecha", "VentaDiaria"),
    ):
        assert "example" in esquemas[esquema]["properties"][campo], (esquema, campo)
    assert esquemas["Resumen"]["properties"]["pico_espectadores_promedio"]["type"] == "number"
    assert esquemas["Saldo"]["properties"]["disponible_cop"]["type"] == "integer"
