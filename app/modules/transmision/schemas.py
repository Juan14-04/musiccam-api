"""DTOs de transmisión y reproducción."""

from marshmallow import RAISE, Schema, fields, validate

from app.common.enums import EstadoEvento, TipoReproduccion


class CredencialesSchema(Schema):
    rtmp_url = fields.Str(
        metadata={
            "description": "URL de ingesta (RTMP) para OBS.",
            "example": "rtmp://localhost:1935/live/3f2a9c1e-7b0d-4c55-9a1e-2d6f8b1c0a11",
        }
    )
    stream_key = fields.Str(
        metadata={
            "description": "Clave de emisión. **Solo se muestra aquí, una vez**: no se guarda en "
            "claro ni se puede volver a consultar; pedir otra la rota (RN-11).",
            "example": "Qk3v1x0Zf8mY2nJhT5uWb7LrD9sAeC4pGoHiXyN6VtM",
        }
    )


class IniciarTransmisionSchema(Schema):
    class Meta:
        unknown = RAISE

    stream_key = fields.Str(
        required=True,
        validate=validate.Length(min=1, max=200),
        metadata={
            "description": "La clave devuelta por `credenciales` (RQ6).",
            "example": "Qk3v1x0Zf8mY2nJhT5uWb7LrD9sAeC4pGoHiXyN6VtM",
        },
    )


class EstadoTransmisionSchema(Schema):
    estado = fields.Enum(EstadoEvento, by_value=True, metadata={"example": "EN_VIVO"})
    espectadores_actuales = fields.Int(metadata={"example": 41})
    pico_espectadores = fields.Int(metadata={"example": 57})
    duracion_transcurrida_min = fields.Int(
        metadata={"description": "Minutos desde `inicio_real` (hasta `fin_real` si ya terminó)."}
    )
    inicio_real = fields.AwareDateTime(allow_none=True)
    fin_real = fields.AwareDateTime(allow_none=True)


class ReproduccionSchema(Schema):
    tipo = fields.Enum(TipoReproduccion, by_value=True, metadata={"example": "VIVO"})
    url = fields.Str(
        metadata={
            "description": "HLS ficticio con el token en la query.",
            "example": "http://localhost:8888/live/3f2a9c1e-7b0d-4c55/index.m3u8?token=eyJhbGci...",
        }
    )
    token = fields.Str(
        metadata={"description": "JWT corto de reproducción (distinto del de sesión)."}
    )
    expira_en = fields.AwareDateTime()


class AudienciaSimuladaSchema(Schema):
    class Meta:
        unknown = RAISE

    espectadores = fields.Int(
        strict=True,
        required=True,
        validate=validate.Range(min=0, max=1_000_000),
        metadata={"description": "Espectadores simultáneos a fijar.", "example": 120},
    )
