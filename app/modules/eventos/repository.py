"""Consultas de `eventos`. Sin `commit` (lo hace el servicio)."""

import uuid
from datetime import datetime

from sqlalchemy import Select, and_, exists, func, or_, select, update
from sqlalchemy.orm import contains_eager, joinedload

from app.common.enums import CategoriaEvento, EstadoCompra, EstadoEvento
from app.common.sql import patron_contiene
from app.extensions import db
from app.modules.compras.models import Compra
from app.modules.eventos.models import VISIBLE_EN_CATALOGO, Evento
from app.modules.social.models import Favorito
from app.modules.usuarios.models import Usuario


class EventoRepository:
    def obtener_por_id(self, evento_id: uuid.UUID, *, bloquear: bool = False) -> Evento | None:
        consulta = select(Evento).where(Evento.id == evento_id)
        if bloquear:
            consulta = consulta.with_for_update()
        return db.session.scalar(consulta)

    def agregar(self, evento: Evento) -> Evento:
        db.session.add(evento)
        db.session.flush()
        return evento

    def eliminar(self, evento: Evento) -> None:
        db.session.delete(evento)
        db.session.flush()

    def contar_por_estado(self, artista_id: uuid.UUID, estado: EstadoEvento) -> int:
        return (
            db.session.scalar(
                select(func.count())
                .select_from(Evento)
                .where(Evento.artista_id == artista_id, Evento.estado == estado)
            )
            or 0
        )

    def tiene_finalizados(self, artista_id: uuid.UUID) -> bool:
        return self.contar_por_estado(artista_id, EstadoEvento.FINALIZADO) > 0

    def tiene_compras_aprobadas(self, evento_id: uuid.UUID) -> bool:
        """¿El evento tiene compras `APROBADA`? (RN-04). Consulta de solo lectura (§7.3)."""
        return bool(
            db.session.scalar(
                select(
                    exists().where(
                        Compra.evento_id == evento_id, Compra.estado == EstadoCompra.APROBADA
                    )
                )
            )
        )

    def consulta_admin(
        self,
        *,
        q: str | None = None,
        categoria: CategoriaEvento | None = None,
        estado: EstadoEvento | None = None,
        artista_id: uuid.UUID | None = None,
        suspendido: bool | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> Select:
        """Todos los eventos, en cualquier estado y sin filtros de visibilidad."""
        consulta = select(Evento).join(Evento.artista).options(contains_eager(Evento.artista))
        if q:
            patron = patron_contiene(q)
            consulta = consulta.where(
                or_(
                    Evento.titulo.ilike(patron, escape="\\"),
                    Usuario.nombre_artistico.ilike(patron, escape="\\"),
                )
            )
        if categoria:
            consulta = consulta.where(Evento.categoria == categoria)
        if estado:
            consulta = consulta.where(Evento.estado == estado)
        if artista_id:
            consulta = consulta.where(Evento.artista_id == artista_id)
        if suspendido is not None:
            consulta = consulta.where(Evento.suspendido_admin.is_(suspendido))
        if desde:
            consulta = consulta.where(Evento.fecha_inicio >= desde)
        if hasta:
            consulta = consulta.where(Evento.fecha_inicio <= hasta)
        return consulta.order_by(Evento.fecha_inicio.desc(), Evento.id)

    def es_favorito(self, usuario_id: uuid.UUID, evento_id: uuid.UUID) -> bool:
        """¿El usuario marcó el evento como favorito? Consulta de solo lectura (§7.3)."""
        return bool(
            db.session.scalar(
                select(
                    exists().where(
                        Favorito.usuario_id == usuario_id, Favorito.evento_id == evento_id
                    )
                )
            )
        )

    def consulta_proximos_de_artista(self, artista_id: uuid.UUID) -> Select:
        """Eventos visibles (RN-21) `PROGRAMADO` o `EN_VIVO` de un artista, por fecha."""
        return (
            select(Evento)
            .join(Evento.artista)
            .options(contains_eager(Evento.artista))
            .where(
                VISIBLE_EN_CATALOGO,
                Evento.artista_id == artista_id,
                Evento.estado.in_([EstadoEvento.PROGRAMADO, EstadoEvento.EN_VIVO]),
            )
            .order_by(Evento.fecha_inicio, Evento.id)
        )

    # --- Contadores de audiencia: un solo UPDATE atómico, sin leer-modificar-escribir ---------

    def sumar_entrada(self, evento_id: uuid.UUID, *, vivo: bool) -> bool:
        """Cuenta una entrada, solo si el evento sigue en el estado que da acceso.

        En vivo suma `espectadores_actuales` y `vistas_vivo` y sube el pico con GREATEST (el
        pico usa el valor anterior + 1, porque en PostgreSQL todo el SET lee la fila original).
        El replay solo suma `vistas_replay`. Devuelve `False` si el estado cambió entretanto.
        """
        if vivo:
            valores = {
                "espectadores_actuales": Evento.espectadores_actuales + 1,
                "pico_espectadores": func.greatest(
                    Evento.pico_espectadores, Evento.espectadores_actuales + 1
                ),
                "vistas_vivo": Evento.vistas_vivo + 1,
            }
            vigente = Evento.estado == EstadoEvento.EN_VIVO
        else:
            valores = {"vistas_replay": Evento.vistas_replay + 1}
            vigente = and_(
                Evento.estado == EstadoEvento.FINALIZADO, Evento.replay_disponible.is_(True)
            )
        resultado = db.session.execute(
            update(Evento)
            .where(Evento.id == evento_id, Evento.suspendido_admin.is_(False), vigente)
            .values(**valores)
            .execution_options(synchronize_session=False)
        )
        return resultado.rowcount == 1

    def restar_salida(self, evento_id: uuid.UUID) -> None:
        """Descuenta un espectador sin bajar nunca de 0."""
        db.session.execute(
            update(Evento)
            .where(Evento.id == evento_id)
            .values(espectadores_actuales=func.greatest(Evento.espectadores_actuales - 1, 0))
            .execution_options(synchronize_session=False)
        )

    def fijar_audiencia(self, evento_id: uuid.UUID, espectadores: int) -> None:
        db.session.execute(
            update(Evento)
            .where(Evento.id == evento_id)
            .values(
                espectadores_actuales=espectadores,
                pico_espectadores=func.greatest(Evento.pico_espectadores, espectadores),
            )
            .execution_options(synchronize_session=False)
        )

    def consulta_catalogo(
        self,
        *,
        q: str | None = None,
        categoria: CategoriaEvento | None = None,
        estado: EstadoEvento | None = None,
        artista_id: uuid.UUID | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> Select:
        consulta = (
            select(Evento)
            .join(Evento.artista)
            .options(contains_eager(Evento.artista))
            .where(VISIBLE_EN_CATALOGO)
        )
        if q:
            patron = patron_contiene(q)
            consulta = consulta.where(
                or_(
                    Evento.titulo.ilike(patron, escape="\\"),
                    Usuario.nombre_artistico.ilike(patron, escape="\\"),
                )
            )
        if categoria:
            consulta = consulta.where(Evento.categoria == categoria)
        if estado:
            consulta = consulta.where(Evento.estado == estado)
        if artista_id:
            consulta = consulta.where(Evento.artista_id == artista_id)
        if desde:
            consulta = consulta.where(Evento.fecha_inicio >= desde)
        if hasta:
            consulta = consulta.where(Evento.fecha_inicio <= hasta)
        return consulta.order_by(Evento.fecha_inicio, Evento.id)

    def consulta_de_artista(
        self, artista_id: uuid.UUID, estado: EstadoEvento | None = None
    ) -> Select:
        consulta = (
            select(Evento)
            .options(joinedload(Evento.artista))
            .where(Evento.artista_id == artista_id)
        )
        if estado:
            consulta = consulta.where(Evento.estado == estado)
        return consulta.order_by(Evento.fecha_inicio.desc(), Evento.id)
