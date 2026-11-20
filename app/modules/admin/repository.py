"""Agregaciones del dashboard de administración. **Solo lectura**: no escribe nada.

Todo son consultas SQL agregadas (`COUNT`, `SUM`, `GROUP BY`); no se traen filas a Python.
"""

from sqlalchemy import func, select

from app.common.enums import EstadoCompra, EstadoEvento, EstadoRetiro, EstadoUsuario, Rol
from app.extensions import db
from app.modules.artistas.models import Retiro
from app.modules.compras.models import Compra
from app.modules.eventos.models import Evento
from app.modules.usuarios.models import Usuario


class AdminRepository:
    def usuarios_por_rol(self) -> dict[Rol, int]:
        filas = db.session.execute(
            select(Usuario.rol, func.count(Usuario.id)).group_by(Usuario.rol)
        ).all()
        return {rol: int(total) for rol, total in filas}

    def usuarios_por_estado(self) -> dict[EstadoUsuario, int]:
        filas = db.session.execute(
            select(Usuario.estado, func.count(Usuario.id)).group_by(Usuario.estado)
        ).all()
        return {estado: int(total) for estado, total in filas}

    def eventos_por_estado(self) -> dict[EstadoEvento, int]:
        filas = db.session.execute(
            select(Evento.estado, func.count(Evento.id)).group_by(Evento.estado)
        ).all()
        return {estado: int(total) for estado, total in filas}

    def totales_de_ventas(self) -> tuple[int, int, int]:
        """`(entradas, ingresos_brutos_cop, comisiones_cop)` de las compras APROBADA."""
        fila = db.session.execute(
            select(
                func.count(Compra.id),
                func.coalesce(func.sum(Compra.monto_cop), 0),
                func.coalesce(func.sum(Compra.comision_cop), 0),
            ).where(Compra.estado == EstadoCompra.APROBADA)
        ).one()
        return int(fila[0]), int(fila[1]), int(fila[2])

    def retiros_pendientes(self) -> tuple[int, int]:
        """`(cantidad, monto_cop)` de los retiros PENDIENTE."""
        fila = db.session.execute(
            select(func.count(Retiro.id), func.coalesce(func.sum(Retiro.monto_cop), 0)).where(
                Retiro.estado == EstadoRetiro.PENDIENTE
            )
        ).one()
        return int(fila[0]), int(fila[1])
