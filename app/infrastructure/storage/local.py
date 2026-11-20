"""Almacenamiento en disco local."""

from pathlib import Path

from app.infrastructure.storage.base import FileStorage


class LocalFileStorage(FileStorage):
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).resolve()

    def _resolver(self, ruta: str) -> Path:
        destino = (self.base_dir / ruta).resolve()
        if not destino.is_relative_to(self.base_dir):
            raise ValueError("Ruta fuera del directorio de almacenamiento.")
        return destino

    def guardar(self, ruta: str, contenido: bytes) -> None:
        destino = self._resolver(ruta)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(contenido)

    def eliminar(self, ruta: str) -> None:
        self._resolver(ruta).unlink(missing_ok=True)
