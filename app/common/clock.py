"""Reloj del sistema, fijable en pruebas."""

from datetime import UTC, datetime


class _Reloj:
    fijo: datetime | None = None


def ahora_utc() -> datetime:
    """Hora actual en UTC (o la hora fijada por las pruebas)."""
    return _Reloj.fijo or datetime.now(UTC)


def fijar_ahora(valor: datetime | None) -> None:
    """Fija la hora devuelta por `ahora_utc()`; `None` restablece el reloj real."""
    _Reloj.fijo = valor
