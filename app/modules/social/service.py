"""Favoritos y seguidos (RN-17). Todas las operaciones son idempotentes."""

import uuid

from app.common.errors import ReglaDeNegocio
from app.common.pagination import paginar
from app.extensions import db
from app.modules.eventos.service import EventoService
from app.modules.social.repository import SocialRepository
from app.modules.usuarios.models import Usuario
from app.modules.usuarios.service import UsuarioService


class SocialService:
    """Gestiona favoritos y seguimientos con operaciones idempotentes."""

    def __init__(
        self,
        repo: SocialRepository | None = None,
        eventos: EventoService | None = None,
        usuarios: UsuarioService | None = None,
    ) -> None:
        self.repo = repo or SocialRepository()
        self.eventos = eventos or EventoService()
        self.usuarios = usuarios or UsuarioService()

    # --- Favoritos (eventos) ---------------------------------------------------------------

    def agregar_favorito(self, usuario: Usuario, evento_id: uuid.UUID) -> None:
        """Solo eventos visibles (RN-21): borrador, cancelado o suspendido → 404."""
        self.eventos.obtener_visible(evento_id)
        self.repo.agregar_favorito(usuario.id, evento_id)
        self._confirmar()

    def quitar_favorito(self, usuario: Usuario, evento_id: uuid.UUID) -> None:
        """Elimina el favorito del usuario; la operación es idempotente."""
        self.repo.quitar_favorito(usuario.id, evento_id)
        self._confirmar()

    def listar_favoritos(self, usuario: Usuario, *, page: int = 1, page_size: int = 20):
        """Lista los favoritos visibles del usuario con paginación."""
        return paginar(self.repo.consulta_favoritos(usuario.id), page, page_size)

    # --- Seguidos (artistas) ---------------------------------------------------------------

    def seguir(self, usuario: Usuario, artista_id: uuid.UUID) -> None:
        """Solo a un ARTISTA `ACTIVO`; a uno mismo → 422; otro caso → 404."""
        if artista_id == usuario.id:
            raise ReglaDeNegocio("NO_SEGUIRSE_A_SI_MISMO", "No puedes seguirte a ti mismo.")
        self.usuarios.obtener_artista_activo(artista_id)
        self.repo.agregar_seguido(usuario.id, artista_id)
        self._confirmar()

    def dejar_de_seguir(self, usuario: Usuario, artista_id: uuid.UUID) -> None:
        """Elimina la relación de seguimiento; no falla si ya no existía."""
        self.repo.quitar_seguido(usuario.id, artista_id)
        self._confirmar()

    def listar_seguidos(self, usuario: Usuario, *, page: int = 1, page_size: int = 20):
        """Lista los artistas activos que sigue el usuario."""
        return paginar(self.repo.consulta_seguidos(usuario.id), page, page_size)

    @staticmethod
    def _confirmar() -> None:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
