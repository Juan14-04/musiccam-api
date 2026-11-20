"""Application factory."""

import os
from typing import Any

from dotenv import load_dotenv
from flask import Flask, send_from_directory
from flask.typing import ResponseReturnValue
from werkzeug.middleware.proxy_fix import ProxyFix

from app.cli import registrar_comandos
from app.common.errors import registrar_manejadores_errores
from app.common.headers import registrar_cabeceras_seguridad
from app.common.limites import registrar_topes_por_ruta
from app.config import CONFIGURACIONES, RAIZ
from app.extensions import api, cors, db, jwt, limiter, migrate
from app.infrastructure.payments import validar_configuracion as validar_configuracion_pagos
from app.infrastructure.streaming import (
    validar_configuracion as validar_configuracion_streaming,
)


def _registrar_manejadores_jwt() -> None:
    """Unifica los errores de Flask-JWT-Extended al formato de §13."""
    from app.common.errors import respuesta_error

    def no_autenticado(*_args: Any) -> ResponseReturnValue:
        return respuesta_error(401, "NO_AUTENTICADO", "Token ausente, inválido o vencido.")

    jwt.unauthorized_loader(no_autenticado)
    jwt.invalid_token_loader(no_autenticado)
    jwt.expired_token_loader(no_autenticado)
    jwt.revoked_token_loader(no_autenticado)
    jwt.needs_fresh_token_loader(no_autenticado)


TAGS_AUXILIARES = {"Social"}  # blueprints cuyas rutas ya declaran el tag `Usuarios`


def _quitar_tags_auxiliares() -> None:
    """Quita de la lista global de tags los de blueprints auxiliares (sin operaciones propias)."""
    api.spec._tags = [t for t in api.spec._tags if t["name"] not in TAGS_AUXILIARES]


def _servir_uploads(app: Flask) -> None:
    """Ruta estática para las fotos de perfil (RN-18)."""

    def archivo(ruta: str) -> ResponseReturnValue:
        return send_from_directory(app.config["UPLOAD_DIR"], ruta)

    app.add_url_rule("/uploads/<path:ruta>", "uploads", archivo)


def create_app(config_name: str | None = None, overrides: dict[str, Any] | None = None) -> Flask:
    load_dotenv(RAIZ / ".env")
    nombre = config_name or os.environ.get("APP_ENV", "development")
    if nombre not in CONFIGURACIONES:
        raise RuntimeError(f"APP_ENV desconocido: {nombre!r}")

    app = Flask(__name__)
    app.config.from_object(CONFIGURACIONES[nombre]())
    app.config.update(overrides or {})
    if nombre == "production":
        # Detrás de Nginx: toma la IP real del cliente (X-Forwarded-For) para el rate limit.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    validar_configuracion_pagos(app.config)
    validar_configuracion_streaming(app.config)
    db.init_app(app)
    migrate.init_app(app, db, directory=str(RAIZ / "migrations"))
    jwt.init_app(app)
    limiter.init_app(app)
    cors.init_app(app, origins=app.config["CORS_ORIGINS"], resources={r"/api/*": {}})
    api.init_app(app)
    api.spec.components.security_scheme(
        "bearerAuth", {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    )

    from app import models  # noqa: F401  (registra los modelos en el metadata)
    from app.modules import registrar_modulos

    registrar_modulos(api)
    _quitar_tags_auxiliares()
    # Después de smorest: nuestro manejador de HTTPException reemplaza al suyo.
    registrar_manejadores_errores(app)
    registrar_cabeceras_seguridad(app)
    registrar_topes_por_ruta(app)
    _registrar_manejadores_jwt()
    _servir_uploads(app)
    registrar_comandos(app)
    return app
