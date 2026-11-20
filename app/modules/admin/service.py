"""Casos de uso del panel de administración. Orquesta los servicios de los demás módulos.

No tiene modelo propio. Cada escritura la hace el servicio dueño de la entidad (§7.3): usuarios,
eventos y artistas (retiros); este módulo solo decide quién puede hacer qué y los combina.
"""

import uuid
from decimal import Decimal
from typing import Any

from flask import current_app

from app.common.enums import EstadoEvento, EstadoRetiro, EstadoUsuario, Rol
from app.common.errors import Conflicto
from app.modules.admin.repository import AdminRepository
from app.modules.artistas.models import Retiro
from app.modules.artistas.service import ArtistasService
from app.modules.compras.service import ComprasService
from app.modules.eventos.models import Evento
from app.modules.eventos.service import EventoService
from app.modules.usuarios.models import Usuario
from app.modules.usuarios.service import UsuarioService


class AdminService:
    """Coordina las operaciones del panel sin apropiarse de datos de otros módulos."""

    def __init__(
        self,
        repo: AdminRepository | None = None,
        usuarios: UsuarioService | None = None,
        eventos: EventoService | None = None,
        compras: ComprasService | None = None,
        artistas: ArtistasService | None = None,
    ) -> None:
        self.repo = repo or AdminRepository()
        self.usuarios = usuarios or UsuarioService()
        self.eventos = eventos or EventoService()
        self.compras = compras or ComprasService()
        self.artistas = artistas or ArtistasService()

    # --- Dashboard -------------------------------------------------------------------------

    def dashboard(self) -> dict[str, Any]:
        """Construye los indicadores agregados del panel administrativo."""
        por_rol = self.repo.usuarios_por_rol()
        por_estado = self.repo.usuarios_por_estado()
        eventos = self.repo.eventos_por_estado()
        entradas, brutos, comisiones = self.repo.totales_de_ventas()
        pendientes, monto_pendiente = self.repo.retiros_pendientes()
        return {
            "usuarios": {
                "por_rol": {r.value: por_rol.get(r, 0) for r in Rol},
                "por_estado": {e.value: por_estado.get(e, 0) for e in EstadoUsuario},
            },
            "eventos_por_estado": {e.value: eventos.get(e, 0) for e in EstadoEvento},
            "entradas_vendidas": entradas,
            "ingresos_brutos_cop": brutos,
            "comisiones_acumuladas_cop": comisiones,
            "retiros_pendientes": {"cantidad": pendientes, "monto_cop": monto_pendiente},
        }

    # --- Usuarios --------------------------------------------------------------------------

    def listar_usuarios(self, **filtros: Any):
        """Delega el listado filtrado de cuentas al módulo de usuarios."""
        return self.usuarios.listar_admin(**filtros)

    def obtener_usuario(self, usuario_id: uuid.UUID) -> Usuario:
        """Obtiene el detalle de una cuenta por UUID."""
        return self.usuarios.obtener(usuario_id)

    def cambiar_estado(
        self, admin: Usuario, usuario_id: uuid.UUID, estado: EstadoUsuario
    ) -> Usuario:
        """Suspende o reactiva una cuenta mediante las reglas del servicio dueño."""
        return self.usuarios.cambiar_estado(admin, usuario_id, estado)

    def cambiar_rol(
        self, admin: Usuario, usuario_id: uuid.UUID, rol: Rol, nombre_artistico: str | None
    ) -> Usuario:
        """Cambia el rol permitido y valida pendientes antes de degradar un artista."""
        return self.usuarios.cambiar_rol(
            admin, usuario_id, rol, nombre_artistico, self._validar_baja_de_artista
        )

    def _validar_baja_de_artista(self, artista: Usuario) -> None:
        """Un artista con eventos en curso o retiros pendientes no puede dejar de serlo."""
        motivos = {}
        if self.eventos.tiene_eventos_en_curso(artista.id):
            motivos["eventos"] = ["Tiene eventos PROGRAMADO o EN_VIVO."]
        if self.artistas.tiene_retiros_pendientes(artista.id):
            motivos["retiros"] = ["Tiene retiros PENDIENTE."]
        if motivos:
            raise Conflicto(
                "ROL_NO_CAMBIABLE",
                "El artista tiene eventos en curso o retiros pendientes.",
                motivos,
            )

    # --- Eventos (moderación) --------------------------------------------------------------

    def listar_eventos(self, **filtros: Any):
        """Delega el listado de eventos del panel al módulo dueño."""
        return self.eventos.listar_admin(**filtros)

    def suspender_evento(self, evento_id: uuid.UUID, motivo: str) -> Evento:
        """Suspende la venta y reproducción de un evento por moderación."""
        return self.eventos.suspender_por_admin(evento_id, motivo)

    def reactivar_evento(self, evento_id: uuid.UUID) -> Evento:
        """Reactiva un evento cuya suspensión administrativa estaba vigente."""
        return self.eventos.reactivar_por_admin(evento_id)

    # --- Compras y retiros -----------------------------------------------------------------

    def listar_compras(self, **filtros: Any):
        """Delega la consulta de compras con filtros al módulo de compras."""
        return self.compras.listar_admin(**filtros)

    def listar_retiros(self, *, estado: EstadoRetiro | None = None, **paginacion: Any):
        """Lista retiros del panel, opcionalmente filtrados por estado."""
        return self.artistas.listar_retiros_admin(estado=estado, **paginacion)

    def aprobar_retiro(self, admin: Usuario, retiro_id: uuid.UUID) -> Retiro:
        """Aprueba un retiro pendiente a través del servicio de artistas."""
        return self.artistas.aprobar_retiro(admin, retiro_id)

    def rechazar_retiro(self, admin: Usuario, retiro_id: uuid.UUID, motivo: str) -> Retiro:
        """Rechaza un retiro pendiente y conserva el motivo."""
        return self.artistas.rechazar_retiro(admin, retiro_id, motivo)

    # --- Configuración (solo lectura) ------------------------------------------------------

    def configuracion(self) -> dict[str, Any]:
        """Lista explícita de valores públicos: nada que sea un secreto ni una credencial."""
        c = current_app.config
        return {
            "entorno": c["APP_ENV"],
            "comision_plataforma_pct": float(Decimal(str(c["PLATFORM_COMMISSION_PCT"]))),
            "precio_min_cop": c["PRECIO_MIN_COP"],
            "precio_max_cop": c["PRECIO_MAX_COP"],
            "retiro_min_cop": c["RETIRO_MIN_COP"],
            "retiro_auto_aprobar": c["RETIRO_AUTO_APROBAR"],
            "compra_pendiente_ttl_min": c["COMPRA_PENDIENTE_TTL_MIN"],
            "max_eventos_programados_nuevo": c["MAX_EVENTOS_PROGRAMADOS_NUEVO"],
            "politica_datos_version": c["POLITICA_DATOS_VERSION"],
            "pasarela_pagos": c["PAYMENT_GATEWAY"],
            "proveedor_streaming": c["STREAMING_PROVIDER"],
            "enforce_start_window": c["ENFORCE_START_WINDOW"],
            "start_window_min": c["START_WINDOW_MIN"],
            "playback_token_ttl_min": c["PLAYBACK_TOKEN_TTL_MIN"],
            "jwt_access_ttl_min": int(c["JWT_ACCESS_TOKEN_EXPIRES"].total_seconds() // 60),
            "jwt_refresh_ttl_dias": c["JWT_REFRESH_TOKEN_EXPIRES"].days,
            "max_upload_mb": c["MAX_UPLOAD_MB"],
            "enable_dev_endpoints": c["ENABLE_DEV_ENDPOINTS"],
            "media_base_url": c["MEDIA_BASE_URL"],
            "rtmp_base_url": c["RTMP_BASE_URL"],
        }
