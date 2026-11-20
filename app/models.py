"""Importa todos los modelos para que Alembic los detecte."""

from app.modules.artistas.models import Retiro
from app.modules.compras.models import Compra
from app.modules.eventos.models import Evento
from app.modules.social.models import Favorito, Seguidor
from app.modules.usuarios.models import Usuario

__all__ = ["Compra", "Evento", "Favorito", "Retiro", "Seguidor", "Usuario"]
