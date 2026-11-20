"""Fase 8: seguridad (§12), formato uniforme de errores (§13) y auditoría de logs."""

import io
import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import create_app
from app.common.enums import Rol
from app.extensions import limiter
from tests.conftest import VALOR_SENSIBLE_EN_SQL
from tests.factories import PASSWORD, crear_usuario, encabezados, iniciar_sesion

CLAVES_DE_ERROR = {"codigo", "mensaje", "detalles"}
INEXISTENTE = "00000000-0000-4000-8000-000000000000"
REGISTRO = {
    "nombre": "Ana",
    "apellido": "Gómez",
    "email": "ana@example.com",
    "password": PASSWORD,
    "rol": "USUARIO",
    "acepta_tratamiento_datos": True,
}


def verificar_error(respuesta, status, codigo=None) -> dict:
    """El único formato de error de la API (§13): `{codigo, mensaje, detalles}`, sin trazas."""
    assert respuesta.status_code == status, respuesta.get_data(as_text=True)
    assert respuesta.is_json, respuesta.get_data(as_text=True)
    cuerpo = respuesta.get_json()
    assert set(cuerpo) == CLAVES_DE_ERROR, cuerpo
    assert isinstance(cuerpo["codigo"], str) and cuerpo["codigo"] == cuerpo["codigo"].upper()
    assert isinstance(cuerpo["mensaje"], str) and cuerpo["mensaje"]
    assert isinstance(cuerpo["detalles"], dict)
    texto = respuesta.get_data(as_text=True)
    assert "Traceback" not in texto and 'File "' not in texto
    if codigo:
        assert cuerpo["codigo"] == codigo
    return cuerpo


@pytest.fixture
def ana(app):
    with app.app_context():
        crear_usuario("ana@example.com")
    return SimpleNamespace(email="ana@example.com")


@pytest.fixture
def sesion(client, ana):
    return encabezados(iniciar_sesion(client)["access_token"])


@pytest.fixture
def limites_activos(monkeypatch):
    """Enciende el rate limit (las pruebas lo apagan por defecto) con contadores a cero."""
    monkeypatch.setattr(limiter, "enabled", True)
    limiter.reset()
    yield
    limiter.reset()


# --- Cabeceras de seguridad ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "ruta", "estado"),
    [
        ("get", "/api/v1/health", 200),
        ("get", "/api/v1/eventos", 200),
        ("get", "/api/docs", 200),
        ("get", "/api/openapi.json", 200),
        ("get", "/api/v1/usuarios/me", 401),
        ("get", "/api/v1/no-existe", 404),
        ("delete", "/api/v1/health", 405),
        ("post", "/api/v1/auth/login", 422),
        ("get", "/uploads/no-existe.webp", 404),
        ("get", "/_test/falla", 500),
    ],
)
def test_todas_las_respuestas_llevan_nosniff(client, metodo, ruta, estado):
    respuesta = getattr(client, metodo)(ruta)
    assert respuesta.status_code == estado
    assert respuesta.headers["X-Content-Type-Options"] == "nosniff"


def test_las_respuestas_autenticadas_no_se_cachean(client, sesion):
    respuesta = client.get("/api/v1/usuarios/me", headers=sesion)
    assert respuesta.status_code == 200
    assert respuesta.headers["Cache-Control"] == "no-store"


def test_tambien_las_peticiones_autenticadas_que_fallan_no_se_cachean(client):
    for encabezado in (encabezados("token-basura"), encabezados("a.b.c")):
        respuesta = client.get("/api/v1/usuarios/me", headers=encabezado)
        assert respuesta.status_code == 401
        assert respuesta.headers["Cache-Control"] == "no-store"


def test_las_respuestas_que_entregan_tokens_no_se_cachean(client, ana):
    registro = client.post("/api/v1/auth/registro", json={**REGISTRO, "email": "nuevo@example.com"})
    login = client.post("/api/v1/auth/login", json={"email": ana.email, "password": PASSWORD})
    refresco = client.post(
        "/api/v1/auth/refresh", headers=encabezados(login.get_json()["refresh_token"])
    )
    for respuesta in (registro, login, refresco):
        assert respuesta.status_code in (200, 201)
        assert respuesta.headers["Cache-Control"] == "no-store"


def test_las_credenciales_de_emision_no_se_cachean(app, client):
    with app.app_context():
        from tests.factories import crear_evento

        artista = crear_usuario("luz@example.com", Rol.ARTISTA, "Luz Band")
        evento_id = crear_evento(artista).id
    sesion = encabezados(iniciar_sesion(client, "luz@example.com")["access_token"])
    respuesta = client.post(f"/api/v1/eventos/{evento_id}/transmision/credenciales", headers=sesion)
    assert respuesta.status_code == 200
    assert respuesta.headers["Cache-Control"] == "no-store"


# --- Rate limiting -----------------------------------------------------------------------------


def test_login_se_limita_a_5_por_minuto_por_ip(client, ana, limites_activos):
    """★ §12: la 6.ª petición del minuto desde la misma IP recibe 429."""
    intento = {"email": ana.email, "password": "incorrecta1"}
    codigos = [client.post("/api/v1/auth/login", json=intento).status_code for _ in range(5)]
    assert codigos == [401] * 5

    bloqueado = client.post("/api/v1/auth/login", json=intento)
    verificar_error(bloqueado, 429, "DEMASIADAS_PETICIONES")
    # El límite es por IP: otra IP no se ve afectada.
    otra_ip = client.post(
        "/api/v1/auth/login", json=intento, environ_overrides={"REMOTE_ADDR": "10.9.9.9"}
    )
    assert otra_ip.status_code == 401


def test_registro_se_limita_a_10_por_hora_por_ip(client, limites_activos):
    """★ §12."""
    invalido = {"nombre": "x"}  # 422 rápido: lo que cuenta es el intento, no el resultado
    codigos = [client.post("/api/v1/auth/registro", json=invalido).status_code for _ in range(10)]
    assert codigos == [422] * 10
    verificar_error(
        client.post("/api/v1/auth/registro", json=invalido), 429, "DEMASIADAS_PETICIONES"
    )
    otra_ip = client.post(
        "/api/v1/auth/registro", json=invalido, environ_overrides={"REMOTE_ADDR": "10.9.9.9"}
    )
    assert otra_ip.status_code == 422


def test_webhook_de_wompi_se_limita_a_120_por_minuto(client, limites_activos):
    """★ §12."""
    codigos = [client.post("/api/v1/webhooks/wompi", json={}).status_code for _ in range(120)]
    assert set(codigos) == {401}
    verificar_error(client.post("/api/v1/webhooks/wompi", json={}), 429, "DEMASIADAS_PETICIONES")


def test_los_limites_de_un_endpoint_no_afectan_a_los_demas(client, ana, limites_activos):
    for _ in range(6):
        client.post("/api/v1/auth/login", json={"email": ana.email, "password": "mala1234"})
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/eventos").status_code == 200


def test_el_429_lleva_retry_after(client, ana, limites_activos):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": ana.email, "password": "mala1234"})
    bloqueado = client.post("/api/v1/auth/login", json={"email": ana.email, "password": "x"})
    assert bloqueado.status_code == 429 and "Retry-After" in bloqueado.headers


# --- Configuración de producción ---------------------------------------------------------------

SECRETOS = ("SECRET_KEY", "JWT_SECRET_KEY", "PLAYBACK_TOKEN_SECRET")
POR_DEFECTO = {
    "SECRET_KEY": "cambia-esto",
    "JWT_SECRET_KEY": "cambia-esto-tambien",
    "PLAYBACK_TOKEN_SECRET": "cambia-esto-3",
}


@pytest.fixture
def entorno_de_produccion(monkeypatch):
    """Entorno aislado del `.env` local, con secretos propios y CORS acotado."""
    monkeypatch.setattr("app.load_dotenv", lambda *args, **kwargs: None)
    for nombre in SECRETOS:
        monkeypatch.setenv(nombre, f"valor-largo-y-unico-para-{nombre.lower()}")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.ejemplo.co")
    monkeypatch.delenv("ENABLE_DEV_ENDPOINTS", raising=False)
    monkeypatch.delenv("ENFORCE_START_WINDOW", raising=False)
    return monkeypatch


def test_produccion_arranca_con_secretos_propios(entorno_de_produccion):
    produccion = create_app("production")
    assert produccion.config["APP_ENV"] == "production"


@pytest.mark.parametrize("secreto", SECRETOS)
def test_produccion_no_arranca_si_un_secreto_tiene_el_valor_por_defecto(
    entorno_de_produccion, secreto
):
    """★ §12: cualquiera de los tres, con el valor del `.env.example`."""
    entorno_de_produccion.setenv(secreto, POR_DEFECTO[secreto])
    with pytest.raises(RuntimeError, match=secreto):
        create_app("production")


@pytest.mark.parametrize("secreto", SECRETOS)
def test_produccion_no_arranca_con_un_secreto_demasiado_corto(entorno_de_produccion, secreto):
    """HS256 exige claves de 32 bytes o más."""
    entorno_de_produccion.setenv(secreto, "a" * 31)
    with pytest.raises(RuntimeError, match="al menos 32"):
        create_app("production")
    entorno_de_produccion.setenv(secreto, "a" * 32)
    assert create_app("production").config["APP_ENV"] == "production"


@pytest.mark.parametrize("secreto", SECRETOS)
def test_produccion_no_arranca_con_un_secreto_vacio(entorno_de_produccion, secreto):
    entorno_de_produccion.setenv(secreto, "")
    with pytest.raises(RuntimeError, match=secreto):
        create_app("production")


def test_produccion_fuerza_dev_endpoints_apagados_y_ventana_de_inicio_encendida(
    entorno_de_produccion,
):
    """★ Aunque el entorno pida lo contrario."""
    entorno_de_produccion.setenv("ENABLE_DEV_ENDPOINTS", "true")
    entorno_de_produccion.setenv("ENFORCE_START_WINDOW", "false")
    produccion = create_app("production")
    assert produccion.config["ENABLE_DEV_ENDPOINTS"] is False
    assert produccion.config["ENFORCE_START_WINDOW"] is True


def test_en_produccion_el_endpoint_de_desarrollo_responde_404(entorno_de_produccion):
    entorno_de_produccion.setenv("ENABLE_DEV_ENDPOINTS", "true")
    produccion = create_app("production")
    respuesta = produccion.test_client().post(
        f"/api/v1/dev/eventos/{INEXISTENTE}/audiencia-simulada", json={"espectadores": 5}
    )
    verificar_error(respuesta, 404)


@pytest.mark.parametrize("origenes", ["*", "https://a.co,*", " * "])
def test_produccion_no_acepta_cors_con_comodin(entorno_de_produccion, origenes):
    entorno_de_produccion.setenv("CORS_ORIGINS", origenes)
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        create_app("production")


def test_produccion_mantiene_activos_los_limites_de_peticiones(entorno_de_produccion):
    assert create_app("production").config["RATELIMIT_ENABLED"] is True


# --- CORS y tamaño máximo ----------------------------------------------------------------------


def test_cors_solo_permite_la_lista_blanca(app, client):
    permitido = app.config["CORS_ORIGINS"][0]
    assert "*" not in app.config["CORS_ORIGINS"]

    bueno = client.get("/api/v1/health", headers={"Origin": permitido})
    assert bueno.headers["Access-Control-Allow-Origin"] == permitido

    malo = client.get("/api/v1/health", headers={"Origin": "https://sitio-malicioso.example"})
    assert malo.status_code == 200
    assert "Access-Control-Allow-Origin" not in malo.headers


def test_cors_responde_el_preflight_solo_a_origenes_permitidos(app, client):
    permitido = app.config["CORS_ORIGINS"][0]
    pedido = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    }
    bueno = client.options("/api/v1/auth/login", headers={"Origin": permitido, **pedido})
    assert bueno.headers["Access-Control-Allow-Origin"] == permitido
    assert "POST" in bueno.headers["Access-Control-Allow-Methods"]

    malo = client.options(
        "/api/v1/auth/login", headers={"Origin": "https://sitio-malicioso.example", **pedido}
    )
    assert "Access-Control-Allow-Origin" not in malo.headers


def test_el_tamano_maximo_de_la_peticion_sigue_a_max_upload_mb(app, monkeypatch):
    mega = 1024 * 1024
    assert app.config["MAX_CONTENT_LENGTH"] == (app.config["MAX_UPLOAD_MB"] + 1) * mega
    monkeypatch.setenv("MAX_UPLOAD_MB", "5")
    otra = create_app("testing")
    assert otra.config["MAX_UPLOAD_MB"] == 5
    assert otra.config["MAX_CONTENT_LENGTH"] == 6 * mega  # tope duro: el límite + 1 MB


# --- Formato uniforme de errores ---------------------------------------------------------------


def test_400_json_mal_formado(client):
    respuesta = client.post(
        "/api/v1/auth/login", data='{"email": ', content_type="application/json"
    )
    assert respuesta.status_code in (400, 422)
    verificar_error(respuesta, respuesta.status_code)


def test_401_sin_token_token_invalido_vencido_y_de_tipo_incorrecto(app, client, ana):
    verificar_error(client.get("/api/v1/usuarios/me"), 401, "NO_AUTENTICADO")
    verificar_error(
        client.get("/api/v1/usuarios/me", headers=encabezados("no.es.un.jwt")),
        401,
        "NO_AUTENTICADO",
    )
    with app.app_context():
        vencido = create_access_token(
            "00000000-0000-4000-8000-000000000001", expires_delta=timedelta(seconds=-5)
        )
    verificar_error(
        client.get("/api/v1/usuarios/me", headers=encabezados(vencido)), 401, "NO_AUTENTICADO"
    )
    refresco = iniciar_sesion(client)["refresh_token"]
    verificar_error(
        client.get("/api/v1/usuarios/me", headers=encabezados(refresco)), 401, "NO_AUTENTICADO"
    )
    verificar_error(
        client.post("/api/v1/auth/login", json={"email": ana.email, "password": "mala1234"}),
        401,
        "CREDENCIALES_INVALIDAS",
    )


def test_403_rol_y_cuenta_suspendida(app, client, sesion):
    verificar_error(client.get("/api/v1/admin/dashboard", headers=sesion), 403, "ROL_NO_PERMITIDO")
    with app.app_context():
        from app.common.enums import EstadoUsuario
        from app.extensions import db
        from app.modules.usuarios.models import Usuario

        db.session.scalar(select(Usuario)).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    verificar_error(client.get("/api/v1/usuarios/me", headers=sesion), 403, "CUENTA_SUSPENDIDA")


def test_404_ruta_inexistente_y_recurso_inexistente(client):
    verificar_error(client.get("/api/v1/no-existe"), 404, "RECURSO_NO_ENCONTRADO")
    verificar_error(client.get("/uploads/no/existe.webp"), 404, "RECURSO_NO_ENCONTRADO")
    verificar_error(client.get(f"/api/v1/eventos/{INEXISTENTE}"), 404, "EVENTO_NO_ENCONTRADO")


def test_405_metodo_no_permitido(client):
    verificar_error(client.delete("/api/v1/health"), 405, "METODO_NO_PERMITIDO")
    verificar_error(client.put("/api/v1/auth/login", json={}), 405, "METODO_NO_PERMITIDO")


def test_409_conflicto(client, ana):
    verificar_error(client.post("/api/v1/auth/registro", json=REGISTRO), 409, "EMAIL_YA_REGISTRADO")


def test_413_peticion_demasiado_grande(client, sesion):
    enorme = b"0" * (4 * 1024 * 1024)
    respuesta = client.put(
        "/api/v1/usuarios/me/foto",
        headers=sesion,
        data={"foto": (io.BytesIO(enorme), "f.png", "image/png")},
        content_type="multipart/form-data",
    )
    verificar_error(respuesta, 413, "ARCHIVO_DEMASIADO_GRANDE")


def test_415_o_422_cuerpo_que_no_es_json(client):
    respuesta = client.post("/api/v1/auth/login", data="email=a", content_type="text/plain")
    assert respuesta.status_code in (415, 422)
    verificar_error(respuesta, respuesta.status_code)


def test_422_validacion_con_detalles_por_campo(client):
    cuerpo = verificar_error(
        client.post("/api/v1/auth/registro", json={"nombre": "", "email": "x", "extra": 1}),
        422,
        "VALIDACION",
    )
    assert {"nombre", "email", "extra"} <= set(cuerpo["detalles"])


def test_422_de_regla_de_negocio(client):
    cuerpo = verificar_error(
        client.post("/api/v1/auth/registro", json={**REGISTRO, "acepta_tratamiento_datos": False}),
        422,
        "CONSENTIMIENTO_REQUERIDO",
    )
    assert cuerpo["detalles"] == {}


def test_429_limite_de_peticiones(client, ana, limites_activos):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": ana.email, "password": "mala1234"})
    verificar_error(
        client.post("/api/v1/auth/login", json={"email": ana.email, "password": "x"}),
        429,
        "DEMASIADAS_PETICIONES",
    )


def test_500_error_inesperado_sin_trazas_ni_mensaje_interno(client, caplog):
    with caplog.at_level(logging.ERROR):
        respuesta = client.get("/_test/falla")
    cuerpo = verificar_error(respuesta, 500, "ERROR_INTERNO")
    assert cuerpo["mensaje"] == "Error interno del servidor."
    assert "fallo inesperado de prueba" not in respuesta.get_data(as_text=True)
    assert "RuntimeError" not in respuesta.get_data(as_text=True)
    assert "Error no controlado" in caplog.text  # sí queda en el log del servidor


def test_503_base_de_datos_caida(client, monkeypatch):
    def cae(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("sin conexion"))

    falsa = SimpleNamespace(session=SimpleNamespace(execute=cae, rollback=lambda: None))
    monkeypatch.setattr("app.modules.sistema.service.db", falsa)
    verificar_error(client.get("/api/v1/health"), 503, "BASE_DE_DATOS_NO_DISPONIBLE")


def test_health_responde_ok_con_la_bd(client):
    respuesta = client.get("/api/v1/health")
    assert respuesta.status_code == 200
    assert respuesta.get_json() == {"estado": "ok", "base_de_datos": "ok"}


# --- Auditoría de logs y respuestas ------------------------------------------------------------


def test_un_error_de_sql_no_filtra_los_valores_enlazados(client, caplog):
    """`hide_parameters`: ni el log ni la respuesta incluyen el valor que la BD rechazó."""
    with caplog.at_level(logging.DEBUG):
        respuesta = client.get("/_test/falla-sql")
    verificar_error(respuesta, 500, "ERROR_INTERNO")
    assert VALOR_SENSIBLE_EN_SQL not in respuesta.get_data(as_text=True)
    assert VALOR_SENSIBLE_EN_SQL not in caplog.text
    assert "StringDataRightTruncation" in caplog.text or "DataError" in caplog.text  # sí se loguea


def test_los_errores_de_validacion_no_repiten_lo_enviado(client, caplog):
    secreto = "clave-secreta-sin-digito"
    with caplog.at_level(logging.DEBUG):
        respuesta = client.post("/api/v1/auth/registro", json={**REGISTRO, "password": secreto})
    verificar_error(respuesta, 422)
    assert secreto not in respuesta.get_data(as_text=True)
    assert secreto not in caplog.text


def test_el_login_fallido_no_repite_ni_registra_la_contrasena(client, ana, caplog):
    secreto = "ContrasenaIncorrecta99"
    with caplog.at_level(logging.DEBUG):
        respuesta = client.post(
            "/api/v1/auth/login", json={"email": ana.email, "password": secreto}
        )
    verificar_error(respuesta, 401, "CREDENCIALES_INVALIDAS")
    assert secreto not in respuesta.get_data(as_text=True) and secreto not in caplog.text


def test_un_token_invalido_no_se_repite_en_la_respuesta_ni_en_los_logs(client, caplog):
    token = "eyJhbGciOiJIUzI1NiJ9.eyJ1c3VhcmlvIjoicG9yLWZhdm9yIn0.firma-falsa"
    with caplog.at_level(logging.DEBUG):
        respuesta = client.get("/api/v1/usuarios/me", headers=encabezados(token))
    verificar_error(respuesta, 401)
    assert token not in respuesta.get_data(as_text=True) and token not in caplog.text


def test_el_numero_de_cuenta_completo_nunca_vuelve_en_la_respuesta(app, client):
    """Una cuenta completa se rechaza (422) y el error no la repite."""
    with app.app_context():
        crear_usuario("luz@example.com", Rol.ARTISTA, "Luz Band")
    sesion = encabezados(iniciar_sesion(client, "luz@example.com")["access_token"])
    cuenta = "9876543210123456"
    respuesta = client.post(
        "/api/v1/artistas/me/retiros",
        headers=sesion,
        json={
            "monto_cop": 60_000,
            "banco": "Banco",
            "tipo_cuenta": "AHORROS",
            "cuenta_ultimos4": cuenta,
        },
    )
    verificar_error(respuesta, 422, "VALIDACION")
    assert cuenta not in respuesta.get_data(as_text=True)
