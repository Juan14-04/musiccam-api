"""Puerto de streaming (§11.1). Los servicios dependen solo de `StreamingProvider`."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.common.enums import TipoReproduccion


class ConId(Protocol):
    id: object


@dataclass(frozen=True)
class Credenciales:
    rtmp_url: str
    stream_key: str


@dataclass(frozen=True)
class TokenDTO:
    token: str
    expira_en: datetime


class StreamingProvider(ABC):
    @abstractmethod
    def generar_credenciales(self, evento: ConId) -> Credenciales:
        """URL de ingesta y clave de emisión nueva. La clave solo existe en esta respuesta."""

    @abstractmethod
    def url_vivo(self, evento: ConId, token: str) -> str: ...

    @abstractmethod
    def url_replay(self, evento: ConId, token: str) -> str: ...

    @abstractmethod
    def emitir_token_reproduccion(
        self, evento: ConId, usuario: ConId, tipo: TipoReproduccion
    ) -> TokenDTO:
        """Token corto que autoriza la lectura del flujo (imita la auth delegada de MediaMTX)."""
