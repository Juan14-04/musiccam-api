"""DTOs de eventos."""

import re
from urllib.parse import parse_qs, urlsplit

from flask_smorest.fields import Upload
from marshmallow import RAISE, Schema, ValidationError, fields, pre_dump, validate

from app.common.enums import CategoriaEvento, EstadoEvento
from app.common.schemas import PaginacionArgsSchema, PaginaSchema

_TITULO = validate.Length(min=1, max=150)
_PORTADA = validate.Length(max=255)
_EJEMPLO_FECHA = "2026-12-05T01:00:00+00:00"
_DOMINIOS_YOUTUBE = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
_ID_YOUTUBE = re.compile(r"[A-Za-z0-9_-]{11}")


def _validar_youtube_url(valor: str) -> None:
    try:
        partes = urlsplit(valor)
        puerto = partes.port
    except ValueError as error:
        raise ValidationError("Usa un enlace válido de YouTube.") from error

    dominio = (partes.hostname or "").lower()
    if (
        partes.scheme != "https"
        or dominio not in _DOMINIOS_YOUTUBE
        or partes.username
        or partes.password
        or puerto not in (None, 443)
    ):
        raise ValidationError("Usa un enlace seguro de youtube.com o youtu.be.")

    segmentos = [segmento for segmento in partes.path.split("/") if segmento]
    if dominio in {"youtu.be", "www.youtu.be"}:
        video_id = segmentos[0] if segmentos else ""
    elif partes.path == "/watch":
        video_id = parse_qs(partes.query).get("v", [""])[0]
    elif segmentos and segmentos[0] in {"embed", "live", "shorts"}:
        video_id = segmentos[1] if len(segmentos) > 1 else ""
    else:
        video_id = ""

    if not _ID_YOUTUBE.fullmatch(video_id):
        raise ValidationError("El enlace debe apuntar a un video de YouTube válido.")


_YOUTUBE_URL = validate.And(validate.Length(max=255), _validar_youtube_url)


def _precio(**extra):
    return fields.Int(
        strict=True,
        metadata={
            "description": "Pesos colombianos enteros. Rango según configuración (RN-04).",
            "example": 20000,
        },
        **extra,
    )


class ArtistaResumenSchema(Schema):
    id = fields.UUID(dump_only=True)
    nombre_artistico = fields.Str(dump_only=True, metadata={"example": "Los Cumbieros"})
    foto_url = fields.Str(dump_only=True, allow_none=True)


class EventoResumenSchema(Schema):
    """Forma de los listados."""

    id = fields.UUID(dump_only=True)
    artista = fields.Nested(ArtistaResumenSchema, dump_only=True)
    titulo = fields.Str(dump_only=True, metadata={"example": "Concierto de lanzamiento"})
    categoria = fields.Enum(CategoriaEvento, by_value=True, dump_only=True)
    portada_url = fields.Str(dump_only=True, allow_none=True)
    youtube_url = fields.Str(
        dump_only=True,
        allow_none=True,
        metadata={"description": "Enlace al tráiler promocional público de YouTube."},
    )
    trailer_video_url = fields.Str(
        dump_only=True,
        allow_none=True,
        metadata={
            "description": "Video del tráiler subido por el artista (MP4 o WebM). "
            "Si existe, tiene prioridad sobre `youtube_url`.",
            "example": "/uploads/trailers/3f2a.../1b9c....mp4",
        },
    )
    precio_cop = fields.Int(dump_only=True, metadata={"example": 20000})
    fecha_inicio = fields.AwareDateTime(dump_only=True)
    estado = fields.Enum(EstadoEvento, by_value=True, dump_only=True)
    replay_disponible = fields.Bool(dump_only=True)


class EventoSchema(EventoResumenSchema):
    """Detalle de un evento. Nunca incluye `stream_key_hash`."""

    descripcion = fields.Str(dump_only=True, allow_none=True)
    duracion_estimada_min = fields.Int(dump_only=True, metadata={"example": 90})
    replay_habilitado = fields.Bool(dump_only=True)
    inicio_real = fields.AwareDateTime(dump_only=True, allow_none=True)
    fin_real = fields.AwareDateTime(dump_only=True, allow_none=True)
    suspendido_admin = fields.Bool(dump_only=True)
    creado_en = fields.AwareDateTime(dump_only=True)
    actualizado_en = fields.AwareDateTime(dump_only=True)


class EventoDetalleSchema(EventoSchema):
    """Detalle público; `es_favorito` y `comprado` solo aparecen si hay sesión."""

    es_favorito = fields.Bool(
        dump_only=True,
        metadata={"description": "Solo con sesión: ¿lo marqué como favorito?"},
    )
    comprado = fields.Bool(
        dump_only=True,
        metadata={"description": "Solo con sesión: ¿tengo una compra `APROBADA`?"},
    )

    @pre_dump
    def _combinar(self, detalle, **kwargs):
        datos = {nombre: getattr(detalle.evento, nombre) for nombre in EventoSchema().fields}
        for bandera in ("es_favorito", "comprado"):
            valor = getattr(detalle, bandera)
            if valor is not None:
                datos[bandera] = valor
        return datos


class EventoCrearSchema(Schema):
    class Meta:
        unknown = RAISE

    titulo = fields.Str(
        required=True, validate=_TITULO, metadata={"example": "Mi primer concierto"}
    )
    descripcion = fields.Str(allow_none=True, metadata={"example": "Repertorio completo en vivo."})
    categoria = fields.Enum(CategoriaEvento, by_value=True, load_default=CategoriaEvento.OTRO)
    portada_url = fields.URL(
        schemes={"http", "https"},
        allow_none=True,
        validate=_PORTADA,
        metadata={"description": "URL externa; no hay subida de portadas."},
    )
    youtube_url = fields.URL(
        schemes={"https"},
        required=True,
        validate=_YOUTUBE_URL,
        metadata={
            "description": (
                "Enlace HTTPS a un video promocional público de YouTube (no la transmisión "
                "completa)."
            ),
            "example": "https://youtu.be/AbC123_deFg",
        },
    )
    precio_cop = _precio(required=True)
    fecha_inicio = fields.AwareDateTime(
        required=True,
        metadata={"description": "ISO 8601 con zona.", "example": _EJEMPLO_FECHA},
    )
    duracion_estimada_min = fields.Int(
        strict=True, load_default=60, validate=validate.Range(min=15, max=240)
    )
    replay_habilitado = fields.Bool(load_default=True)


class EventoActualizarSchema(Schema):
    """Edición parcial; qué campos se aceptan depende del estado (RN-04)."""

    class Meta:
        unknown = RAISE

    titulo = fields.Str(validate=_TITULO)
    descripcion = fields.Str(allow_none=True)
    categoria = fields.Enum(CategoriaEvento, by_value=True)
    portada_url = fields.URL(schemes={"http", "https"}, allow_none=True, validate=_PORTADA)
    youtube_url = fields.URL(schemes={"https"}, allow_none=True, validate=_YOUTUBE_URL)
    precio_cop = _precio()
    fecha_inicio = fields.AwareDateTime(metadata={"example": _EJEMPLO_FECHA})
    duracion_estimada_min = fields.Int(strict=True, validate=validate.Range(min=15, max=240))
    replay_habilitado = fields.Bool()


class _FiltroEstadoSchema(PaginacionArgsSchema):
    class Meta:
        unknown = RAISE

    estado = fields.Enum(EstadoEvento, by_value=True)


class EventoFiltrosSchema(_FiltroEstadoSchema):
    """Filtros del catálogo público (RN-21)."""

    q = fields.Str(
        validate=validate.Length(min=1, max=100),
        metadata={"description": "Busca en el título y en el nombre artístico."},
    )
    categoria = fields.Enum(CategoriaEvento, by_value=True)
    artista_id = fields.UUID()
    desde = fields.AwareDateTime(metadata={"description": "`fecha_inicio` mayor o igual."})
    hasta = fields.AwareDateTime(metadata={"description": "`fecha_inicio` menor o igual."})


class MisEventosFiltrosSchema(_FiltroEstadoSchema):
    pass


class EventoPaginaSchema(PaginaSchema):
    items = fields.List(fields.Nested(EventoResumenSchema))


class TrailerSubirSchema(Schema):
    trailer = Upload(
        required=True,
        metadata={"description": "Video MP4 o WebM; el tamaño máximo lo fija `MAX_TRAILER_MB`."},
    )
