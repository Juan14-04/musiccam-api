"""Cabeceras de seguridad (§12)."""

from flask import Flask, Response, request

# Rutas que devuelven credenciales aunque la petición no lleve token (login, registro, refresh).
PREFIJO_AUTH = "/api/v1/auth/"


def registrar_cabeceras_seguridad(app: Flask) -> None:
    @app.after_request
    def _cabeceras(respuesta: Response) -> Response:
        # Todas las respuestas: el navegador no adivina el tipo de contenido.
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        # Respuestas autenticadas (o que entregan tokens): nada de cachés compartidas.
        if request.headers.get("Authorization") or request.path.startswith(PREFIJO_AUTH):
            respuesta.headers["Cache-Control"] = "no-store"
        return respuesta
