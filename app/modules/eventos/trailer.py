"""Validación del video del tráiler subido por el artista.

El formato se reconoce por el contenido (firma del archivo), nunca por el nombre ni por el
`Content-Type` que envía el cliente. Solo se aceptan contenedores que los navegadores reproducen
con `<video>`: MP4 (ISO BMFF) y WebM. QuickTime (`.mov`) se rechaza porque no todos lo reproducen.
"""

FIRMA_WEBM = b"\x1aE\xdf\xa3"  # cabecera EBML


def detectar_formato(contenido: bytes) -> str | None:
    """Devuelve la extensión (`mp4` o `webm`) o `None` si no es un video admitido."""
    if len(contenido) >= 12 and contenido[4:8] == b"ftyp":
        marca = contenido[8:12]
        return None if marca.startswith(b"qt") else "mp4"
    if contenido.startswith(FIRMA_WEBM):
        return "webm"
    return None
