"""Unidades de `common/*` y configuración."""

from datetime import UTC, datetime

import pytest

from app import create_app
from app.common.clock import ahora_utc, fijar_ahora
from app.common.money import calcular_comision


def test_comision_20000_al_10_por_ciento():
    """★ RN-06: 20.000 COP al 10 % → comisión 2.000 / neto 18.000."""
    assert calcular_comision(20_000, 10) == (2_000, 18_000)


@pytest.mark.parametrize(
    ("monto", "pct", "comision"),
    [(12_345, "10", 1_235), (12_344, "10", 1_234), (5_005, "10", 501), (10_000, "10.5", 1_050)],
)
def test_comision_redondea_half_up_y_suma_exacta(monto, pct, comision):
    calculada, neto = calcular_comision(monto, pct)
    assert calculada == comision
    assert calculada + neto == monto


def test_reloj_se_puede_fijar():
    fijo = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)
    fijar_ahora(fijo)
    assert ahora_utc() == fijo
    fijar_ahora(None)
    assert ahora_utc() != fijo


def test_produccion_no_arranca_con_secretos_por_defecto(monkeypatch):
    for nombre in ("SECRET_KEY", "JWT_SECRET_KEY", "PLAYBACK_TOKEN_SECRET"):
        monkeypatch.delenv(nombre, raising=False)
    with pytest.raises(RuntimeError, match="valor por defecto"):
        create_app("production")


def test_produccion_arranca_con_secretos_propios(monkeypatch):
    for nombre in ("SECRET_KEY", "JWT_SECRET_KEY", "PLAYBACK_TOKEN_SECRET"):
        monkeypatch.setenv(nombre, f"valor-largo-y-unico-para-{nombre}")
    assert create_app("production").config["APP_ENV"] == "production"
