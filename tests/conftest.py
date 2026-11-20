"""Fixtures: app contra PostgreSQL real, esquema creado con las migraciones."""

import uuid

import pytest
from flask_migrate import upgrade
from sqlalchemy import text

from app import create_app
from app.common.clock import fijar_ahora
from app.common.enums import Rol
from app.common.security import requiere_roles
from app.extensions import db, limiter

VALOR_SENSIBLE_EN_SQL = "9876543210SECRETO"


def _falla():
    raise RuntimeError("fallo inesperado de prueba")


def _falla_sql():
    """INSERT que la BD rechaza (CHAR(4)): su valor no debe llegar a logs ni a la respuesta."""
    from app.common.enums import TipoCuenta
    from app.modules.artistas.models import Retiro

    db.session.add(
        Retiro(
            artista_id=uuid.uuid4(),
            monto_cop=1,
            banco="B",
            tipo_cuenta=TipoCuenta.AHORROS,
            cuenta_ultimos4=VALOR_SENSIBLE_EN_SQL,
        )
    )
    db.session.commit()
    return {"ok": True}


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    # El limiter se inicializa con almacenamiento, pero cada prueba lo apaga (ver `_limpiar`);
    # solo las pruebas de rate limit lo encienden.
    overrides = {
        "UPLOAD_DIR": str(tmp_path_factory.mktemp("uploads")),
        "RATELIMIT_ENABLED": True,
        "SECRET_KEY": "clave-de-pruebas-secret-key-de-32-caracteres-o-mas",
        "JWT_SECRET_KEY": "clave-de-pruebas-jwt-de-sesion-de-32-caracteres-o-mas",
        "PLAYBACK_TOKEN_SECRET": "clave-de-pruebas-reproduccion-de-32-caracteres-o-mas",
    }
    app = create_app("testing", overrides)
    # Ruta solo de pruebas para ejercitar `requiere_roles` sin depender de fases futuras.
    app.add_url_rule(
        "/_test/solo-artista",
        "test_solo_artista",
        requiere_roles(Rol.ARTISTA)(lambda: {"ok": True}),
    )
    # Rutas solo de pruebas, registradas antes de la primera petición: una que falla con una
    # excepción cualquiera y otra que provoca un error de SQL con un valor reconocible.
    app.add_url_rule("/_test/falla", "test_falla", _falla)
    app.add_url_rule("/_test/falla-sql", "test_falla_sql", _falla_sql)
    with app.app_context():
        nombre_bd = db.engine.url.database or ""
        assert nombre_bd.endswith("_test"), f"Las pruebas solo corren sobre *_test, no {nombre_bd}"
        with db.engine.begin() as conexion:
            conexion.execute(text("DROP SCHEMA public CASCADE"))
            conexion.execute(text("CREATE SCHEMA public"))
        upgrade()
    return app


@pytest.fixture(autouse=True)
def _limpiar(app):
    """Aísla las pruebas: TRUNCATE de todas las tablas, reloj real y rate limit apagado."""
    # `create_app("production")` en otras pruebas re-inicializa el limiter global y lo enciende.
    limiter.enabled = False
    yield
    fijar_ahora(None)
    with app.app_context():
        db.session.remove()
        for tabla in reversed(db.metadata.sorted_tables):
            db.session.execute(tabla.delete())
        db.session.commit()
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def contexto(app):
    with app.app_context():
        yield
