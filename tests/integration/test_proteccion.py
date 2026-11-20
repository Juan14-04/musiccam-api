"""Barrido de protección: todo endpoint que §8 no marca como público exige autenticación."""

import re

import pytest

from tests.integration.test_endurecimiento import INEXISTENTE, verificar_error

# Endpoints públicos o que se autentican de otra forma (§4, §8).
PUBLICOS = {
    ("GET", "/api/v1/health"),
    ("POST", "/api/v1/auth/registro"),
    ("POST", "/api/v1/auth/login"),
    ("GET", "/api/v1/eventos"),
    ("GET", "/api/v1/eventos/<uuid:evento_id>"),
    ("GET", "/api/v1/artistas"),
    ("GET", "/api/v1/artistas/<uuid:artista_id>"),
    ("POST", "/api/v1/webhooks/wompi"),  # se autentica con la firma del evento
}


@pytest.fixture(autouse=True)
def _con_endpoints_de_desarrollo(app, monkeypatch):
    """Sin esto, el endpoint `/dev/...` respondería 404 en vez de 401 si el entorno lo apaga."""
    monkeypatch.setitem(app.config, "ENABLE_DEV_ENDPOINTS", True)


def reglas_de_la_api(app):
    for regla in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if regla.rule.startswith("/api/v1"):
            for metodo in sorted(regla.methods - {"HEAD", "OPTIONS"}):
                yield metodo, regla.rule


def url_concreta(plantilla: str) -> str:
    return re.sub(r"<uuid:\w+>", INEXISTENTE, re.sub(r"<string:\w+>", "referencia-x", plantilla))


def test_hay_47_endpoints_protegidos_y_8_publicos(app):
    todos = set(reglas_de_la_api(app))
    assert len(todos) == 55
    assert PUBLICOS <= todos
    assert len(todos - PUBLICOS) == 47


def test_sin_token_todo_endpoint_protegido_responde_401(app, client):
    """★ §4 / §15: cada endpoint protegido, sin token → 401 con el formato uniforme."""
    protegidos = [(m, r) for m, r in reglas_de_la_api(app) if (m, r) not in PUBLICOS]
    assert len(protegidos) == 47
    for metodo, regla in protegidos:
        respuesta = getattr(client, metodo.lower())(url_concreta(regla), json={})
        verificar_error(respuesta, 401, "NO_AUTENTICADO")


@pytest.mark.parametrize(("metodo", "regla"), sorted(PUBLICOS))
def test_los_publicos_no_piden_sesion(client, metodo, regla):
    respuesta = getattr(client, metodo.lower())(url_concreta(regla), json={})
    assert respuesta.status_code != 401 or respuesta.get_json()["codigo"] != "NO_AUTENTICADO"


def test_con_un_refresh_token_no_se_entra_a_ningun_endpoint_de_acceso(app, client):
    from tests.factories import crear_usuario, encabezados, iniciar_sesion

    with app.app_context():
        crear_usuario("ana@example.com")
    refresco = encabezados(iniciar_sesion(client)["refresh_token"])
    for metodo, regla in reglas_de_la_api(app):
        if (metodo, regla) in PUBLICOS or regla.endswith("/auth/refresh"):
            continue
        respuesta = getattr(client, metodo.lower())(url_concreta(regla), headers=refresco, json={})
        verificar_error(respuesta, 401, "NO_AUTENTICADO")
