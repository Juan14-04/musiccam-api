"""Transmisión simulada: URLs ficticias y token de reproducción JWT (§11.1)."""

import secrets
from datetime import UTC, datetime, timedelta

import jwt

from app.common.clock import ahora_utc
from app.common.enums import TipoReproduccion
from app.infrastructure.streaming.base import ConId, Credenciales, StreamingProvider, TokenDTO

ALGORITMO = "HS256"


class StreamingSimulado(StreamingProvider):
    def __init__(
        self, media_base_url: str, rtmp_base_url: str, playback_secret: str, token_ttl_min: int
    ) -> None:
        self._media = media_base_url.rstrip("/")
        self._rtmp = rtmp_base_url.rstrip("/")
        # Secreto propio, distinto del JWT de sesión: un token de reproducción no abre la API.
        self._secreto = playback_secret
        self._ttl = timedelta(minutes=token_ttl_min)

    def generar_credenciales(self, evento: ConId) -> Credenciales:
        return Credenciales(
            rtmp_url=f"{self._rtmp}/{evento.id}", stream_key=secrets.token_urlsafe(32)
        )

    def url_vivo(self, evento: ConId, token: str) -> str:
        return f"{self._media}/live/{evento.id}/index.m3u8?token={token}"

    def url_replay(self, evento: ConId, token: str) -> str:
        return f"{self._media}/replay/{evento.id}/index.m3u8?token={token}"

    def emitir_token_reproduccion(
        self, evento: ConId, usuario: ConId, tipo: TipoReproduccion
    ) -> TokenDTO:
        ahora = ahora_utc()
        exp = int((ahora + self._ttl).timestamp())
        claims = {
            "evento_id": str(evento.id),
            "usuario_id": str(usuario.id),
            "tipo": TipoReproduccion(tipo).value,
            "iat": int(ahora.timestamp()),
            "exp": exp,
        }
        token = jwt.encode(claims, self._secreto, algorithm=ALGORITMO)
        return TokenDTO(token=token, expira_en=datetime.fromtimestamp(exp, UTC))
