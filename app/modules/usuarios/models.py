"""Modelo ORM de `usuarios` (§5.1)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.clock import ahora_utc
from app.common.enums import EstadoUsuario, Rol
from app.extensions import db


class Usuario(db.Model):
    __tablename__ = "usuarios"
    __table_args__ = (
        CheckConstraint(
            "rol <> 'ARTISTA' OR nombre_artistico IS NOT NULL",
            name="artista_requiere_nombre_artistico",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(255))
    nombre: Mapped[str] = mapped_column(String(100))
    # Opcional en la base (cuentas previas y ADMIN creados por SQL); el registro lo exige.
    apellido: Mapped[str | None] = mapped_column(String(100))
    rol: Mapped[Rol] = mapped_column(Enum(Rol, name="rol"))
    estado: Mapped[EstadoUsuario] = mapped_column(
        Enum(EstadoUsuario, name="estado_usuario"),
        default=EstadoUsuario.ACTIVO,
        server_default=text("'ACTIVO'"),
    )
    foto_url: Mapped[str | None] = mapped_column(String(255))
    nombre_artistico: Mapped[str | None] = mapped_column(String(100))
    biografia: Mapped[str | None] = mapped_column(Text)
    consentimiento_datos_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version_politica_datos: Mapped[str] = mapped_column(String(20))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_utc, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=ahora_utc, onupdate=ahora_utc, server_default=func.now()
    )


# Unicidad sin distinguir mayúsculas (email) y nombre artístico único solo entre artistas.
Index("uq_usuarios_email_lower", func.lower(Usuario.email), unique=True)
Index(
    "uq_usuarios_nombre_artistico_lower",
    func.lower(Usuario.nombre_artistico),
    unique=True,
    postgresql_where=text("rol = 'ARTISTA'"),
)
