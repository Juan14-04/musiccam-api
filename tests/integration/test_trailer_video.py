"""Video del tráiler subido por el artista: formato, tamaño, estado, propiedad y limpieza."""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.common.enums import EstadoEvento, Rol
from app.modules.eventos.trailer import detectar_formato
from tests.factories import crear_evento, crear_usuario, encabezados, iniciar_sesion

EVENTOS = "/api/v1/eventos"
# Cabeceras mínimas reales: MP4 (caja `ftyp` con marca isom) y WebM (EBML).
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64
WEBM = b"\x1aE\xdf\xa3" + b"\x00" * 64
MOV = b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  " + b"\x00" * 64


def entrar(client, email):
    return encabezados(iniciar_sesion(client, email)["access_token"])


def subir(client, sesion, evento_id, contenido: bytes, nombre="trailer.mp4"):
    return client.put(
        f"{EVENTOS}/{evento_id}/trailer",
        headers=sesion,
        data={"trailer": (BytesIO(contenido), nombre, "video/mp4")},
        content_type="multipart/form-data",
    )


@pytest.fixture
def artista(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("luz@example.com", Rol.ARTISTA, "Luz Band").id)


@pytest.fixture
def sesion(client, artista):
    return entrar(client, "luz@example.com")


@pytest.fixture
def evento_id(app, artista):
    with app.app_context():
        return crear_evento(artista, estado=EstadoEvento.BORRADOR).id


def archivo_de(app, url: str) -> Path:
    return Path(app.config["UPLOAD_DIR"]) / url.removeprefix("/uploads/")


def test_detecta_mp4_y_webm_por_contenido_y_rechaza_el_resto():
    assert detectar_formato(MP4) == "mp4"
    assert detectar_formato(WEBM) == "webm"
    assert detectar_formato(MOV) is None
    assert detectar_formato(b"GIF89a" + b"\x00" * 20) is None
    assert detectar_formato(b"") is None


def test_subir_guarda_el_video_y_lo_expone_en_el_evento_y_el_catalogo(
    app, client, sesion, evento_id
):
    respuesta = subir(client, sesion, evento_id, MP4)
    assert respuesta.status_code == 200, respuesta.get_json()
    url = respuesta.get_json()["trailer_video_url"]
    assert url.startswith(f"/uploads/trailers/{evento_id}/") and url.endswith(".mp4")
    assert archivo_de(app, url).read_bytes() == MP4
    # El archivo se sirve (con soporte de rangos para que el <video> pueda adelantar).
    servido = client.get(url, headers={"Range": "bytes=0-3"})
    assert servido.status_code == 206 and servido.data == MP4[:4]


def test_reemplazar_borra_el_archivo_anterior(app, client, sesion, evento_id):
    primero = subir(client, sesion, evento_id, MP4).get_json()["trailer_video_url"]
    segundo = subir(client, sesion, evento_id, WEBM, "t.webm").get_json()["trailer_video_url"]
    assert segundo.endswith(".webm")
    assert not archivo_de(app, primero).exists()
    assert archivo_de(app, segundo).exists()


def test_formato_no_admitido_o_vacio_es_422_con_codigo_propio(client, sesion, evento_id):
    for contenido in (MOV, b"no es un video", b""):
        respuesta = subir(client, sesion, evento_id, contenido)
        assert respuesta.status_code == 422
        assert respuesta.get_json()["codigo"] == "VIDEO_INVALIDO"


def test_video_mayor_al_limite_es_422(app, client, sesion, evento_id, monkeypatch):
    monkeypatch.setitem(app.config, "MAX_TRAILER_MB", 1)
    grande = MP4 + b"\x00" * (1024 * 1024)
    respuesta = subir(client, sesion, evento_id, grande)
    assert respuesta.status_code == 422
    assert "1 MB" in respuesta.get_json()["mensaje"]


def test_la_ruta_del_video_admite_mas_que_el_tope_global(app, client, sesion, evento_id):
    """El tope global (fotos) es 3 MB; el tráiler acepta hasta MAX_TRAILER_MB."""
    mediano = MP4 + b"\x00" * (4 * 1024 * 1024)
    assert subir(client, sesion, evento_id, mediano).status_code == 200


def test_solo_el_dueno_y_solo_en_borrador_o_programado(app, client, sesion, artista, evento_id):
    with app.app_context():
        crear_usuario("otro@example.com", Rol.ARTISTA, "Otro Grupo")
        finalizado = crear_evento(artista, estado=EstadoEvento.FINALIZADO).id
    ajeno = entrar(client, "otro@example.com")
    assert subir(client, ajeno, evento_id, MP4).status_code == 403
    assert subir(client, sesion, finalizado, MP4).status_code == 409


def test_quitar_borra_el_archivo_y_es_idempotente(app, client, sesion, evento_id):
    url = subir(client, sesion, evento_id, MP4).get_json()["trailer_video_url"]
    for _ in range(2):
        respuesta = client.delete(f"{EVENTOS}/{evento_id}/trailer", headers=sesion)
        assert respuesta.status_code == 200
        assert respuesta.get_json()["trailer_video_url"] is None
    assert not archivo_de(app, url).exists()


def test_eliminar_el_borrador_borra_su_video(app, client, sesion, evento_id):
    url = subir(client, sesion, evento_id, MP4).get_json()["trailer_video_url"]
    assert client.delete(f"{EVENTOS}/{evento_id}", headers=sesion).status_code == 204
    assert not archivo_de(app, url).exists()
