"""Eventos (Fase 2): RN-02, RN-04, RN-05 (BORRADOR→PROGRAMADO/CANCELADO) y RN-21."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.common.clock import fijar_ahora
from app.common.enums import CategoriaEvento, EstadoEvento, EstadoUsuario, Rol
from app.extensions import db
from app.modules.eventos.models import Evento
from app.modules.eventos.service import EventoService
from app.modules.usuarios.models import Usuario
from tests.factories import (
    PASSWORD,
    crear_evento,
    crear_usuario,
    datos_evento,
    encabezados,
    iniciar_sesion,
)

EVENTOS = "/api/v1/eventos"
MIS_EVENTOS = "/api/v1/artistas/me/eventos"
AHORA = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


def entrar(client, email):
    return encabezados(iniciar_sesion(client, email)["access_token"])


@pytest.fixture
def artista(app):
    with app.app_context():
        # Solo el id: el objeto ORM quedaría desvinculado al cerrar el contexto.
        return SimpleNamespace(id=crear_usuario("luz@example.com", Rol.ARTISTA, "Luz Band").id)


@pytest.fixture
def sesion_artista(client, artista):
    return entrar(client, "luz@example.com")


@pytest.fixture
def otra_sesion(client, app):
    """Un segundo artista, ajeno a los eventos de `artista`."""
    with app.app_context():
        crear_usuario("otro@example.com", Rol.ARTISTA, "Otro Grupo")
    return entrar(client, "otro@example.com")


@pytest.fixture
def sesion_usuario(client, app):
    with app.app_context():
        crear_usuario("ana@example.com", Rol.USUARIO)
    return entrar(client, "ana@example.com")


def crear_borrador(client, sesion, **cambios):
    respuesta = client.post(EVENTOS, headers=sesion, json=datos_evento(**cambios))
    assert respuesta.status_code == 201, respuesta.get_json()
    return respuesta.get_json()


def ids_del_catalogo(client, **params):
    respuesta = client.get(EVENTOS, query_string=params)
    assert respuesta.status_code == 200, respuesta.get_json()
    return [e["id"] for e in respuesta.get_json()["items"]]


# --- C1: alta autoservicio -------------------------------------------------------------------


def test_artista_recien_registrado_crea_y_publica_sin_admin(client, app):
    """★ C1/RQ1: todo el flujo ocurre sin que exista ningún ADMIN."""
    registro = client.post(
        "/api/v1/auth/registro",
        json={
            "nombre": "Luz",
            "apellido": "Mora",
            "email": "nueva@example.com",
            "password": PASSWORD,
            "rol": "ARTISTA",
            "nombre_artistico": "Nueva Banda",
            "acepta_tratamiento_datos": True,
        },
    )
    assert registro.status_code == 201
    sesion = entrar(client, "nueva@example.com")

    borrador = crear_borrador(client, sesion)
    assert borrador["estado"] == "BORRADOR"
    publicado = client.post(f"{EVENTOS}/{borrador['id']}/publicar", headers=sesion)

    assert publicado.status_code == 200
    assert publicado.get_json()["estado"] == "PROGRAMADO"
    with app.app_context():
        assert db.session.scalar(select(func.count()).where(Usuario.rol == Rol.ADMIN)) == 0
    assert borrador["id"] in ids_del_catalogo(client)


# --- C2: sin cargos al artista ---------------------------------------------------------------


def test_crear_y_publicar_no_genera_cargos(client, app, sesion_artista):
    """★ C2/RN-04: no hay tabla ni endpoint de cobros, y el evento no registra ningún cargo."""
    borrador = crear_borrador(client, sesion_artista)
    publicado = client.post(f"{EVENTOS}/{borrador['id']}/publicar", headers=sesion_artista)
    assert publicado.status_code == 200

    cuerpo = publicado.get_json()
    assert cuerpo["precio_cop"] == 20_000  # el precio es el del espectador, sin recargos
    prohibidos = ("cargo", "cuota", "suscripcion", "membresia", "comision", "fee")
    assert not [campo for campo in cuerpo if any(p in campo for p in prohibidos)]

    spec = client.get("/api/openapi.json").get_json()
    assert not [ruta for ruta in spec["paths"] if any(p in ruta for p in prohibidos)]
    with app.app_context():
        tablas = {t.name for t in db.metadata.sorted_tables}
    # Solo las 6 entidades del modelo: ninguna de cobros fijos ni suscripciones.
    assert tablas <= {"usuarios", "eventos", "compras", "retiros", "favoritos", "seguidores"}


# --- Creación y validación (RN-04) ------------------------------------------------------------


def test_crear_devuelve_borrador_del_artista_con_valores_por_defecto(
    client, artista, sesion_artista
):
    cuerpo = crear_borrador(client, sesion_artista)
    assert cuerpo["estado"] == "BORRADOR"
    assert cuerpo["categoria"] == "OTRO"
    assert cuerpo["duracion_estimada_min"] == 60
    assert cuerpo["replay_habilitado"] is True and cuerpo["replay_disponible"] is False
    assert cuerpo["youtube_url"] == "https://youtu.be/AbC123_deFg"
    assert cuerpo["artista"]["nombre_artistico"] == "Luz Band"
    assert "stream_key_hash" not in cuerpo


@pytest.mark.parametrize("precio", [0, -1, 4_999, 500_001])
def test_precio_fuera_de_rango_es_422(client, sesion_artista, precio):
    respuesta = client.post(EVENTOS, headers=sesion_artista, json=datos_evento(precio_cop=precio))
    assert respuesta.status_code == 422
    assert respuesta.get_json()["codigo"] == "MONTO_FUERA_DE_RANGO"


@pytest.mark.parametrize("precio", [5_000, 500_000])
def test_precio_en_los_limites_es_valido(client, sesion_artista, precio):
    assert (
        client.post(
            EVENTOS, headers=sesion_artista, json=datos_evento(precio_cop=precio)
        ).status_code
        == 201
    )


@pytest.mark.parametrize("precio", [20_000.5, "20000", True, None])
def test_precio_debe_ser_entero(client, sesion_artista, precio):
    respuesta = client.post(EVENTOS, headers=sesion_artista, json=datos_evento(precio_cop=precio))
    assert respuesta.status_code == 422
    assert "precio_cop" in respuesta.get_json()["detalles"]


@pytest.mark.parametrize(
    "cambio",
    [
        {"titulo": ""},
        {"titulo": "x" * 151},
        {"duracion_estimada_min": 14},
        {"duracion_estimada_min": 241},
        {"categoria": "DUBSTEP"},
        {"portada_url": "no-es-una-url"},
        {"portada_url": "javascript:alert(1)"},
        {"youtube_url": "https://example.com/video?v=AbC123_deFg"},
        {"youtube_url": "https://youtube.com/watch?v=corto"},
        {"fecha_inicio": "2030-01-01T20:00:00"},  # sin zona horaria
        {"fecha_inicio": "mañana"},
        {"artista_id": "00000000-0000-0000-0000-000000000000"},
        {"estado": "EN_VIVO"},
    ],
)
def test_creacion_rechaza_datos_invalidos(client, sesion_artista, cambio):
    respuesta = client.post(EVENTOS, headers=sesion_artista, json=datos_evento(**cambio))
    assert respuesta.status_code == 422, respuesta.get_json()
    assert respuesta.get_json()["codigo"] == "VALIDACION"


def test_creacion_exige_titulo_precio_y_fecha(client, sesion_artista):
    respuesta = client.post(EVENTOS, headers=sesion_artista, json={})
    assert set(respuesta.get_json()["detalles"]) == {
        "titulo",
        "precio_cop",
        "fecha_inicio",
        "youtube_url",
    }


def test_el_dueno_es_siempre_el_usuario_autenticado(client, app, artista, sesion_artista):
    crear_borrador(client, sesion_artista)
    with app.app_context():
        assert db.session.scalar(select(Evento)).artista_id == artista.id


def test_ddl_rechaza_precio_no_positivo_y_duracion_fuera_de_rango(app, artista):
    with app.app_context():
        for invalido in ({"precio_cop": 0}, {"duracion_estimada_min": 10}):
            with pytest.raises(IntegrityError):
                crear_evento(artista, **invalido)
            db.session.rollback()


# --- Autenticación y roles --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("post", EVENTOS),
        ("patch", f"{EVENTOS}/{'0' * 8}-0000-0000-0000-{'0' * 12}"),
        ("delete", f"{EVENTOS}/{'0' * 8}-0000-0000-0000-{'0' * 12}"),
        ("post", f"{EVENTOS}/{'0' * 8}-0000-0000-0000-{'0' * 12}/publicar"),
        ("post", f"{EVENTOS}/{'0' * 8}-0000-0000-0000-{'0' * 12}/cancelar"),
        ("get", MIS_EVENTOS),
    ],
)
def test_escrituras_y_panel_sin_token_son_401(client, metodo, ruta):
    assert getattr(client, metodo)(ruta).status_code == 401


def test_usuario_normal_no_puede_crear_eventos(client, sesion_usuario):
    respuesta = client.post(EVENTOS, headers=sesion_usuario, json=datos_evento())
    assert respuesta.status_code == 403
    assert respuesta.get_json()["codigo"] == "ROL_NO_PERMITIDO"
    assert client.get(MIS_EVENTOS, headers=sesion_usuario).status_code == 403


def test_artista_suspendido_no_puede_crear(client, app, sesion_artista):
    with app.app_context():
        db.session.scalar(select(Usuario)).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    respuesta = client.post(EVENTOS, headers=sesion_artista, json=datos_evento())
    assert respuesta.status_code == 403 and respuesta.get_json()["codigo"] == "CUENTA_SUSPENDIDA"


# --- Propiedad (anti-IDOR) --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "sufijo", "cuerpo"),
    [
        ("patch", "", {"titulo": "Robado"}),
        ("post", "/publicar", None),
        ("post", "/cancelar", None),
        ("delete", "", None),
    ],
)
def test_no_dueno_recibe_403(client, sesion_artista, otra_sesion, metodo, sufijo, cuerpo):
    evento = crear_borrador(client, sesion_artista)
    respuesta = getattr(client, metodo)(
        f"{EVENTOS}/{evento['id']}{sufijo}", headers=otra_sesion, json=cuerpo
    )
    assert respuesta.status_code == 403
    assert respuesta.get_json()["codigo"] == "NO_ES_DUENO"
    # El evento no cambió.
    asegurado = client.get(f"{EVENTOS}/{evento['id']}", headers=sesion_artista).get_json()
    assert asegurado["titulo"] == "Mi primer concierto" and asegurado["estado"] == "BORRADOR"


def test_evento_inexistente_es_404(client, sesion_artista):
    ruta = f"{EVENTOS}/00000000-0000-4000-8000-000000000000"
    for respuesta in (
        client.get(ruta),
        client.patch(ruta, headers=sesion_artista, json={"titulo": "x"}),
        client.post(f"{ruta}/publicar", headers=sesion_artista),
        client.post(f"{ruta}/cancelar", headers=sesion_artista),
        client.delete(ruta, headers=sesion_artista),
    ):
        assert respuesta.status_code == 404
        assert respuesta.get_json()["codigo"] == "EVENTO_NO_ENCONTRADO"


# --- Publicar (RN-04, RN-05, RN-02) -----------------------------------------------------------


def test_publicar_exige_fecha_al_menos_30_min_adelante(client, sesion_artista):
    """RN-04: el límite es ≥ ahora + 30 min."""
    fijar_ahora(AHORA)
    casos = [
        (AHORA + timedelta(minutes=29, seconds=59), 422),
        (AHORA + timedelta(minutes=30), 200),
    ]
    for fecha, esperado in casos:
        evento = crear_borrador(client, sesion_artista, fecha_inicio=fecha.isoformat())
        respuesta = client.post(f"{EVENTOS}/{evento['id']}/publicar", headers=sesion_artista)
        assert respuesta.status_code == esperado, fecha
        if esperado == 422:
            assert respuesta.get_json()["codigo"] == "FECHA_INICIO_INVALIDA"
            estado = client.get(f"{EVENTOS}/{evento['id']}", headers=sesion_artista).get_json()
            assert estado["estado"] == "BORRADOR"


def test_publicar_con_fecha_pasada_es_422(client, sesion_artista):
    evento = crear_borrador(client, sesion_artista, fecha_inicio="2020-01-01T00:00:00+00:00")
    respuesta = client.post(f"{EVENTOS}/{evento['id']}/publicar", headers=sesion_artista)
    assert respuesta.status_code == 422


def test_publicar_dos_veces_es_409(client, sesion_artista):
    evento = crear_borrador(client, sesion_artista)
    ruta = f"{EVENTOS}/{evento['id']}/publicar"
    assert client.post(ruta, headers=sesion_artista).status_code == 200
    segunda = client.post(ruta, headers=sesion_artista)
    assert segunda.status_code == 409 and segunda.get_json()["codigo"] == "TRANSICION_INVALIDA"


@pytest.mark.parametrize(
    "estado", [EstadoEvento.EN_VIVO, EstadoEvento.FINALIZADO, EstadoEvento.CANCELADO]
)
def test_no_se_publica_desde_otros_estados(client, app, artista, sesion_artista, estado):
    with app.app_context():
        evento = crear_evento(artista, estado=estado)
        evento_id = evento.id
    respuesta = client.post(f"{EVENTOS}/{evento_id}/publicar", headers=sesion_artista)
    assert respuesta.status_code == 409


def test_artista_nuevo_no_publica_un_cuarto_evento_programado(client, sesion_artista):
    """RN-02: sin eventos FINALIZADO, máximo 3 PROGRAMADO a la vez."""
    for _ in range(3):
        evento = crear_borrador(client, sesion_artista)
        assert (
            client.post(f"{EVENTOS}/{evento['id']}/publicar", headers=sesion_artista).status_code
            == 200
        )
    cuarto = crear_borrador(client, sesion_artista)
    respuesta = client.post(f"{EVENTOS}/{cuarto['id']}/publicar", headers=sesion_artista)

    assert respuesta.status_code == 422
    assert respuesta.get_json()["codigo"] == "LIMITE_EVENTOS_PROGRAMADOS"
    asegurado = client.get(f"{EVENTOS}/{cuarto['id']}", headers=sesion_artista).get_json()
    assert asegurado["estado"] == "BORRADOR"


def test_cancelar_un_programado_libera_cupo_del_tope(client, sesion_artista):
    programados = []
    for _ in range(3):
        evento = crear_borrador(client, sesion_artista)
        client.post(f"{EVENTOS}/{evento['id']}/publicar", headers=sesion_artista)
        programados.append(evento["id"])
    cuarto = crear_borrador(client, sesion_artista)
    ruta = f"{EVENTOS}/{cuarto['id']}/publicar"
    assert client.post(ruta, headers=sesion_artista).status_code == 422

    assert (
        client.post(f"{EVENTOS}/{programados[0]}/cancelar", headers=sesion_artista).status_code
        == 200
    )
    assert client.post(ruta, headers=sesion_artista).status_code == 200


def test_el_tope_no_aplica_a_artistas_con_un_evento_finalizado(
    client, app, artista, sesion_artista
):
    with app.app_context():
        crear_evento(artista, estado=EstadoEvento.FINALIZADO)
    for _ in range(4):
        evento = crear_borrador(client, sesion_artista)
        assert (
            client.post(f"{EVENTOS}/{evento['id']}/publicar", headers=sesion_artista).status_code
            == 200
        )


def test_el_tope_es_por_artista(client, sesion_artista, otra_sesion):
    for _ in range(3):
        evento = crear_borrador(client, sesion_artista)
        client.post(f"{EVENTOS}/{evento['id']}/publicar", headers=sesion_artista)
    ajeno = crear_borrador(client, otra_sesion)
    assert client.post(f"{EVENTOS}/{ajeno['id']}/publicar", headers=otra_sesion).status_code == 200


# --- Edición (RN-04) --------------------------------------------------------------------------


def test_en_borrador_se_edita_todo(client, sesion_artista):
    evento = crear_borrador(client, sesion_artista)
    cambios = {
        "titulo": "Nuevo título",
        "descripcion": "Nueva descripción",
        "categoria": "SALSA_TROPICAL",
        "portada_url": "https://cdn.example.com/portada.jpg",
        "youtube_url": "https://www.youtube.com/watch?v=AbC123_deFg",
        "precio_cop": 35_000,
        "fecha_inicio": "2030-01-01T20:00:00+00:00",
        "duracion_estimada_min": 120,
        "replay_habilitado": False,
    }
    respuesta = client.patch(f"{EVENTOS}/{evento['id']}", headers=sesion_artista, json=cambios)
    assert respuesta.status_code == 200, respuesta.get_json()
    cuerpo = respuesta.get_json()
    assert {k: cuerpo[k] for k in ("titulo", "categoria", "precio_cop", "replay_habilitado")} == {
        "titulo": "Nuevo título",
        "categoria": "SALSA_TROPICAL",
        "precio_cop": 35_000,
        "replay_habilitado": False,
    }
    assert cuerpo["fecha_inicio"].startswith("2030-01-01T20:00:00")
    assert cuerpo["youtube_url"] == "https://www.youtube.com/watch?v=AbC123_deFg"


def test_edicion_valida_precio_y_campos_desconocidos(client, sesion_artista):
    evento = crear_borrador(client, sesion_artista)
    ruta = f"{EVENTOS}/{evento['id']}"
    for cuerpo in ({"precio_cop": 100}, {"precio_cop": 9_000_000}):
        respuesta = client.patch(ruta, headers=sesion_artista, json=cuerpo)
        assert respuesta.status_code == 422
        assert respuesta.get_json()["codigo"] == "MONTO_FUERA_DE_RANGO"
    for cuerpo in ({"estado": "EN_VIVO"}, {"artista_id": "x"}, {"stream_key_hash": "x"}):
        assert client.patch(ruta, headers=sesion_artista, json=cuerpo).status_code == 422


def test_en_programado_se_editan_los_campos_libres_y_tambien_precio_sin_compras(
    client, sesion_artista
):
    evento = crear_borrador(client, sesion_artista)
    client.post(f"{EVENTOS}/{evento['id']}/publicar", headers=sesion_artista)
    cambios = {"titulo": "Otro", "descripcion": None, "categoria": "ROCK", "precio_cop": 25_000}
    respuesta = client.patch(f"{EVENTOS}/{evento['id']}", headers=sesion_artista, json=cambios)
    assert respuesta.status_code == 200
    assert respuesta.get_json()["precio_cop"] == 25_000


def test_en_programado_con_compras_aprobadas_no_se_edita_precio_fecha_ni_replay(
    client, sesion_artista, monkeypatch
):
    """RN-04. La consulta de compras es un stub hasta la Fase 3; aquí se simula `True`."""
    evento = crear_borrador(client, sesion_artista)
    client.post(f"{EVENTOS}/{evento['id']}/publicar", headers=sesion_artista)
    monkeypatch.setattr(
        "app.modules.eventos.repository.EventoRepository.tiene_compras_aprobadas",
        lambda self, evento_id: True,
    )
    ruta = f"{EVENTOS}/{evento['id']}"
    for restringido in (
        {"precio_cop": 30_000},
        {"fecha_inicio": "2030-01-01T20:00:00+00:00"},
        {"replay_habilitado": False},
        {"duracion_estimada_min": 90},
    ):
        respuesta = client.patch(ruta, headers=sesion_artista, json=restringido)
        assert respuesta.status_code == 409, restringido
        assert respuesta.get_json()["codigo"] == "EVENTO_NO_EDITABLE"
        assert set(respuesta.get_json()["detalles"]) == set(restringido)
    libre = client.patch(ruta, headers=sesion_artista, json={"titulo": "Sigue editable"})
    assert libre.status_code == 200 and libre.get_json()["titulo"] == "Sigue editable"
    assert client.get(ruta).get_json()["precio_cop"] == 20_000


def test_en_programado_la_nueva_fecha_tambien_exige_30_min(client, sesion_artista):
    evento = crear_borrador(client, sesion_artista)
    client.post(f"{EVENTOS}/{evento['id']}/publicar", headers=sesion_artista)
    respuesta = client.patch(
        f"{EVENTOS}/{evento['id']}",
        headers=sesion_artista,
        json={"fecha_inicio": "2020-01-01T00:00:00+00:00"},
    )
    assert respuesta.status_code == 422
    assert respuesta.get_json()["codigo"] == "FECHA_INICIO_INVALIDA"


@pytest.mark.parametrize(
    "estado", [EstadoEvento.EN_VIVO, EstadoEvento.FINALIZADO, EstadoEvento.CANCELADO]
)
def test_en_vivo_finalizado_y_cancelado_no_se_editan(client, app, artista, sesion_artista, estado):
    with app.app_context():
        evento_id = crear_evento(artista, estado=estado).id
    respuesta = client.patch(
        f"{EVENTOS}/{evento_id}", headers=sesion_artista, json={"titulo": "Cambio"}
    )
    assert respuesta.status_code == 409
    assert respuesta.get_json()["codigo"] == "EVENTO_NO_EDITABLE"


# --- Cancelar (RN-05, RN-13) y eliminar -------------------------------------------------------


@pytest.mark.parametrize("publicar", [False, True], ids=["desde-borrador", "desde-programado"])
def test_cancelar_borrador_o_programado(client, sesion_artista, publicar):
    evento = crear_borrador(client, sesion_artista)
    if publicar:
        client.post(f"{EVENTOS}/{evento['id']}/publicar", headers=sesion_artista)
    respuesta = client.post(f"{EVENTOS}/{evento['id']}/cancelar", headers=sesion_artista)
    assert respuesta.status_code == 200
    assert respuesta.get_json()["estado"] == "CANCELADO"
    assert evento["id"] not in ids_del_catalogo(client)


@pytest.mark.parametrize(
    "estado", [EstadoEvento.EN_VIVO, EstadoEvento.FINALIZADO, EstadoEvento.CANCELADO]
)
def test_no_se_cancela_en_vivo_finalizado_ni_cancelado(
    client, app, artista, sesion_artista, estado
):
    with app.app_context():
        evento_id = crear_evento(artista, estado=estado).id
    respuesta = client.post(f"{EVENTOS}/{evento_id}/cancelar", headers=sesion_artista)
    assert respuesta.status_code == 409
    assert respuesta.get_json()["codigo"] == "TRANSICION_INVALIDA"


def test_cancelar_invoca_el_punto_de_extension_de_reembolsos(client, sesion_artista, monkeypatch):
    """RN-13: la Fase 3 enchufa aquí `compras.reembolsar_por_evento()`, en la misma transacción."""
    llamados = []
    monkeypatch.setattr(
        EventoService, "_reembolsar_compras", lambda self, evento: llamados.append(evento.id)
    )
    evento = crear_borrador(client, sesion_artista)
    client.post(f"{EVENTOS}/{evento['id']}/publicar", headers=sesion_artista)
    client.post(f"{EVENTOS}/{evento['id']}/cancelar", headers=sesion_artista)
    assert [str(i) for i in llamados] == [evento["id"]]


def test_si_el_reembolso_falla_la_cancelacion_se_revierte(client, app, sesion_artista, monkeypatch):
    def falla(self, evento):
        raise RuntimeError("fallo del reembolso")

    monkeypatch.setattr(EventoService, "_reembolsar_compras", falla)
    evento = crear_borrador(client, sesion_artista)
    assert (
        client.post(f"{EVENTOS}/{evento['id']}/cancelar", headers=sesion_artista).status_code == 500
    )
    monkeypatch.undo()
    assert client.get(f"{EVENTOS}/{evento['id']}", headers=sesion_artista).get_json()["estado"] == (
        "BORRADOR"
    )


def test_eliminar_borrador(client, sesion_artista):
    evento = crear_borrador(client, sesion_artista)
    assert client.delete(f"{EVENTOS}/{evento['id']}", headers=sesion_artista).status_code == 204
    assert client.get(f"{EVENTOS}/{evento['id']}", headers=sesion_artista).status_code == 404


@pytest.mark.parametrize("estado", [e for e in EstadoEvento if e != EstadoEvento.BORRADOR])
def test_solo_se_eliminan_borradores(client, app, artista, sesion_artista, estado):
    with app.app_context():
        evento_id = crear_evento(artista, estado=estado).id
    respuesta = client.delete(f"{EVENTOS}/{evento_id}", headers=sesion_artista)
    assert respuesta.status_code == 409 and respuesta.get_json()["codigo"] == "TRANSICION_INVALIDA"
    with app.app_context():
        assert db.session.get(Evento, evento_id) is not None


# --- Visibilidad (RN-21) ----------------------------------------------------------------------


def test_borrador_solo_lo_ve_su_dueno(client, sesion_artista, otra_sesion, sesion_usuario):
    evento = crear_borrador(client, sesion_artista)
    ruta = f"{EVENTOS}/{evento['id']}"
    assert evento["id"] not in ids_del_catalogo(client)
    assert client.get(ruta, headers=sesion_artista).status_code == 200
    for visitante in ({}, otra_sesion, sesion_usuario):
        respuesta = client.get(ruta, headers=visitante)
        assert respuesta.status_code == 404
        assert respuesta.get_json()["codigo"] == "EVENTO_NO_ENCONTRADO"


def test_cancelado_no_aparece_en_el_catalogo_ni_para_terceros(
    client, app, artista, sesion_artista, otra_sesion
):
    with app.app_context():
        evento_id = str(crear_evento(artista, estado=EstadoEvento.CANCELADO).id)
    ruta = f"{EVENTOS}/{evento_id}"
    assert evento_id not in ids_del_catalogo(client)
    assert client.get(ruta).status_code == 404
    assert client.get(ruta, headers=otra_sesion).status_code == 404
    assert client.get(ruta, headers=sesion_artista).status_code == 200  # su dueño sí


def test_evento_suspendido_por_admin_sale_del_catalogo(client, app, artista, sesion_artista):
    with app.app_context():
        evento_id = str(crear_evento(artista, suspendido_admin=True).id)
    assert evento_id not in ids_del_catalogo(client)
    assert client.get(f"{EVENTOS}/{evento_id}").status_code == 404
    assert client.get(f"{EVENTOS}/{evento_id}", headers=sesion_artista).status_code == 200


def test_eventos_de_un_artista_suspendido_salen_del_catalogo(client, app, artista):
    with app.app_context():
        evento_id = str(crear_evento(artista).id)
    assert evento_id in ids_del_catalogo(client)
    with app.app_context():
        db.session.scalar(select(Usuario)).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    assert evento_id not in ids_del_catalogo(client)
    assert client.get(f"{EVENTOS}/{evento_id}").status_code == 404


def test_catalogo_incluye_programado_en_vivo_y_finalizado_con_replay(client, app, artista):
    with app.app_context():
        visibles = {
            str(crear_evento(artista, estado=EstadoEvento.PROGRAMADO).id),
            str(crear_evento(artista, estado=EstadoEvento.EN_VIVO).id),
            str(crear_evento(artista, estado=EstadoEvento.FINALIZADO, replay_disponible=True).id),
        }
        ocultos = {
            str(crear_evento(artista, estado=EstadoEvento.BORRADOR).id),
            str(crear_evento(artista, estado=EstadoEvento.CANCELADO).id),
            str(crear_evento(artista, estado=EstadoEvento.FINALIZADO).id),
            str(crear_evento(artista, estado=EstadoEvento.FINALIZADO, replay_habilitado=False).id),
        }
    en_catalogo = set(ids_del_catalogo(client))
    assert en_catalogo == visibles
    assert not en_catalogo & ocultos
    for evento_id in visibles:
        assert client.get(f"{EVENTOS}/{evento_id}").status_code == 200
    for evento_id in ocultos:
        assert client.get(f"{EVENTOS}/{evento_id}").status_code == 404


# --- Catálogo: filtros, orden y paginación ----------------------------------------------------


@pytest.fixture
def catalogo(app, artista):
    """Tres eventos de `artista` y uno de otro artista, con fechas y categorías distintas."""
    with app.app_context():
        otro = crear_usuario("otro@example.com", Rol.ARTISTA, "Orquesta Sol")
        base = datetime(2030, 1, 10, 20, 0, tzinfo=UTC)
        eventos = {
            "rock": crear_evento(
                artista,
                titulo="Noche de Rock",
                categoria=CategoriaEvento.ROCK,
                fecha_inicio=base + timedelta(days=2),
            ),
            "salsa": crear_evento(
                artista,
                titulo="Salsa 100% brava_",
                categoria=CategoriaEvento.SALSA_TROPICAL,
                fecha_inicio=base,
                estado=EstadoEvento.EN_VIVO,
            ),
            "jazz": crear_evento(
                artista,
                titulo="Jazz en casa",
                categoria=CategoriaEvento.JAZZ_BLUES,
                fecha_inicio=base + timedelta(days=1),
            ),
            "sol": crear_evento(otro, titulo="Gran baile", fecha_inicio=base + timedelta(days=3)),
        }
        return {nombre: str(e.id) for nombre, e in eventos.items()} | {
            "artista": str(artista.id),
            "otro": str(otro.id),
        }


def test_catalogo_ordena_por_fecha_inicio(client, catalogo):
    assert ids_del_catalogo(client) == [
        catalogo["salsa"],
        catalogo["jazz"],
        catalogo["rock"],
        catalogo["sol"],
    ]


def test_catalogo_es_publico_y_no_filtra_datos_sensibles(client, catalogo):
    item = client.get(EVENTOS).get_json()["items"][0]
    assert set(item["artista"]) == {"id", "nombre_artistico", "foto_url"}
    assert "stream_key_hash" not in item and "email" not in str(item)


def test_catalogo_filtra_por_q_en_titulo_y_nombre_artistico(client, catalogo):
    assert ids_del_catalogo(client, q="ROCK") == [catalogo["rock"]]
    assert ids_del_catalogo(client, q="orquesta") == [catalogo["sol"]]  # nombre artístico
    assert set(ids_del_catalogo(client, q="luz band")) == {
        catalogo["salsa"],
        catalogo["jazz"],
        catalogo["rock"],
    }


def test_q_trata_porcentaje_y_guion_bajo_como_texto(client, catalogo):
    assert ids_del_catalogo(client, q="100%") == [catalogo["salsa"]]
    assert ids_del_catalogo(client, q="brava_") == [catalogo["salsa"]]
    assert ids_del_catalogo(client, q="%") == [catalogo["salsa"]]
    assert ids_del_catalogo(client, q="_") == [catalogo["salsa"]]


def test_catalogo_filtra_por_categoria_estado_y_artista(client, catalogo):
    assert ids_del_catalogo(client, categoria="JAZZ_BLUES") == [catalogo["jazz"]]
    assert ids_del_catalogo(client, estado="EN_VIVO") == [catalogo["salsa"]]
    assert set(ids_del_catalogo(client, artista_id=catalogo["otro"])) == {catalogo["sol"]}
    assert ids_del_catalogo(client, estado="BORRADOR") == []  # nunca visible (RN-21)


def test_catalogo_filtra_por_rango_de_fechas(client, catalogo):
    assert ids_del_catalogo(
        client, desde="2030-01-11T00:00:00+00:00", hasta="2030-01-12T20:00:00+00:00"
    ) == [catalogo["jazz"], catalogo["rock"]]
    assert ids_del_catalogo(client, desde="2030-01-13T00:00:00+00:00") == [catalogo["sol"]]
    assert ids_del_catalogo(client, hasta="2030-01-10T20:00:00+00:00") == [catalogo["salsa"]]


def test_catalogo_combina_filtros(client, catalogo):
    assert ids_del_catalogo(client, artista_id=catalogo["artista"], categoria="ROCK") == [
        catalogo["rock"]
    ]
    assert ids_del_catalogo(client, artista_id=catalogo["otro"], categoria="ROCK") == []


def test_catalogo_pagina(client, catalogo):
    primera = client.get(EVENTOS, query_string={"page": 1, "page_size": 3}).get_json()
    segunda = client.get(EVENTOS, query_string={"page": 2, "page_size": 3}).get_json()
    assert (primera["page"], primera["page_size"], primera["total"]) == (1, 3, 4)
    assert len(primera["items"]) == 3 and len(segunda["items"]) == 1
    assert segunda["items"][0]["id"] == catalogo["sol"]


def test_catalogo_recorta_page_size_a_100(client, catalogo):
    assert client.get(EVENTOS, query_string={"page_size": 500}).get_json()["page_size"] == 100


@pytest.mark.parametrize(
    "params",
    [
        {"categoria": "DUBSTEP"},
        {"estado": "VOLANDO"},
        {"artista_id": "no-uuid"},
        {"desde": "ayer"},
        {"page": 0},
        {"page_size": 0},
        {"orden": "precio"},
    ],
)
def test_catalogo_rechaza_filtros_invalidos(client, params):
    respuesta = client.get(EVENTOS, query_string=params)
    assert respuesta.status_code == 422
    assert respuesta.get_json()["codigo"] == "VALIDACION"


# --- Detalle: es_favorito y comprado ----------------------------------------------------------


def test_detalle_sin_sesion_omite_es_favorito_y_comprado(client, catalogo):
    cuerpo = client.get(f"{EVENTOS}/{catalogo['rock']}").get_json()
    assert "es_favorito" not in cuerpo and "comprado" not in cuerpo
    assert cuerpo["titulo"] == "Noche de Rock" and cuerpo["descripcion"] is None


def test_detalle_con_sesion_trae_ambos_en_false(client, catalogo, sesion_usuario):
    """Hasta las Fases 3 y 6 no hay compras ni favoritos: siempre `false`."""
    cuerpo = client.get(f"{EVENTOS}/{catalogo['rock']}", headers=sesion_usuario).get_json()
    assert cuerpo["es_favorito"] is False and cuerpo["comprado"] is False


def test_detalle_con_token_invalido_es_401_y_con_cuenta_suspendida_403(
    client, app, catalogo, sesion_usuario
):
    ruta = f"{EVENTOS}/{catalogo['rock']}"
    assert client.get(ruta, headers=encabezados("basura")).status_code == 401
    with app.app_context():
        usuario = db.session.scalar(select(Usuario).where(Usuario.email == "ana@example.com"))
        usuario.estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    assert client.get(ruta, headers=sesion_usuario).status_code == 403


def test_uuid_mal_formado_es_404(client):
    assert client.get(f"{EVENTOS}/no-es-un-uuid").status_code == 404


# --- GET /artistas/me/eventos -----------------------------------------------------------------


def test_mis_eventos_lista_todos_los_estados_solo_los_propios(
    client, app, artista, sesion_artista, otra_sesion
):
    with app.app_context():
        propios = {
            str(crear_evento(artista, estado=estado).id)
            for estado in (
                EstadoEvento.BORRADOR,
                EstadoEvento.PROGRAMADO,
                EstadoEvento.CANCELADO,
                EstadoEvento.FINALIZADO,
            )
        }
    crear_borrador(client, otra_sesion)

    cuerpo = client.get(MIS_EVENTOS, headers=sesion_artista).get_json()
    assert {e["id"] for e in cuerpo["items"]} == propios
    assert cuerpo["total"] == 4


def test_mis_eventos_filtra_por_estado_y_pagina(client, app, artista, sesion_artista):
    with app.app_context():
        for dia in range(5):
            crear_evento(
                artista,
                estado=EstadoEvento.BORRADOR,
                fecha_inicio=datetime(2030, 1, 1 + dia, tzinfo=UTC),
            )
        crear_evento(artista, estado=EstadoEvento.PROGRAMADO)
    borradores = client.get(
        MIS_EVENTOS, headers=sesion_artista, query_string={"estado": "BORRADOR", "page_size": 2}
    ).get_json()
    assert borradores["total"] == 5 and len(borradores["items"]) == 2
    assert {e["estado"] for e in borradores["items"]} == {"BORRADOR"}
    # Más recientes primero.
    assert borradores["items"][0]["fecha_inicio"] > borradores["items"][1]["fecha_inicio"]


def test_mis_eventos_rechaza_estado_invalido(client, sesion_artista):
    respuesta = client.get(MIS_EVENTOS, headers=sesion_artista, query_string={"estado": "X"})
    assert respuesta.status_code == 422


def test_mis_eventos_incluye_los_suspendidos_por_admin(client, app, artista, sesion_artista):
    with app.app_context():
        evento_id = str(crear_evento(artista, suspendido_admin=True).id)
    cuerpo = client.get(MIS_EVENTOS, headers=sesion_artista).get_json()
    assert [e["id"] for e in cuerpo["items"]] == [evento_id]


def test_la_fila_del_evento_se_conserva_tras_cancelar(client, app, sesion_artista):
    """Sin borrado físico de lo publicado: cancelar solo cambia el estado."""
    evento = crear_borrador(client, sesion_artista)
    client.post(f"{EVENTOS}/{evento['id']}/publicar", headers=sesion_artista)
    client.post(f"{EVENTOS}/{evento['id']}/cancelar", headers=sesion_artista)
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Evento)) == 1


def test_mis_eventos_rechaza_parametros_desconocidos(client, sesion_artista):
    respuesta = client.get(MIS_EVENTOS, headers=sesion_artista, query_string={"orden": "x"})
    assert respuesta.status_code == 422
