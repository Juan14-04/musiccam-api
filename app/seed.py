"""Datos de demo (`flask seed`).

Todo se crea **a través de los servicios** (registro, eventos, compras, transmisión, social,
retiros, admin), no con INSERT directo: así se respetan las reglas de negocio y el saldo, las
estadísticas y el dashboard salen coherentes.

Es idempotente: cada usuario, evento y retiro se busca antes de crearse. Un evento ya existente
(mismo artista y título) se da por completo y no se vuelve a procesar.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import click
from flask import current_app

from app.common.clock import ahora_utc, fijar_ahora
from app.common.enums import CategoriaEvento, EstadoUsuario, MetodoPago, Rol, TipoCuenta
from app.modules.admin.service import AdminService
from app.modules.artistas.service import ArtistasService
from app.modules.auth.service import AuthService
from app.modules.compras.service import ComprasService, PagosService
from app.modules.eventos.models import Evento
from app.modules.eventos.service import EventoService
from app.modules.social.service import SocialService
from app.modules.transmision.service import TransmisionService
from app.modules.usuarios.models import Usuario
from app.modules.usuarios.service import UsuarioService

CLAVE_DEMO = "Demo1234"  # cumple RN-03 (letra + dígito); solo para datos de demostración

ARTISTAS = (
    ("artista1@demo.co", "Marta Rojas", "Luna Cumbiera", "Cumbia y vallenato desde Barranquilla."),
    ("artista2@demo.co", "Iván Quispe", "Trío Andino", "Música andina colombiana en formato trío."),
    ("artista3@demo.co", "Sara Neón", "Neón Beat", "Electrónica en vivo con visuales."),
)
USUARIOS = (
    ("usuario1@demo.co", "Ana Gómez"),
    ("usuario2@demo.co", "Beto Pérez"),
    ("usuario3@demo.co", "Carla Díaz"),
    ("usuario4@demo.co", "Diego Ruiz"),
    ("usuario5@demo.co", "Elena Mora"),
)


@dataclass(frozen=True)
class Credencial:
    rol: str
    email: str
    password: str


@contextmanager
def _en_el_instante(instante: datetime) -> Iterator[None]:
    """Fija el reloj (`common.clock`) para dejar historia coherente; siempre lo restablece."""
    fijar_ahora(instante)
    try:
        yield
    finally:
        fijar_ahora(None)


@contextmanager
def _configuracion_temporal(**valores: Any) -> Iterator[None]:
    """Cambia ajustes mientras se siembra y los devuelve a su valor original."""
    config = current_app.config
    anteriores = {clave: config[clave] for clave in valores}
    config.update(valores)
    try:
        yield
    finally:
        config.update(anteriores)


class Sembrador:
    def __init__(self) -> None:
        self.usuarios = UsuarioService()
        self.auth = AuthService(self.usuarios)
        self.eventos = EventoService()
        self.compras = ComprasService()
        self.pagos = PagosService()
        self.transmision = TransmisionService()
        self.social = SocialService()
        self.artistas = ArtistasService()
        self.admin = AdminService()
        self.credenciales: list[Credencial] = []

    # --- Cuentas ---------------------------------------------------------------------------

    def _admin_activo_existente(self) -> Usuario:
        administradores = self.usuarios.listar_admin(
            rol=Rol.ADMIN, estado=EstadoUsuario.ACTIVO, page_size=1
        )["items"]
        if not administradores:
            raise click.ClickException(
                "Crea un administrador activo directamente en la base de datos antes de "
                "ejecutar el seed."
            )
        return administradores[0]

    def _cuenta(
        self,
        email: str,
        nombre: str,
        rol: Rol,
        nombre_artistico: str | None = None,
        biografia: str | None = None,
    ) -> Usuario:
        self.credenciales.append(Credencial(rol.value, email, CLAVE_DEMO))
        existente = self.usuarios.obtener_por_email(email)
        if existente is not None:
            return existente
        # Nombres completos de la demo: la primera palabra es el nombre; el resto, el apellido.
        primero, _, resto = nombre.partition(" ")
        usuario = self.auth.registrar(
            nombre=primero,
            apellido=resto or None,
            email=email,
            password=CLAVE_DEMO,
            rol=rol,
            nombre_artistico=nombre_artistico,
            acepta_tratamiento_datos=True,
        )
        if biografia:
            self.usuarios.actualizar_perfil(usuario, {"biografia": biografia})
        return usuario

    # --- Eventos ---------------------------------------------------------------------------

    def _evento(
        self,
        artista: Usuario,
        titulo: str,
        categoria: CategoriaEvento,
        precio_cop: int,
        fecha_inicio: datetime,
        descripcion: str,
    ) -> tuple[Evento, bool]:
        """Devuelve `(evento, creado)`. Si ya existía, no se vuelve a procesar."""
        for existente in self.eventos.listar_de_artista(artista, page_size=100)["items"]:
            if existente.titulo == titulo:
                return existente, False
        evento = self.eventos.crear(
            artista,
            {
                "titulo": titulo,
                "descripcion": descripcion,
                "categoria": categoria,
                "precio_cop": precio_cop,
                "fecha_inicio": fecha_inicio,
                "duracion_estimada_min": 90,
                "replay_habilitado": True,
            },
        )
        return evento, True

    def _programar(self, artista: Usuario, evento: Evento) -> Evento:
        return self.eventos.publicar(artista, evento.id)

    def _comprar(
        self,
        usuario: Usuario,
        evento: Evento,
        resultado: str | None = "APPROVED",
        metodo: MetodoPago = MetodoPago.NEQUI,
    ) -> None:
        """Compra y, si `resultado` no es None, simula el desenlace del pago (como el simulador)."""
        compra, _ = self.compras.crear(usuario, evento.id, metodo)
        if resultado is not None:
            self.pagos.simular(usuario, compra.referencia, resultado)

    def _transmitir(self, artista: Usuario, evento: Evento) -> None:
        clave = self.transmision.generar_credenciales(artista, evento.id)
        self.transmision.iniciar(artista, evento.id, clave.stream_key)

    # --- Siembra ---------------------------------------------------------------------------

    def sembrar(self) -> list[Credencial]:
        admin = self._admin_activo_existente()
        a1, a2, a3 = (
            self._cuenta(email, nombre, Rol.ARTISTA, artistico, bio)
            for email, nombre, artistico, bio in ARTISTAS
        )
        u1, u2, u3, u4, u5 = (
            self._cuenta(email, nombre, Rol.USUARIO) for email, nombre in USUARIOS
        )

        ahora = ahora_utc()
        dia = timedelta(days=1)

        # 1) BORRADOR: solo creado.
        self._evento(
            a1,
            "Gira de verano (borrador)",
            CategoriaEvento.SALSA_TROPICAL,
            20_000,
            ahora + 30 * dia,
            "Aún sin publicar.",
        )

        # 2) PROGRAMADO con compras en varios estados (aprobadas, pendiente y rechazada).
        e2, nuevo = self._evento(
            a1,
            "Cumbia bajo las estrellas",
            CategoriaEvento.SALSA_TROPICAL,
            25_000,
            ahora + 10 * dia,
            "Noche de cumbia y vallenato.",
        )
        if nuevo:
            self._programar(a1, e2)
            self._comprar(u1, e2)
            self._comprar(u2, e2)
            self._comprar(u3, e2, resultado=None)  # queda PENDIENTE
            self._comprar(u4, e2, "DECLINED")  # queda RECHAZADA

        # 3) PROGRAMADO de otro artista.
        e3, nuevo = self._evento(
            a2,
            "Noche de música andina",
            CategoriaEvento.FOLCLOR,
            30_000,
            ahora + 20 * dia,
            "Quenas, charangos y zampoñas.",
        )
        if nuevo:
            self._programar(a2, e3)
            self._comprar(u1, e3, metodo=MetodoPago.PSE)

        # 4) EN_VIVO: se publica "hace 35 minutos" para que hoy esté en emisión.
        e4, nuevo = self._evento(
            a3,
            "Neón Beat en vivo",
            CategoriaEvento.ELECTRONICA,
            20_000,
            ahora - timedelta(minutes=4),
            "Sesión de electrónica con visuales.",
        )
        if nuevo:
            self._evento_en_vivo(a3, e4, ahora, [u2, u3])

        # 5) FINALIZADO con replay, cinco ventas hace cuatro días (alimenta saldo y estadísticas).
        e5, nuevo = self._evento(
            a1,
            "Concierto de aniversario",
            CategoriaEvento.SALSA_TROPICAL,
            30_000,
            ahora - 4 * dia + timedelta(hours=2),
            "Repertorio completo del aniversario.",
        )
        if nuevo:
            self._evento_finalizado(a1, e5, ahora, [u1, u2, u3, u4, u5])

        # 6) CANCELADO: una compra aprobada (queda REEMBOLSADA) y una anulada.
        e6, nuevo = self._evento(
            a3,
            "Festival cancelado",
            CategoriaEvento.ELECTRONICA,
            15_000,
            ahora + 15 * dia,
            "Se canceló por fuerza mayor.",
        )
        if nuevo:
            self._programar(a3, e6)
            self._comprar(u4, e6)
            self._comprar(u5, e6, "VOIDED")  # queda ANULADA
            self.eventos.cancelar(a3, e6.id)

        # 7) PROGRAMADO y SUSPENDIDO por moderación.
        e7, nuevo = self._evento(
            a2,
            "Evento suspendido por moderación",
            CategoriaEvento.FOLCLOR,
            22_000,
            ahora + 12 * dia,
            "Suspendido tras una denuncia.",
        )
        if nuevo:
            self._programar(a2, e7)
            self._comprar(u5, e7)
            self.admin.suspender_evento(e7.id, "Contenido en revisión por denuncia")

        # 8) PROGRAMADO próximo.
        e8, nuevo = self._evento(
            a3,
            "Sesión electrónica de estreno",
            CategoriaEvento.ELECTRONICA,
            18_000,
            ahora + 5 * dia,
            "Estreno del nuevo set.",
        )
        if nuevo:
            self._programar(a3, e8)
            self._comprar(u3, e8)

        self._social(a1, a2, a3, (u1, u2, u3, u4, u5), (e2, e3, e4, e5, e8))
        self._retiros(a1, admin)
        return self.credenciales

    def _evento_en_vivo(self, artista: Usuario, evento: Evento, ahora: datetime, publico) -> None:
        """Publica y compra "hace 35 min", empieza al llegar su hora y deja a `publico` viendo."""
        inicio = evento.fecha_inicio
        with _en_el_instante(inicio - timedelta(minutes=35)):
            self._programar(artista, evento)
            for usuario in publico:
                self._comprar(usuario, evento)
        with _en_el_instante(inicio):
            self._transmitir(artista, evento)
        for usuario in publico:
            self.transmision.entrar(usuario, evento.id)

    def _evento_finalizado(
        self, artista: Usuario, evento: Evento, ahora: datetime, publico
    ) -> None:
        """Ciclo completo en el pasado: ventas, emisión, audiencia y replay posterior."""
        inicio = evento.fecha_inicio
        with _en_el_instante(inicio - timedelta(hours=2)):
            self._programar(artista, evento)
            for usuario in publico:
                self._comprar(usuario, evento)
        with _en_el_instante(inicio):
            self._transmitir(artista, evento)
            for usuario in publico:
                self.transmision.entrar(usuario, evento.id)
        with _en_el_instante(inicio + timedelta(minutes=90)):
            self.transmision.finalizar(artista, evento.id)
        for usuario in publico[:2]:  # ya con la emisión terminada: dos reproducciones del replay
            self.transmision.entrar(usuario, evento.id)

    def _social(self, a1, a2, a3, usuarios, eventos) -> None:
        u1, u2, u3, u4, u5 = usuarios
        for usuario, artistas in (
            (u1, (a1, a2)),
            (u2, (a1,)),
            (u3, (a3,)),
            (u4, (a1, a3)),
            (u5, (a2,)),
        ):
            for artista in artistas:
                self.social.seguir(usuario, artista.id)
        e2, e3, e4, e5, e8 = eventos
        for usuario, favoritos in ((u1, (e2, e4)), (u2, (e2, e5)), (u3, (e3,)), (u4, (e8,))):
            for evento in favoritos:
                self.social.agregar_favorito(usuario, evento.id)

    def _retiros(self, artista: Usuario, admin: Usuario) -> None:
        """Un retiro PAGADO (aprobado por el admin) y otro PENDIENTE, sobre el saldo real."""
        if self.artistas.listar_retiros(artista)["total"]:
            return
        retiro = self._solicitar(artista, 50_000, "Bancolombia", TipoCuenta.AHORROS, "0042")
        self.admin.aprobar_retiro(admin, retiro.id)
        self._solicitar(artista, 50_000, "Nequi", TipoCuenta.NEQUI, "7788")

    def _solicitar(self, artista, monto, banco, tipo, ultimos4):
        return self.artistas.solicitar_retiro(
            artista, monto_cop=monto, banco=banco, tipo_cuenta=tipo, cuenta_ultimos4=ultimos4
        )


def sembrar_demo() -> list[Credencial]:
    """Punto de entrada de `flask seed`. Se niega a correr con `APP_ENV=production`."""
    if current_app.config["APP_ENV"] == "production":
        raise click.ClickException("`flask seed` no se ejecuta con APP_ENV=production.")
    # Los ajustes que alteran el resultado se fijan durante la siembra y luego se restauran.
    with _configuracion_temporal(RETIRO_AUTO_APROBAR=False, ENFORCE_START_WINDOW=False):
        return Sembrador().sembrar()


__all__ = ["CLAVE_DEMO", "Credencial", "sembrar_demo"]
