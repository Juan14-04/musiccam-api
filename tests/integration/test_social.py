"""Social (Fase 6): favoritos, seguidos, perfil público y «mis entradas» (RN-17, RN-21)."""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.common.enums import EstadoCompra, EstadoEvento, EstadoUsuario, Rol
from app.extensions import db
from app.modules.eventos.models import Evento
from app.modules.social.models import Favorito, Seguidor
from app.modules.social.repository import SocialRepository
from app.modules.usuarios.models import Usuario
from tests.factories import (
    crear_compra,
    crear_evento,
    crear_usuario,
    encabezados,
    iniciar_sesion,
)

FAV = "/api/v1/usuarios/me/favoritos"
SEG = "/api/v1/usuarios/me/seguidos"
MIS_COMPRAS = "/api/v1/usuarios/me/compras"
ARTISTAS = "/api/v1/artistas"
EVENTOS = "/api/v1/eventos"
BASE = datetime(2030, 3, 1, 20, 0, tzinfo=UTC)
INEXISTENTE = "00000000-0000-4000-8000-000000000000"


def entrar_sesion(client, email):
    return encabezados(iniciar_sesion(client, email)["access_token"])


def contar(app, modelo, *condiciones) -> int:
    with app.app_context():
        return db.session.scalar(select(func.count()).select_from(modelo).where(*condiciones))


# --- Fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def artista(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("luz@example.com", Rol.ARTISTA, "Luz Band").id)


@pytest.fixture
def sesion_artista(client, artista):
    return entrar_sesion(client, "luz@example.com")


@pytest.fixture
def otro_artista(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("otro@example.com", Rol.ARTISTA, "Orquesta Sol").id)


@pytest.fixture
def sesion_otro(client, otro_artista):
    return entrar_sesion(client, "otro@example.com")


@pytest.fixture
def ana(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("ana@example.com").id)


@pytest.fixture
def sesion_ana(client, ana):
    return entrar_sesion(client, "ana@example.com")


@pytest.fixture
def beto(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("beto@example.com").id)


@pytest.fixture
def sesion_beto(client, beto):
    return entrar_sesion(client, "beto@example.com")


@pytest.fixture
def admin(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("admin@example.com", Rol.ADMIN).id)


@pytest.fixture
def sesion_admin(client, admin):
    return entrar_sesion(client, "admin@example.com")


@pytest.fixture
def evento(app, artista):
    with app.app_context():
        return SimpleNamespace(id=str(crear_evento(artista, titulo="Concierto base").id))


def nuevo_evento(app, artista, **campos) -> str:
    with app.app_context():
        return str(crear_evento(artista, **campos).id)


def favoritos(client, sesion, **params):
    return client.get(FAV, headers=sesion, query_string=params)


def ids(respuesta) -> list[str]:
    assert respuesta.status_code == 200, respuesta.get_json()
    return [item["id"] for item in respuesta.get_json()["items"]]


# --- Favoritos: idempotencia y concurrencia ----------------------------------------------------


def test_put_favorito_dos_veces_es_204_y_deja_una_sola_fila(app, client, ana, sesion_ana, evento):
    """★ RN-17: idempotente."""
    ruta = f"{FAV}/{evento.id}"
    assert client.put(ruta, headers=sesion_ana).status_code == 204
    assert client.put(ruta, headers=sesion_ana).status_code == 204
    assert contar(app, Favorito, Favorito.usuario_id == ana.id) == 1
    assert ids(favoritos(client, sesion_ana)) == [evento.id]


def test_delete_favorito_inexistente_es_204(app, client, sesion_ana, evento):
    assert client.delete(f"{FAV}/{evento.id}", headers=sesion_ana).status_code == 204
    assert client.delete(f"{FAV}/{INEXISTENTE}", headers=sesion_ana).status_code == 204


def test_delete_favorito_lo_quita_y_repetirlo_es_204(app, client, sesion_ana, evento):
    client.put(f"{FAV}/{evento.id}", headers=sesion_ana)
    assert client.delete(f"{FAV}/{evento.id}", headers=sesion_ana).status_code == 204
    assert ids(favoritos(client, sesion_ana)) == []
    assert client.delete(f"{FAV}/{evento.id}", headers=sesion_ana).status_code == 204
    assert contar(app, Favorito) == 0


def test_put_favorito_concurrente_no_da_500_y_deja_una_fila(
    app, client, ana, sesion_ana, evento, monkeypatch
):
    """★ `INSERT … ON CONFLICT DO NOTHING`: todos llegan al INSERT a la vez."""
    hilos_n = 8
    barrera = threading.Barrier(hilos_n, timeout=20)
    original = SocialRepository.agregar_favorito

    def sincronizado(self, usuario_id, evento_id):
        barrera.wait()
        return original(self, usuario_id, evento_id)

    monkeypatch.setattr(SocialRepository, "agregar_favorito", sincronizado)
    codigos = []

    def poner():
        codigos.append(app.test_client().put(f"{FAV}/{evento.id}", headers=sesion_ana).status_code)

    hilos = [threading.Thread(target=poner) for _ in range(hilos_n)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(40)

    assert codigos == [204] * hilos_n
    assert contar(app, Favorito, Favorito.usuario_id == ana.id) == 1


# --- Favoritos: visibilidad (RN-21) ------------------------------------------------------------


@pytest.mark.parametrize(
    "campos",
    [
        {"estado": EstadoEvento.BORRADOR},
        {"estado": EstadoEvento.CANCELADO},
        {"suspendido_admin": True},
        {"estado": EstadoEvento.FINALIZADO},  # sin replay disponible
    ],
    ids=["borrador", "cancelado", "suspendido", "finalizado-sin-replay"],
)
def test_no_se_marca_favorito_un_evento_no_visible(app, client, artista, sesion_ana, campos):
    evento_id = nuevo_evento(app, artista, **campos)
    respuesta = client.put(f"{FAV}/{evento_id}", headers=sesion_ana)
    assert respuesta.status_code == 404
    assert respuesta.get_json()["codigo"] == "EVENTO_NO_ENCONTRADO"
    assert contar(app, Favorito) == 0


def test_ni_el_dueno_marca_favorito_su_propio_borrador(app, client, artista, sesion_artista):
    evento_id = nuevo_evento(app, artista, estado=EstadoEvento.BORRADOR)
    assert client.put(f"{FAV}/{evento_id}", headers=sesion_artista).status_code == 404


def test_no_se_marca_favorito_el_evento_de_un_artista_suspendido(
    app, client, artista, evento, sesion_ana
):
    with app.app_context():
        db.session.get(Usuario, artista.id).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    assert client.put(f"{FAV}/{evento.id}", headers=sesion_ana).status_code == 404


def test_favorito_de_evento_inexistente_es_404(client, sesion_ana):
    respuesta = client.put(f"{FAV}/{INEXISTENTE}", headers=sesion_ana)
    assert respuesta.status_code == 404 and respuesta.get_json()["codigo"] == "EVENTO_NO_ENCONTRADO"


@pytest.mark.parametrize(
    "campos",
    [
        {"estado": EstadoEvento.PROGRAMADO},
        {"estado": EstadoEvento.EN_VIVO},
        {"estado": EstadoEvento.FINALIZADO, "replay_disponible": True},
    ],
    ids=["programado", "en-vivo", "finalizado-con-replay"],
)
def test_se_marcan_favoritos_los_eventos_visibles(app, client, artista, sesion_ana, campos):
    evento_id = nuevo_evento(app, artista, **campos)
    assert client.put(f"{FAV}/{evento_id}", headers=sesion_ana).status_code == 204
    assert ids(favoritos(client, sesion_ana)) == [evento_id]


def test_un_favorito_cuyo_evento_se_suspende_deja_de_aparecer(app, client, evento, sesion_ana):
    """★ El listado excluye lo que dejó de ser visible; la fila se conserva."""
    client.put(f"{FAV}/{evento.id}", headers=sesion_ana)
    assert ids(favoritos(client, sesion_ana)) == [evento.id]

    with app.app_context():
        db.session.get(Evento, uuid.UUID(evento.id)).suspendido_admin = True
        db.session.commit()
    assert ids(favoritos(client, sesion_ana)) == []
    assert favoritos(client, sesion_ana).get_json()["total"] == 0
    assert contar(app, Favorito) == 1

    with app.app_context():  # al reactivarlo vuelve a aparecer
        db.session.get(Evento, uuid.UUID(evento.id)).suspendido_admin = False
        db.session.commit()
    assert ids(favoritos(client, sesion_ana)) == [evento.id]


@pytest.mark.parametrize("como", ["cancelado", "artista-suspendido", "borrador"])
def test_otros_motivos_que_ocultan_un_favorito(app, client, artista, evento, sesion_ana, como):
    client.put(f"{FAV}/{evento.id}", headers=sesion_ana)
    with app.app_context():
        if como == "artista-suspendido":
            db.session.get(Usuario, artista.id).estado = EstadoUsuario.SUSPENDIDO
        else:
            nuevo = EstadoEvento.CANCELADO if como == "cancelado" else EstadoEvento.BORRADOR
            db.session.get(Evento, uuid.UUID(evento.id)).estado = nuevo
        db.session.commit()
    assert ids(favoritos(client, sesion_ana)) == []


def test_listado_de_favoritos_devuelve_el_resumen_del_evento(client, evento, sesion_ana):
    client.put(f"{FAV}/{evento.id}", headers=sesion_ana)
    item = favoritos(client, sesion_ana).get_json()["items"][0]
    assert set(item) == {
        "id",
        "artista",
        "titulo",
        "categoria",
        "portada_url",
        "youtube_url",
        "trailer_video_url",
        "precio_cop",
        "fecha_inicio",
        "estado",
        "replay_disponible",
    }
    assert set(item["artista"]) == {"id", "nombre_artistico", "foto_url"}
    assert item["titulo"] == "Concierto base" and item["precio_cop"] == 20_000


def test_favoritos_pagina_ordena_y_son_solo_mios(app, client, artista, sesion_ana, sesion_beto):
    eventos = [nuevo_evento(app, artista, titulo=f"E{i}") for i in range(5)]
    for evento_id in eventos:
        client.put(f"{FAV}/{evento_id}", headers=sesion_ana)
    client.put(f"{FAV}/{eventos[0]}", headers=sesion_beto)

    primera = favoritos(client, sesion_ana, page_size=2).get_json()
    assert (primera["page"], primera["page_size"], primera["total"]) == (1, 2, 5)
    # El más recientemente marcado primero.
    assert [i["id"] for i in primera["items"]] == [eventos[4], eventos[3]]
    assert ids(favoritos(client, sesion_ana, page=3, page_size=2)) == [eventos[0]]
    assert ids(favoritos(client, sesion_beto)) == [eventos[0]]


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"orden": "x"}])
def test_favoritos_rechaza_parametros_invalidos(client, sesion_ana, params):
    assert favoritos(client, sesion_ana, **params).status_code == 422


# --- Detalle del evento: es_favorito -----------------------------------------------------------


def test_es_favorito_cambia_al_agregar_y_quitar(client, evento, sesion_ana, sesion_beto):
    ruta = f"{EVENTOS}/{evento.id}"
    assert client.get(ruta, headers=sesion_ana).get_json()["es_favorito"] is False

    client.put(f"{FAV}/{evento.id}", headers=sesion_ana)
    assert client.get(ruta, headers=sesion_ana).get_json()["es_favorito"] is True
    assert (
        client.get(ruta, headers=sesion_beto).get_json()["es_favorito"] is False
    )  # es por usuario
    assert "es_favorito" not in client.get(ruta).get_json()  # sin sesión se omite

    client.delete(f"{FAV}/{evento.id}", headers=sesion_ana)
    assert client.get(ruta, headers=sesion_ana).get_json()["es_favorito"] is False


# --- Seguidos: idempotencia, reglas y listado --------------------------------------------------


def test_put_seguido_dos_veces_es_204_y_deja_una_sola_fila(app, client, ana, artista, sesion_ana):
    """★ RN-17."""
    ruta = f"{SEG}/{artista.id}"
    assert client.put(ruta, headers=sesion_ana).status_code == 204
    assert client.put(ruta, headers=sesion_ana).status_code == 204
    assert contar(app, Seguidor, Seguidor.usuario_id == ana.id) == 1


def test_delete_seguido_inexistente_es_204(app, client, artista, sesion_ana):
    assert client.delete(f"{SEG}/{artista.id}", headers=sesion_ana).status_code == 204
    assert client.delete(f"{SEG}/{INEXISTENTE}", headers=sesion_ana).status_code == 204


def test_dejar_de_seguir(app, client, artista, sesion_ana):
    client.put(f"{SEG}/{artista.id}", headers=sesion_ana)
    assert client.delete(f"{SEG}/{artista.id}", headers=sesion_ana).status_code == 204
    assert contar(app, Seguidor) == 0
    assert client.delete(f"{SEG}/{artista.id}", headers=sesion_ana).status_code == 204


def test_put_seguido_concurrente_no_da_500_y_deja_una_fila(
    app, client, ana, artista, sesion_ana, monkeypatch
):
    hilos_n = 8
    barrera = threading.Barrier(hilos_n, timeout=20)
    original = SocialRepository.agregar_seguido

    def sincronizado(self, usuario_id, artista_id):
        barrera.wait()
        return original(self, usuario_id, artista_id)

    monkeypatch.setattr(SocialRepository, "agregar_seguido", sincronizado)
    codigos = []

    def seguir():
        codigos.append(app.test_client().put(f"{SEG}/{artista.id}", headers=sesion_ana).status_code)

    hilos = [threading.Thread(target=seguir) for _ in range(hilos_n)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(40)
    assert codigos == [204] * hilos_n
    assert contar(app, Seguidor, Seguidor.usuario_id == ana.id) == 1


def test_no_se_puede_seguir_a_uno_mismo(app, client, ana, artista, sesion_ana, sesion_artista):
    """★ RN-17: 422, sea artista o usuario normal."""
    for sesion, propio in ((sesion_artista, artista.id), (sesion_ana, ana.id)):
        respuesta = client.put(f"{SEG}/{propio}", headers=sesion)
        assert respuesta.status_code == 422
        assert respuesta.get_json()["codigo"] == "NO_SEGUIRSE_A_SI_MISMO"
    assert contar(app, Seguidor) == 0


def test_no_se_sigue_a_un_usuario_normal_ni_a_un_admin_ni_a_un_inexistente(
    app, client, beto, admin, sesion_ana
):
    """★ RN-17: 404 `USUARIO_NO_ENCONTRADO`."""
    for objetivo in (beto.id, admin.id, INEXISTENTE):
        respuesta = client.put(f"{SEG}/{objetivo}", headers=sesion_ana)
        assert respuesta.status_code == 404
        assert respuesta.get_json()["codigo"] == "USUARIO_NO_ENCONTRADO"
    assert contar(app, Seguidor) == 0


def test_no_se_sigue_a_un_artista_suspendido(app, client, artista, sesion_ana):
    with app.app_context():
        db.session.get(Usuario, artista.id).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    respuesta = client.put(f"{SEG}/{artista.id}", headers=sesion_ana)
    assert (
        respuesta.status_code == 404 and respuesta.get_json()["codigo"] == "USUARIO_NO_ENCONTRADO"
    )


def test_un_artista_puede_seguir_a_otro_artista(app, client, artista, sesion_otro, otro_artista):
    assert client.put(f"{SEG}/{artista.id}", headers=sesion_otro).status_code == 204
    assert contar(app, Seguidor, Seguidor.usuario_id == otro_artista.id) == 1


def test_listado_de_seguidos_solo_muestra_datos_publicos(
    app, client, artista, otro_artista, sesion_ana
):
    with app.app_context():
        usuario = db.session.get(Usuario, artista.id)
        usuario.biografia = "Cumbia desde Barranquilla"
        usuario.foto_url = "/uploads/perfiles/x/y.webp"
        db.session.commit()
    client.put(f"{SEG}/{artista.id}", headers=sesion_ana)
    client.put(f"{SEG}/{otro_artista.id}", headers=sesion_ana)

    respuesta = client.get(SEG, headers=sesion_ana)
    cuerpo = respuesta.get_json()
    assert cuerpo["total"] == 2
    # Más reciente primero.
    assert [a["nombre_artistico"] for a in cuerpo["items"]] == ["Orquesta Sol", "Luz Band"]
    luz = cuerpo["items"][1]
    assert set(luz) == {"id", "nombre_artistico", "foto_url", "biografia"}
    assert luz["biografia"] == "Cumbia desde Barranquilla"
    assert luz["foto_url"] == "/uploads/perfiles/x/y.webp"
    texto = respuesta.get_data(as_text=True)
    for privado in ("luz@example.com", "otro@example.com", "email", "password", "rol", "estado"):
        assert privado not in texto


def test_el_listado_de_seguidos_excluye_artistas_suspendidos_y_es_solo_mio(
    app, client, artista, otro_artista, sesion_ana, sesion_beto
):
    client.put(f"{SEG}/{artista.id}", headers=sesion_ana)
    client.put(f"{SEG}/{otro_artista.id}", headers=sesion_ana)
    client.put(f"{SEG}/{artista.id}", headers=sesion_beto)
    with app.app_context():
        db.session.get(Usuario, otro_artista.id).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()

    assert ids(client.get(SEG, headers=sesion_ana)) == [str(artista.id)]
    assert ids(client.get(SEG, headers=sesion_beto)) == [str(artista.id)]
    # Se puede dejar de seguir a un artista suspendido.
    assert client.delete(f"{SEG}/{otro_artista.id}", headers=sesion_ana).status_code == 204
    assert contar(app, Seguidor, Seguidor.artista_id == otro_artista.id) == 0


def test_seguidos_pagina_y_rechaza_parametros_invalidos(app, client, sesion_ana):
    with app.app_context():
        artistas = [
            crear_usuario(f"a{i}@example.com", Rol.ARTISTA, f"Artista {i}").id for i in range(3)
        ]
    for artista_id in artistas:
        client.put(f"{SEG}/{artista_id}", headers=sesion_ana)
    pagina = client.get(SEG, headers=sesion_ana, query_string={"page_size": 2}).get_json()
    assert (pagina["total"], len(pagina["items"])) == (3, 2)
    assert client.get(SEG, headers=sesion_ana, query_string={"page": 0}).status_code == 422
    assert client.get(SEG, headers=sesion_ana, query_string={"x": 1}).status_code == 422


# --- Roles -------------------------------------------------------------------------------------

RUTAS_SOCIALES = [
    ("get", FAV),
    ("put", f"{FAV}/{INEXISTENTE}"),
    ("delete", f"{FAV}/{INEXISTENTE}"),
    ("get", SEG),
    ("put", f"{SEG}/{INEXISTENTE}"),
    ("delete", f"{SEG}/{INEXISTENTE}"),
    ("get", MIS_COMPRAS),
]


@pytest.mark.parametrize(("metodo", "ruta"), RUTAS_SOCIALES)
def test_un_admin_recibe_403_en_favoritos_seguidos_y_mis_compras(
    client, sesion_admin, metodo, ruta
):
    """★ RN-17 / §4."""
    respuesta = getattr(client, metodo)(ruta, headers=sesion_admin)
    assert respuesta.status_code == 403 and respuesta.get_json()["codigo"] == "ROL_NO_PERMITIDO"


@pytest.mark.parametrize(("metodo", "ruta"), RUTAS_SOCIALES)
def test_sin_token_es_401(client, metodo, ruta):
    assert getattr(client, metodo)(ruta).status_code == 401


def test_un_admin_no_deja_filas_aunque_lo_intente(app, client, evento, artista, sesion_admin):
    client.put(f"{FAV}/{evento.id}", headers=sesion_admin)
    client.put(f"{SEG}/{artista.id}", headers=sesion_admin)
    assert contar(app, Favorito) == 0 and contar(app, Seguidor) == 0


def test_un_artista_tambien_usa_favoritos(app, client, otro_artista, evento, sesion_otro):
    assert client.put(f"{FAV}/{evento.id}", headers=sesion_otro).status_code == 204
    assert ids(favoritos(client, sesion_otro)) == [evento.id]


def test_usuario_suspendido_no_usa_lo_social(app, client, ana, evento, sesion_ana):
    with app.app_context():
        db.session.get(Usuario, ana.id).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    respuesta = client.put(f"{FAV}/{evento.id}", headers=sesion_ana)
    assert respuesta.status_code == 403 and respuesta.get_json()["codigo"] == "CUENTA_SUSPENDIDA"


# --- Restricciones de la BD --------------------------------------------------------------------


def test_la_bd_impone_pk_compuesta_check_y_restrict(app, ana, artista, evento):
    with app.app_context():
        db.session.add(Favorito(usuario_id=ana.id, evento_id=uuid.UUID(evento.id)))
        db.session.commit()
        db.session.add(Favorito(usuario_id=ana.id, evento_id=uuid.UUID(evento.id)))
        with pytest.raises(IntegrityError):  # PK (usuario_id, evento_id)
            db.session.commit()
        db.session.rollback()

        db.session.add(Seguidor(usuario_id=artista.id, artista_id=artista.id))
        with pytest.raises(IntegrityError):  # CHECK usuario_id <> artista_id
            db.session.commit()
        db.session.rollback()

        db.session.delete(db.session.get(Evento, uuid.UUID(evento.id)))
        with pytest.raises(IntegrityError):  # ON DELETE RESTRICT
            db.session.commit()
        db.session.rollback()

        db.session.add(Seguidor(usuario_id=ana.id, artista_id=artista.id))
        db.session.commit()
        db.session.delete(db.session.get(Usuario, artista.id))
        with pytest.raises(DBAPIError):  # RESTRICT (y los eventos del artista)
            db.session.commit()
        db.session.rollback()


# --- Perfil público de artistas ----------------------------------------------------------------


def perfil(client, artista_id, sesion=None):
    return client.get(f"{ARTISTAS}/{artista_id}", headers=sesion or {})


def test_perfil_publico_cuenta_los_seguidores(
    app, client, artista, sesion_ana, sesion_beto, sesion_otro
):
    """★ El conteo es un `COUNT(*)`, y nunca la lista."""
    assert perfil(client, artista.id).get_json()["seguidores"] == 0
    for sesion in (sesion_ana, sesion_beto, sesion_otro):
        client.put(f"{SEG}/{artista.id}", headers=sesion)
    cuerpo = perfil(client, artista.id).get_json()
    assert cuerpo["seguidores"] == 3
    assert isinstance(cuerpo["seguidores"], int)
    client.delete(f"{SEG}/{artista.id}", headers=sesion_beto)
    assert perfil(client, artista.id).get_json()["seguidores"] == 2


def test_perfil_publico_forma_y_ausencia_de_datos_privados(app, client, artista, sesion_ana):
    with app.app_context():
        usuario = db.session.get(Usuario, artista.id)
        usuario.biografia = "Música en vivo"
        usuario.foto_url = "/uploads/perfiles/x/y.webp"
        db.session.commit()
    client.put(f"{SEG}/{artista.id}", headers=sesion_ana)

    respuesta = perfil(client, artista.id)
    assert respuesta.status_code == 200
    cuerpo = respuesta.get_json()
    assert set(cuerpo) == {
        "id",
        "nombre_artistico",
        "foto_url",
        "biografia",
        "seguidores",
        "eventos_proximos",
    }
    assert (cuerpo["nombre_artistico"], cuerpo["biografia"]) == ("Luz Band", "Música en vivo")
    texto = respuesta.get_data(as_text=True)
    for privado in ("luz@example.com", "ana@example.com", "password", "hash", "rol", "estado"):
        assert privado not in texto


def test_siguiendo_aparece_solo_con_sesion(client, artista, sesion_ana, sesion_beto, sesion_admin):
    """★"""
    client.put(f"{SEG}/{artista.id}", headers=sesion_ana)
    assert "siguiendo" not in perfil(client, artista.id).get_json()  # anónimo
    assert perfil(client, artista.id, sesion_ana).get_json()["siguiendo"] is True
    assert perfil(client, artista.id, sesion_beto).get_json()["siguiendo"] is False
    assert perfil(client, artista.id, sesion_admin).get_json()["siguiendo"] is False
    client.delete(f"{SEG}/{artista.id}", headers=sesion_ana)
    assert perfil(client, artista.id, sesion_ana).get_json()["siguiendo"] is False


def test_perfil_con_token_invalido_es_401(client, artista):
    assert perfil(client, artista.id, encabezados("basura")).status_code == 401


def test_perfil_de_artista_suspendido_inexistente_o_no_artista_es_404(
    app, client, artista, ana, admin
):
    """★"""
    for objetivo in (ana.id, admin.id, INEXISTENTE):
        respuesta = perfil(client, objetivo)
        assert respuesta.status_code == 404
        assert respuesta.get_json()["codigo"] == "USUARIO_NO_ENCONTRADO"
    assert perfil(client, artista.id).status_code == 200
    with app.app_context():
        db.session.get(Usuario, artista.id).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    assert perfil(client, artista.id).status_code == 404


def test_eventos_proximos_solo_visibles_programados_o_en_vivo_por_fecha(app, client, artista):
    """★ Sin borradores, cancelados, suspendidos ni finalizados."""
    esperado = [
        nuevo_evento(app, artista, titulo="B", fecha_inicio=BASE + timedelta(days=1)),
        nuevo_evento(
            app,
            artista,
            titulo="A",
            estado=EstadoEvento.EN_VIVO,
            fecha_inicio=BASE + timedelta(days=2),
        ),
        nuevo_evento(app, artista, titulo="C", fecha_inicio=BASE + timedelta(days=3)),
    ]
    # El de fecha más temprana se crea al final: el orden es por fecha, no por creación.
    primero = nuevo_evento(app, artista, titulo="Z", fecha_inicio=BASE)
    for campos in (
        {"estado": EstadoEvento.BORRADOR},
        {"estado": EstadoEvento.CANCELADO},
        {"suspendido_admin": True},
        {"estado": EstadoEvento.FINALIZADO, "replay_disponible": True},
        {"estado": EstadoEvento.FINALIZADO},
    ):
        nuevo_evento(app, artista, **campos)

    proximos = perfil(client, artista.id).get_json()["eventos_proximos"]
    assert [e["id"] for e in proximos] == [primero, *esperado]
    assert {e["estado"] for e in proximos} == {"PROGRAMADO", "EN_VIVO"}
    assert set(proximos[0]) >= {"id", "titulo", "fecha_inicio", "precio_cop", "artista"}


def test_eventos_proximos_son_solo_del_artista(app, client, artista, otro_artista):
    propio = nuevo_evento(app, artista)
    nuevo_evento(app, otro_artista)
    assert [e["id"] for e in perfil(client, artista.id).get_json()["eventos_proximos"]] == [propio]


# --- Lista pública de artistas -----------------------------------------------------------------


def test_lista_publica_de_artistas_solo_activos_y_sin_datos_privados(
    app, client, artista, otro_artista, ana, admin
):
    with app.app_context():
        crear_usuario(
            "susp@example.com", Rol.ARTISTA, "Suspendido", estado=EstadoUsuario.SUSPENDIDO
        )
    respuesta = client.get(ARTISTAS)
    assert respuesta.status_code == 200
    cuerpo = respuesta.get_json()
    assert [a["nombre_artistico"] for a in cuerpo["items"]] == ["Luz Band", "Orquesta Sol"]
    assert cuerpo["total"] == 2
    assert set(cuerpo["items"][0]) == {"id", "nombre_artistico", "foto_url", "biografia"}
    texto = respuesta.get_data(as_text=True)
    assert "@example.com" not in texto and "Suspendido" not in texto


def test_lista_de_artistas_filtra_por_q_pagina_y_valida(app, client, artista):
    with app.app_context():
        for nombre in ("Sol Aurora", "Sol_Zeta", "100% Salsa", "Los Rockeros"):
            crear_usuario(f"{nombre.split()[0].lower()}@example.com", Rol.ARTISTA, nombre)

    def nombres(**params):
        return [
            a["nombre_artistico"]
            for a in client.get(ARTISTAS, query_string=params).get_json()["items"]
        ]

    assert nombres(q="SOL") == ["Sol Aurora", "Sol_Zeta"]
    assert nombres(q="luz") == ["Luz Band"]
    assert nombres(q="_") == ["Sol_Zeta"]  # el guion bajo es texto, no comodín
    assert nombres(q="%") == ["100% Salsa"]
    assert nombres(q="nada") == []
    pagina = client.get(ARTISTAS, query_string={"page_size": 2, "page": 2}).get_json()
    assert (pagina["page"], pagina["total"], len(pagina["items"])) == (2, 5, 2)
    for params in ({"page": 0}, {"q": ""}, {"orden": "x"}):
        assert client.get(ARTISTAS, query_string=params).status_code == 422


def test_las_rutas_del_panel_no_chocan_con_el_perfil_publico(client, artista, sesion_artista):
    """★ `/artistas/me/...` sigue funcionando junto a `/artistas/<uuid>`."""
    assert client.get(f"{ARTISTAS}/me/saldo", headers=sesion_artista).status_code == 200
    assert client.get(f"{ARTISTAS}/me/eventos", headers=sesion_artista).status_code == 200
    assert client.get(f"{ARTISTAS}/me/estadisticas", headers=sesion_artista).status_code == 200
    assert client.get(f"{ARTISTAS}/me/retiros", headers=sesion_artista).status_code == 200
    assert perfil(client, artista.id).status_code == 200
    assert client.get(f"{ARTISTAS}/me").status_code == 404  # `me` no es un uuid


# --- Estadísticas del artista (conexión con la Fase 5) -----------------------------------------


def test_estadisticas_cuentan_seguidores_y_favoritos_reales(
    app, client, artista, otro_artista, sesion_artista, sesion_ana, sesion_beto, sesion_otro
):
    e1, e2, e3 = (nuevo_evento(app, artista, titulo=f"E{i}") for i in range(1, 4))
    for sesion in (sesion_ana, sesion_beto, sesion_otro):
        client.put(f"{SEG}/{artista.id}", headers=sesion)
    for sesion in (sesion_ana, sesion_beto):
        client.put(f"{FAV}/{e1}", headers=sesion)
    client.put(f"{FAV}/{e2}", headers=sesion_ana)

    panel = "/api/v1/artistas/me"
    resumen = client.get(f"{panel}/estadisticas", headers=sesion_artista).get_json()["resumen"]
    assert resumen["seguidores"] == 3
    favoritos_de = {
        e: client.get(f"{panel}/eventos/{e}/estadisticas", headers=sesion_artista).get_json()[
            "favoritos"
        ]
        for e in (e1, e2, e3)
    }
    assert favoritos_de == {e1: 2, e2: 1, e3: 0}

    client.delete(f"{SEG}/{artista.id}", headers=sesion_ana)
    client.delete(f"{FAV}/{e1}", headers=sesion_ana)
    assert (
        client.get(f"{panel}/estadisticas", headers=sesion_artista).get_json()["resumen"][
            "seguidores"
        ]
        == 2
    )
    assert (
        client.get(f"{panel}/eventos/{e1}/estadisticas", headers=sesion_artista).get_json()[
            "favoritos"
        ]
        == 1
    )


def test_los_seguidores_de_otro_artista_no_cuentan(
    client, artista, otro_artista, sesion_artista, sesion_ana
):
    client.put(f"{SEG}/{otro_artista.id}", headers=sesion_ana)
    resumen = client.get("/api/v1/artistas/me/estadisticas", headers=sesion_artista).get_json()
    assert resumen["resumen"]["seguidores"] == 0


# --- GET /usuarios/me/compras ------------------------------------------------------------------


@pytest.fixture
def entradas(app, artista, ana, beto):
    """Compras de `ana` en distintos estados + una de `beto` que ella nunca debe ver."""
    with app.app_context():
        ev = {
            "lejano": crear_evento(artista, titulo="Lejano", fecha_inicio=BASE + timedelta(days=5)),
            "cercano": crear_evento(
                artista,
                titulo="Cercano",
                estado=EstadoEvento.EN_VIVO,
                fecha_inicio=BASE + timedelta(days=1),
            ),
            "pasado": crear_evento(
                artista, titulo="Pasado", estado=EstadoEvento.FINALIZADO, fecha_inicio=BASE
            ),
            "pendiente": crear_evento(artista, titulo="Pendiente"),
            "cancelado": crear_evento(artista, titulo="Cancelado", estado=EstadoEvento.CANCELADO),
            "rechazado": crear_evento(artista, titulo="Rechazado"),
            "suspendido": crear_evento(
                artista,
                titulo="Suspendido",
                suspendido_admin=True,
                fecha_inicio=BASE + timedelta(days=3),
            ),
        }
        creada = BASE - timedelta(days=30)
        plan = [  # (evento, estado, antigüedad en días)
            ("lejano", EstadoCompra.APROBADA, 1),
            ("cercano", EstadoCompra.APROBADA, 2),
            ("pasado", EstadoCompra.APROBADA, 3),
            ("pendiente", EstadoCompra.PENDIENTE, 4),
            ("cancelado", EstadoCompra.REEMBOLSADA, 5),
            ("rechazado", EstadoCompra.RECHAZADA, 6),
            ("suspendido", EstadoCompra.APROBADA, 7),
        ]
        compras = {}
        for nombre, estado, dias in plan:
            compras[nombre] = str(
                crear_compra(
                    ana.id,
                    ev[nombre].id,
                    estado=estado,
                    monto_cop=25_000,
                    comision_cop=2_500,
                    neto_artista_cop=22_500,
                    creado_en=creada + timedelta(days=30 - dias),
                ).id
            )
        crear_compra(beto.id, ev["lejano"].id, estado=EstadoCompra.APROBADA)
        return SimpleNamespace(
            compras=compras,
            eventos={n: str(e.id) for n, e in ev.items()},
            beto_evento=ev["lejano"].id,
        )


def mis_compras(client, sesion, **params):
    respuesta = client.get(MIS_COMPRAS, headers=sesion, query_string=params)
    assert respuesta.status_code == 200, respuesta.get_json()
    return respuesta.get_json()


def test_mis_compras_devuelve_solo_las_mias_mas_recientes_primero(client, sesion_ana, entradas):
    """★"""
    cuerpo = mis_compras(client, sesion_ana)
    assert cuerpo["total"] == 7
    assert [c["id"] for c in cuerpo["items"]] == [
        entradas.compras[n]
        for n in (
            "lejano",
            "cercano",
            "pasado",
            "pendiente",
            "cancelado",
            "rechazado",
            "suspendido",
        )
    ]


def test_otro_usuario_no_ve_mis_compras(client, sesion_ana, sesion_beto, entradas):
    """★"""
    suyas = mis_compras(client, sesion_beto)
    assert suyas["total"] == 1
    ajenas = set(entradas.compras.values())
    assert not ajenas & {c["id"] for c in suyas["items"]}
    assert mis_compras(client, sesion_ana)["total"] == 7  # y las de beto no están en las de ana


def test_un_usuario_sin_compras_recibe_una_lista_vacia(client, sesion_artista):
    assert mis_compras(client, sesion_artista) == {
        "items": [],
        "page": 1,
        "page_size": 20,
        "total": 0,
    }


def test_cada_entrada_trae_el_resumen_del_evento_el_estado_y_el_monto(client, sesion_ana, entradas):
    item = mis_compras(client, sesion_ana, estado="APROBADA", page_size=1)["items"][0]
    assert item["id"] == entradas.compras["lejano"]
    assert (item["estado"], item["monto_cop"], item["metodo_pago"]) == ("APROBADA", 25_000, "NEQUI")
    assert item["evento"]["id"] == entradas.eventos["lejano"]
    assert item["evento"]["titulo"] == "Lejano"
    assert set(item["evento"]) >= {"fecha_inicio", "estado", "artista", "precio_cop"}
    assert set(item["evento"]["artista"]) == {"id", "nombre_artistico", "foto_url"}
    # El comprador no ve lo que es del artista ni de la plataforma.
    assert not {"comision_cop", "neto_artista_cop", "comision_pct", "usuario_id"} & set(item)


def test_filtro_proximos_solo_aprobadas_de_eventos_programados_o_en_vivo_por_fecha(
    client, sesion_ana, entradas
):
    """★ `proximos=true`: «Mi calendario»."""
    cuerpo = mis_compras(client, sesion_ana, proximos="true")
    # Cercano (EN_VIVO, +1 d) → Suspendido (+3 d, sigue siendo una entrada comprada) → Lejano (+5 d)
    assert [c["id"] for c in cuerpo["items"]] == [
        entradas.compras["cercano"],
        entradas.compras["suspendido"],
        entradas.compras["lejano"],
    ]
    assert {c["estado"] for c in cuerpo["items"]} == {"APROBADA"}
    fechas = [c["evento"]["fecha_inicio"] for c in cuerpo["items"]]
    assert fechas == sorted(fechas)
    # Las del pasado (FINALIZADO), pendientes, reembolsadas y rechazadas no están.
    assert cuerpo["total"] == 3


def test_proximos_false_equivale_a_no_filtrar(client, sesion_ana, entradas):
    assert mis_compras(client, sesion_ana, proximos="false")["total"] == 7


@pytest.mark.parametrize(
    ("estado", "esperadas"),
    [
        ("APROBADA", {"lejano", "cercano", "pasado", "suspendido"}),
        ("PENDIENTE", {"pendiente"}),
        ("REEMBOLSADA", {"cancelado"}),
        ("RECHAZADA", {"rechazado"}),
        ("ANULADA", set()),
    ],
)
def test_filtro_por_estado(client, sesion_ana, entradas, estado, esperadas):
    cuerpo = mis_compras(client, sesion_ana, estado=estado)
    assert {c["id"] for c in cuerpo["items"]} == {entradas.compras[n] for n in esperadas}


def test_estado_y_proximos_se_combinan(client, sesion_ana, entradas):
    assert mis_compras(client, sesion_ana, estado="APROBADA", proximos="true")["total"] == 3
    assert mis_compras(client, sesion_ana, estado="PENDIENTE", proximos="true")["total"] == 0


def test_mis_compras_pagina(client, sesion_ana, entradas):
    primera = mis_compras(client, sesion_ana, page_size=3)
    assert (primera["page"], primera["page_size"], primera["total"], len(primera["items"])) == (
        1,
        3,
        7,
        3,
    )
    assert len(mis_compras(client, sesion_ana, page=3, page_size=3)["items"]) == 1


@pytest.mark.parametrize(
    "params",
    [{"estado": "NADA"}, {"proximos": "quizas"}, {"page": 0}, {"page_size": 0}, {"orden": "x"}],
)
def test_mis_compras_rechaza_parametros_invalidos(client, sesion_ana, params):
    assert client.get(MIS_COMPRAS, headers=sesion_ana, query_string=params).status_code == 422


def test_mis_compras_de_un_artista_comprador(client, sesion_otro, artista, otro_artista, app):
    with app.app_context():
        evento_id = crear_evento(artista).id
        crear_compra(otro_artista.id, evento_id, estado=EstadoCompra.APROBADA)
    assert mis_compras(client, sesion_otro)["total"] == 1


# --- Swagger -----------------------------------------------------------------------------------


def test_swagger_documenta_lo_social_los_perfiles_y_mis_entradas(client):
    spec = client.get("/api/openapi.json").get_json()
    assert {"Usuarios", "Artistas"} <= {t["name"] for t in spec["tags"]}

    esperados = [
        (FAV, "get", "Usuarios", {"200", "401", "403", "422"}),
        (f"{FAV}/{{evento_id}}", "put", "Usuarios", {"204", "401", "403", "404"}),
        (f"{FAV}/{{evento_id}}", "delete", "Usuarios", {"204", "401", "403"}),
        (SEG, "get", "Usuarios", {"200", "401", "403", "422"}),
        (f"{SEG}/{{artista_id}}", "put", "Usuarios", {"204", "401", "403", "404", "422"}),
        (f"{SEG}/{{artista_id}}", "delete", "Usuarios", {"204", "401", "403"}),
        (MIS_COMPRAS, "get", "Usuarios", {"200", "401", "403", "422"}),
        (ARTISTAS, "get", "Artistas", {"200", "422"}),
        (f"{ARTISTAS}/{{artista_id}}", "get", "Artistas", {"200", "404"}),
    ]
    for ruta, metodo, tag, codigos in esperados:
        operacion = spec["paths"][ruta][metodo]
        assert operacion["tags"] == [tag], (ruta, metodo, operacion["tags"])
        assert operacion["summary"] and operacion["description"], (ruta, metodo)
        assert codigos <= set(operacion["responses"]), (ruta, metodo, set(operacion["responses"]))

    # Lo público no exige sesión; el perfil la acepta de forma opcional.
    assert "security" not in spec["paths"][ARTISTAS]["get"]
    assert spec["paths"][f"{ARTISTAS}/{{artista_id}}"]["get"]["security"] == [
        {},
        {"bearerAuth": []},
    ]
    assert spec["paths"][FAV]["get"]["security"] == [{"bearerAuth": []}]

    esquemas = spec["components"]["schemas"]
    for esquema, campo in (
        ("ArtistaPublico", "nombre_artistico"),
        ("ArtistaPublico", "biografia"),
        ("ArtistaPerfil", "seguidores"),
    ):
        assert "example" in esquemas[esquema]["properties"][campo], (esquema, campo)
    assert "siguiendo" in esquemas["ArtistaPerfil"]["properties"]
    assert "password_hash" not in str(esquemas["ArtistaPerfil"])
    assert "MiCompra" in esquemas
