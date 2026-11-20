"""Selección del proveedor de streaming según `STREAMING_PROVIDER`."""

from collections.abc import Callable, Mapping
from typing import Any

from flask import current_app

from app.infrastructure.streaming.base import StreamingProvider
from app.infrastructure.streaming.simulado import StreamingSimulado

# Futuro: "mediamtx" → MediaMtxProvider (§11.2). Solo cambia este registro.
FABRICAS: dict[str, Callable[[Mapping[str, Any]], StreamingProvider]] = {
    "simulado": lambda cfg: StreamingSimulado(
        cfg["MEDIA_BASE_URL"],
        cfg["RTMP_BASE_URL"],
        cfg["PLAYBACK_TOKEN_SECRET"],
        cfg["PLAYBACK_TOKEN_TTL_MIN"],
    ),
}


def validar_configuracion(config: Mapping[str, Any]) -> None:
    nombre = config["STREAMING_PROVIDER"]
    if nombre not in FABRICAS:
        raise RuntimeError(
            f"STREAMING_PROVIDER desconocido: {nombre!r} (válidos: {sorted(FABRICAS)})"
        )


def obtener_provider() -> StreamingProvider:
    return FABRICAS[current_app.config["STREAMING_PROVIDER"]](current_app.config)
