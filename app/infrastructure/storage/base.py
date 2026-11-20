"""Puerto de almacenamiento de archivos."""

from abc import ABC, abstractmethod


class FileStorage(ABC):
    @abstractmethod
    def guardar(self, ruta: str, contenido: bytes) -> None:
        """Guarda `contenido` en la ruta relativa dada."""

    @abstractmethod
    def eliminar(self, ruta: str) -> None:
        """Elimina el archivo si existe (idempotente)."""
