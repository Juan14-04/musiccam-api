"""Consultas de `compras`. Sin `commit` (lo hace el servicio).

El evento se lee solo aquí, en solo lectura, para no depender de `eventos.service` (§7.3).
"""

import uuid
from datetime import datetime

from sqlalchemy import Select, exists, select, update
from sqlalchemy.orm import contains_eager, joinedload

from app.common.enums import EstadoCompra, EstadoEvento
from app.extensions import db
from app.modules.compras.models import Compra
from app.modules.eventos.models import Evento

ESTADOS_ACTIVOS = (EstadoCompra.PENDIENTE, EstadoCompra.APROBADA)


class ComprasRepository:
    def obtener_por_id(self, compra_id: uuid.UUID) -> Compra | None:
        return db.session.get(Compra, compra_id)

    def obtener_por_referencia(self, referencia: str, *, bloquear: bool = False) -> Compra | None:
        consulta = select(Compra).where(Compra.referencia == referencia)
        if bloquear:
            consulta = consulta.with_for_update()
        return db.session.scalar(consulta)

    def agregar(self, compra: Compra) -> Compra:
        db.session.add(compra)
        db.session.flush()
        return compra

    def obtener_evento_para_compra(self, evento_id: uuid.UUID) -> Evento | None:
        """Lee el evento (con su artista) tomando `FOR SHARE`.

        Así `cancelar` (que pide `FOR UPDATE`) y las ediciones de precio esperan a que esta
        compra termine: la compra no queda sin reembolsar ni con un precio a medias.
        """
        return db.session.scalar(
            select(Evento)
            .options(joinedload(Evento.artista, innerjoin=True))
            .where(Evento.id == evento_id)
            .with_for_update(read=True, of=Evento)
        )

    def anular_pendientes_vencidas(
        self, usuario_id: uuid.UUID, evento_id: uuid.UUID, ahora: datetime
    ) -> int:
        """Expiración perezosa (RN-07): PENDIENTE vencidas del usuario para ese evento → ANULADA."""
        resultado = db.session.execute(
            update(Compra)
            .where(
                Compra.usuario_id == usuario_id,
                Compra.evento_id == evento_id,
                Compra.estado == EstadoCompra.PENDIENTE,
                Compra.expira_en <= ahora,
            )
            .values(estado=EstadoCompra.ANULADA, actualizado_en=ahora)
        )
        db.session.expire_all()
        return resultado.rowcount

    def consulta_de_usuario(
        self, usuario_id: uuid.UUID, estado: EstadoCompra | None = None, proximos: bool = False
    ) -> Select:
        """Mis compras con su evento. `proximos`: APROBADA de eventos PROGRAMADO/EN_VIVO."""
        consulta = (
            select(Compra)
            .join(Compra.evento)
            .join(Evento.artista)
            .options(contains_eager(Compra.evento).contains_eager(Evento.artista))
            .where(Compra.usuario_id == usuario_id)
        )
        if estado:
            consulta = consulta.where(Compra.estado == estado)
        if proximos:
            return consulta.where(
                Compra.estado == EstadoCompra.APROBADA,
                Evento.estado.in_([EstadoEvento.PROGRAMADO, EstadoEvento.EN_VIVO]),
            ).order_by(Evento.fecha_inicio, Compra.id)
        return consulta.order_by(Compra.creado_en.desc(), Compra.id)

    def consulta_admin(
        self,
        *,
        estado: EstadoCompra | None = None,
        evento_id: uuid.UUID | None = None,
        usuario_id: uuid.UUID | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
    ) -> Select:
        """Todas las compras (solo lectura) con comprador y evento, las más recientes primero."""
        consulta = (
            select(Compra)
            .join(Compra.usuario)
            .join(Compra.evento)
            .options(contains_eager(Compra.usuario), contains_eager(Compra.evento))
        )
        if estado:
            consulta = consulta.where(Compra.estado == estado)
        if evento_id:
            consulta = consulta.where(Compra.evento_id == evento_id)
        if usuario_id:
            consulta = consulta.where(Compra.usuario_id == usuario_id)
        if desde:
            consulta = consulta.where(Compra.creado_en >= desde)
        if hasta:
            consulta = consulta.where(Compra.creado_en <= hasta)
        return consulta.order_by(Compra.creado_en.desc(), Compra.id)

    def existe_activa(self, usuario_id: uuid.UUID, evento_id: uuid.UUID) -> bool:
        return self._existe(usuario_id, evento_id, ESTADOS_ACTIVOS)

    def existe_aprobada(self, usuario_id: uuid.UUID, evento_id: uuid.UUID) -> bool:
        return self._existe(usuario_id, evento_id, (EstadoCompra.APROBADA,))

    @staticmethod
    def _existe(usuario_id: uuid.UUID, evento_id: uuid.UUID, estados: tuple) -> bool:
        return bool(
            db.session.scalar(
                select(
                    exists().where(
                        Compra.usuario_id == usuario_id,
                        Compra.evento_id == evento_id,
                        Compra.estado.in_(estados),
                    )
                )
            )
        )

    def bloquear_activas_de_evento(self, evento_id: uuid.UUID) -> list[Compra]:
        """Compras PENDIENTE/APROBADA del evento, con la fila bloqueada.

        Si un webhook está aprobando una de ellas, se espera a que termine y se re-evalúa el
        estado: ninguna aprobación queda sin reembolsar (RN-13).
        """
        return list(
            db.session.scalars(
                select(Compra)
                .where(Compra.evento_id == evento_id, Compra.estado.in_(ESTADOS_ACTIVOS))
                .order_by(Compra.id)
                .with_for_update()
            )
        )
