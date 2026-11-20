"""Auth: registro (RN-01..03), login y refresh."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.common.clock import fijar_ahora
from app.common.enums import EstadoUsuario, Rol
from app.extensions import db
from app.modules.usuarios.models import Usuario
from tests.factories import PASSWORD, crear_usuario, encabezados, iniciar_sesion

REGISTRO = {
    "nombre": "Ana",
    "apellido": "Gómez",
    "email": "ana@example.com",
    "password": PASSWORD,
    "rol": "USUARIO",
    "acepta_tratamiento_datos": True,
}
ARTISTA = {**REGISTRO, "email": "luz@example.com", "rol": "ARTISTA", "nombre_artistico": "Luz Band"}


def registrar(client, **cambios):
    return client.post("/api/v1/auth/registro", json={**REGISTRO, **cambios})


def test_registro_de_usuario_no_expone_secretos(client):
    respuesta = registrar(client)
    cuerpo = respuesta.get_json()
    assert respuesta.status_code == 201
    assert cuerpo["rol"] == "USUARIO" and cuerpo["estado"] == "ACTIVO"
    assert "password" not in cuerpo and "password_hash" not in cuerpo


def test_registro_de_artista_crea_cuenta_activa_sin_intervencion_de_admin(client):
    """★ C1/RQ1: el artista queda ACTIVO y puede iniciar sesión de inmediato."""
    respuesta = client.post("/api/v1/auth/registro", json=ARTISTA)
    assert respuesta.status_code == 201
    assert respuesta.get_json()["nombre_artistico"] == "Luz Band"
    sesion = iniciar_sesion(client, "luz@example.com")
    assert sesion["usuario"]["rol"] == "ARTISTA" and sesion["usuario"]["estado"] == "ACTIVO"


def test_registro_sin_consentimiento_es_422(client):
    """★ RQ7/RN-01."""
    for valor in (False, None):
        datos = {**REGISTRO, "acepta_tratamiento_datos": valor}
        respuesta = client.post("/api/v1/auth/registro", json=datos)
        assert respuesta.status_code == 422
    sin_campo = {k: v for k, v in REGISTRO.items() if k != "acepta_tratamiento_datos"}
    assert client.post("/api/v1/auth/registro", json=sin_campo).status_code == 422


def test_registro_rechazado_por_falta_de_consentimiento_usa_codigo_propio(client, app):
    respuesta = registrar(client, acepta_tratamiento_datos=False)
    assert respuesta.get_json()["codigo"] == "CONSENTIMIENTO_REQUERIDO"
    with app.app_context():
        assert db.session.scalar(select(Usuario)) is None


def test_registro_con_consentimiento_guarda_fecha_y_version(client, app):
    """★ RQ7/RN-01."""
    fijo = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)
    fijar_ahora(fijo)
    assert registrar(client).status_code == 201
    with app.app_context():
        usuario = db.session.scalar(select(Usuario))
        assert usuario.consentimiento_datos_en == fijo
        assert usuario.version_politica_datos == "v1"


def test_no_se_puede_autoregistrar_admin(client):
    respuesta = registrar(client, rol="ADMIN")
    assert respuesta.status_code == 422
    assert "rol" in respuesta.get_json()["detalles"]


def test_artista_exige_nombre_artistico(client):
    respuesta = registrar(client, rol="ARTISTA")
    assert respuesta.status_code == 422
    assert "nombre_artistico" in respuesta.get_json()["detalles"]


def test_usuario_normal_ignora_nombre_artistico(client, app):
    assert registrar(client, nombre_artistico="Nadie").status_code == 201
    with app.app_context():
        assert db.session.scalar(select(Usuario)).nombre_artistico is None


def test_politica_de_password(client):
    """RN-03."""
    for malo in ("corta1", "sololetrasaqui", "12345678", "a" * 129 + "1"):
        assert registrar(client, password=malo).status_code == 422, malo
    assert registrar(client, password="Abcdefg1").status_code == 201


def test_email_duplicado_sin_distinguir_mayusculas_es_409(client):
    assert registrar(client).status_code == 201
    respuesta = registrar(client, email="ANA@Example.com")
    assert respuesta.status_code == 409
    assert respuesta.get_json()["codigo"] == "EMAIL_YA_REGISTRADO"


def test_email_se_guarda_en_minusculas(client, app):
    registrar(client, email="Ana@Example.COM")
    with app.app_context():
        assert db.session.scalar(select(Usuario)).email == "ana@example.com"


def test_nombre_artistico_duplicado_es_409(client):
    assert client.post("/api/v1/auth/registro", json=ARTISTA).status_code == 201
    otro = {**ARTISTA, "email": "otro@example.com", "nombre_artistico": "LUZ band"}
    respuesta = client.post("/api/v1/auth/registro", json=otro)
    assert respuesta.status_code == 409
    assert respuesta.get_json()["codigo"] == "NOMBRE_ARTISTICO_EN_USO"


def test_campos_desconocidos_se_rechazan(client):
    assert registrar(client, es_admin=True).status_code == 422


def test_login_devuelve_tokens_y_usuario(client):
    registrar(client)
    cuerpo = iniciar_sesion(client)
    assert cuerpo["access_token"] and cuerpo["refresh_token"]
    assert cuerpo["usuario"]["email"] == "ana@example.com"


def test_login_no_revela_si_el_correo_existe(client):
    """RN-03."""
    registrar(client)
    mala = client.post("/api/v1/auth/login", json={"email": "ana@example.com", "password": "x"})
    inexistente = client.post(
        "/api/v1/auth/login", json={"email": "no@example.com", "password": "x"}
    )
    assert mala.status_code == inexistente.status_code == 401
    assert mala.get_json() == inexistente.get_json()
    assert mala.get_json()["codigo"] == "CREDENCIALES_INVALIDAS"


def test_login_acepta_email_con_otras_mayusculas(client):
    registrar(client)
    assert iniciar_sesion(client, "ANA@example.com")["usuario"]["email"] == "ana@example.com"


def test_login_de_cuenta_suspendida_es_403(client, app):
    with app.app_context():
        crear_usuario(estado=EstadoUsuario.SUSPENDIDO)
    respuesta = client.post(
        "/api/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD}
    )
    assert respuesta.status_code == 403
    assert respuesta.get_json()["codigo"] == "CUENTA_SUSPENDIDA"


def test_refresh_entrega_access_token_nuevo(client, app):
    registrar(client)
    sesion = iniciar_sesion(client)
    respuesta = client.post("/api/v1/auth/refresh", headers=encabezados(sesion["refresh_token"]))
    assert respuesta.status_code == 200
    nuevo = respuesta.get_json()["access_token"]
    assert client.get("/api/v1/usuarios/me", headers=encabezados(nuevo)).status_code == 200


def test_refresh_rechaza_un_access_token_y_la_ausencia_de_token(client):
    registrar(client)
    sesion = iniciar_sesion(client)
    con_access = client.post("/api/v1/auth/refresh", headers=encabezados(sesion["access_token"]))
    assert con_access.status_code == 401
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_access_token_no_sirve_como_refresh_y_viceversa(client):
    registrar(client)
    sesion = iniciar_sesion(client)
    respuesta = client.get("/api/v1/usuarios/me", headers=encabezados(sesion["refresh_token"]))
    assert respuesta.status_code == 401


def test_refresh_de_cuenta_suspendida_es_403(client, app):
    registrar(client)
    sesion = iniciar_sesion(client)
    with app.app_context():
        db.session.scalar(select(Usuario)).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    respuesta = client.post("/api/v1/auth/refresh", headers=encabezados(sesion["refresh_token"]))
    assert respuesta.status_code == 403
    assert respuesta.get_json()["codigo"] == "CUENTA_SUSPENDIDA"


def test_rol_enum_de_registro_solo_acepta_usuario_o_artista():
    assert {Rol.USUARIO, Rol.ARTISTA} < set(Rol)
