"""Transmisión y reproducción (Fase 4): RN-05, RN-10, RN-11, RN-12, contadores, C4, RQ5, RQ6."""

import hashlib
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import select

from app.common.clock import ahora_utc, fijar_ahora
from app.common.enums import EstadoCompra, EstadoEvento, EstadoUsuario, Rol
from app.extensions import db
from app.modules.eventos.models import Evento
from app.modules.usuarios.models import Usuario
from tests.factories import (
    crear_compra,
    crear_evento,
    crear_usuario,
    datos_evento,
    encabezados,
    iniciar_sesion,
)

AHORA = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)


def ruta(evento_id, sufijo="") -> str:
    return f"/api/v1/eventos/{evento_id}{sufijo}"


def entrar_sesion(client, email):
    return encabezados(iniciar_sesion(client, email)["access_token"])


# --- Fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def artista(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("luz@example.com", Rol.ARTISTA, "Luz Band").id)


@pytest.fixture
def sesion_artista(client, artista):
    return entrar_sesion(client, "luz@example.com")


@pytest.fixture
def sesion_otro_artista(client, app):
    with app.app_context():
        crear_usuario("otro@example.com", Rol.ARTISTA, "Otro Grupo")
    return entrar_sesion(client, "otro@example.com")


@pytest.fixture
def comprador(client, app):
    with app.app_context():
        crear_usuario("ana@example.com")
    return entrar_sesion(client, "ana@example.com")


@pytest.fixture
def sesion_admin(client, app):
    with app.app_context():
        crear_usuario("admin@example.com", Rol.ADMIN)
    return entrar_sesion(client, "admin@example.com")


@pytest.fixture
def evento(app, artista):
    """Evento PROGRAMADO dentro de una semana, de `artista`."""
    with app.app_context():
        return SimpleNamespace(id=str(crear_evento(artista).id))


def nuevo_evento(app, artista, **campos) -> str:
    with app.app_context():
        return str(crear_evento(artista, **campos).id)


def pedir_clave(client, sesion, evento_id) -> str:
    respuesta = client.post(ruta(evento_id, "/transmision/credenciales"), headers=sesion)
    assert respuesta.status_code == 200, respuesta.get_json()
    return respuesta.get_json()["stream_key"]


def iniciar(client, sesion, evento_id, clave):
    return client.post(
        ruta(evento_id, "/transmision/iniciar"), headers=sesion, json={"stream_key": clave}
    )


def poner_en_vivo(client, sesion, evento_id) -> str:
    clave = pedir_clave(client, sesion, evento_id)
    assert iniciar(client, sesion, evento_id, clave).status_code == 200
    return clave


def finalizar(client, sesion, evento_id):
    return client.post(ruta(evento_id, "/transmision/finalizar"), headers=sesion)


def entrar(client, sesion, evento_id):
    return client.post(ruta(evento_id, "/reproduccion/entrar"), headers=sesion)


def salir(client, sesion, evento_id):
    return client.post(ruta(evento_id, "/reproduccion/salir"), headers=sesion)


def comprar_aprobada(client, sesion, evento_id) -> None:
    compra = client.post(
        f"/api/v1/eventos/{evento_id}/compras", headers=sesion, json={"metodo_pago": "NEQUI"}
    )
    assert compra.status_code == 201, compra.get_json()
    referencia = compra.get_json()["compra"]["referencia"]
    pago = client.post(
        f"/api/v1/pagos/wompi/simulador/{referencia}",
        headers=sesion,
        json={"resultado": "APPROVED"},
    )
    assert pago.get_json()["estado"] == "APROBADA"


def evento_en_bd(app, evento_id) -> SimpleNamespace:
    with app.app_context():
        fila = db.session.get(Evento, uuid.UUID(str(evento_id)))
        return SimpleNamespace(**{c.name: getattr(fila, c.name) for c in Evento.__table__.columns})


def usuario_id(app, email):
    with app.app_context():
        return db.session.scalar(select(Usuario.id).where(Usuario.email == email))


def estado_de(app, evento_id) -> EstadoEvento:
    return evento_en_bd(app, evento_id).estado


# --- Credenciales (RN-11) ----------------------------------------------------------------------


def test_credenciales_devuelven_url_rtmp_y_clave(client, evento, sesion_artista):
    respuesta = client.post(ruta(evento.id, "/transmision/credenciales"), headers=sesion_artista)
    assert respuesta.status_code == 200
    cuerpo = respuesta.get_json()
    assert set(cuerpo) == {"rtmp_url", "stream_key"}
    assert cuerpo["rtmp_url"] == f"rtmp://localhost:1935/live/{evento.id}"
    assert len(cuerpo["stream_key"]) >= 43


def test_la_clave_se_guarda_solo_como_hash_sha256(app, client, evento, sesion_artista):
    """★ RN-11."""
    clave = pedir_clave(client, sesion_artista, evento.id)
    fila = evento_en_bd(app, evento.id)
    assert fila.stream_key_hash == hashlib.sha256(clave.encode()).hexdigest()
    assert len(fila.stream_key_hash) == 64
    assert clave not in {str(valor) for valor in vars(fila).values()}  # ninguna columna la tiene


def test_la_clave_no_aparece_en_ninguna_respuesta_posterior(
    app, client, evento, sesion_artista, comprador
):
    """★ RN-11: se muestra una única vez; tampoco su hash."""
    clave = pedir_clave(client, sesion_artista, evento.id)
    hash_clave = hashlib.sha256(clave.encode()).hexdigest()
    comprar_aprobada(client, comprador, evento.id)

    respuestas = [
        client.get(ruta(evento.id)),
        client.get(ruta(evento.id), headers=sesion_artista),
        client.get("/api/v1/eventos"),
        client.get("/api/v1/artistas/me/eventos", headers=sesion_artista),
        client.get(ruta(evento.id, "/transmision"), headers=sesion_artista),
        iniciar(client, sesion_artista, evento.id, clave),
        entrar(client, comprador, evento.id),
        client.get(ruta(evento.id), headers=comprador),
        client.get(ruta(evento.id, "/transmision"), headers=sesion_artista),
        finalizar(client, sesion_artista, evento.id),
        entrar(client, comprador, evento.id),
        client.get(ruta(evento.id), headers=sesion_artista),
    ]
    for respuesta in respuestas:
        assert respuesta.status_code == 200, respuesta.get_json()
        assert clave not in respuesta.get_data(as_text=True)
        assert hash_clave not in respuesta.get_data(as_text=True)
        assert "stream_key" not in respuesta.get_json()


def test_pedir_credenciales_de_nuevo_rota_la_clave(app, client, evento, sesion_artista):
    """★ RN-11: la anterior deja de valer."""
    primera = pedir_clave(client, sesion_artista, evento.id)
    segunda = pedir_clave(client, sesion_artista, evento.id)
    assert primera != segunda
    assert (
        evento_en_bd(app, evento.id).stream_key_hash == hashlib.sha256(segunda.encode()).hexdigest()
    )

    vieja = iniciar(client, sesion_artista, evento.id, primera)
    assert vieja.status_code == 401 and vieja.get_json()["codigo"] == "CLAVE_EMISION_INVALIDA"
    assert estado_de(app, evento.id) == EstadoEvento.PROGRAMADO
    assert iniciar(client, sesion_artista, evento.id, segunda).status_code == 200


@pytest.mark.parametrize(
    "estado",
    [EstadoEvento.BORRADOR, EstadoEvento.EN_VIVO, EstadoEvento.FINALIZADO, EstadoEvento.CANCELADO],
)
def test_credenciales_solo_con_el_evento_programado(app, client, artista, sesion_artista, estado):
    evento_id = nuevo_evento(app, artista, estado=estado)
    respuesta = client.post(ruta(evento_id, "/transmision/credenciales"), headers=sesion_artista)
    assert respuesta.status_code == 409
    assert respuesta.get_json()["codigo"] == "TRANSICION_INVALIDA"
    assert evento_en_bd(app, evento_id).stream_key_hash is None


# --- Iniciar (RQ6, RN-05) ----------------------------------------------------------------------


def test_iniciar_con_clave_invalida_es_401(app, client, evento, sesion_artista):
    """★ RQ6."""
    pedir_clave(client, sesion_artista, evento.id)
    respuesta = iniciar(client, sesion_artista, evento.id, "clave-incorrecta")
    assert respuesta.status_code == 401
    assert respuesta.get_json()["codigo"] == "CLAVE_EMISION_INVALIDA"
    assert estado_de(app, evento.id) == EstadoEvento.PROGRAMADO
    assert evento_en_bd(app, evento.id).inicio_real is None


def test_iniciar_sin_haber_pedido_credenciales_es_401(client, evento, sesion_artista):
    respuesta = iniciar(client, sesion_artista, evento.id, "lo-que-sea")
    assert respuesta.status_code == 401
    assert respuesta.get_json()["codigo"] == "CLAVE_EMISION_INVALIDA"


def test_iniciar_con_la_clave_valida_pone_el_evento_en_vivo(app, client, evento, sesion_artista):
    """★ RQ6/RN-05."""
    fijar_ahora(AHORA)
    clave = pedir_clave(client, sesion_artista, evento.id)
    respuesta = iniciar(client, sesion_artista, evento.id, clave)

    assert respuesta.status_code == 200
    cuerpo = respuesta.get_json()
    assert (cuerpo["estado"], cuerpo["espectadores_actuales"], cuerpo["pico_espectadores"]) == (
        "EN_VIVO",
        0,
        0,
    )
    assert cuerpo["inicio_real"].startswith("2026-11-02T20:00:00") and cuerpo["fin_real"] is None
    fila = evento_en_bd(app, evento.id)
    assert fila.estado == EstadoEvento.EN_VIVO and fila.inicio_real == AHORA


def test_iniciar_dos_veces_es_409(client, evento, sesion_artista):
    clave = poner_en_vivo(client, sesion_artista, evento.id)
    segunda = iniciar(client, sesion_artista, evento.id, clave)
    assert segunda.status_code == 409 and segunda.get_json()["codigo"] == "TRANSICION_INVALIDA"


@pytest.mark.parametrize(
    "cuerpo",
    [{}, {"stream_key": ""}, {"stream_key": 123}, {"stream_key": "x", "estado": "EN_VIVO"}],
)
def test_iniciar_valida_el_cuerpo(client, evento, sesion_artista, cuerpo):
    pedir_clave(client, sesion_artista, evento.id)
    respuesta = client.post(
        ruta(evento.id, "/transmision/iniciar"), headers=sesion_artista, json=cuerpo
    )
    assert respuesta.status_code == 422


@pytest.mark.parametrize("estado", [EstadoEvento.BORRADOR, EstadoEvento.CANCELADO])
def test_no_se_inicia_un_evento_que_no_esta_programado(
    app, client, artista, sesion_artista, estado
):
    evento_id = nuevo_evento(app, artista, estado=estado)
    respuesta = iniciar(client, sesion_artista, evento_id, "x")
    assert respuesta.status_code == 409


def test_un_artista_no_puede_tener_dos_eventos_en_vivo(app, client, artista, sesion_artista):
    """RN-05."""
    primero, segundo = nuevo_evento(app, artista), nuevo_evento(app, artista)
    clave2 = pedir_clave(client, sesion_artista, segundo)
    poner_en_vivo(client, sesion_artista, primero)

    respuesta = iniciar(client, sesion_artista, segundo, clave2)
    assert respuesta.status_code == 409
    assert respuesta.get_json()["codigo"] == "YA_HAY_EVENTO_EN_VIVO"
    assert estado_de(app, segundo) == EstadoEvento.PROGRAMADO

    # Al finalizar el primero, el segundo ya puede salir.
    assert finalizar(client, sesion_artista, primero).status_code == 200
    assert iniciar(client, sesion_artista, segundo, clave2).status_code == 200


def test_el_tope_de_un_evento_en_vivo_es_por_artista(
    app, client, artista, sesion_artista, sesion_otro_artista
):
    poner_en_vivo(client, sesion_artista, nuevo_evento(app, artista))
    otro = usuario_id(app, "otro@example.com")
    ajeno = nuevo_evento(app, SimpleNamespace(id=otro))
    clave = pedir_clave(client, sesion_otro_artista, ajeno)
    assert iniciar(client, sesion_otro_artista, ajeno, clave).status_code == 200


def test_dos_inicios_simultaneos_del_mismo_artista_dejan_uno_en_vivo(
    app, client, artista, sesion_artista
):
    """RN-05: el bloqueo del artista serializa los inicios."""
    eventos = [nuevo_evento(app, artista), nuevo_evento(app, artista)]
    claves = [pedir_clave(client, sesion_artista, e) for e in eventos]
    codigos = []

    def intentar(evento_id, clave):
        codigos.append(iniciar(app.test_client(), sesion_artista, evento_id, clave).status_code)

    hilos = [
        threading.Thread(target=intentar, args=par) for par in zip(eventos, claves, strict=True)
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(30)

    assert sorted(codigos) == [200, 409]
    assert sorted(estado_de(app, e).value for e in eventos) == ["EN_VIVO", "PROGRAMADO"]


def test_ventana_de_inicio_con_enforce_start_window(
    app, client, artista, sesion_artista, monkeypatch
):
    """RN-05: solo desde `fecha_inicio − START_WINDOW_MIN`."""
    monkeypatch.setitem(app.config, "ENFORCE_START_WINDOW", True)
    fijar_ahora(AHORA)
    evento_id = nuevo_evento(app, artista, fecha_inicio=AHORA + timedelta(hours=2))
    clave = pedir_clave(client, sesion_artista, evento_id)

    for antes in (timedelta(hours=1), timedelta(minutes=30, seconds=1)):
        fijar_ahora(AHORA + timedelta(hours=2) - antes)
        respuesta = iniciar(client, sesion_artista, evento_id, clave)
        assert respuesta.status_code == 409, antes
        assert respuesta.get_json()["codigo"] == "VENTANA_DE_INICIO_NO_ABIERTA"
    assert estado_de(app, evento_id) == EstadoEvento.PROGRAMADO

    fijar_ahora(AHORA + timedelta(hours=1, minutes=30))  # justo 30 min antes
    assert iniciar(client, sesion_artista, evento_id, clave).status_code == 200


def test_sin_enforce_start_window_se_puede_iniciar_antes(app, client, evento, sesion_artista):
    assert app.config["ENFORCE_START_WINDOW"] is False
    poner_en_vivo(client, sesion_artista, evento.id)


# --- Finalizar (RN-12) y estado ----------------------------------------------------------------


@pytest.mark.parametrize("replay_habilitado", [True, False])
def test_finalizar_fija_fin_real_audiencia_en_cero_y_replay(
    app, client, artista, sesion_artista, replay_habilitado
):
    """★ RN-12."""
    evento_id = nuevo_evento(app, artista, replay_habilitado=replay_habilitado)
    fijar_ahora(AHORA)
    poner_en_vivo(client, sesion_artista, evento_id)
    client.post(
        f"/api/v1/dev/eventos/{evento_id}/audiencia-simulada",
        headers=sesion_artista,
        json={"espectadores": 30},
    )

    fijar_ahora(AHORA + timedelta(minutes=75))
    respuesta = finalizar(client, sesion_artista, evento_id)

    assert respuesta.status_code == 200
    cuerpo = respuesta.get_json()
    assert cuerpo["estado"] == "FINALIZADO"
    assert (cuerpo["espectadores_actuales"], cuerpo["pico_espectadores"]) == (0, 30)
    assert cuerpo["duracion_transcurrida_min"] == 75
    fila = evento_en_bd(app, evento_id)
    assert fila.fin_real == AHORA + timedelta(minutes=75)
    assert fila.replay_disponible is replay_habilitado


@pytest.mark.parametrize(
    "estado", [EstadoEvento.PROGRAMADO, EstadoEvento.FINALIZADO, EstadoEvento.CANCELADO]
)
def test_solo_se_finaliza_un_evento_en_vivo(app, client, artista, sesion_artista, estado):
    evento_id = nuevo_evento(app, artista, estado=estado)
    respuesta = finalizar(client, sesion_artista, evento_id)
    assert respuesta.status_code == 409 and respuesta.get_json()["codigo"] == "TRANSICION_INVALIDA"


def test_estado_de_la_transmision(client, evento, sesion_artista):
    ruta_estado = ruta(evento.id, "/transmision")
    antes = client.get(ruta_estado, headers=sesion_artista).get_json()
    assert (antes["estado"], antes["duracion_transcurrida_min"], antes["inicio_real"]) == (
        "PROGRAMADO",
        0,
        None,
    )

    fijar_ahora(AHORA)
    poner_en_vivo(client, sesion_artista, evento.id)
    fijar_ahora(AHORA + timedelta(minutes=95, seconds=40))
    vivo = client.get(ruta_estado, headers=sesion_artista).get_json()
    assert (vivo["estado"], vivo["duracion_transcurrida_min"]) == ("EN_VIVO", 95)

    finalizar(client, sesion_artista, evento.id)
    fijar_ahora(AHORA + timedelta(hours=5))
    despues = client.get(ruta_estado, headers=sesion_artista).get_json()
    assert (despues["estado"], despues["duracion_transcurrida_min"]) == ("FINALIZADO", 95)


# --- Propiedad y roles -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "sufijo", "cuerpo"),
    [
        ("post", "/transmision/credenciales", None),
        ("post", "/transmision/iniciar", {"stream_key": "x"}),
        ("post", "/transmision/finalizar", None),
        ("get", "/transmision", None),
    ],
)
def test_un_artista_no_dueno_recibe_403(
    app, client, evento, sesion_artista, sesion_otro_artista, metodo, sufijo, cuerpo
):
    """★ §4 anti-IDOR."""
    pedir_clave(client, sesion_artista, evento.id)
    respuesta = getattr(client, metodo)(
        ruta(evento.id, sufijo), headers=sesion_otro_artista, json=cuerpo
    )
    assert respuesta.status_code == 403 and respuesta.get_json()["codigo"] == "NO_ES_DUENO"
    assert estado_de(app, evento.id) == EstadoEvento.PROGRAMADO


def test_el_no_dueno_no_puede_rotar_la_clave_del_dueno(
    app, client, evento, sesion_artista, sesion_otro_artista
):
    clave = pedir_clave(client, sesion_artista, evento.id)
    client.post(ruta(evento.id, "/transmision/credenciales"), headers=sesion_otro_artista)
    assert iniciar(client, sesion_artista, evento.id, clave).status_code == 200  # sigue valiendo


@pytest.mark.parametrize(
    ("metodo", "sufijo"),
    [
        ("post", "/transmision/credenciales"),
        ("post", "/transmision/iniciar"),
        ("post", "/transmision/finalizar"),
        ("get", "/transmision"),
    ],
)
def test_usuario_normal_y_admin_no_gestionan_la_emision(
    client, evento, comprador, sesion_admin, metodo, sufijo
):
    for sesion in (comprador, sesion_admin):
        respuesta = getattr(client, metodo)(
            ruta(evento.id, sufijo), headers=sesion, json={"stream_key": "x"}
        )
        assert respuesta.status_code == 403
        assert respuesta.get_json()["codigo"] == "ROL_NO_PERMITIDO"


@pytest.mark.parametrize(
    ("metodo", "sufijo"),
    [
        ("post", "/transmision/credenciales"),
        ("post", "/transmision/iniciar"),
        ("post", "/transmision/finalizar"),
        ("get", "/transmision"),
        ("post", "/reproduccion/entrar"),
        ("post", "/reproduccion/salir"),
    ],
)
def test_sin_token_es_401(client, evento, metodo, sufijo):
    assert getattr(client, metodo)(ruta(evento.id, sufijo)).status_code == 401


def test_evento_inexistente_es_404(client, sesion_artista, comprador):
    inexistente = "00000000-0000-4000-8000-000000000000"
    for respuesta in (
        client.post(ruta(inexistente, "/transmision/credenciales"), headers=sesion_artista),
        iniciar(client, sesion_artista, inexistente, "x"),
        finalizar(client, sesion_artista, inexistente),
        client.get(ruta(inexistente, "/transmision"), headers=sesion_artista),
        entrar(client, comprador, inexistente),
        salir(client, comprador, inexistente),
    ):
        assert respuesta.status_code == 404
        assert respuesta.get_json()["codigo"] == "EVENTO_NO_ENCONTRADO"


# --- Reproducción: acceso (C4, RN-10) ----------------------------------------------------------


def test_sin_compra_no_hay_vivo_ni_replay(app, client, artista, sesion_artista, comprador):
    """★ C4."""
    vivo = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    replay = nuevo_evento(app, artista, estado=EstadoEvento.FINALIZADO, replay_disponible=True)
    for evento_id in (vivo, replay):
        respuesta = entrar(client, comprador, evento_id)
        assert respuesta.status_code == 403
        assert respuesta.get_json()["codigo"] == "ACCESO_NO_AUTORIZADO"
    assert evento_en_bd(app, vivo).vistas_vivo == 0
    assert evento_en_bd(app, replay).vistas_replay == 0


def test_con_compra_aprobada_se_entrega_url_con_token(app, client, artista, comprador):
    """★ C4: vivo y replay."""
    vivo = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    replay = nuevo_evento(app, artista, estado=EstadoEvento.FINALIZADO, replay_disponible=True)
    for evento_id in (vivo, replay):
        comprar_aprobada(client, comprador, evento_id)

    en_vivo = entrar(client, comprador, vivo)
    assert en_vivo.status_code == 200
    cuerpo = en_vivo.get_json()
    assert set(cuerpo) == {"tipo", "url", "token", "expira_en"} and cuerpo["tipo"] == "VIVO"
    assert cuerpo["url"] == f"http://localhost:8888/live/{vivo}/index.m3u8?token={cuerpo['token']}"

    en_replay = entrar(client, comprador, replay).get_json()
    assert en_replay["tipo"] == "REPLAY"
    assert en_replay["url"] == (
        f"http://localhost:8888/replay/{replay}/index.m3u8?token={en_replay['token']}"
    )


@pytest.mark.parametrize(
    "estado_compra",
    [
        EstadoCompra.PENDIENTE,
        EstadoCompra.RECHAZADA,
        EstadoCompra.ANULADA,
        EstadoCompra.REEMBOLSADA,
    ],
)
def test_solo_la_compra_aprobada_da_acceso(app, client, artista, comprador, estado_compra):
    """★ C4: PENDIENTE y RECHAZADA (y las demás) no dan acceso."""
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    crear_compra_para(app, "ana@example.com", evento_id, estado_compra)
    respuesta = entrar(client, comprador, evento_id)
    assert respuesta.status_code == 403
    assert respuesta.get_json()["codigo"] == "ACCESO_NO_AUTORIZADO"


def crear_compra_para(app, email, evento_id, estado):
    with app.app_context():
        usuario = db.session.scalar(select(Usuario.id).where(Usuario.email == email))
        crear_compra(usuario, evento_id, estado=estado)


def test_la_compra_de_otro_evento_o_de_otro_usuario_no_da_acceso(
    app, client, artista, comprador, sesion_otro_artista
):
    comprado = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    otro_evento = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    comprar_aprobada(client, comprador, comprado)
    assert entrar(client, comprador, otro_evento).status_code == 403
    assert entrar(client, sesion_otro_artista, comprado).status_code == 403


def test_dueno_y_admin_acceden_sin_compra_pero_un_usuario_no(
    app, client, artista, sesion_artista, sesion_admin, comprador, sesion_otro_artista
):
    """★ RN-10."""
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    assert entrar(client, sesion_artista, evento_id).status_code == 200
    assert entrar(client, sesion_admin, evento_id).status_code == 200
    assert entrar(client, comprador, evento_id).status_code == 403  # USUARIO sin compra
    assert entrar(client, sesion_otro_artista, evento_id).status_code == 403  # otro ARTISTA


def test_un_artista_con_compra_aprobada_si_accede_al_evento_de_otro(
    app, client, artista, sesion_otro_artista
):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    comprar_aprobada(client, sesion_otro_artista, evento_id)
    assert entrar(client, sesion_otro_artista, evento_id).status_code == 200


@pytest.mark.parametrize("quien", ["comprador", "dueno", "admin", "sin_compra"])
def test_evento_suspendido_es_403_evento_suspendido(
    app, client, artista, sesion_artista, sesion_admin, comprador, quien
):
    """RN-10: se comprueba antes que el acceso."""
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO, suspendido_admin=True)
    if quien == "comprador":
        crear_compra_para(app, "ana@example.com", evento_id, EstadoCompra.APROBADA)
    sesion = {
        "comprador": comprador,
        "sin_compra": comprador,
        "dueno": sesion_artista,
        "admin": sesion_admin,
    }[quien]
    respuesta = entrar(client, sesion, evento_id)
    assert respuesta.status_code == 403
    assert respuesta.get_json()["codigo"] == "EVENTO_SUSPENDIDO"
    assert evento_en_bd(app, evento_id).vistas_vivo == 0


@pytest.mark.parametrize(
    "campos",
    [
        {"estado": EstadoEvento.PROGRAMADO},
        {"estado": EstadoEvento.BORRADOR},
        {"estado": EstadoEvento.CANCELADO},
        {"estado": EstadoEvento.FINALIZADO},  # sin replay_disponible
        {"estado": EstadoEvento.FINALIZADO, "replay_habilitado": False},
    ],
    ids=["programado", "borrador", "cancelado", "finalizado-sin-replay", "replay-deshabilitado"],
)
def test_sin_vivo_ni_replay_nadie_obtiene_url(
    app, client, artista, sesion_artista, sesion_admin, comprador, campos
):
    """★ RN-10: ni comprador, ni dueño, ni admin."""
    evento_id = nuevo_evento(app, artista, **campos)
    crear_compra_para(app, "ana@example.com", evento_id, EstadoCompra.APROBADA)
    for sesion in (comprador, sesion_artista, sesion_admin):
        respuesta = entrar(client, sesion, evento_id)
        assert respuesta.status_code == 403
        assert respuesta.get_json()["codigo"] == "ACCESO_NO_AUTORIZADO"


def test_usuario_suspendido_no_entra(app, client, artista, comprador):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    comprar_aprobada(client, comprador, evento_id)
    with app.app_context():
        db.session.scalar(
            select(Usuario).where(Usuario.email == "ana@example.com")
        ).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    respuesta = entrar(client, comprador, evento_id)
    assert respuesta.status_code == 403 and respuesta.get_json()["codigo"] == "CUENTA_SUSPENDIDA"


def test_replay_deshabilitado_de_punta_a_punta(
    app, client, artista, sesion_artista, comprador, sesion_admin
):
    """★ RN-10/RN-12: `replay_habilitado=false` → `replay_disponible=false` y nadie lo ve."""
    evento_id = nuevo_evento(app, artista, replay_habilitado=False)
    comprar_aprobada(client, comprador, evento_id)
    poner_en_vivo(client, sesion_artista, evento_id)
    assert entrar(client, comprador, evento_id).get_json()["tipo"] == "VIVO"
    finalizar(client, sesion_artista, evento_id)

    assert evento_en_bd(app, evento_id).replay_disponible is False
    for sesion in (comprador, sesion_artista, sesion_admin):
        assert entrar(client, sesion, evento_id).status_code == 403
    assert evento_en_bd(app, evento_id).vistas_replay == 0
    # Y como no hay replay, tampoco se vende (RN-07).
    with app.app_context():
        crear_usuario("nuevo@example.com")
    nuevo = entrar_sesion(client, "nuevo@example.com")
    compra = client.post(
        f"/api/v1/eventos/{evento_id}/compras", headers=nuevo, json={"metodo_pago": "PSE"}
    )
    assert compra.status_code == 409 and compra.get_json()["codigo"] == "EVENTO_NO_DISPONIBLE"


# --- Flujo completo ----------------------------------------------------------------------------


def test_flujo_completo_publicar_comprar_emitir_ver_y_replay(
    app, client, sesion_artista, comprador
):
    """★ C3 + C4 + RQ5 + RQ6: de punta a punta, sin intervención de un admin."""
    # 1. El artista crea y publica.
    borrador = client.post(
        "/api/v1/eventos", headers=sesion_artista, json=datos_evento(precio_cop=20_000)
    ).get_json()
    evento_id = borrador["id"]
    publicado = client.post(ruta(evento_id, "/publicar"), headers=sesion_artista)
    assert publicado.get_json()["estado"] == "PROGRAMADO"

    # 2. El espectador compra y el pago aprobado le da acceso.
    assert entrar(client, comprador, evento_id).status_code == 403  # aún sin acceso
    comprar_aprobada(client, comprador, evento_id)

    # 3. El artista obtiene credenciales e inicia.
    clave = pedir_clave(client, sesion_artista, evento_id)
    assert iniciar(client, sesion_artista, evento_id, clave).get_json()["estado"] == "EN_VIVO"

    # 4. El espectador entra al vivo.
    vivo = entrar(client, comprador, evento_id).get_json()
    assert vivo["tipo"] == "VIVO" and "/live/" in vivo["url"]
    estado = client.get(ruta(evento_id, "/transmision"), headers=sesion_artista).get_json()
    assert (estado["espectadores_actuales"], estado["pico_espectadores"]) == (1, 1)

    # 5. El artista finaliza: audiencia en cero, replay disponible.
    final = finalizar(client, sesion_artista, evento_id).get_json()
    assert (final["estado"], final["espectadores_actuales"], final["pico_espectadores"]) == (
        "FINALIZADO",
        0,
        1,
    )

    # 6. El espectador vuelve en modo REPLAY.
    replay = entrar(client, comprador, evento_id).get_json()
    assert replay["tipo"] == "REPLAY" and "/replay/" in replay["url"]
    fila = evento_en_bd(app, evento_id)
    assert (fila.vistas_vivo, fila.vistas_replay, fila.espectadores_actuales) == (1, 1, 0)
    assert fila.replay_disponible is True and fila.inicio_real and fila.fin_real

    # 7. Quien no compró sigue sin poder ver el replay.
    with app.app_context():
        crear_usuario("curioso@example.com")
    curioso = entrar_sesion(client, "curioso@example.com")
    assert entrar(client, curioso, evento_id).status_code == 403


def test_se_puede_comprar_un_evento_finalizado_con_replay_y_verlo(
    app, client, artista, sesion_artista, comprador
):
    """RN-07: FINALIZADO con `replay_disponible` está a la venta."""
    evento_id = nuevo_evento(app, artista)
    poner_en_vivo(client, sesion_artista, evento_id)
    finalizar(client, sesion_artista, evento_id)
    assert evento_en_bd(app, evento_id).replay_disponible is True

    assert entrar(client, comprador, evento_id).status_code == 403
    comprar_aprobada(client, comprador, evento_id)
    replay = entrar(client, comprador, evento_id)
    assert replay.status_code == 200 and replay.get_json()["tipo"] == "REPLAY"


# --- Token de reproducción ---------------------------------------------------------------------


def test_el_token_de_reproduccion_lleva_evento_usuario_y_tipo(
    app, client, artista, sesion_artista, comprador
):
    vivo = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    replay = nuevo_evento(app, artista, estado=EstadoEvento.FINALIZADO, replay_disponible=True)
    ana = str(usuario_id(app, "ana@example.com"))
    for evento_id, tipo in ((vivo, "VIVO"), (replay, "REPLAY")):
        comprar_aprobada(client, comprador, evento_id)
        token = entrar(client, comprador, evento_id).get_json()["token"]
        claims = jwt.decode(token, app.config["PLAYBACK_TOKEN_SECRET"], algorithms=["HS256"])
        assert (claims["evento_id"], claims["usuario_id"], claims["tipo"]) == (evento_id, ana, tipo)


def test_el_token_expira_en_10_minutos_y_expira_en_coincide(app, client, artista, sesion_artista):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    respuesta = entrar(client, sesion_artista, evento_id).get_json()
    claims = jwt.decode(
        respuesta["token"], app.config["PLAYBACK_TOKEN_SECRET"], algorithms=["HS256"]
    )
    assert claims["exp"] - claims["iat"] == 600
    assert datetime.fromisoformat(respuesta["expira_en"]) == datetime.fromtimestamp(
        claims["exp"], UTC
    )


def test_un_token_emitido_hace_mas_de_10_minutos_esta_vencido(app, client, artista, sesion_artista):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    fijar_ahora(ahora_utc() - timedelta(minutes=11))
    token = entrar(client, sesion_artista, evento_id).get_json()["token"]
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(token, app.config["PLAYBACK_TOKEN_SECRET"], algorithms=["HS256"])


def test_el_token_de_reproduccion_no_es_un_token_de_sesion(app, client, artista, sesion_artista):
    """Se firma con otro secreto: no abre la API ni se valida con el de sesión."""
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    token = entrar(client, sesion_artista, evento_id).get_json()["token"]
    assert client.get("/api/v1/usuarios/me", headers=encabezados(token)).status_code == 401
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, app.config["JWT_SECRET_KEY"], algorithms=["HS256"])


def test_ni_la_clave_ni_los_tokens_ni_el_hash_van_a_los_logs(
    app, client, evento, sesion_artista, comprador, caplog
):
    comprar_aprobada(client, comprador, evento.id)
    with caplog.at_level(logging.DEBUG):
        clave = pedir_clave(client, sesion_artista, evento.id)
        iniciar(client, sesion_artista, evento.id, "clave-mala-que-tambien-es-secreta")
        iniciar(client, sesion_artista, evento.id, clave)
        token = entrar(client, comprador, evento.id).get_json()["token"]
        finalizar(client, sesion_artista, evento.id)
        entrar(client, comprador, evento.id)
    sensibles = (
        clave,
        "clave-mala-que-tambien-es-secreta",
        hashlib.sha256(clave.encode()).hexdigest(),
        token,
        sesion_artista["Authorization"].split()[1],
        app.config["PLAYBACK_TOKEN_SECRET"],
    )
    for secreto in sensibles:
        assert secreto not in caplog.text


# --- Contadores de audiencia -------------------------------------------------------------------


def test_entrar_actualiza_los_tres_contadores_en_un_solo_update(
    app, client, artista, sesion_artista
):
    """El pico usa GREATEST y todo ocurre en SQL, sin leer-modificar-escribir en Python."""
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    sentencias = []

    def capturar(conexion, cursor, statement, parameters, context, executemany):
        sentencias.append(statement)

    with app.app_context():
        motor = db.engine
    sa_event.listen(motor, "before_cursor_execute", capturar)
    try:
        assert entrar(client, sesion_artista, evento_id).status_code == 200
    finally:
        sa_event.remove(motor, "before_cursor_execute", capturar)

    actualizaciones = [s for s in sentencias if s.lstrip().upper().startswith("UPDATE EVENTOS")]
    assert len(actualizaciones) == 1
    sql = actualizaciones[0].lower()
    assert "greatest(" in sql
    assert "espectadores_actuales + " in sql and "vistas_vivo + " in sql
    assert "pico_espectadores=greatest" in sql.replace(" ", "")


def test_pico_y_actuales_siguen_la_audiencia_y_salir_no_baja_de_cero(
    app, client, artista, sesion_artista
):
    """★ contadores."""
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)

    def contadores():
        fila = evento_en_bd(app, evento_id)
        return fila.espectadores_actuales, fila.pico_espectadores, fila.vistas_vivo

    for _ in range(5):
        entrar(client, sesion_artista, evento_id)
    assert contadores() == (5, 5, 5)
    for _ in range(3):
        assert salir(client, sesion_artista, evento_id).status_code == 204
    assert contadores() == (2, 5, 5)  # el pico no baja
    entrar(client, sesion_artista, evento_id)
    assert contadores() == (3, 5, 6)
    for _ in range(10):  # más salidas que entradas
        assert salir(client, sesion_artista, evento_id).status_code == 204
    assert contadores() == (0, 5, 6)


def test_el_pico_sube_cuando_se_supera(app, client, artista, sesion_artista):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    for _ in range(3):
        entrar(client, sesion_artista, evento_id)
    for _ in range(2):
        salir(client, sesion_artista, evento_id)
    for _ in range(4):
        entrar(client, sesion_artista, evento_id)
    fila = evento_en_bd(app, evento_id)
    assert (fila.espectadores_actuales, fila.pico_espectadores) == (5, 5)


def _en_paralelo(hilos: int, accion) -> list:
    resultados = []

    def correr():
        resultados.append(accion())

    trabajos = [threading.Thread(target=correr) for _ in range(hilos)]
    for trabajo in trabajos:
        trabajo.start()
    for trabajo in trabajos:
        trabajo.join(60)
    return resultados


def test_entradas_concurrentes_no_pierden_cuentas(app, client, artista, sesion_artista):
    """★ contadores atómicos: 12 entradas simultáneas → 12 exactas."""
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    codigos = _en_paralelo(
        12, lambda: entrar(app.test_client(), sesion_artista, evento_id).status_code
    )
    assert codigos == [200] * 12
    fila = evento_en_bd(app, evento_id)
    assert (fila.espectadores_actuales, fila.pico_espectadores, fila.vistas_vivo) == (12, 12, 12)


def test_salidas_concurrentes_nunca_dejan_el_contador_negativo(
    app, client, artista, sesion_artista
):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    for _ in range(4):
        entrar(client, sesion_artista, evento_id)
    codigos = _en_paralelo(
        12, lambda: salir(app.test_client(), sesion_artista, evento_id).status_code
    )
    assert codigos == [204] * 12  # un 500 por violar el CHECK >= 0 no puede ocurrir
    fila = evento_en_bd(app, evento_id)
    assert (fila.espectadores_actuales, fila.pico_espectadores) == (0, 4)


def test_entradas_y_salidas_mezcladas_en_paralelo_mantienen_el_balance(
    app, client, artista, sesion_artista
):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    for _ in range(6):
        entrar(client, sesion_artista, evento_id)
    cliente = app.test_client
    acciones = [lambda: entrar(cliente(), sesion_artista, evento_id)] * 6 + [
        lambda: salir(cliente(), sesion_artista, evento_id)
    ] * 6
    resultados = []

    def correr(accion):
        resultados.append(accion().status_code)

    hilos = [threading.Thread(target=correr, args=(a,)) for a in acciones]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(60)

    assert len(resultados) == 12 and set(resultados) <= {200, 204}
    fila = evento_en_bd(app, evento_id)
    assert fila.espectadores_actuales == 6  # 6 + 6 entradas - 6 salidas
    assert 6 <= fila.pico_espectadores <= 12 and fila.vistas_vivo == 12


def test_el_replay_solo_cuenta_vistas_de_replay(app, client, artista, sesion_artista):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.FINALIZADO, replay_disponible=True)
    for _ in range(3):
        entrar(client, sesion_artista, evento_id)
    fila = evento_en_bd(app, evento_id)
    assert (fila.vistas_replay, fila.vistas_vivo) == (3, 0)
    assert (fila.espectadores_actuales, fila.pico_espectadores) == (0, 0)
    assert salir(client, sesion_artista, evento_id).status_code == 204
    assert evento_en_bd(app, evento_id).espectadores_actuales == 0


def test_el_update_de_entrada_exige_el_estado_que_da_acceso(app, artista):
    """Si el evento cambió entre la lectura y el UPDATE (p. ej. se finalizó), no se cuenta."""
    from app.modules.eventos.service import EventoService

    finalizado = nuevo_evento(app, artista, estado=EstadoEvento.FINALIZADO)  # sin replay
    con_replay = nuevo_evento(app, artista, estado=EstadoEvento.FINALIZADO, replay_disponible=True)
    en_vivo = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    suspendido = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO, suspendido_admin=True)
    with app.app_context():
        servicio = EventoService()
        assert servicio.registrar_entrada(uuid.UUID(finalizado), vivo=True) is False
        assert servicio.registrar_entrada(uuid.UUID(finalizado), vivo=False) is False
        assert servicio.registrar_entrada(uuid.UUID(con_replay), vivo=True) is False
        assert servicio.registrar_entrada(uuid.UUID(en_vivo), vivo=False) is False
        assert servicio.registrar_entrada(uuid.UUID(suspendido), vivo=True) is False
        assert servicio.registrar_entrada(uuid.UUID(en_vivo), vivo=True) is True
    for evento_id in (finalizado, con_replay, suspendido):
        fila = evento_en_bd(app, evento_id)
        assert (fila.espectadores_actuales, fila.vistas_vivo, fila.vistas_replay) == (0, 0, 0)
    assert evento_en_bd(app, en_vivo).espectadores_actuales == 1


def test_salir_exige_derecho_de_acceso(
    app, client, artista, sesion_artista, comprador, sesion_otro_artista
):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    entrar(client, sesion_artista, evento_id)
    assert salir(client, comprador, evento_id).status_code == 403  # sin compra
    assert salir(client, sesion_otro_artista, evento_id).status_code == 403
    assert evento_en_bd(app, evento_id).espectadores_actuales == 1  # no lo tocaron
    comprar_aprobada(client, comprador, evento_id)
    assert salir(client, comprador, evento_id).status_code == 204


def test_salir_funciona_aunque_el_evento_ya_haya_terminado(
    app, client, artista, sesion_artista, comprador
):
    evento_id = nuevo_evento(app, artista)
    comprar_aprobada(client, comprador, evento_id)
    poner_en_vivo(client, sesion_artista, evento_id)
    entrar(client, comprador, evento_id)
    finalizar(client, sesion_artista, evento_id)
    assert salir(client, comprador, evento_id).status_code == 204
    assert evento_en_bd(app, evento_id).espectadores_actuales == 0


# --- Endpoint de desarrollo --------------------------------------------------------------------

DEV = "/api/v1/dev/eventos/{}/audiencia-simulada"


def test_audiencia_simulada_fija_actuales_y_sube_el_pico(app, client, artista, sesion_artista):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    primera = client.post(DEV.format(evento_id), headers=sesion_artista, json={"espectadores": 150})
    assert primera.status_code == 200
    assert (
        primera.get_json()["espectadores_actuales"],
        primera.get_json()["pico_espectadores"],
    ) == (
        150,
        150,
    )
    segunda = client.post(DEV.format(evento_id), headers=sesion_artista, json={"espectadores": 40})
    assert (
        segunda.get_json()["espectadores_actuales"],
        segunda.get_json()["pico_espectadores"],
    ) == (
        40,
        150,
    )


def test_audiencia_simulada_para_dueno_o_admin(
    app, client, artista, sesion_artista, sesion_admin, sesion_otro_artista, comprador
):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    cuerpo = {"espectadores": 5}
    assert client.post(DEV.format(evento_id), headers=sesion_admin, json=cuerpo).status_code == 200
    ajeno = client.post(DEV.format(evento_id), headers=sesion_otro_artista, json=cuerpo)
    assert ajeno.status_code == 403 and ajeno.get_json()["codigo"] == "NO_ES_DUENO"
    normal = client.post(DEV.format(evento_id), headers=comprador, json=cuerpo)
    assert normal.status_code == 403 and normal.get_json()["codigo"] == "ROL_NO_PERMITIDO"
    assert client.post(DEV.format(evento_id), json=cuerpo).status_code == 401


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"espectadores": -1},
        {"espectadores": 1.5},
        {"espectadores": "9"},
        {"espectadores": 5, "x": 1},
    ],
)
def test_audiencia_simulada_valida_el_cuerpo(app, client, artista, sesion_artista, cuerpo):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    assert (
        client.post(DEV.format(evento_id), headers=sesion_artista, json=cuerpo).status_code == 422
    )


def test_audiencia_simulada_es_404_con_enable_dev_endpoints_falso(
    app, client, artista, sesion_artista, monkeypatch
):
    """★ §8.6: 404 aunque el token sea válido y aunque no haya token."""
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.EN_VIVO)
    monkeypatch.setitem(app.config, "ENABLE_DEV_ENDPOINTS", False)
    cuerpo = {"espectadores": 10}
    for encabezado in (sesion_artista, {}):
        respuesta = client.post(DEV.format(evento_id), headers=encabezado, json=cuerpo)
        assert respuesta.status_code == 404
        assert respuesta.get_json()["codigo"] == "RECURSO_NO_ENCONTRADO"
    assert evento_en_bd(app, evento_id).espectadores_actuales == 0

    monkeypatch.setitem(app.config, "ENABLE_DEV_ENDPOINTS", True)
    assert (
        client.post(DEV.format(evento_id), headers=sesion_artista, json=cuerpo).status_code == 200
    )


def test_en_produccion_los_endpoints_de_desarrollo_estan_apagados_por_defecto(monkeypatch):
    from app import create_app

    for nombre in ("SECRET_KEY", "JWT_SECRET_KEY", "PLAYBACK_TOKEN_SECRET"):
        monkeypatch.setenv(nombre, f"valor-largo-y-unico-para-{nombre}")
    monkeypatch.setattr("app.load_dotenv", lambda *args, **kwargs: None)  # ignora el .env local
    monkeypatch.delenv("ENABLE_DEV_ENDPOINTS", raising=False)
    monkeypatch.delenv("ENFORCE_START_WINDOW", raising=False)
    produccion = create_app("production")
    assert produccion.config["ENABLE_DEV_ENDPOINTS"] is False
    assert produccion.config["ENFORCE_START_WINDOW"] is True


# --- Swagger -----------------------------------------------------------------------------------


def test_swagger_documenta_transmision_y_reproduccion(client):
    spec = client.get("/api/openapi.json").get_json()
    assert "Transmision" in {t["name"] for t in spec["tags"]}
    esperados = [
        (
            "/api/v1/eventos/{evento_id}/transmision/credenciales",
            "post",
            {"200", "401", "403", "404", "409"},
        ),
        (
            "/api/v1/eventos/{evento_id}/transmision/iniciar",
            "post",
            {"200", "401", "403", "404", "409", "422"},
        ),
        (
            "/api/v1/eventos/{evento_id}/transmision/finalizar",
            "post",
            {"200", "401", "403", "404", "409"},
        ),
        ("/api/v1/eventos/{evento_id}/transmision", "get", {"200", "401", "403", "404"}),
        ("/api/v1/eventos/{evento_id}/reproduccion/entrar", "post", {"200", "401", "403", "404"}),
        ("/api/v1/eventos/{evento_id}/reproduccion/salir", "post", {"204", "401", "403", "404"}),
        (
            "/api/v1/dev/eventos/{evento_id}/audiencia-simulada",
            "post",
            {"200", "401", "403", "404", "422"},
        ),
    ]
    for ruta_spec, metodo, codigos in esperados:
        operacion = spec["paths"][ruta_spec][metodo]
        assert operacion["tags"] == ["Transmision"] and operacion["summary"], ruta_spec
        assert operacion["security"], ruta_spec
        assert codigos <= set(operacion["responses"]), (ruta_spec, set(operacion["responses"]))
    esquemas = spec["components"]["schemas"]
    assert "example" in esquemas["IniciarTransmision"]["properties"]["stream_key"]
    assert "example" in esquemas["Credenciales"]["properties"]["rtmp_url"]
    assert "example" in esquemas["Reproduccion"]["properties"]["url"]
    assert "example" in esquemas["AudienciaSimulada"]["properties"]["espectadores"]
