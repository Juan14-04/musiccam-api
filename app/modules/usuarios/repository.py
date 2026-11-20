"""Consultas de `usuarios`. Sin `commit` (lo hace el servicio)."""

import uuid

from sqlalchemy import Select, func, or_, select

from app.common.enums import EstadoUsuario, Rol
from app.common.sql import patron_contiene
from app.extensions import db
from app.modules.usuarios.models import Usuario


class UsuarioRepository:
    def obtener_por_id(self, usuario_id: uuid.UUID, *, bloquear: bool = False) -> Usuario | None:
        consulta = select(Usuario).where(Usuario.id == usuario_id)
        if bloquear:
            # `populate_existing`: tras esperar el bloqueo se lee el estado vigente de la fila.
            consulta = consulta.with_for_update().execution_options(populate_existing=True)
        return db.session.scalar(consulta)

    def obtener_por_email(self, email: str) -> Usuario | None:
        return db.session.scalar(select(Usuario).where(func.lower(Usuario.email) == email.lower()))

    def existe_email(self, email: str) -> bool:
        return self.obtener_por_email(email) is not None

    def existe_nombre_artistico(
        self, nombre_artistico: str, *, excluir_id: uuid.UUID | None = None
    ) -> bool:
        consulta = select(Usuario.id).where(
            func.lower(Usuario.nombre_artistico) == nombre_artistico.lower(),
            Usuario.rol == "ARTISTA",
        )
        if excluir_id is not None:
            consulta = consulta.where(Usuario.id != excluir_id)
        return db.session.scalar(consulta.limit(1)) is not None

    def consulta_artistas_activos(self, q: str | None = None) -> Select:
        """Artistas `ACTIVO` por nombre artístico; `q` busca dentro del nombre."""
        consulta = select(Usuario).where(
            Usuario.rol == Rol.ARTISTA, Usuario.estado == EstadoUsuario.ACTIVO
        )
        if q:
            consulta = consulta.where(
                Usuario.nombre_artistico.ilike(patron_contiene(q), escape="\\")
            )
        return consulta.order_by(func.lower(Usuario.nombre_artistico), Usuario.id)

    def consulta_admin(
        self,
        rol: Rol | None = None,
        estado: EstadoUsuario | None = None,
        q: str | None = None,
    ) -> Select:
        """Todos los usuarios para el panel de administración."""
        consulta = select(Usuario)
        if rol:
            consulta = consulta.where(Usuario.rol == rol)
        if estado:
            consulta = consulta.where(Usuario.estado == estado)
        if q:
            patron = patron_contiene(q)
            consulta = consulta.where(
                or_(
                    Usuario.nombre.ilike(patron, escape="\\"),
                    Usuario.apellido.ilike(patron, escape="\\"),
                    Usuario.email.ilike(patron, escape="\\"),
                    Usuario.nombre_artistico.ilike(patron, escape="\\"),
                )
            )
        return consulta.order_by(Usuario.creado_en.desc(), Usuario.id)

    def bloquear_admins_activos(self) -> list[uuid.UUID]:
        """`SELECT … FOR UPDATE` de los ADMIN `ACTIVO`, siempre en orden de `id` (RN-20).

        Dos operaciones simultáneas sobre administradores se serializan aquí; la segunda, tras
        esperar, vuelve a evaluar el filtro y ya no ve a quien la primera suspendió.
        """
        return list(
            db.session.scalars(
                select(Usuario.id)
                .where(Usuario.rol == Rol.ADMIN, Usuario.estado == EstadoUsuario.ACTIVO)
                .order_by(Usuario.id)
                .with_for_update()
            )
        )

    def agregar(self, usuario: Usuario) -> Usuario:
        db.session.add(usuario)
        db.session.flush()
        return usuario
