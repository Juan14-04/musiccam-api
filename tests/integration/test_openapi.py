"""Auditoría de Swagger/OpenAPI (§14): todo lo de §8 documentado, con tags, RN y errores."""

import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
BASE = "/api/v1"
BEARER = [{"bearerAuth": []}]
OPCIONAL = [{}, {"bearerAuth": []}]  # token opcional: el endpoint es público pero lo acepta

TAGS = {
    "Sistema",
    "Auth",
    "Usuarios",
    "Artistas",
    "Artista (panel)",
    "Eventos",
    "Transmision",
    "Compras",
    "Admin",
}

# (método, ruta, tag, reglas RN que la descripción debe citar, seguridad esperada)
ENDPOINTS = [
    # Sistema
    ("get", "/health", "Sistema", [], None),
    # §8.1 Auth
    ("post", "/auth/registro", "Auth", ["RN-01", "RN-02", "RN-03"], None),
    ("post", "/auth/login", "Auth", ["RN-03"], None),
    ("post", "/auth/refresh", "Auth", ["RN-19"], BEARER),
    # §8.2 Usuarios
    ("get", "/usuarios/me", "Usuarios", [], BEARER),
    ("patch", "/usuarios/me", "Usuarios", [], BEARER),
    ("put", "/usuarios/me/foto", "Usuarios", ["RN-18"], BEARER),
    ("delete", "/usuarios/me/foto", "Usuarios", ["RN-18"], BEARER),
    ("get", "/usuarios/me/favoritos", "Usuarios", ["RN-21"], BEARER),
    ("put", "/usuarios/me/favoritos/{evento_id}", "Usuarios", ["RN-17", "RN-21"], BEARER),
    ("delete", "/usuarios/me/favoritos/{evento_id}", "Usuarios", ["RN-17"], BEARER),
    ("get", "/usuarios/me/seguidos", "Usuarios", ["RN-17"], BEARER),
    ("put", "/usuarios/me/seguidos/{artista_id}", "Usuarios", ["RN-17"], BEARER),
    ("delete", "/usuarios/me/seguidos/{artista_id}", "Usuarios", ["RN-17"], BEARER),
    ("get", "/usuarios/me/compras", "Usuarios", ["RN-08"], BEARER),
    # §8.3 Artistas públicos
    ("get", "/artistas", "Artistas", ["RN-21"], None),
    ("get", "/artistas/{artista_id}", "Artistas", ["RN-21"], OPCIONAL),
    # §8.4 Panel del artista
    ("get", "/artistas/me/eventos", "Artista (panel)", [], BEARER),
    ("get", "/artistas/me/estadisticas", "Artista (panel)", ["RN-14"], BEARER),
    ("get", "/artistas/me/eventos/{evento_id}/estadisticas", "Artista (panel)", ["RN-14"], BEARER),
    ("get", "/artistas/me/saldo", "Artista (panel)", ["RN-14"], BEARER),
    ("post", "/artistas/me/retiros", "Artista (panel)", ["RN-15"], BEARER),
    ("get", "/artistas/me/retiros", "Artista (panel)", ["RN-16"], BEARER),
    # §8.5 Eventos
    ("get", "/eventos", "Eventos", ["RN-21"], None),
    ("get", "/eventos/{evento_id}", "Eventos", ["RN-21"], OPCIONAL),
    ("post", "/eventos", "Eventos", ["RN-04"], BEARER),
    ("patch", "/eventos/{evento_id}", "Eventos", ["RN-04"], BEARER),
    ("delete", "/eventos/{evento_id}", "Eventos", ["RN-05"], BEARER),
    ("put", "/eventos/{evento_id}/trailer", "Eventos", ["RN-04"], BEARER),
    ("delete", "/eventos/{evento_id}/trailer", "Eventos", ["RN-04"], BEARER),
    ("post", "/eventos/{evento_id}/publicar", "Eventos", ["RN-02", "RN-04", "RN-05"], BEARER),
    ("post", "/eventos/{evento_id}/cancelar", "Eventos", ["RN-05", "RN-13"], BEARER),
    # §8.6 Transmisión
    ("post", "/eventos/{evento_id}/transmision/credenciales", "Transmision", ["RN-11"], BEARER),
    ("post", "/eventos/{evento_id}/transmision/iniciar", "Transmision", ["RN-05", "RN-11"], BEARER),
    ("post", "/eventos/{evento_id}/transmision/finalizar", "Transmision", ["RN-12"], BEARER),
    ("get", "/eventos/{evento_id}/transmision", "Transmision", ["RN-12"], BEARER),
    ("post", "/eventos/{evento_id}/reproduccion/entrar", "Transmision", ["RN-10"], BEARER),
    ("post", "/eventos/{evento_id}/reproduccion/salir", "Transmision", ["RN-10"], BEARER),
    ("post", "/dev/eventos/{evento_id}/audiencia-simulada", "Transmision", [], BEARER),
    # §8.7 Compras y pagos
    ("post", "/eventos/{evento_id}/compras", "Compras", ["RN-06", "RN-07", "RN-08"], BEARER),
    ("get", "/compras/{compra_id}", "Compras", [], BEARER),
    ("post", "/pagos/wompi/simulador/{referencia}", "Compras", ["RN-09"], BEARER),
    ("post", "/webhooks/wompi", "Compras", ["RN-09"], None),
    # §8.8 Admin
    ("get", "/admin/dashboard", "Admin", [], BEARER),
    ("get", "/admin/usuarios", "Admin", [], BEARER),
    ("get", "/admin/usuarios/{usuario_id}", "Admin", [], BEARER),
    ("patch", "/admin/usuarios/{usuario_id}/estado", "Admin", ["RN-19", "RN-20"], BEARER),
    ("patch", "/admin/usuarios/{usuario_id}/rol", "Admin", ["RN-20"], BEARER),
    ("get", "/admin/eventos", "Admin", [], BEARER),
    ("post", "/admin/eventos/{evento_id}/suspender", "Admin", ["RN-07", "RN-10", "RN-21"], BEARER),
    ("post", "/admin/eventos/{evento_id}/reactivar", "Admin", [], BEARER),
    ("get", "/admin/compras", "Admin", [], BEARER),
    ("get", "/admin/retiros", "Admin", [], BEARER),
    ("post", "/admin/retiros/{retiro_id}/aprobar", "Admin", ["RN-16"], BEARER),
    ("post", "/admin/retiros/{retiro_id}/rechazar", "Admin", ["RN-16"], BEARER),
    ("get", "/admin/configuracion", "Admin", [], BEARER),
]


@pytest.fixture(scope="module")
def spec(app):
    return app.test_client().get("/api/openapi.json").get_json()


def operacion(spec, metodo, ruta):
    return spec["paths"][BASE + ruta][metodo]


def test_openapi_json_responde_200_y_swagger_ui_lo_carga(client):
    respuesta = client.get("/api/openapi.json")
    assert respuesta.status_code == 200 and respuesta.is_json
    assert respuesta.get_json()["openapi"] == "3.0.3"
    docs = client.get("/api/docs")
    assert docs.status_code == 200 and b"openapi.json" in docs.data


def test_los_tags_son_exactamente_los_de_la_seccion_8(spec):
    assert {t["name"] for t in spec["tags"]} == TAGS


def test_el_esquema_bearer_esta_registrado_una_sola_vez(spec):
    esquemas = spec["components"]["securitySchemes"]
    assert esquemas == {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}


def test_la_especificacion_no_tiene_endpoints_de_mas_ni_de_menos(spec):
    documentados = {
        (metodo, ruta.removeprefix(BASE))
        for ruta, item in spec["paths"].items()
        for metodo in item
        if metodo != "parameters"
    }
    esperados = {(metodo, ruta) for metodo, ruta, *_ in ENDPOINTS}
    assert documentados == esperados
    assert len(ENDPOINTS) == 56


@pytest.mark.parametrize(
    ("metodo", "ruta", "tag", "reglas", "seguridad"),
    ENDPOINTS,
    ids=[f"{m.upper()} {r}" for m, r, *_ in ENDPOINTS],
)
def test_cada_endpoint_cumple_14(spec, metodo, ruta, tag, reglas, seguridad):
    op = operacion(spec, metodo, ruta)

    # tag por módulo, resumen y descripción en español
    assert op["tags"] == [tag]
    assert op["summary"].strip() and op["description"].strip()
    assert op["summary"][0].isupper()
    texto = f"{op['summary']} {op['description']}"
    for regla in reglas:
        assert regla in texto, f"{regla} no aparece en la descripción"

    # seguridad
    if seguridad is None:
        assert "security" not in op
    else:
        assert op["security"] == seguridad

    # respuestas: un éxito, los errores aplicables y todos con ErrorSchema
    codigos = set(op["responses"])
    assert any(c.startswith("2") for c in codigos)
    if seguridad == BEARER:
        assert {"401", "403"} <= codigos
    if "requestBody" in op or any(p["in"] == "query" for p in op.get("parameters", [])):
        assert "422" in codigos
    for codigo, respuesta in op["responses"].items():
        if codigo.startswith(("4", "5")):
            assert respuesta["description"].strip()
            esquema = respuesta["content"]["application/json"]["schema"]
            assert esquema["$ref"].endswith("/Error"), (codigo, esquema)


def test_los_endpoints_con_cuerpo_documentan_ejemplos_en_sus_campos_clave(spec):
    esquemas = spec["components"]["schemas"]
    for esquema, campos in {
        "Registro": ["nombre", "email", "password", "acepta_tratamiento_datos"],
        "Login": ["email", "password"],
        "EventoCrear": ["titulo", "precio_cop", "fecha_inicio"],
        "CompraCrear": ["metodo_pago"],
        "Simulador": ["resultado"],
        "IniciarTransmision": ["stream_key"],
        "RetiroCrear": ["monto_cop", "banco", "tipo_cuenta", "cuenta_ultimos4"],
        "EstadoUsuario": ["estado"],
        "Motivo": ["motivo"],
        "AudienciaSimulada": ["espectadores"],
    }.items():
        for campo in campos:
            propiedad = esquemas[esquema]["properties"][campo]
            assert "example" in propiedad or "enum" in propiedad, (esquema, campo)


def test_ningun_esquema_publicado_expone_credenciales(spec):
    texto = json.dumps(spec["components"]["schemas"])
    for prohibido in ("password_hash", "stream_key_hash"):
        assert prohibido not in texto
    # `password` solo aparece como entrada en registro/login.
    con_password = [
        nombre
        for nombre, esquema in spec["components"]["schemas"].items()
        if "password" in esquema.get("properties", {})
    ]
    assert set(con_password) == {"Registro", "Login"}


def test_docs_openapi_json_esta_al_dia_con_la_especificacion_viva(client):
    """`make openapi` regenera el archivo versionado; esta prueba avisa si quedó desactualizado."""
    exportado = json.loads((RAIZ / "docs" / "openapi.json").read_text(encoding="utf-8"))
    vivo = client.get("/api/openapi.json").get_json()
    assert exportado == vivo, "docs/openapi.json está desactualizado: ejecuta `make openapi`"
