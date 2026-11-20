"""Casos de uso de eventos: CRUD, publicar, cancelar y catálogo (RN-02, RN-04, RN-05, RN-21)."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from flask import current_app

from app.common.clock import ahora_utc
from app.common.enums import EstadoEvento, EstadoUsuario, Rol
from app.common.errors import Conflicto, NoAutorizado, NoEncontrado, Prohibido, ReglaDeNegocio
from app.common.pagination import paginar
from app.extensions import db
from app.infrastructure.storage.base import FileStorage
from app.infrastructure.storage.local import LocalFileStorage
from app.modules.compras.service import ComprasService
from app.modules.eventos.models import Evento
from app.modules.eventos.repository import EventoRepository
from app.modules.eventos.trailer import detectar_formato
from app.modules.usuarios.models import Usuario
from app.modules.usuarios.service import UsuarioService

# RN-04: al publicar, la fecha de inicio debe estar al menos 30 min en el futuro.
MARGEN_PUBLICACION = timedelta(minutes=30)

# RN-04: en PROGRAMADO estos campos solo se editan si no hay compras APROBADA.
CAMPOS_SIN_COMPRAS = {"precio_cop", "replay_habilitado", "fecha_inicio", "duracion_estimada_min"}
CAMPOS_PROGRAMADO = {"titulo", "descripcion", "portada_url", "youtube_url", "categoria"}

# El tráiler (enlace o video subido) se puede cambiar mientras el evento sea editable.
ESTADOS_CON_TRAILER = {EstadoEvento.BORRADOR, EstadoEvento.PROGRAMADO}
PREFIJO_UPLOADS = "/uploads/"


@dataclass
class DetalleEvento:
    """Evento más los datos que dependen de la sesión (`None` = sin sesión, se omiten)."""

    evento: Evento
    es_favorito: bool | None = None
    comprado: bool | None = None


class EventoService:
    """Gestiona el ciclo de vida, la visibilidad y la audiencia de los eventos."""

    def __init__(
        self,
        repo: EventoRepository | None = None,
        usuarios: UsuarioService | None = None,
        compras: ComprasService | None = None,
        storage: FileStorage | None = None,
    ) -> None:
        self.repo = repo or EventoRepository()
        self.usuarios = usuarios or UsuarioService()
        self.compras = compras or ComprasService()
        self.storage = storage or LocalFileStorage(current_app.config["UPLOAD_DIR"])

    # --- Consultas -------------------------------------------------------------------------

    def listar_catalogo(self, *, page: int = 1, page_size: int = 20, **filtros: Any):
        """Lista eventos del catálogo con filtros y paginación."""
        return paginar(self.repo.consulta_catalogo(**filtros), page, page_size)

    def listar_de_artista(
        self,
        artista: Usuario,
        *,
        estado: EstadoEvento | None = None,
        page: int = 1,
        page_size: int = 20,
    ):
        """Lista únicamente los eventos del artista autenticado."""
        return paginar(self.repo.consulta_de_artista(artista.id, estado), page, page_size)

    def obtener_detalle(self, evento_id: uuid.UUID, viewer: Usuario | None) -> DetalleEvento:
        """Detalle público. Lo no visible (RN-21) solo lo ve su dueño; para el resto es 404."""
        evento = self.repo.obtener_por_id(evento_id)
        es_dueno = viewer is not None and evento is not None and evento.artista_id == viewer.id
        if evento is None or not (es_dueno or self._es_visible_publicamente(evento)):
            raise self._no_encontrado()
        if viewer is None:
            return DetalleEvento(evento)
        es_favorito = self.repo.es_favorito(viewer.id, evento.id)
        comprado = self.compras.tiene_acceso(viewer.id, evento.id)
        return DetalleEvento(evento, es_favorito=es_favorito, comprado=comprado)

    def listar_admin(self, *, page: int = 1, page_size: int = 20, **filtros: Any):
        """Lista todos los eventos para el panel administrativo."""
        return paginar(self.repo.consulta_admin(**filtros), page, page_size)

    def tiene_eventos_en_curso(self, artista_id: uuid.UUID) -> bool:
        """¿Tiene eventos `PROGRAMADO` o `EN_VIVO`?"""
        return any(
            self.repo.contar_por_estado(artista_id, estado) > 0
            for estado in (EstadoEvento.PROGRAMADO, EstadoEvento.EN_VIVO)
        )

    def suspender_por_admin(self, evento_id: uuid.UUID, motivo: str) -> Evento:
        """Moderación: corta venta y reproducción sin finalizar ni cancelar el evento."""
        evento = self._obtener_para_moderar(evento_id)
        if evento.suspendido_admin:
            raise Conflicto("TRANSICION_INVALIDA", "El evento ya está suspendido.")
        evento.suspendido_admin = True
        evento.motivo_suspension = motivo.strip()
        self._confirmar()
        return evento

    def reactivar_por_admin(self, evento_id: uuid.UUID) -> Evento:
        """Retira la suspensión administrativa de un evento suspendido."""
        evento = self._obtener_para_moderar(evento_id)
        if not evento.suspendido_admin:
            raise Conflicto("TRANSICION_INVALIDA", "El evento no está suspendido.")
        evento.suspendido_admin = False
        evento.motivo_suspension = None
        self._confirmar()
        return evento

    def _obtener_para_moderar(self, evento_id: uuid.UUID) -> Evento:
        evento = self.repo.obtener_por_id(evento_id, bloquear=True)
        if evento is None:
            raise self._no_encontrado()
        return evento

    def obtener_visible(self, evento_id: uuid.UUID) -> Evento:
        """Evento visible para el público (RN-21); si no, 404 aunque exista."""
        evento = self.repo.obtener_por_id(evento_id)
        if evento is None or not self._es_visible_publicamente(evento):
            raise self._no_encontrado()
        return evento

    def proximos_de_artista(self, artista_id: uuid.UUID, limite: int = 20) -> list[Evento]:
        """Próximos eventos visibles de un artista (hasta `limite`), por `fecha_inicio`."""
        consulta = self.repo.consulta_proximos_de_artista(artista_id).limit(limite)
        return list(db.session.scalars(consulta))

    # --- Escrituras ------------------------------------------------------------------------

    def crear(self, artista: Usuario, datos: dict[str, Any]) -> Evento:
        """Crea un borrador propiedad del artista después de validar el precio."""
        self._validar_precio(datos["precio_cop"])
        evento = Evento(artista_id=artista.id, estado=EstadoEvento.BORRADOR, **datos)
        self.repo.agregar(evento)
        self._confirmar()
        return evento

    def actualizar(self, artista: Usuario, evento_id: uuid.UUID, datos: dict[str, Any]) -> Evento:
        """Aplica solo los cambios permitidos por el estado y las ventas del evento."""
        evento = self._obtener_propio(artista, evento_id)
        self._validar_campos_editables(evento, datos)
        if "precio_cop" in datos:
            self._validar_precio(datos["precio_cop"])
        if "fecha_inicio" in datos and evento.estado == EstadoEvento.PROGRAMADO:
            self._validar_fecha_de_publicacion(datos["fecha_inicio"])
        for campo, valor in datos.items():
            setattr(evento, campo, valor)
        self._confirmar()
        return evento

    def publicar(self, artista: Usuario, evento_id: uuid.UUID) -> Evento:
        """BORRADOR → PROGRAMADO. Sin ningún cargo al artista (C2)."""
        # Se bloquea la fila del artista para que dos publicaciones simultáneas no se salten
        # el tope de RN-02.
        self.usuarios.bloquear(artista.id)
        evento = self._obtener_propio(artista, evento_id)
        self._exigir_transicion(evento, {EstadoEvento.BORRADOR}, EstadoEvento.PROGRAMADO)
        self._validar_fecha_de_publicacion(evento.fecha_inicio)
        self._validar_tope_artista_nuevo(artista)
        evento.estado = EstadoEvento.PROGRAMADO
        self._confirmar()
        return evento

    def cancelar(self, artista: Usuario, evento_id: uuid.UUID) -> Evento:
        """BORRADOR/PROGRAMADO → CANCELADO (RN-13)."""
        evento = self._obtener_propio(artista, evento_id)
        self._exigir_transicion(
            evento, {EstadoEvento.BORRADOR, EstadoEvento.PROGRAMADO}, EstadoEvento.CANCELADO
        )
        evento.estado = EstadoEvento.CANCELADO
        self._reembolsar_compras(evento)
        self._confirmar()
        return evento

    def eliminar(self, artista: Usuario, evento_id: uuid.UUID) -> None:
        """Borrado físico: solo BORRADOR (nunca tiene historial financiero)."""
        evento = self._obtener_propio(artista, evento_id)
        if evento.estado != EstadoEvento.BORRADOR:
            raise Conflicto("TRANSICION_INVALIDA", "Solo se pueden eliminar eventos en BORRADOR.")
        video = self._ruta_de(evento.trailer_video_url)
        self.repo.eliminar(evento)
        self._confirmar()
        if video:
            self.storage.eliminar(video)

    # --- Tráiler en video ------------------------------------------------------------------

    def subir_trailer(self, artista: Usuario, evento_id: uuid.UUID, contenido: bytes) -> Evento:
        """Guarda el video del tráiler (MP4/WebM) y reemplaza el anterior."""
        evento = self._obtener_propio(artista, evento_id)
        self._exigir_estado(evento, ESTADOS_CON_TRAILER, "cambiar el tráiler")
        limite_mb = current_app.config["MAX_TRAILER_MB"]
        if not contenido:
            raise self._video_invalido("El archivo está vacío.")
        if len(contenido) > limite_mb * 1024 * 1024:
            raise self._video_invalido(f"El video supera el máximo de {limite_mb} MB.")
        formato = detectar_formato(contenido)
        if formato is None:
            raise self._video_invalido("Sube un video en formato MP4 o WebM.")
        ruta = f"trailers/{evento.id}/{uuid.uuid4()}.{formato}"
        anterior = self._ruta_de(evento.trailer_video_url)
        self.storage.guardar(ruta, contenido)
        evento.trailer_video_url = PREFIJO_UPLOADS + ruta
        try:
            self._confirmar()
        except Exception:
            self.storage.eliminar(ruta)
            raise
        if anterior:
            self.storage.eliminar(anterior)
        return evento

    def eliminar_trailer(self, artista: Usuario, evento_id: uuid.UUID) -> Evento:
        """Quita el video del tráiler y borra el archivo. Idempotente."""
        evento = self._obtener_propio(artista, evento_id)
        self._exigir_estado(evento, ESTADOS_CON_TRAILER, "cambiar el tráiler")
        anterior = self._ruta_de(evento.trailer_video_url)
        evento.trailer_video_url = None
        self._confirmar()
        if anterior:
            self.storage.eliminar(anterior)
        return evento

    @staticmethod
    def _video_invalido(mensaje: str) -> ReglaDeNegocio:
        return ReglaDeNegocio("VIDEO_INVALIDO", mensaje, {"trailer": [mensaje]})

    @staticmethod
    def _ruta_de(url: str | None) -> str | None:
        if url and url.startswith(PREFIJO_UPLOADS):
            return url.removeprefix(PREFIJO_UPLOADS)
        return None

    # --- Transmisión (la orquesta `transmision.service`; las escrituras viven aquí, §7.3) ---

    def rotar_clave_emision(
        self, artista: Usuario, evento_id: uuid.UUID, stream_key_hash: str
    ) -> Evento:
        """Guarda el hash de la nueva clave (RN-11); la anterior deja de valer."""
        evento = self._obtener_propio(artista, evento_id)
        self._exigir_estado(evento, {EstadoEvento.PROGRAMADO}, "generar credenciales")
        evento.stream_key_hash = stream_key_hash
        self._confirmar()
        return evento

    def iniciar_emision(
        self,
        artista: Usuario,
        evento_id: uuid.UUID,
        clave_valida: Callable[[str | None], bool],
    ) -> Evento:
        """PROGRAMADO → EN_VIVO (RN-05). `clave_valida` recibe el hash guardado (RQ6)."""
        # Se bloquea al artista y luego el evento: dos inicios simultáneos no pueden dejar
        # dos eventos EN_VIVO a la vez.
        self.usuarios.bloquear(artista.id)
        evento = self._obtener_propio(artista, evento_id)
        self._exigir_estado(evento, {EstadoEvento.PROGRAMADO}, "iniciar la transmisión")
        if evento.suspendido_admin:
            raise Prohibido("EVENTO_SUSPENDIDO", "El evento está suspendido por moderación.")
        if not clave_valida(evento.stream_key_hash):
            raise NoAutorizado("CLAVE_EMISION_INVALIDA", "La clave de emisión no es válida.")
        ahora = ahora_utc()
        ventana = timedelta(minutes=current_app.config["START_WINDOW_MIN"])
        if current_app.config["ENFORCE_START_WINDOW"] and ahora < evento.fecha_inicio - ventana:
            raise Conflicto(
                "VENTANA_DE_INICIO_NO_ABIERTA",
                f"Solo se puede iniciar desde {ventana.seconds // 60} minutos antes de la hora.",
            )
        if self.repo.contar_por_estado(artista.id, EstadoEvento.EN_VIVO) > 0:
            raise Conflicto("YA_HAY_EVENTO_EN_VIVO", "Ya tienes un evento en vivo.")
        evento.estado = EstadoEvento.EN_VIVO
        evento.inicio_real = ahora
        self._confirmar()
        return evento

    def finalizar_emision(self, artista: Usuario, evento_id: uuid.UUID) -> Evento:
        """EN_VIVO → FINALIZADO (RN-12)."""
        evento = self._obtener_propio(artista, evento_id)
        self._exigir_estado(evento, {EstadoEvento.EN_VIVO}, "finalizar la transmisión")
        evento.estado = EstadoEvento.FINALIZADO
        evento.fin_real = ahora_utc()
        evento.espectadores_actuales = 0
        evento.replay_disponible = evento.replay_habilitado
        self._confirmar()
        return evento

    def obtener_de_artista(self, artista: Usuario, evento_id: uuid.UUID) -> Evento:
        """Lectura de un evento propio (sin bloqueo)."""
        evento = self.repo.obtener_por_id(evento_id)
        if evento is None:
            raise self._no_encontrado()
        if evento.artista_id != artista.id:
            raise Prohibido("NO_ES_DUENO", "Este evento no es tuyo.")
        return evento

    def obtener_para_reproduccion(self, evento_id: uuid.UUID) -> Evento:
        """Lectura sin filtros de visibilidad: `transmision` decide el acceso (RN-10)."""
        evento = self.repo.obtener_por_id(evento_id)
        if evento is None:
            raise self._no_encontrado()
        return evento

    def registrar_entrada(self, evento_id: uuid.UUID, *, vivo: bool) -> bool:
        """Cuenta una entrada con un `UPDATE` atómico. `False` si el evento ya no la admite."""
        contada = self.repo.sumar_entrada(evento_id, vivo=vivo)
        self._confirmar()
        return contada

    def registrar_salida(self, evento_id: uuid.UUID) -> None:
        """Reduce el contador de audiencia de forma atómica y nunca por debajo de cero."""
        self.repo.restar_salida(evento_id)
        self._confirmar()

    def fijar_audiencia(self, usuario: Usuario, evento_id: uuid.UUID, espectadores: int) -> Evento:
        """Solo desarrollo: fija la audiencia para poblar estadísticas en demos."""
        evento = self.repo.obtener_por_id(evento_id)
        if evento is None:
            raise self._no_encontrado()
        if evento.artista_id != usuario.id and usuario.rol != Rol.ADMIN:
            raise Prohibido("NO_ES_DUENO", "Este evento no es tuyo.")
        self.repo.fijar_audiencia(evento_id, espectadores)
        self._confirmar()
        return self.repo.obtener_por_id(evento_id)

    # --- Reglas internas -------------------------------------------------------------------

    def _reembolsar_compras(self, evento: Evento) -> None:
        """RN-13: APROBADA → REEMBOLSADA y PENDIENTE → ANULADA, en la misma transacción."""
        self.compras.reembolsar_por_evento(evento.id)

    def _obtener_propio(self, artista: Usuario, evento_id: uuid.UUID) -> Evento:
        """Carga el evento con la fila bloqueada y verifica propiedad (anti-IDOR, §4)."""
        evento = self.repo.obtener_por_id(evento_id, bloquear=True)
        if evento is None:
            raise self._no_encontrado()
        if evento.artista_id != artista.id:
            raise Prohibido("NO_ES_DUENO", "Este evento no es tuyo.")
        return evento

    @staticmethod
    def _no_encontrado() -> NoEncontrado:
        return NoEncontrado("EVENTO_NO_ENCONTRADO", "El evento no existe.")

    @staticmethod
    def _es_visible_publicamente(evento: Evento) -> bool:
        """Misma regla que `VISIBLE_EN_CATALOGO` (RN-21), evaluada sobre un evento cargado."""
        if evento.suspendido_admin or evento.artista.estado != EstadoUsuario.ACTIVO:
            return False
        if evento.estado in (EstadoEvento.PROGRAMADO, EstadoEvento.EN_VIVO):
            return True
        return evento.estado == EstadoEvento.FINALIZADO and evento.replay_disponible

    @staticmethod
    def _exigir_transicion(evento: Evento, desde: set[EstadoEvento], hacia: EstadoEvento) -> None:
        if evento.estado not in desde:
            raise Conflicto(
                "TRANSICION_INVALIDA",
                f"Un evento {evento.estado.value} no puede pasar a {hacia.value}.",
            )

    @staticmethod
    def _exigir_estado(evento: Evento, permitidos: set[EstadoEvento], accion: str) -> None:
        if evento.estado not in permitidos:
            esperado = " o ".join(sorted(e.value for e in permitidos))
            raise Conflicto(
                "TRANSICION_INVALIDA",
                f"Para {accion} el evento debe estar {esperado} (hoy {evento.estado.value}).",
            )

    @staticmethod
    def _validar_precio(precio_cop: int) -> None:
        minimo = current_app.config["PRECIO_MIN_COP"]
        maximo = current_app.config["PRECIO_MAX_COP"]
        if not minimo <= precio_cop <= maximo:
            mensaje = f"El precio debe estar entre {minimo} y {maximo} COP."
            raise ReglaDeNegocio("MONTO_FUERA_DE_RANGO", mensaje, {"precio_cop": [mensaje]})

    @staticmethod
    def _validar_fecha_de_publicacion(fecha_inicio: datetime) -> None:
        if fecha_inicio < ahora_utc() + MARGEN_PUBLICACION:
            mensaje = "La fecha de inicio debe ser al menos 30 minutos posterior a la actual."
            raise ReglaDeNegocio("FECHA_INICIO_INVALIDA", mensaje, {"fecha_inicio": [mensaje]})

    def _validar_tope_artista_nuevo(self, artista: Usuario) -> None:
        """RN-02: sin ningún evento FINALIZADO, máximo N eventos PROGRAMADO a la vez."""
        tope = current_app.config["MAX_EVENTOS_PROGRAMADOS_NUEVO"]
        if self.repo.tiene_finalizados(artista.id):
            return
        if self.repo.contar_por_estado(artista.id, EstadoEvento.PROGRAMADO) >= tope:
            raise ReglaDeNegocio(
                "LIMITE_EVENTOS_PROGRAMADOS",
                f"Hasta que finalices tu primer evento puedes tener máximo {tope} programados.",
            )

    def _validar_campos_editables(self, evento: Evento, datos: dict[str, Any]) -> None:
        """RN-04: qué campos se pueden editar según el estado."""
        if evento.estado == EstadoEvento.BORRADOR:
            return
        if evento.estado != EstadoEvento.PROGRAMADO:
            raise Conflicto(
                "EVENTO_NO_EDITABLE",
                f"Un evento {evento.estado.value} no se puede editar.",
            )
        permitidos = set(CAMPOS_PROGRAMADO)
        if not self.repo.tiene_compras_aprobadas(evento.id):
            permitidos |= CAMPOS_SIN_COMPRAS
        bloqueados = sorted(set(datos) - permitidos)
        if bloqueados:
            raise Conflicto(
                "EVENTO_NO_EDITABLE",
                "Con compras aprobadas no se pueden cambiar precio, fecha, duración ni replay.",
                {campo: ["No editable con compras aprobadas."] for campo in bloqueados},
            )

    @staticmethod
    def _confirmar() -> None:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
