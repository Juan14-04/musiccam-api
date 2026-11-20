"""Registro de los módulos (blueprints) en la API."""

from flask_smorest import Api


def registrar_modulos(api: Api) -> None:
    from app.modules.admin.routes import blp as admin_blp
    from app.modules.artistas.routes import blp as artistas_blp
    from app.modules.artistas.routes import blp_publico as artistas_publico_blp
    from app.modules.auth.routes import blp as auth_blp
    from app.modules.compras.routes import blp as compras_blp
    from app.modules.eventos.routes import blp as eventos_blp
    from app.modules.sistema.routes import blp as sistema_blp
    from app.modules.social.routes import blp as social_blp
    from app.modules.transmision.routes import blp as transmision_blp
    from app.modules.usuarios.routes import blp as usuarios_blp

    blueprints = (
        sistema_blp,
        auth_blp,
        usuarios_blp,
        social_blp,
        eventos_blp,
        transmision_blp,
        compras_blp,
        artistas_publico_blp,
        artistas_blp,
        admin_blp,
    )
    for blueprint in blueprints:
        api.register_blueprint(blueprint)
