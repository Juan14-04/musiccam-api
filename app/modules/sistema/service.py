"""Estado de salud del sistema."""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.common.errors import ServicioNoDisponible
from app.extensions import db


def verificar_salud() -> dict[str, str]:
    """Comprueba la conexión a la base de datos para los health checks de la API."""
    try:
        db.session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        db.session.rollback()
        raise ServicioNoDisponible(
            "BASE_DE_DATOS_NO_DISPONIBLE", "No hay conexión con la base de datos."
        ) from exc
    return {"estado": "ok", "base_de_datos": "ok"}
