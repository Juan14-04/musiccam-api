"""Configuración por entorno, leída de variables de entorno."""

import os
from datetime import timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

SECRETOS_POR_DEFECTO = {"cambia-esto", "cambia-esto-tambien", "cambia-esto-3"}
# HS256 pide claves de al menos 32 bytes (RFC 7518 §3.2); PyJWT avisa si son más cortas.
LONGITUD_MINIMA_SECRETO = 32


def _texto(nombre: str, defecto: str) -> str:
    return os.environ.get(nombre, defecto)


def _entero(nombre: str, defecto: int) -> int:
    return int(os.environ.get(nombre, defecto))


def _booleano(nombre: str, defecto: bool) -> bool:
    return os.environ.get(nombre, str(defecto)).strip().lower() in {"1", "true", "si", "sí", "yes"}


class Config:
    """Base común. Se instancia en `create_app()` para leer el entorno vigente."""

    def __init__(self) -> None:
        self.APP_ENV = "development"
        self.SECRET_KEY = _texto("SECRET_KEY", "cambia-esto")
        self.SQLALCHEMY_DATABASE_URI = _texto(
            "DATABASE_URL", "postgresql+psycopg://app:app@localhost:5433/streaming"
        )
        # `hide_parameters`: los errores de SQLAlchemy no incluyen los valores enlazados (podrían
        # ser contraseñas, hashes o números de cuenta) y por tanto tampoco acaban en los logs.
        self.SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "hide_parameters": True}
        self.CORS_ORIGINS = [o.strip() for o in _texto("CORS_ORIGINS", "").split(",") if o.strip()]

        self.JWT_SECRET_KEY = _texto("JWT_SECRET_KEY", "cambia-esto-tambien")
        self.JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=_entero("JWT_ACCESS_TTL_MIN", 15))
        self.JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=_entero("JWT_REFRESH_TTL_DAYS", 7))

        self.PRECIO_MIN_COP = _entero("PRECIO_MIN_COP", 5000)
        self.PRECIO_MAX_COP = _entero("PRECIO_MAX_COP", 500000)
        self.MAX_EVENTOS_PROGRAMADOS_NUEVO = _entero("MAX_EVENTOS_PROGRAMADOS_NUEVO", 3)
        self.PLATFORM_COMMISSION_PCT = _texto("PLATFORM_COMMISSION_PCT", "10")
        self.COMPRA_PENDIENTE_TTL_MIN = _entero("COMPRA_PENDIENTE_TTL_MIN", 30)
        self.RETIRO_MIN_COP = _entero("RETIRO_MIN_COP", 50000)
        self.RETIRO_AUTO_APROBAR = _booleano("RETIRO_AUTO_APROBAR", False)
        self.PAYMENT_GATEWAY = _texto("PAYMENT_GATEWAY", "wompi_simulado")
        self.WOMPI_INTEGRITY_SECRET = _texto("WOMPI_INTEGRITY_SECRET", "sim-integrity-secret")
        self.WOMPI_EVENTS_SECRET = _texto("WOMPI_EVENTS_SECRET", "sim-events-secret")
        self.POLITICA_DATOS_VERSION = _texto("POLITICA_DATOS_VERSION", "v1")
        self.PLAYBACK_TOKEN_SECRET = _texto("PLAYBACK_TOKEN_SECRET", "cambia-esto-3")
        self.PLAYBACK_TOKEN_TTL_MIN = _entero("PLAYBACK_TOKEN_TTL_MIN", 10)
        self.STREAMING_PROVIDER = _texto("STREAMING_PROVIDER", "simulado")
        self.MEDIA_BASE_URL = _texto("MEDIA_BASE_URL", "http://localhost:8888")
        self.RTMP_BASE_URL = _texto("RTMP_BASE_URL", "rtmp://localhost:1935/live")
        self.ENFORCE_START_WINDOW = _booleano("ENFORCE_START_WINDOW", False)
        self.START_WINDOW_MIN = _entero("START_WINDOW_MIN", 30)

        upload = Path(_texto("UPLOAD_DIR", "uploads"))
        self.UPLOAD_DIR = str(upload if upload.is_absolute() else RAIZ / upload)
        self.MAX_UPLOAD_MB = _entero("MAX_UPLOAD_MB", 2)
        # Tope duro de la petición (1 MB de holgura para el multipart); el límite de 2 MB de la
        # foto lo valida el servicio y responde 422 (RN-18).
        self.MAX_CONTENT_LENGTH = (self.MAX_UPLOAD_MB + 1) * 1024 * 1024
        # Video del tráiler: solo su ruta amplía el tope de la petición (ver eventos/routes.py).
        self.MAX_TRAILER_MB = _entero("MAX_TRAILER_MB", 50)

        self.ENABLE_DEV_ENDPOINTS = _booleano("ENABLE_DEV_ENDPOINTS", True)

        self.RATELIMIT_STORAGE_URI = "memory://"
        self.RATELIMIT_ENABLED = True
        self.RATELIMIT_HEADERS_ENABLED = True  # Retry-After y X-RateLimit-* en las respuestas

        self.API_TITLE = "API de transmisión en vivo de conciertos"
        self.API_VERSION = "v1"
        self.OPENAPI_VERSION = "3.0.3"
        self.OPENAPI_URL_PREFIX = "/api"
        self.OPENAPI_JSON_PATH = "openapi.json"
        self.OPENAPI_SWAGGER_UI_PATH = "/docs"
        self.OPENAPI_SWAGGER_UI_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist/"
        self.PROPAGATE_EXCEPTIONS = False


class DevelopmentConfig(Config):
    pass


class TestingConfig(Config):
    def __init__(self) -> None:
        super().__init__()
        self.APP_ENV = "testing"
        self.TESTING = True
        self.SQLALCHEMY_DATABASE_URI = _texto(
            "TEST_DATABASE_URL", "postgresql+psycopg://app:app@localhost:5433/streaming_test"
        )
        self.RATELIMIT_ENABLED = False


class ProductionConfig(Config):
    def __init__(self) -> None:
        super().__init__()
        self.APP_ENV = "production"
        # En producción no se pueden activar por entorno: siempre apagados / encendida.
        self.ENABLE_DEV_ENDPOINTS = False
        self.ENFORCE_START_WINDOW = True
        for nombre in ("SECRET_KEY", "JWT_SECRET_KEY", "PLAYBACK_TOKEN_SECRET"):
            valor = getattr(self, nombre)
            if not valor or valor in SECRETOS_POR_DEFECTO:
                raise RuntimeError(
                    f"{nombre} usa un valor por defecto; no se puede arrancar en production."
                )
            if len(valor) < LONGITUD_MINIMA_SECRETO:
                raise RuntimeError(
                    f"{nombre} debe tener al menos {LONGITUD_MINIMA_SECRETO} caracteres."
                )
        if "*" in self.CORS_ORIGINS:
            raise RuntimeError("CORS_ORIGINS no puede contener '*' en production.")


CONFIGURACIONES: dict[str, type[Config]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
