"""Consultas del panel del artista.

Todo se calcula con agregaciones SQL (nada de traer filas a Python). Las lecturas sobre
`eventos` y `compras` son de solo lectura (§7.3); lo único que se escribe aquí es `retiros`.
Solo cuentan compras `APROBADA`: `REEMBOLSADA`, `ANULADA`, `RECHAZADA` y `PENDIENTE` nunca suman.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, exists, func, literal_column, select
from sqlalchemy.orm import contains_eager

from app.common.enums import EstadoCompra, EstadoEvento, EstadoRetiro
from app.extensions import db
from app.modules.artistas.models import Retiro
from app.modules.compras.models import Compra
from app.modules.eventos.models import Evento
from app.modules.social.models import Favorito, Seguidor

# Los días de `ventas_por_dia` se agrupan en la hora de Colombia (sin horario de verano).
ZONA_HORARIA = "America/Bogota"


@dataclass(frozen=True)
class SumasDeIngresos:
    liberado_cop: int  # netos de compras APROBADA de eventos FINALIZADO
    retenido_cop: int  # netos de compras APROBADA de eventos PROGRAMADO / EN_VIVO


@dataclass(frozen=True)
class SumasDeRetiros:
    en_proceso_cop: int  # retiros PENDIENTE
    retirado_cop: int  # retiros PAGADO


@dataclass(frozen=True)
class TotalesDeVentas:
    entradas: int
    brutos_cop: int
    comision_cop: int
    netos_cop: int


@dataclass(frozen=True)
class VentaDiaria:
    fecha: date
    entradas: int
    netos_cop: int


def _suma(columna, *condiciones):
    """`COALESCE(SUM(columna) FILTER (WHERE ...), 0)`: entero, nunca nulo."""
    suma = func.sum(columna)
    if condiciones:
        suma = suma.filter(*condiciones)
    return func.coalesce(suma, 0)


class ArtistasRepository:
    # --- Saldo (RN-14) ---------------------------------------------------------------------

    def sumas_de_ingresos(self, artista_id: uuid.UUID) -> SumasDeIngresos:
        fila = db.session.execute(
            select(
                _suma(Compra.neto_artista_cop, Evento.estado == EstadoEvento.FINALIZADO),
                _suma(
                    Compra.neto_artista_cop,
                    Evento.estado.in_([EstadoEvento.PROGRAMADO, EstadoEvento.EN_VIVO]),
                ),
            )
            .select_from(Compra)
            .join(Evento, Evento.id == Compra.evento_id)
            .where(Evento.artista_id == artista_id, Compra.estado == EstadoCompra.APROBADA)
        ).one()
        return SumasDeIngresos(liberado_cop=int(fila[0]), retenido_cop=int(fila[1]))

    def sumas_de_retiros(self, artista_id: uuid.UUID) -> SumasDeRetiros:
        fila = db.session.execute(
            select(
                _suma(Retiro.monto_cop, Retiro.estado == EstadoRetiro.PENDIENTE),
                _suma(Retiro.monto_cop, Retiro.estado == EstadoRetiro.PAGADO),
            ).where(Retiro.artista_id == artista_id)
        ).one()
        return SumasDeRetiros(en_proceso_cop=int(fila[0]), retirado_cop=int(fila[1]))

    # --- Estadísticas ----------------------------------------------------------------------

    def _ventas(self, artista_id: uuid.UUID, evento_id: uuid.UUID | None) -> Select:
        consulta = (
            select()
            .select_from(Compra)
            .join(Evento, Evento.id == Compra.evento_id)
            .where(Evento.artista_id == artista_id, Compra.estado == EstadoCompra.APROBADA)
        )
        if evento_id is not None:
            consulta = consulta.where(Compra.evento_id == evento_id)
        return consulta

    def totales_de_ventas(
        self, artista_id: uuid.UUID, evento_id: uuid.UUID | None = None
    ) -> TotalesDeVentas:
        fila = db.session.execute(
            self._ventas(artista_id, evento_id).add_columns(
                func.count(Compra.id),
                _suma(Compra.monto_cop),
                _suma(Compra.comision_cop),
                _suma(Compra.neto_artista_cop),
            )
        ).one()
        return TotalesDeVentas(
            entradas=int(fila[0]),
            brutos_cop=int(fila[1]),
            comision_cop=int(fila[2]),
            netos_cop=int(fila[3]),
        )

    def ventas_por_dia(
        self, artista_id: uuid.UUID, evento_id: uuid.UUID | None = None
    ) -> list[VentaDiaria]:
        """Solo los días con ventas, por fecha en `America/Bogota`, de más antigua a más nueva."""
        dia = func.date(
            func.timezone(ZONA_HORARIA, func.coalesce(Compra.aprobado_en, Compra.creado_en))
        )
        filas = db.session.execute(
            self._ventas(artista_id, evento_id)
            .add_columns(dia, func.count(Compra.id), _suma(Compra.neto_artista_cop))
            .group_by(literal_column("1"))
            .order_by(literal_column("1"))
        ).all()
        # `add_columns` sobre un `select()` vacío deja las columnas en el orden dado.
        return [VentaDiaria(fecha=f[0], entradas=int(f[1]), netos_cop=int(f[2])) for f in filas]

    def eventos_por_estado(self, artista_id: uuid.UUID) -> dict[EstadoEvento, int]:
        filas = db.session.execute(
            select(Evento.estado, func.count(Evento.id))
            .where(Evento.artista_id == artista_id)
            .group_by(Evento.estado)
        ).all()
        return {estado: int(total) for estado, total in filas}

    def pico_promedio(self, artista_id: uuid.UUID) -> Decimal | None:
        """Promedio de `pico_espectadores` solo de eventos que llegaron a transmitirse."""
        return db.session.scalar(
            select(func.avg(Evento.pico_espectadores)).where(
                Evento.artista_id == artista_id, Evento.inicio_real.is_not(None)
            )
        )

    def contar_seguidores(self, artista_id: uuid.UUID) -> int:
        """`COUNT(*)` sobre `seguidores` (solo lectura; el conteo no se almacena, §5.7)."""
        return int(
            db.session.scalar(
                select(func.count()).select_from(Seguidor).where(Seguidor.artista_id == artista_id)
            )
            or 0
        )

    def sigue_a(self, usuario_id: uuid.UUID, artista_id: uuid.UUID) -> bool:
        return bool(
            db.session.scalar(
                select(
                    exists().where(
                        Seguidor.usuario_id == usuario_id, Seguidor.artista_id == artista_id
                    )
                )
            )
        )

    def contar_favoritos(self, evento_id: uuid.UUID) -> int:
        """`COUNT(*)` sobre `favoritos` de un evento."""
        return int(
            db.session.scalar(
                select(func.count()).select_from(Favorito).where(Favorito.evento_id == evento_id)
            )
            or 0
        )

    # --- Retiros ---------------------------------------------------------------------------

    def agregar_retiro(self, retiro: Retiro) -> Retiro:
        db.session.add(retiro)
        db.session.flush()
        return retiro

    def obtener_retiro(self, retiro_id: uuid.UUID, *, bloquear: bool = False) -> Retiro | None:
        consulta = select(Retiro).where(Retiro.id == retiro_id)
        if bloquear:
            consulta = consulta.with_for_update().execution_options(populate_existing=True)
        return db.session.scalar(consulta)

    def tiene_retiros_pendientes(self, artista_id: uuid.UUID) -> bool:
        return bool(
            db.session.scalar(
                select(
                    exists().where(
                        Retiro.artista_id == artista_id, Retiro.estado == EstadoRetiro.PENDIENTE
                    )
                )
            )
        )

    def consulta_retiros_admin(self, estado: EstadoRetiro | None = None) -> Select:
        """Retiros de todos los artistas (los más recientes primero)."""
        consulta = select(Retiro).join(Retiro.artista).options(contains_eager(Retiro.artista))
        if estado:
            consulta = consulta.where(Retiro.estado == estado)
        return consulta.order_by(Retiro.solicitado_en.desc(), Retiro.id)

    def consulta_retiros(self, artista_id: uuid.UUID, estado: EstadoRetiro | None = None) -> Select:
        consulta = select(Retiro).where(Retiro.artista_id == artista_id)
        if estado:
            consulta = consulta.where(Retiro.estado == estado)
        return consulta.order_by(Retiro.solicitado_en.desc(), Retiro.id)
