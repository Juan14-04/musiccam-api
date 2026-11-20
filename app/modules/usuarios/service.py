"""Casos de uso de usuarios: creación, perfil y foto de perfil (RN-18)."""

import uuid
from collections.abc import Callable
from io import BytesIO
from typing import Any

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.exc import IntegrityError

from app.common.clock import ahora_utc
from app.common.enums import EstadoUsuario, Rol
from app.common.errors import Conflicto, NoEncontrado, Prohibido, ReglaDeNegocio
from app.common.pagination import paginar
from app.extensions import db
from app.infrastructure.storage.base import FileStorage
from app.infrastructure.storage.local import LocalFileStorage
from app.modules.usuarios.models import Usuario
from app.modules.usuarios.repository import UsuarioRepository

PREFIJO_FOTOS = "/uploads/"
LADO_FOTO = 512
FORMATOS_FOTO = {"JPEG", "PNG", "WEBP"}

_CONFLICTOS_POR_INDICE = {
    "uq_usuarios_email_lower": Conflicto("EMAIL_YA_REGISTRADO", "El correo ya está registrado."),
    "uq_usuarios_nombre_artistico_lower": Conflicto(
        "NOMBRE_ARTISTICO_EN_USO", "El nombre artístico ya está en uso."
    ),
}


def _foto_invalida(mensaje: str) -> ReglaDeNegocio:
    return ReglaDeNegocio("FOTO_INVALIDA", mensaje, {"foto": [mensaje]})


def procesar_foto(contenido: bytes) -> bytes:
    """Valida por contenido y devuelve WebP 512×512 cuadrado, sin EXIF (RN-18)."""
    try:
        with Image.open(BytesIO(contenido)) as imagen:
            if imagen.format not in FORMATOS_FOTO:
                raise _foto_invalida("La foto debe ser JPEG, PNG o WebP.")
            imagen.load()
            imagen = ImageOps.exif_transpose(imagen)
            modo = "RGBA" if "A" in imagen.getbands() or "transparency" in imagen.info else "RGB"
            cuadrada = ImageOps.fit(imagen.convert(modo), (LADO_FOTO, LADO_FOTO))
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise _foto_invalida("El archivo no es una imagen válida.") from exc
    salida = BytesIO()
    cuadrada.save(salida, format="WEBP", quality=85)  # sin `exif=`: no se conserva metadata
    return salida.getvalue()


class UsuarioService:
    """Aplica reglas de cuenta y coordina el repositorio y el almacenamiento de fotos."""

    def __init__(
        self, repo: UsuarioRepository | None = None, storage: FileStorage | None = None
    ) -> None:
        self.repo = repo or UsuarioRepository()
        self.storage = storage or LocalFileStorage(current_app.config["UPLOAD_DIR"])

    # --- Consultas -------------------------------------------------------------------------

    def obtener_por_email(self, email: str) -> Usuario | None:
        """Busca una cuenta por correo normalizado; devuelve `None` si no existe."""
        return self.repo.obtener_por_email(email.strip())

    def obtener(self, usuario_id: uuid.UUID) -> Usuario:
        """Devuelve una cuenta por UUID o genera el error de usuario inexistente."""
        usuario = self.repo.obtener_por_id(usuario_id)
        if usuario is None:
            raise NoEncontrado("USUARIO_NO_ENCONTRADO", "El usuario no existe.")
        return usuario

    def obtener_artista_activo(self, artista_id: uuid.UUID) -> Usuario:
        """Un artista `ACTIVO`; si no existe, no es ARTISTA o está suspendido, es 404."""
        artista = self.repo.obtener_por_id(artista_id)
        if artista is None or artista.rol != Rol.ARTISTA or artista.estado != EstadoUsuario.ACTIVO:
            raise NoEncontrado("USUARIO_NO_ENCONTRADO", "El artista no existe.")
        return artista

    def listar_artistas(self, *, q: str | None = None, page: int = 1, page_size: int = 20):
        """Lista artistas activos con búsqueda opcional y paginación."""
        return paginar(self.repo.consulta_artistas_activos(q), page, page_size)

    def listar_admin(
        self,
        *,
        rol: Rol | None = None,
        estado: EstadoUsuario | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ):
        """Lista cuentas para administración aplicando filtros y paginación."""
        return paginar(self.repo.consulta_admin(rol, estado, q), page, page_size)

    def cambiar_estado(
        self, actor: Usuario, usuario_id: uuid.UUID, nuevo: EstadoUsuario
    ) -> Usuario:
        """Suspende o reactiva una cuenta (RN-19) respetando las salvaguardas de RN-20."""
        if usuario_id == actor.id and nuevo == EstadoUsuario.SUSPENDIDO:
            raise self._autoedicion_prohibida()
        # Primero se bloquean los admins activos y después se cuenta; así dos suspensiones
        # simultáneas no pueden dejar el sistema sin administradores.
        activos = self.repo.bloquear_admins_activos()
        objetivo = self._obtener_bloqueado(usuario_id)
        if nuevo == EstadoUsuario.SUSPENDIDO and objetivo.rol == Rol.ADMIN:
            self._exigir_otro_admin_activo(objetivo, activos)
        objetivo.estado = nuevo
        self._confirmar()
        return objetivo

    def cambiar_rol(
        self,
        actor: Usuario,
        usuario_id: uuid.UUID,
        rol: Rol,
        nombre_artistico: str | None,
        validar_baja_de_artista: Callable[[Usuario], None],
    ) -> Usuario:
        """Cambia el rol de otro usuario. `validar_baja_de_artista` revisa eventos y retiros."""
        if rol == Rol.ADMIN:
            raise ReglaDeNegocio(
                "ADMIN_SOLO_BD",
                "Los administradores solo se crean directamente en la base de datos.",
            )
        if usuario_id == actor.id:
            raise self._autoedicion_prohibida()
        activos = self.repo.bloquear_admins_activos()
        objetivo = self._obtener_bloqueado(usuario_id)
        if objetivo.rol == rol:
            return objetivo
        if rol == Rol.ARTISTA:
            if not nombre_artistico or not nombre_artistico.strip():
                raise ReglaDeNegocio(
                    detalles={"nombre_artistico": ["Obligatorio para pasar a ARTISTA."]}
                )
            if self.repo.existe_nombre_artistico(nombre_artistico, excluir_id=objetivo.id):
                raise Conflicto("NOMBRE_ARTISTICO_EN_USO", "El nombre artístico ya está en uso.")
        if objetivo.rol == Rol.ADMIN:
            self._exigir_otro_admin_activo(objetivo, activos)
        if objetivo.rol == Rol.ARTISTA:
            validar_baja_de_artista(objetivo)
        objetivo.rol = rol
        if rol == Rol.ARTISTA:
            objetivo.nombre_artistico = nombre_artistico.strip()
        self._confirmar()
        return objetivo

    @staticmethod
    def _autoedicion_prohibida() -> Prohibido:
        return Prohibido(
            "ADMIN_NO_PUEDE_AUTOMODIFICARSE",
            "Un administrador no puede suspenderse ni cambiar su propio rol.",
        )

    def _obtener_bloqueado(self, usuario_id: uuid.UUID) -> Usuario:
        objetivo = self.repo.obtener_por_id(usuario_id, bloquear=True)
        if objetivo is None:
            raise NoEncontrado("USUARIO_NO_ENCONTRADO", "El usuario no existe.")
        return objetivo

    @staticmethod
    def _exigir_otro_admin_activo(objetivo: Usuario, activos: list[uuid.UUID]) -> None:
        """RN-20: si `objetivo` es un admin activo, debe quedar al menos otro."""
        if objetivo.estado == EstadoUsuario.ACTIVO and not [i for i in activos if i != objetivo.id]:
            raise Conflicto(
                "ULTIMO_ADMIN_ACTIVO", "El sistema no puede quedarse sin administradores activos."
            )

    def bloquear(self, usuario_id: uuid.UUID) -> Usuario:
        """Toma la fila del usuario con `FOR UPDATE` para serializar operaciones sobre él."""
        usuario = self.repo.obtener_por_id(usuario_id, bloquear=True)
        if usuario is None:
            raise NoEncontrado("USUARIO_NO_ENCONTRADO", "El usuario no existe.")
        return usuario

    # --- Escrituras ------------------------------------------------------------------------

    def crear(
        self,
        *,
        nombre: str,
        apellido: str | None = None,
        email: str,
        password_hash: str,
        rol: Rol,
        nombre_artistico: str | None,
        version_politica: str,
    ) -> Usuario:
        """Crea un usuario o artista; el rol `ADMIN` solo se provisiona directamente en BD."""
        if rol == Rol.ADMIN:
            raise ReglaDeNegocio(
                "ADMIN_SOLO_BD",
                "Los administradores solo se crean directamente en la base de datos.",
            )
        email = email.strip().lower()
        if self.repo.existe_email(email):
            raise _CONFLICTOS_POR_INDICE["uq_usuarios_email_lower"]
        if rol == Rol.ARTISTA:
            if not nombre_artistico:
                raise ReglaDeNegocio(detalles={"nombre_artistico": ["Obligatorio para artistas."]})
            if self.repo.existe_nombre_artistico(nombre_artistico):
                raise _CONFLICTOS_POR_INDICE["uq_usuarios_nombre_artistico_lower"]
        else:
            nombre_artistico = None
        usuario = Usuario(
            email=email,
            password_hash=password_hash,
            nombre=nombre.strip(),
            apellido=apellido.strip() if apellido else None,
            rol=rol,
            nombre_artistico=nombre_artistico,
            consentimiento_datos_en=ahora_utc(),
            version_politica_datos=version_politica,
        )
        self.repo.agregar(usuario)
        self._confirmar()
        return usuario

    def actualizar_perfil(self, usuario: Usuario, datos: dict[str, Any]) -> Usuario:
        """Actualiza los campos de perfil permitidos y comprueba unicidad artística."""
        if "nombre_artistico" in datos:
            if usuario.rol != Rol.ARTISTA:
                raise ReglaDeNegocio(
                    detalles={"nombre_artistico": ["Solo los artistas tienen nombre artístico."]}
                )
            if self.repo.existe_nombre_artistico(datos["nombre_artistico"], excluir_id=usuario.id):
                raise _CONFLICTOS_POR_INDICE["uq_usuarios_nombre_artistico_lower"]
        for campo in ("nombre", "biografia", "nombre_artistico"):
            if campo in datos:
                setattr(usuario, campo, datos[campo])
        self._confirmar()
        return usuario

    def subir_foto(self, usuario: Usuario, contenido: bytes) -> Usuario:
        """Valida, normaliza y reemplaza la foto, limpiando el archivo anterior."""
        limite_mb = current_app.config["MAX_UPLOAD_MB"]
        if len(contenido) > limite_mb * 1024 * 1024:
            raise _foto_invalida(f"La foto supera el máximo de {limite_mb} MB.")
        webp = procesar_foto(contenido)
        ruta = f"perfiles/{usuario.id}/{uuid.uuid4()}.webp"
        anterior = self._ruta_de(usuario.foto_url)
        self.storage.guardar(ruta, webp)
        usuario.foto_url = PREFIJO_FOTOS + ruta
        try:
            self._confirmar()
        except Exception:
            self.storage.eliminar(ruta)
            raise
        if anterior:
            self.storage.eliminar(anterior)
        return usuario

    def eliminar_foto(self, usuario: Usuario) -> Usuario:
        """Quita la referencia a la foto del perfil y elimina el archivo guardado."""
        anterior = self._ruta_de(usuario.foto_url)
        usuario.foto_url = None
        self._confirmar()
        if anterior:
            self.storage.eliminar(anterior)
        return usuario

    # --- Internos --------------------------------------------------------------------------

    @staticmethod
    def _ruta_de(foto_url: str | None) -> str | None:
        if foto_url and foto_url.startswith(PREFIJO_FOTOS):
            return foto_url.removeprefix(PREFIJO_FOTOS)
        return None

    @staticmethod
    def _confirmar() -> None:
        """Cierra la transacción; traduce violaciones de unicidad en carrera a 409."""
        try:
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            indice = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if indice in _CONFLICTOS_POR_INDICE:
                raise _CONFLICTOS_POR_INDICE[indice] from exc
            raise
        except Exception:
            db.session.rollback()
            raise
