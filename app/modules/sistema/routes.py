"""Health check (§8)."""

from flask_smorest import Blueprint
from marshmallow import Schema, fields

from app.common.schemas import documentar_errores
from app.modules.sistema.service import verificar_salud

blp = Blueprint("Sistema", __name__, url_prefix="/api/v1", description="Estado del servicio.")


class SaludSchema(Schema):
    estado = fields.Str(metadata={"example": "ok"})
    base_de_datos = fields.Str(metadata={"example": "ok"})


@blp.route("/health", methods=["GET"])
@blp.doc(summary="Estado del servicio", description="Verifica la conexión con la base de datos.")
@blp.response(200, SaludSchema)
@documentar_errores(blp, 503)
def health():
    return verificar_salud()
