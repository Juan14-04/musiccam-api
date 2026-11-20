"""Modelo ORM de `retiros` (§5.4). Solo se guardan los últimos 4 dígitos de la cuenta."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.clock import ahora_utc
from app.common.enums import EstadoRetiro, TipoCuenta
from app.extensions import db
from app.modules.usuarios.models import Usuario


class Retiro(db.Model):
    __tablename__ = "retiros"
    __table_args__ = (
        CheckConstraint("monto_cop > 0", name="monto_positivo"),
        CheckConstraint("cuenta_ultimos4 ~ '^[0-9]{4}$'", name="cuenta_ultimos4_son_4_digitos"),
        Index("ix_retiros_artista_id", "artista_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    artista_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    monto_cop: Mapped[int] = mapped_column(Integer)
    banco: Mapped[str] = mapped_column(String(60))
    tipo_cuenta: Mapped[TipoCuenta] = mapped_column(Enum(TipoCuenta, name="tipo_cuenta"))
    # Nunca el número completo (§5.4).
    cuenta_ultimos4: Mapped[str] = mapped_column(CHAR(4))
    estado: Mapped[EstadoRetiro] = mapped_column(
        Enum(EstadoRetiro, name="estado_retiro"),
        default=EstadoRetiro.PENDIENTE,
        server_default=text("'PENDIENTE'"),
    )
    referencia_pago: Mapped[str | None] = mapped_column(String(64))
    motivo_rechazo: Mapped[str | None] = mapped_column(String(255))
    resuelto_por: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("usuarios.id"))
    solicitado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_utc, server_default=func.now()
    )
    resuelto_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    artista: Mapped[Usuario] = relationship(foreign_keys=[artista_id], lazy="select")
