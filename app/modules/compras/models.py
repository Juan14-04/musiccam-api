"""Modelo ORM de `compras` (§5.3). El acceso a un evento *es* una compra APROBADA."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.clock import ahora_utc
from app.common.enums import EstadoCompra, MetodoPago
from app.extensions import db
from app.modules.eventos.models import Evento
from app.modules.usuarios.models import Usuario


class Compra(db.Model):
    __tablename__ = "compras"
    __table_args__ = (
        CheckConstraint("monto_cop > 0", name="monto_positivo"),
        CheckConstraint(
            "monto_cop = comision_cop + neto_artista_cop", name="monto_es_comision_mas_neto"
        ),
        # Evita la doble compra y las condiciones de carrera (RN-07).
        Index(
            "uq_compras_usuario_evento_activa",
            "usuario_id",
            "evento_id",
            unique=True,
            postgresql_where=text("estado IN ('PENDIENTE','APROBADA')"),
        ),
        Index("ix_compras_evento_id_estado", "evento_id", "estado"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    usuario_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("usuarios.id", ondelete="RESTRICT")
    )
    evento_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("eventos.id", ondelete="RESTRICT")
    )
    referencia: Mapped[str] = mapped_column(String(64), unique=True)
    # Precio y comisión congelados al comprar (RN-06).
    monto_cop: Mapped[int] = mapped_column(Integer)
    comision_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    comision_cop: Mapped[int] = mapped_column(Integer)
    neto_artista_cop: Mapped[int] = mapped_column(Integer)
    metodo_pago: Mapped[MetodoPago] = mapped_column(Enum(MetodoPago, name="metodo_pago"))
    estado: Mapped[EstadoCompra] = mapped_column(
        Enum(EstadoCompra, name="estado_compra"),
        default=EstadoCompra.PENDIENTE,
        server_default=text("'PENDIENTE'"),
    )
    wompi_transaccion_id: Mapped[str | None] = mapped_column(String(64))
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_utc, server_default=func.now()
    )
    aprobado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_utc, onupdate=ahora_utc, server_default=func.now()
    )

    evento: Mapped[Evento] = relationship(lazy="select")
    usuario: Mapped[Usuario] = relationship(lazy="select")
