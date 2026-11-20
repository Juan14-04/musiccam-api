"""Cálculos de dinero en COP enteros. Único lugar donde se redondea."""

from decimal import ROUND_HALF_UP, Decimal


def calcular_comision(monto_cop: int, comision_pct: Decimal | int | str) -> tuple[int, int]:
    """Devuelve `(comision_cop, neto_artista_cop)` con redondeo half-up (RN-06)."""
    bruto = Decimal(monto_cop) * Decimal(str(comision_pct)) / Decimal(100)
    comision = int(bruto.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return comision, monto_cop - comision
