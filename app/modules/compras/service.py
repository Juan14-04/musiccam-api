"""Casos de uso de compras y pagos (RN-06..RN-09, RN-13)."""

import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.common.clock import ahora_utc
from app.common.enums import EstadoCompra, EstadoEvento, EstadoUsuario, MetodoPago, Rol
from app.common.errors import Conflicto, NoEncontrado, Prohibido
from app.common.money import calcular_comision
from app.common.pagination import paginar
from app.extensions import db
from app.infrastructure.payments import obtener_gateway
from app.infrastructure.payments.base import (
    APPROVED,
    DECLINED,
    ERROR,
    MONEDA,
    VOIDED,
    CheckoutDTO,
    PaymentGateway,
)
from app.modules.compras.models import Compra
from app.modules.compras.repository import ComprasRepository
from app.modules.eventos.models import Evento
from app.modules.usuarios.models import Usuario

logger = logging.getLogger(__name__)

# Estado de la compra al que lleva cada estado de transacción de Wompi (RN-09).
# `PENDING` (y cualquier estado desconocido) no cambia la compra.
ESTADO_POR_TRANSACCION = {
    APPROVED: EstadoCompra.APROBADA,
    DECLINED: EstadoCompra.RECHAZADA,
    ERROR: EstadoCompra.RECHAZADA,
    VOIDED: EstadoCompra.ANULADA,
}

_INDICE_COMPRA_ACTIVA = "uq_compras_usuario_evento_activa"


def _compra_duplicada() -> Conflicto:
    return Conflicto(
        "COMPRA_DUPLICADA", "Ya tienes una compra pendiente o aprobada de este evento."
    )


def _compra_no_encontrada() -> NoEncontrado:
    return NoEncontrado("COMPRA_NO_ENCONTRADA", "La compra no existe.")


class _ConGateway:
    """Resuelve el gateway al usarlo, para no construirlo si el caso de uso no lo necesita."""

    def __init__(self, repo: ComprasRepository | None, gateway: PaymentGateway | None) -> None:
        self.repo = repo or ComprasRepository()
        self._gateway = gateway

    @property
    def gateway(self) -> PaymentGateway:
        if self._gateway is None:
            self._gateway = obtener_gateway()
        return self._gateway


class ComprasService(_ConGateway):
    """Gestiona compras, acceso derivado y reembolsos."""

    def __init__(
        self, repo: ComprasRepository | None = None, gateway: PaymentGateway | None = None
    ) -> None:
        super().__init__(repo, gateway)

    # --- Acceso (lo usan eventos y, en la Fase 4, transmisión: RN-10) -------------------------

    def tiene_acceso(self, usuario_id: uuid.UUID, evento_id: uuid.UUID) -> bool:
        """¿Existe una compra APROBADA del usuario para el evento? Es el acceso (RN-08)."""
        return self.repo.existe_aprobada(usuario_id, evento_id)

    # --- Compra ----------------------------------------------------------------------------

    def crear(
        self, usuario: Usuario, evento_id: uuid.UUID, metodo_pago: MetodoPago
    ) -> tuple[Compra, CheckoutDTO]:
        """Crea una compra PENDIENTE con precio y comisión congelados (RN-06, RN-07)."""
        ahora = ahora_utc()
        evento = self.repo.obtener_evento_para_compra(evento_id)
        self._exigir_evento_comprable(evento, usuario)

        self.repo.anular_pendientes_vencidas(usuario.id, evento.id, ahora)
        if self.repo.existe_activa(usuario.id, evento.id):
            raise _compra_duplicada()

        # El monto sale del evento guardado, nunca de la petición.
        pct = Decimal(str(current_app.config["PLATFORM_COMMISSION_PCT"]))
        comision, neto = calcular_comision(evento.precio_cop, pct)
        compra = Compra(
            usuario_id=usuario.id,
            evento_id=evento.id,
            referencia=f"conc-{uuid.uuid4().hex}",
            monto_cop=evento.precio_cop,
            comision_pct=pct,
            comision_cop=comision,
            neto_artista_cop=neto,
            metodo_pago=metodo_pago,
            estado=EstadoCompra.PENDIENTE,
            expira_en=ahora + timedelta(minutes=current_app.config["COMPRA_PENDIENTE_TTL_MIN"]),
        )
        try:
            self.repo.agregar(compra)
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            indice = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if indice == _INDICE_COMPRA_ACTIVA:  # carrera: otra petición ganó el insert
                raise _compra_duplicada() from exc
            raise
        except Exception:
            db.session.rollback()
            raise
        return compra, self.gateway.crear_checkout(compra)

    def obtener(self, usuario: Usuario, compra_id: uuid.UUID) -> Compra:
        """Detalle de una compra: su dueño o un ADMIN (anti-IDOR)."""
        compra = self.repo.obtener_por_id(compra_id)
        if compra is None:
            raise _compra_no_encontrada()
        if compra.usuario_id != usuario.id and usuario.rol != Rol.ADMIN:
            raise Prohibido("NO_ES_DUENO", "Esta compra no es tuya.")
        return compra

    def listar_admin(self, *, page: int = 1, page_size: int = 20, **filtros):
        """Listado administrativo de solo lectura."""
        return paginar(self.repo.consulta_admin(**filtros), page, page_size)

    def listar_de_usuario(
        self,
        usuario: Usuario,
        *,
        estado: EstadoCompra | None = None,
        proximos: bool = False,
        page: int = 1,
        page_size: int = 20,
    ):
        """«Mis entradas»: las compras del propio usuario (nunca las de otro)."""
        return paginar(self.repo.consulta_de_usuario(usuario.id, estado, proximos), page, page_size)

    def reembolsar_por_evento(self, evento_id: uuid.UUID) -> int:
        """RN-13: APROBADA → REEMBOLSADA y PENDIENTE → ANULADA.

        No hace `commit`: forma parte de la transacción de `EventoService.cancelar`.
        """
        activas = self.repo.bloquear_activas_de_evento(evento_id)
        for compra in activas:
            aprobada = compra.estado == EstadoCompra.APROBADA
            compra.estado = EstadoCompra.REEMBOLSADA if aprobada else EstadoCompra.ANULADA
        db.session.flush()
        return len(activas)

    # --- Reglas internas -------------------------------------------------------------------

    @staticmethod
    def _exigir_evento_comprable(evento: Evento | None, usuario: Usuario) -> None:
        """RN-07: quién puede comprar y qué eventos están a la venta."""
        if evento is None:
            raise NoEncontrado("EVENTO_NO_ENCONTRADO", "El evento no existe.")
        if evento.artista_id == usuario.id:
            raise Prohibido("COMPRA_PROPIO_EVENTO", "No puedes comprar tu propio evento.")
        # Un borrador o el evento de un artista suspendido no existen para el público.
        if evento.estado == EstadoEvento.BORRADOR or evento.artista.estado != EstadoUsuario.ACTIVO:
            raise NoEncontrado("EVENTO_NO_ENCONTRADO", "El evento no existe.")
        if evento.suspendido_admin:
            raise Prohibido("EVENTO_SUSPENDIDO", "El evento está suspendido.")
        a_la_venta = evento.estado in (EstadoEvento.PROGRAMADO, EstadoEvento.EN_VIVO) or (
            evento.estado == EstadoEvento.FINALIZADO and evento.replay_disponible
        )
        if not a_la_venta:
            raise Conflicto("EVENTO_NO_DISPONIBLE", "El evento no está a la venta.")


class PagosService(_ConGateway):
    """Procesa pagos simulados y webhooks por un único flujo idempotente."""

    def __init__(
        self, repo: ComprasRepository | None = None, gateway: PaymentGateway | None = None
    ) -> None:
        super().__init__(repo, gateway)

    def procesar_evento_wompi(
        self, payload: dict, checksum_header: str | None = None
    ) -> Compra | None:
        """Único camino de pago: lo usan el webhook y el simulador (RN-09).

        Verifica la firma, bloquea la compra y aplica el estado de forma idempotente. Devuelve
        la compra, o `None` si la referencia es desconocida. Lanza `FirmaInvalida` (401).
        """
        transaccion = self.gateway.verificar_evento(payload, checksum_header)
        compra = self.repo.obtener_por_referencia(transaccion.referencia, bloquear=True)
        if compra is None:
            logger.warning("Evento de pago con referencia desconocida: %s", transaccion.referencia)
            db.session.rollback()
            return None

        nuevo = ESTADO_POR_TRANSACCION.get(transaccion.estado)
        monto_ok = (
            transaccion.monto_en_centavos == compra.monto_cop * 100 and transaccion.moneda == MONEDA
        )
        if not monto_ok:
            logger.warning("Monto o moneda distintos al de la compra %s; se ignora", compra.id)
        elif nuevo is not None and compra.estado == EstadoCompra.PENDIENTE:
            # Solo PENDIENTE se mueve: mismo estado = idempotente; los terminales no retroceden.
            compra.estado = nuevo
            compra.wompi_transaccion_id = transaccion.id
            if nuevo == EstadoCompra.APROBADA:
                compra.aprobado_en = ahora_utc()
            logger.info("Compra %s → %s", compra.id, nuevo.value)
        db.session.commit()
        return compra

    def simular(self, usuario: Usuario, referencia: str, resultado: str) -> Compra:
        """Simula el fin del checkout. Genera un evento firmado y lo procesa como el webhook."""
        if not self.gateway.soporta_simulacion:
            raise NoEncontrado("RECURSO_NO_ENCONTRADO", "El simulador no está habilitado.")
        compra = self.repo.obtener_por_referencia(referencia)
        if compra is None:
            raise _compra_no_encontrada()
        if compra.usuario_id != usuario.id:
            raise Prohibido("NO_ES_DUENO", "Esta compra no es tuya.")
        evento = self.gateway.construir_evento_simulado(compra, resultado)
        return self.procesar_evento_wompi(evento)
