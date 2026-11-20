"""Utilidades SQL compartidas."""


def patron_contiene(texto: str) -> str:
    """Patrón `ILIKE '%texto%'` donde `%`, `_` y `\\` del usuario se tratan como texto."""
    escapado = texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escapado}%"
