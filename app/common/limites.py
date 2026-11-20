"""Tope de tamaño por ruta para las subidas grandes.

`MAX_CONTENT_LENGTH` (global) está pensado para las fotos de perfil. Solo la subida del video del
tráiler (`PUT /api/v1/eventos/<id>/trailer`) admite peticiones más grandes, hasta
`MAX_TRAILER_MB` + 1 MB de holgura para el multipart. El resto de la API conserva el tope global.
"""

from flask import Flask, request

MEGA = 1024 * 1024


def registrar_topes_por_ruta(app: Flask) -> None:
    @app.before_request
    def _ampliar_tope_para_video() -> None:
        regla = request.url_rule.rule if request.url_rule else ""
        if request.method == "PUT" and regla.endswith("/trailer"):
            request.max_content_length = (app.config["MAX_TRAILER_MB"] + 1) * MEGA
