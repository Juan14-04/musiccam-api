"""Modelo ORM de `eventos` (§5.2)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    and_,
    func,
    or_,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.clock import ahora_utc
from app.common.enums import CategoriaEvento, EstadoEvento, EstadoUsuario
from app.extensions import db
from app.modules.usuarios.models import Usuario


class Evento(db.Model):
    __tablename__ = "eventos"
    __table_args__ = (
        CheckConstraint("precio_cop > 0", name="precio_positivo"),
        CheckConstraint("duracion_estimada_min BETWEEN 15 AND 240", name="duracion_en_rango"),
        CheckConstraint("espectadores_actuales >= 0", name="espectadores_no_negativos"),
        Index("ix_eventos_estado_fecha_inicio", "estado", "fecha_inicio"),
        Index("ix_eventos_artista_id_estado", "artista_id", "estado"),
        Index("ix_eventos_categoria", "categoria"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    artista_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    titulo: Mapped[str] = mapped_column(String(150))
    descripcion: Mapped[str | None] = mapped_column(Text)
    categoria: Mapped[CategoriaEvento] = mapped_column(
        Enum(CategoriaEvento, name="categoria_evento"),
        default=CategoriaEvento.OTRO,
        server_default=text("'OTRO'"),
    )
    portada_url: Mapped[str | None] = mapped_column(String(255))
    youtube_url: Mapped[str | None] = mapped_column(String(255))
    # Video propio subido por el artista (`/uploads/trailers/...`); alternativa a YouTube.
    trailer_video_url: Mapped[str | None] = mapped_column(String(255))
    precio_cop: Mapped[int] = mapped_column(Integer)
    fecha_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duracion_estimada_min: Mapped[int] = mapped_column(
        Integer, default=60, server_default=text("60")
    )
    estado: Mapped[EstadoEvento] = mapped_column(
        Enum(EstadoEvento, name="estado_evento"),
        default=EstadoEvento.BORRADOR,
        server_default=text("'BORRADOR'"),
    )
    replay_habilitado: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    replay_disponible: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    stream_key_hash: Mapped[str | None] = mapped_column(String(64))
    inicio_real: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fin_real: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    espectadores_actuales: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    pico_espectadores: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    vistas_vivo: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    vistas_replay: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    suspendido_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    motivo_suspension: Mapped[str | None] = mapped_column(String(255))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_utc, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_utc, onupdate=ahora_utc, server_default=func.now()
    )

    artista: Mapped[Usuario] = relationship(lazy="select")


# RN-21: criterio de visibilidad del catálogo público. Se declara junto a la tabla (como los
# índices) para que cualquier módulo lo reutilice en consultas de solo lectura sin importar el
# repositorio de eventos. Las consultas que lo usan deben unirse a `Evento.artista`.
VISIBLE_EN_CATALOGO = and_(
    Evento.suspendido_admin.is_(False),
    Usuario.estado == EstadoUsuario.ACTIVO,
    or_(
        Evento.estado.in_([EstadoEvento.PROGRAMADO, EstadoEvento.EN_VIVO]),
        and_(Evento.estado == EstadoEvento.FINALIZADO, Evento.replay_disponible.is_(True)),
    ),
)
