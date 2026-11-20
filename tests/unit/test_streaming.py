"""Proveedor de streaming simulado: credenciales, URLs y token de reproducción (§11.1)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest

from app import create_app
from app.common.clock import ahora_utc, fijar_ahora
from app.common.enums import TipoReproduccion
from app.infrastructure.streaming import FABRICAS, obtener_provider
from app.infrastructure.streaming.simulado import StreamingSimulado

SECRETO = "secreto-de-reproduccion-con-32-caracteres-o-mas"
EVENTO = SimpleNamespace(id="3f2a9c1e-7b0d-4c55-9a1e-2d6f8b1c0a11")
USUARIO = SimpleNamespace(id="9d1b7e52-0c44-4a8e-b3a5-6f1c2e8d7a90")
AHORA = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)


@pytest.fixture
def provider():
    return StreamingSimulado(
        "http://media.test:8888/", "rtmp://ingesta.test:1935/live/", SECRETO, 10
    )


def decodificar(token: str, **opciones) -> dict:
    return jwt.decode(token, SECRETO, algorithms=["HS256"], **opciones)


def test_credenciales_con_url_rtmp_y_clave_aleatoria(provider):
    primera = provider.generar_credenciales(EVENTO)
    segunda = provider.generar_credenciales(EVENTO)
    assert primera.rtmp_url == f"rtmp://ingesta.test:1935/live/{EVENTO.id}"
    assert len(primera.stream_key) >= 43  # secrets.token_urlsafe(32)
    assert primera.stream_key != segunda.stream_key


def test_urls_de_vivo_y_replay(provider):
    assert provider.url_vivo(EVENTO, "tok") == (
        f"http://media.test:8888/live/{EVENTO.id}/index.m3u8?token=tok"
    )
    assert provider.url_replay(EVENTO, "tok") == (
        f"http://media.test:8888/replay/{EVENTO.id}/index.m3u8?token=tok"
    )


@pytest.mark.parametrize("tipo", [TipoReproduccion.VIVO, TipoReproduccion.REPLAY])
def test_el_token_lleva_evento_usuario_y_tipo(provider, tipo):
    fijar_ahora(AHORA)
    emitido = provider.emitir_token_reproduccion(EVENTO, USUARIO, tipo)
    claims = decodificar(emitido.token, options={"verify_exp": False, "verify_iat": False})
    assert claims["evento_id"] == EVENTO.id
    assert claims["usuario_id"] == USUARIO.id
    assert claims["tipo"] == tipo.value


def test_el_token_expira_a_los_10_minutos_y_expira_en_coincide(provider):
    fijar_ahora(AHORA)
    emitido = provider.emitir_token_reproduccion(EVENTO, USUARIO, TipoReproduccion.VIVO)
    claims = decodificar(emitido.token, options={"verify_exp": False, "verify_iat": False})
    assert claims["exp"] - claims["iat"] == 600
    assert emitido.expira_en == AHORA + timedelta(minutes=10)
    assert int(emitido.expira_en.timestamp()) == claims["exp"]


def test_un_token_vencido_se_rechaza(provider):
    fijar_ahora(ahora_utc() - timedelta(minutes=11))
    emitido = provider.emitir_token_reproduccion(EVENTO, USUARIO, TipoReproduccion.VIVO)
    with pytest.raises(jwt.ExpiredSignatureError):
        decodificar(emitido.token)


def test_un_token_vigente_se_acepta(provider):
    fijar_ahora(ahora_utc())
    emitido = provider.emitir_token_reproduccion(EVENTO, USUARIO, TipoReproduccion.REPLAY)
    assert decodificar(emitido.token)["tipo"] == "REPLAY"


def test_el_token_no_se_valida_con_otro_secreto(provider):
    emitido = provider.emitir_token_reproduccion(EVENTO, USUARIO, TipoReproduccion.VIVO)
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            emitido.token, "otro-secreto-distinto-de-32-caracteres-o-mas", algorithms=["HS256"]
        )


def test_el_ttl_es_configurable():
    corto = StreamingSimulado("http://m", "rtmp://r", SECRETO, 2)
    fijar_ahora(AHORA)
    emitido = corto.emitir_token_reproduccion(EVENTO, USUARIO, TipoReproduccion.VIVO)
    assert emitido.expira_en == AHORA + timedelta(minutes=2)


def test_seleccion_del_proveedor_por_configuracion(app):
    with app.app_context():
        proveedor = obtener_provider()
        assert isinstance(proveedor, StreamingSimulado)
        # El token de reproducción usa su propio secreto, no el de sesión.
        assert app.config["PLAYBACK_TOKEN_SECRET"] != app.config["JWT_SECRET_KEY"]
    assert "simulado" in FABRICAS


def test_proveedor_desconocido_impide_arrancar():
    with pytest.raises(RuntimeError, match="STREAMING_PROVIDER desconocido"):
        create_app("testing", {"STREAMING_PROVIDER": "mediamtx"})
