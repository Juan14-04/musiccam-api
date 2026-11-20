"""Consultas de `favoritos` y `seguidores`. Sin `commit` (lo hace el servicio)."""

import uuid

from sqlalchemy import Select, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import contains_eager

from app.common.clock import ahora_utc
from app.common.enums import EstadoUsuario, Rol
from app.extensions import db
from app.modules.eventos.models import VISIBLE_EN_CATALOGO, Evento
from app.modules.social.models import Favorito, Seguidor
from app.modules.usuarios.models import Usuario


class SocialRepository:
    # --- Favoritos -------------------------------------------------------------------------

    def agregar_favorito(self, usuario_id: uuid.UUID, evento_id: uuid.UUID) -> None:
        """`INSERT … ON CONFLICT DO NOTHING`: dos PUT simultáneos no dan error (RN-17)."""
        db.session.execute(
            insert(Favorito)
            .values(usuario_id=usuario_id, evento_id=evento_id, creado_en=ahora_utc())
            .on_conflict_do_nothing(index_elements=["usuario_id", "evento_id"])
        )

    def quitar_favorito(self, usuario_id: uuid.UUID, evento_id: uuid.UUID) -> None:
        db.session.execute(
            delete(Favorito).where(
                Favorito.usuario_id == usuario_id, Favorito.evento_id == evento_id
            )
        )

    def consulta_favoritos(self, usuario_id: uuid.UUID) -> Select:
        """Eventos favoritos que siguen siendo visibles (RN-21), del más reciente al más viejo."""
        return (
            select(Evento)
            .join(Favorito, Favorito.evento_id == Evento.id)
            .join(Evento.artista)
            .options(contains_eager(Evento.artista))
            .where(Favorito.usuario_id == usuario_id, VISIBLE_EN_CATALOGO)
            .order_by(Favorito.creado_en.desc(), Evento.id)
        )

    # --- Seguidos --------------------------------------------------------------------------

    def agregar_seguido(self, usuario_id: uuid.UUID, artista_id: uuid.UUID) -> None:
        db.session.execute(
            insert(Seguidor)
            .values(usuario_id=usuario_id, artista_id=artista_id, creado_en=ahora_utc())
            .on_conflict_do_nothing(index_elements=["usuario_id", "artista_id"])
        )

    def quitar_seguido(self, usuario_id: uuid.UUID, artista_id: uuid.UUID) -> None:
        db.session.execute(
            delete(Seguidor).where(
                Seguidor.usuario_id == usuario_id, Seguidor.artista_id == artista_id
            )
        )

    def consulta_seguidos(self, usuario_id: uuid.UUID) -> Select:
        """Artistas `ACTIVO` que sigo, del más reciente al más viejo."""
        return (
            select(Usuario)
            .join(Seguidor, Seguidor.artista_id == Usuario.id)
            .where(
                Seguidor.usuario_id == usuario_id,
                Usuario.rol == Rol.ARTISTA,
                Usuario.estado == EstadoUsuario.ACTIVO,
            )
            .order_by(Seguidor.creado_en.desc(), Usuario.id)
        )
