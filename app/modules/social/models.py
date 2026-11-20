"""Modelos ORM de las tablas puente `favoritos` y `seguidores` (§5.5 y §5.6)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.common.clock import ahora_utc
from app.extensions import db


class Favorito(db.Model):
    """Un usuario marca un evento como favorito. PK compuesta `(usuario_id, evento_id)`."""

    __tablename__ = "favoritos"
    __table_args__ = (Index("ix_favoritos_evento_id", "evento_id"),)

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usuarios.id", ondelete="RESTRICT"), primary_key=True
    )
    evento_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("eventos.id", ondelete="RESTRICT"), primary_key=True
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_utc, server_default=func.now()
    )


class Seguidor(db.Model):
    """Un usuario sigue a un artista. PK compuesta `(usuario_id, artista_id)`.

    Que `artista_id` tenga `rol = ARTISTA` se valida en el servicio.
    """

    __tablename__ = "seguidores"
    __table_args__ = (
        CheckConstraint("usuario_id <> artista_id", name="no_seguirse_a_si_mismo"),
        Index("ix_seguidores_artista_id", "artista_id"),
    )

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usuarios.id", ondelete="RESTRICT"), primary_key=True
    )
    artista_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usuarios.id", ondelete="RESTRICT"), primary_key=True
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_utc, server_default=func.now()
    )
