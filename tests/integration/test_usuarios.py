"""Usuarios: perfil, foto (RN-18), suspensión (RN-19) y `requiere_roles` (§4)."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app.common.enums import EstadoUsuario, Rol
from app.extensions import db
from app.modules.usuarios.models import Usuario
from tests.factories import crear_usuario, encabezados, imagen_bytes, iniciar_sesion

ME = "/api/v1/usuarios/me"
FOTO = "/api/v1/usuarios/me/foto"


@pytest.fixture
def sesion(client, app):
    with app.app_context():
        crear_usuario()
    return encabezados(iniciar_sesion(client)["access_token"])


@pytest.fixture
def sesion_artista(client, app):
    with app.app_context():
        crear_usuario("luz@example.com", Rol.ARTISTA, "Luz Band")
    return encabezados(iniciar_sesion(client, "luz@example.com")["access_token"])


def subir(client, sesion, contenido: bytes, nombre="foto.png", tipo="image/png"):
    return client.put(
        FOTO,
        headers=sesion,
        data={"foto": (BytesIO(contenido), nombre, tipo)},
        content_type="multipart/form-data",
    )


# --- Autenticación y autorización -------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [("get", ME), ("patch", ME), ("put", FOTO), ("delete", FOTO)],
)
def test_endpoints_protegidos_sin_token_son_401(client, metodo, ruta):
    respuesta = getattr(client, metodo)(ruta)
    assert respuesta.status_code == 401
    assert respuesta.get_json()["codigo"] == "NO_AUTENTICADO"


def test_token_basura_es_401(client):
    assert client.get(ME, headers=encabezados("basura")).status_code == 401


def test_usuario_suspendido_con_token_valido_es_403(client, app, sesion):
    """RN-19: la suspensión surte efecto de inmediato."""
    assert client.get(ME, headers=sesion).status_code == 200
    with app.app_context():
        db.session.scalar(select(Usuario)).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    respuesta = client.get(ME, headers=sesion)
    assert respuesta.status_code == 403
    assert respuesta.get_json()["codigo"] == "CUENTA_SUSPENDIDA"


def test_requiere_roles_rechaza_rol_incorrecto_y_acepta_el_correcto(client, sesion, sesion_artista):
    ruta = "/_test/solo-artista"
    assert client.get(ruta).status_code == 401
    respuesta = client.get(ruta, headers=sesion)
    assert respuesta.status_code == 403
    assert respuesta.get_json()["codigo"] == "ROL_NO_PERMITIDO"
    assert client.get(ruta, headers=sesion_artista).status_code == 200


def test_requiere_roles_usa_el_rol_de_la_bd_no_el_del_token(client, app, sesion_artista):
    """§4: el claim `rol` es solo una pista; si el rol cambió en la BD, manda la BD."""
    ruta = "/_test/solo-artista"
    assert client.get(ruta, headers=sesion_artista).status_code == 200
    with app.app_context():
        usuario = db.session.scalar(select(Usuario))
        usuario.rol = Rol.USUARIO
        db.session.commit()
    assert client.get(ruta, headers=sesion_artista).status_code == 403


def test_token_de_usuario_borrado_es_401(client, app, sesion):
    with app.app_context():
        db.session.delete(db.session.scalar(select(Usuario)))
        db.session.commit()
    assert client.get(ME, headers=sesion).status_code == 401


# --- Perfil -----------------------------------------------------------------------------------


def test_get_me(client, sesion):
    cuerpo = client.get(ME, headers=sesion).get_json()
    assert cuerpo["email"] == "ana@example.com"
    assert "password_hash" not in cuerpo


def test_patch_me_edita_nombre_y_biografia(client, sesion):
    respuesta = client.patch(ME, headers=sesion, json={"nombre": "Ana María", "biografia": "Hola"})
    assert respuesta.status_code == 200
    assert (respuesta.get_json()["nombre"], respuesta.get_json()["biografia"]) == (
        "Ana María",
        "Hola",
    )
    assert client.get(ME, headers=sesion).get_json()["biografia"] == "Hola"


def test_patch_me_puede_borrar_la_biografia(client, sesion):
    client.patch(ME, headers=sesion, json={"biografia": "Hola"})
    respuesta = client.patch(ME, headers=sesion, json={"biografia": None})
    assert respuesta.get_json()["biografia"] is None


def test_patch_me_valida_longitudes_y_campos_desconocidos(client, sesion):
    assert client.patch(ME, headers=sesion, json={"biografia": "x" * 1001}).status_code == 422
    assert client.patch(ME, headers=sesion, json={"nombre": ""}).status_code == 422
    assert client.patch(ME, headers=sesion, json={"rol": "ADMIN"}).status_code == 422
    assert client.patch(ME, headers=sesion, json={"estado": "ACTIVO"}).status_code == 422
    assert client.get(ME, headers=sesion).get_json()["rol"] == "USUARIO"


def test_patch_me_con_cuerpo_vacio_no_cambia_nada(client, sesion):
    assert client.patch(ME, headers=sesion, json={}).status_code == 200


def test_artista_edita_su_nombre_artistico(client, sesion_artista):
    respuesta = client.patch(ME, headers=sesion_artista, json={"nombre_artistico": "Luz 2.0"})
    assert respuesta.get_json()["nombre_artistico"] == "Luz 2.0"
    # Conservar el propio nombre no es conflicto.
    assert (
        client.patch(ME, headers=sesion_artista, json={"nombre_artistico": "luz 2.0"}).status_code
        == 200
    )


def test_usuario_normal_no_puede_tener_nombre_artistico(client, sesion):
    respuesta = client.patch(ME, headers=sesion, json={"nombre_artistico": "Yo"})
    assert respuesta.status_code == 422
    assert "nombre_artistico" in respuesta.get_json()["detalles"]


def test_nombre_artistico_en_uso_es_409(client, app, sesion_artista):
    with app.app_context():
        crear_usuario("otro@example.com", Rol.ARTISTA, "Otro Grupo")
    respuesta = client.patch(ME, headers=sesion_artista, json={"nombre_artistico": "otro grupo"})
    assert respuesta.status_code == 409
    assert respuesta.get_json()["codigo"] == "NOMBRE_ARTISTICO_EN_USO"


# --- Foto de perfil (RN-18) -------------------------------------------------------------------


def archivo_de(app, foto_url: str) -> Path:
    return Path(app.config["UPLOAD_DIR"]) / foto_url.removeprefix("/uploads/")


def test_foto_valida_se_convierte_a_webp_512_sin_exif(client, app, sesion):
    exif = Image.Exif()
    exif[0x0112] = 6  # orientación: rotar 90°
    exif[0x010F] = "MarcaCamara"  # fabricante: dato que no debe sobrevivir
    contenido = imagen_bytes("JPEG", (800, 400), exif=exif.tobytes())

    respuesta = subir(client, sesion, contenido, "mi foto.jpg", "image/jpeg")

    assert respuesta.status_code == 200
    foto_url = respuesta.get_json()["foto_url"]
    assert foto_url.startswith("/uploads/perfiles/") and foto_url.endswith(".webp")
    with Image.open(archivo_de(app, foto_url)) as guardada:
        assert guardada.format == "WEBP"
        assert guardada.size == (512, 512)
        assert len(guardada.getexif()) == 0
    assert b"MarcaCamara" not in archivo_de(app, foto_url).read_bytes()


def test_foto_se_sirve_por_la_ruta_estatica(client, sesion):
    foto_url = subir(client, sesion, imagen_bytes()).get_json()["foto_url"]
    respuesta = client.get(foto_url)
    assert respuesta.status_code == 200
    assert respuesta.data[:4] == b"RIFF"


def test_foto_acepta_png_y_webp_aunque_el_nombre_mienta(client, sesion):
    assert subir(client, sesion, imagen_bytes("PNG"), "x.exe", "text/plain").status_code == 200
    assert subir(client, sesion, imagen_bytes("WEBP"), "x.jpg", "image/jpeg").status_code == 200


def test_reemplazar_foto_borra_la_anterior(client, app, sesion):
    primera = subir(client, sesion, imagen_bytes()).get_json()["foto_url"]
    segunda = subir(client, sesion, imagen_bytes()).get_json()["foto_url"]
    assert primera != segunda
    assert not archivo_de(app, primera).exists()
    assert archivo_de(app, segunda).exists()


def test_quitar_foto_borra_el_archivo(client, app, sesion):
    foto_url = subir(client, sesion, imagen_bytes()).get_json()["foto_url"]
    respuesta = client.delete(FOTO, headers=sesion)
    assert respuesta.status_code == 200 and respuesta.get_json()["foto_url"] is None
    assert not archivo_de(app, foto_url).exists()
    assert client.delete(FOTO, headers=sesion).status_code == 200  # idempotente


@pytest.mark.parametrize(
    "contenido",
    [b"esto no es una imagen", b"", imagen_bytes("PNG")[:40], b"GIF89a" + b"\x00" * 50],
    ids=["texto", "vacio", "png-truncado", "gif-falso"],
)
def test_foto_invalida_es_422(client, sesion, contenido):
    respuesta = subir(client, sesion, contenido)
    assert respuesta.status_code == 422
    assert client.get(ME, headers=sesion).get_json()["foto_url"] is None


def test_foto_en_formato_no_permitido_es_422(client, sesion):
    respuesta = subir(client, sesion, imagen_bytes("GIF"), "a.gif", "image/gif")
    assert respuesta.status_code == 422
    assert respuesta.get_json()["codigo"] == "FOTO_INVALIDA"


def test_foto_de_mas_de_2_mb_es_422(client, sesion):
    grande = imagen_bytes("BMP", (900, 900))  # BMP sin compresión: ~2,4 MB
    assert 2 * 1024 * 1024 < len(grande) < 3 * 1024 * 1024
    respuesta = subir(client, sesion, grande, "grande.bmp", "image/bmp")
    assert respuesta.status_code == 422
    assert respuesta.get_json()["codigo"] == "FOTO_INVALIDA"


def test_foto_muy_por_encima_del_tope_de_la_peticion_es_413(client, sesion):
    respuesta = subir(client, sesion, b"0" * (4 * 1024 * 1024))
    assert respuesta.status_code == 413
    assert respuesta.get_json()["codigo"] == "ARCHIVO_DEMASIADO_GRANDE"


def test_foto_sin_campo_es_422(client, sesion):
    respuesta = client.put(FOTO, headers=sesion, data={}, content_type="multipart/form-data")
    assert respuesta.status_code == 422
    assert "foto" in respuesta.get_json()["detalles"]


def test_foto_de_un_usuario_no_toca_la_de_otro(client, app, sesion, sesion_artista):
    suya = subir(client, sesion, imagen_bytes()).get_json()["foto_url"]
    subir(client, sesion_artista, imagen_bytes())
    assert archivo_de(app, suya).exists()


def test_ruta_estatica_no_permite_salir_del_directorio(client):
    assert client.get("/uploads/../../.env").status_code == 404
    assert client.get("/uploads/%2e%2e/%2e%2e/.env").status_code == 404
