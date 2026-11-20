"""Casos de uso del panel del artista: estadísticas, saldo y retiros (RN-14, RN-15)."""

import uuid
from dataclasses import dataclass
from typing import Any

from flask import current_app

from app.common.clock import ahora_utc
from app.common.enums import EstadoEvento, EstadoRetiro, TipoCuenta
from app.common.errors import Conflicto, NoEncontrado, ReglaDeNegocio
from app.common.pagination import paginar
from app.extensions import db
from app.modules.artistas.models import Retiro
from app.modules.artistas.repository import ArtistasRepository, VentaDiaria
from app.modules.eventos.models import Evento
from app.modules.eventos.service import EventoService
from app.modules.usuarios.models import Usuario
from app.modules.usuarios.service import UsuarioService


def _venta_diaria(venta: VentaDiaria) -> dict[str, Any]:
    return {
        "fecha": venta.fecha,
        "entradas": venta.entradas,
        "ingresos_netos_cop": venta.netos_cop,
    }


@dataclass(frozen=True)
class PerfilArtista:
    """Perfil público de un artista; `siguiendo` es `None` si la petición no trae sesión."""

    artista: Usuario
    seguidores: int
    eventos_proximos: list[Evento]
    siguiendo: bool | None


class ArtistasService:
    """Coordina perfiles públicos, estadísticas, saldo y retiros de artistas."""

    def __init__(
        self,
        repo: ArtistasRepository | None = None,
        eventos: EventoService | None = None,
        usuarios: UsuarioService | None = None,
    ) -> None:
        self.repo = repo or ArtistasRepository()
        self.eventos = eventos or EventoService()
        self.usuarios = usuarios or UsuarioService()

    # --- Perfil público (§8.3) -------------------------------------------------------------

    def listar_publicos(self, *, q: str | None = None, page: int = 1, page_size: int = 20):
        """Lista artistas públicos activos con búsqueda opcional."""
        return self.usuarios.listar_artistas(q=q, page=page, page_size=page_size)

    def perfil_publico(self, artista_id: uuid.UUID, viewer: Usuario | None) -> PerfilArtista:
        """Solo artistas `ACTIVO`: uno suspendido, inexistente o que no es artista es 404."""
        artista = self.usuarios.obtener_artista_activo(artista_id)
        return PerfilArtista(
            artista=artista,
            seguidores=self.repo.contar_seguidores(artista.id),
            eventos_proximos=self.eventos.proximos_de_artista(artista.id),
            siguiendo=None if viewer is None else self.repo.sigue_a(viewer.id, artista.id),
        )

    # --- Saldo (RN-14) ---------------------------------------------------------------------

    def saldo(self, artista: Usuario) -> dict[str, int]:
        """El saldo no se guarda: se calcula en cada consulta (§5.7).

        `disponible = ingresos_liberados − en_proceso − retirado`. Los retiros RECHAZADO no
        restan. `retenido` es lo ganado en eventos que aún no terminan.
        """
        ingresos = self.repo.sumas_de_ingresos(artista.id)
        retiros = self.repo.sumas_de_retiros(artista.id)
        return {
            "disponible_cop": ingresos.liberado_cop - retiros.en_proceso_cop - retiros.retirado_cop,
            "retenido_cop": ingresos.retenido_cop,
            "en_proceso_cop": retiros.en_proceso_cop,
            "retirado_cop": retiros.retirado_cop,
        }

    # --- Estadísticas ----------------------------------------------------------------------

    def estadisticas(self, artista: Usuario) -> dict[str, Any]:
        """Resumen global del artista (forma de §8.4.1)."""
        totales = self.repo.totales_de_ventas(artista.id)
        por_estado = self.repo.eventos_por_estado(artista.id)
        promedio = self.repo.pico_promedio(artista.id)
        return {
            "resumen": {
                "eventos_por_estado": {e.value: por_estado.get(e, 0) for e in EstadoEvento},
                "entradas_vendidas": totales.entradas,
                "ingresos_brutos_cop": totales.brutos_cop,
                "comision_plataforma_cop": totales.comision_cop,
                "ingresos_netos_cop": totales.netos_cop,
                "seguidores": self.repo.contar_seguidores(artista.id),
                # Único decimal de la API; 0.0 si ningún evento llegó a transmitirse.
                "pico_espectadores_promedio": round(float(promedio), 1) if promedio else 0.0,
            },
            "saldo": self.saldo(artista),
            "ventas_por_dia": [_venta_diaria(v) for v in self.repo.ventas_por_dia(artista.id)],
        }

    def estadisticas_de_evento(self, artista: Usuario, evento_id: uuid.UUID) -> dict[str, Any]:
        """Métricas de un evento propio. Uno ajeno responde 403 `NO_ES_DUENO`."""
        evento = self.eventos.obtener_de_artista(artista, evento_id)
        totales = self.repo.totales_de_ventas(artista.id, evento.id)
        duracion = None
        if evento.inicio_real is not None and evento.fin_real is not None:
            duracion = max(0, int((evento.fin_real - evento.inicio_real).total_seconds() // 60))
        return {
            "evento_id": evento.id,
            "entradas_vendidas": totales.entradas,
            "ingresos_brutos_cop": totales.brutos_cop,
            "ingresos_netos_cop": totales.netos_cop,
            "pico_espectadores": evento.pico_espectadores,
            "vistas_vivo": evento.vistas_vivo,
            "vistas_replay": evento.vistas_replay,
            "duracion_real_min": duracion,
            "favoritos": self.repo.contar_favoritos(evento.id),
            "ventas_por_dia": [
                _venta_diaria(v) for v in self.repo.ventas_por_dia(artista.id, evento.id)
            ],
        }

    # --- Retiros (RN-15) -------------------------------------------------------------------

    def solicitar_retiro(
        self,
        artista: Usuario,
        *,
        monto_cop: int,
        banco: str,
        tipo_cuenta: TipoCuenta,
        cuenta_ultimos4: str,
    ) -> Retiro:
        """Crea un retiro. Con `RETIRO_AUTO_APROBAR` nace `PAGADO` (payout simulado).

        Se bloquea la fila del artista en `usuarios` mientras se calcula el disponible y se
        inserta el retiro, para que dos solicitudes simultáneas no puedan exceder el saldo.
        """
        minimo = current_app.config["RETIRO_MIN_COP"]
        if monto_cop < minimo:
            mensaje = f"El monto mínimo de retiro es {minimo} COP."
            raise ReglaDeNegocio("MONTO_FUERA_DE_RANGO", mensaje, {"monto_cop": [mensaje]})

        self.usuarios.bloquear(artista.id)
        disponible = self.saldo(artista)["disponible_cop"]
        if monto_cop > disponible:
            mensaje = f"El monto supera tu saldo disponible ({disponible} COP)."
            raise ReglaDeNegocio("SALDO_INSUFICIENTE", mensaje, {"monto_cop": [mensaje]})

        retiro = Retiro(
            artista_id=artista.id,
            monto_cop=monto_cop,
            banco=banco.strip(),
            tipo_cuenta=tipo_cuenta,
            cuenta_ultimos4=cuenta_ultimos4,
            estado=EstadoRetiro.PENDIENTE,
        )
        if current_app.config["RETIRO_AUTO_APROBAR"]:
            retiro.estado = EstadoRetiro.PAGADO
            retiro.referencia_pago = f"sim-payout-{uuid.uuid4()}"
            retiro.resuelto_en = ahora_utc()
        try:
            self.repo.agregar_retiro(retiro)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return retiro

    # --- Resolución de retiros por el admin (RN-16) ----------------------------------------

    def listar_retiros_admin(
        self, *, estado: EstadoRetiro | None = None, page: int = 1, page_size: int = 20
    ):
        """Lista retiros para administración, opcionalmente filtrados por estado."""
        return paginar(self.repo.consulta_retiros_admin(estado), page, page_size)

    def tiene_retiros_pendientes(self, artista_id: uuid.UUID) -> bool:
        """Indica si el artista tiene retiros que impiden cambiar su rol."""
        return self.repo.tiene_retiros_pendientes(artista_id)

    def aprobar_retiro(self, admin: Usuario, retiro_id: uuid.UUID) -> Retiro:
        """PENDIENTE → PAGADO con payout simulado."""
        retiro = self._retiro_pendiente_bloqueado(retiro_id)
        retiro.estado = EstadoRetiro.PAGADO
        retiro.referencia_pago = f"sim-payout-{uuid.uuid4()}"
        self._cerrar_retiro(retiro, admin)
        return retiro

    def rechazar_retiro(self, admin: Usuario, retiro_id: uuid.UUID, motivo: str) -> Retiro:
        """PENDIENTE → RECHAZADO. Deja de restar del saldo (RN-14)."""
        retiro = self._retiro_pendiente_bloqueado(retiro_id)
        retiro.estado = EstadoRetiro.RECHAZADO
        retiro.motivo_rechazo = motivo.strip()
        self._cerrar_retiro(retiro, admin)
        return retiro

    def _retiro_pendiente_bloqueado(self, retiro_id: uuid.UUID) -> Retiro:
        """Bloquea la fila: dos admins a la vez no pueden resolver el mismo retiro."""
        retiro = self.repo.obtener_retiro(retiro_id, bloquear=True)
        if retiro is None:
            raise NoEncontrado("RETIRO_NO_ENCONTRADO", "El retiro no existe.")
        if retiro.estado != EstadoRetiro.PENDIENTE:
            raise Conflicto("RETIRO_YA_RESUELTO", "El retiro ya fue resuelto.")
        return retiro

    @staticmethod
    def _cerrar_retiro(retiro: Retiro, admin: Usuario) -> None:
        retiro.resuelto_por = admin.id
        retiro.resuelto_en = ahora_utc()
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

    def listar_retiros(
        self,
        artista: Usuario,
        *,
        estado: EstadoRetiro | None = None,
        page: int = 1,
        page_size: int = 20,
    ):
        """Lista el historial de retiros del propio artista."""
        return paginar(self.repo.consulta_retiros(artista.id, estado), page, page_size)
