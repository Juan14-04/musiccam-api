"""Casos de uso de transmisión y reproducción (RN-05, RN-10, RN-11, RN-12). Sin modelo propio.

Las lecturas y escrituras sobre el evento pasan por `EventoService` (dueño de la entidad) y el
acceso se consulta a `ComprasService` (§7.3). Nada aquí registra en logs la clave de emisión
ni los tokens.
"""

import hashlib
import hmac
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.common.clock import ahora_utc
from app.common.enums import EstadoEvento, Rol, TipoReproduccion
from app.common.errors import Prohibido
from app.infrastructure.streaming import obtener_provider
from app.infrastructure.streaming.base import Credenciales, StreamingProvider
from app.modules.compras.service import ComprasService
from app.modules.eventos.models import Evento
from app.modules.eventos.service import EventoService
from app.modules.usuarios.models import Usuario


@dataclass(frozen=True)
class EstadoTransmision:
    estado: EstadoEvento
    espectadores_actuales: int
    pico_espectadores: int
    duracion_transcurrida_min: int
    inicio_real: datetime | None
    fin_real: datetime | None


@dataclass(frozen=True)
class Reproduccion:
    tipo: TipoReproduccion
    url: str
    token: str
    expira_en: datetime


def hash_clave_emision(clave: str) -> str:
    """SHA-256 en hexadecimal: lo único de la clave que se guarda (RN-11)."""
    return hashlib.sha256(clave.encode()).hexdigest()


def _comparador_de_clave(clave: str) -> Callable[[str | None], bool]:
    """Compara en tiempo constante la clave recibida contra el hash guardado."""
    recibido = hash_clave_emision(clave)

    def comparar(hash_guardado: str | None) -> bool:
        return hash_guardado is not None and hmac.compare_digest(recibido, hash_guardado)

    return comparar


def _acceso_denegado() -> Prohibido:
    return Prohibido("ACCESO_NO_AUTORIZADO", "No tienes acceso a la reproducción de este evento.")


class TransmisionService:
    """Coordina permisos de reproducción, estados de emisión y proveedor multimedia."""

    def __init__(
        self,
        eventos: EventoService | None = None,
        compras: ComprasService | None = None,
        provider: StreamingProvider | None = None,
    ) -> None:
        self.eventos = eventos or EventoService()
        self.compras = compras or ComprasService()
        self._provider = provider

    @property
    def provider(self) -> StreamingProvider:
        if self._provider is None:
            self._provider = obtener_provider()
        return self._provider

    # --- Emisión (artista dueño) -----------------------------------------------------------

    def generar_credenciales(self, artista: Usuario, evento_id: uuid.UUID) -> Credenciales:
        """Genera o rota la clave. Se devuelve en claro solo aquí; se guarda su hash."""
        evento = self.eventos.obtener_de_artista(artista, evento_id)
        credenciales = self.provider.generar_credenciales(evento)
        self.eventos.rotar_clave_emision(
            artista, evento_id, hash_clave_emision(credenciales.stream_key)
        )
        return credenciales

    def iniciar(self, artista: Usuario, evento_id: uuid.UUID, stream_key: str) -> EstadoTransmision:
        """Valida la clave de emisión y cambia el evento programado a en vivo."""
        evento = self.eventos.iniciar_emision(artista, evento_id, _comparador_de_clave(stream_key))
        return self._estado(evento)

    def finalizar(self, artista: Usuario, evento_id: uuid.UUID) -> EstadoTransmision:
        """Finaliza la emisión y devuelve el resumen de su estado."""
        return self._estado(self.eventos.finalizar_emision(artista, evento_id))

    def estado(self, artista: Usuario, evento_id: uuid.UUID) -> EstadoTransmision:
        """Obtiene el estado y la audiencia del evento propio del artista."""
        return self._estado(self.eventos.obtener_de_artista(artista, evento_id))

    # --- Reproducción (RN-10) --------------------------------------------------------------

    def entrar(self, usuario: Usuario, evento_id: uuid.UUID) -> Reproduccion:
        """Entrega la URL de vivo o replay solo con acceso (C4) y cuenta la entrada."""
        evento = self.eventos.obtener_para_reproduccion(evento_id)
        if evento.suspendido_admin:
            raise Prohibido("EVENTO_SUSPENDIDO", "El evento está suspendido.")
        tipo = self._tipo_disponible(evento)
        if tipo is None or not self._tiene_derecho(usuario, evento):
            raise _acceso_denegado()

        emitido = self.provider.emitir_token_reproduccion(evento, usuario, tipo)
        if tipo == TipoReproduccion.VIVO:
            url = self.provider.url_vivo(evento, emitido.token)
        else:
            url = self.provider.url_replay(evento, emitido.token)
        # El UPDATE atómico vuelve a exigir el estado: si el evento cambió entre la lectura y
        # ahora (p. ej. se finalizó), no se entrega acceso.
        if not self.eventos.registrar_entrada(evento.id, vivo=tipo == TipoReproduccion.VIVO):
            raise _acceso_denegado()
        return Reproduccion(tipo=tipo, url=url, token=emitido.token, expira_en=emitido.expira_en)

    def salir(self, usuario: Usuario, evento_id: uuid.UUID) -> None:
        """Descuenta un espectador (nunca por debajo de 0). Siempre debe poder ejecutarse."""
        evento = self.eventos.obtener_para_reproduccion(evento_id)
        if not self._tiene_derecho(usuario, evento):
            raise _acceso_denegado()
        self.eventos.registrar_salida(evento.id)

    # --- Solo desarrollo (ENABLE_DEV_ENDPOINTS) --------------------------------------------

    def audiencia_simulada(
        self, usuario: Usuario, evento_id: uuid.UUID, espectadores: int
    ) -> EstadoTransmision:
        """Fija la audiencia de demostración en entornos con herramientas de desarrollo."""
        return self._estado(self.eventos.fijar_audiencia(usuario, evento_id, espectadores))

    # --- Reglas internas -------------------------------------------------------------------

    @staticmethod
    def _tipo_disponible(evento: Evento) -> TipoReproduccion | None:
        """RN-10: VIVO si está EN_VIVO; REPLAY si FINALIZADO con `replay_disponible`."""
        if evento.estado == EstadoEvento.EN_VIVO:
            return TipoReproduccion.VIVO
        if evento.estado == EstadoEvento.FINALIZADO and evento.replay_disponible:
            return TipoReproduccion.REPLAY
        return None

    def _tiene_derecho(self, usuario: Usuario, evento: Evento) -> bool:
        """Dueño, ADMIN (moderación) o comprador con compra APROBADA (C4)."""
        if evento.artista_id == usuario.id or usuario.rol == Rol.ADMIN:
            return True
        return self.compras.tiene_acceso(usuario.id, evento.id)

    @staticmethod
    def _estado(evento: Evento) -> EstadoTransmision:
        transcurrido = 0
        if evento.inicio_real is not None:
            fin = evento.fin_real or ahora_utc()
            transcurrido = max(0, int((fin - evento.inicio_real).total_seconds() // 60))
        return EstadoTransmision(
            estado=evento.estado,
            espectadores_actuales=evento.espectadores_actuales,
            pico_espectadores=evento.pico_espectadores,
            duracion_transcurrida_min=transcurrido,
            inicio_real=evento.inicio_real,
            fin_real=evento.fin_real,
        )
