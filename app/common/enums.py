"""Enumeraciones del dominio compartidas entre módulos."""

from enum import StrEnum


class Rol(StrEnum):
    USUARIO = "USUARIO"
    ARTISTA = "ARTISTA"
    ADMIN = "ADMIN"


class EstadoUsuario(StrEnum):
    ACTIVO = "ACTIVO"
    SUSPENDIDO = "SUSPENDIDO"


class EstadoEvento(StrEnum):
    BORRADOR = "BORRADOR"
    PROGRAMADO = "PROGRAMADO"
    EN_VIVO = "EN_VIVO"
    FINALIZADO = "FINALIZADO"
    CANCELADO = "CANCELADO"


class EstadoCompra(StrEnum):
    PENDIENTE = "PENDIENTE"
    APROBADA = "APROBADA"
    RECHAZADA = "RECHAZADA"
    ANULADA = "ANULADA"
    REEMBOLSADA = "REEMBOLSADA"


class MetodoPago(StrEnum):
    CARD = "CARD"
    PSE = "PSE"
    NEQUI = "NEQUI"


class EstadoRetiro(StrEnum):
    PENDIENTE = "PENDIENTE"
    PAGADO = "PAGADO"
    RECHAZADO = "RECHAZADO"


class TipoCuenta(StrEnum):
    AHORROS = "AHORROS"
    CORRIENTE = "CORRIENTE"
    NEQUI = "NEQUI"


class TipoReproduccion(StrEnum):
    VIVO = "VIVO"
    REPLAY = "REPLAY"


class CategoriaEvento(StrEnum):
    POP = "POP"
    ROCK = "ROCK"
    URBANO = "URBANO"
    ELECTRONICA = "ELECTRONICA"
    SALSA_TROPICAL = "SALSA_TROPICAL"
    FOLCLOR = "FOLCLOR"
    JAZZ_BLUES = "JAZZ_BLUES"
    CLASICA = "CLASICA"
    OTRO = "OTRO"
