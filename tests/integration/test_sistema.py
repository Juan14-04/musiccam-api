"""Fase 0: health, Swagger y formato de error unificado."""

from sqlalchemy import select

from app.common.pagination import paginar
from app.extensions import db
from app.modules.usuarios.models import Usuario
from tests.factories import crear_usuario

TAGS = {
    "Sistema",
    "Auth",
    "Usuarios",
    "Eventos",
    "Compras",
    "Transmision",
    "Artistas",
    "Artista (panel)",
}


def test_health_verifica_la_bd(client):
    respuesta = client.get("/api/v1/health")
    assert respuesta.status_code == 200
    assert respuesta.get_json() == {"estado": "ok", "base_de_datos": "ok"}


def test_swagger_ui_carga(client):
    respuesta = client.get("/api/docs")
    assert respuesta.status_code == 200
    assert b"swagger-ui" in respuesta.data


def test_openapi_json_contiene_todos_los_tags_y_seguridad(client):
    """§14: `/api/openapi.json` responde 200 y contiene los tags."""
    respuesta = client.get("/api/openapi.json")
    assert respuesta.status_code == 200
    spec = respuesta.get_json()
    assert TAGS <= {t["name"] for t in spec["tags"]}
    assert "bearerAuth" in spec["components"]["securitySchemes"]
    for ruta in (
        "/api/v1/health",
        "/api/v1/auth/registro",
        "/api/v1/usuarios/me/foto",
        "/api/v1/eventos",
        "/api/v1/eventos/{evento_id}",
        "/api/v1/eventos/{evento_id}/publicar",
        "/api/v1/eventos/{evento_id}/cancelar",
        "/api/v1/artistas/me/eventos",
        "/api/v1/eventos/{evento_id}/compras",
        "/api/v1/webhooks/wompi",
        "/api/v1/eventos/{evento_id}/transmision/credenciales",
        "/api/v1/eventos/{evento_id}/reproduccion/entrar",
        "/api/v1/artistas/me/estadisticas",
        "/api/v1/artistas",
        "/api/v1/artistas/{artista_id}",
        "/api/v1/usuarios/me/favoritos",
        "/api/v1/usuarios/me/seguidos/{artista_id}",
        "/api/v1/usuarios/me/compras",
        "/api/v1/artistas/me/saldo",
        "/api/v1/artistas/me/retiros",
    ):
        assert ruta in spec["paths"]


def test_ruta_inexistente_usa_formato_unificado(client):
    respuesta = client.get("/api/v1/no-existe")
    assert respuesta.status_code == 404
    assert set(respuesta.get_json()) == {"codigo", "mensaje", "detalles"}
    assert respuesta.get_json()["codigo"] == "RECURSO_NO_ENCONTRADO"


def test_metodo_no_permitido_usa_formato_unificado(client):
    respuesta = client.delete("/api/v1/health")
    assert respuesta.status_code == 405
    assert respuesta.get_json()["codigo"] == "METODO_NO_PERMITIDO"


def test_error_de_validacion_trae_detalles_por_campo(client):
    respuesta = client.post("/api/v1/auth/login", json={"email": "a@b.co", "extra": 1})
    cuerpo = respuesta.get_json()
    assert respuesta.status_code == 422
    assert cuerpo["codigo"] == "VALIDACION"
    assert "password" in cuerpo["detalles"] and "extra" in cuerpo["detalles"]


def test_paginar(contexto):
    for i in range(5):
        crear_usuario(email=f"u{i}@example.com")
    consulta = select(Usuario).order_by(Usuario.email)
    pagina = paginar(consulta, page=2, page_size=2)
    assert (pagina["page"], pagina["page_size"], pagina["total"]) == (2, 2, 5)
    assert [u.email for u in pagina["items"]] == ["u2@example.com", "u3@example.com"]
    assert paginar(consulta, page=1, page_size=500)["page_size"] == 100
    db.session.rollback()
