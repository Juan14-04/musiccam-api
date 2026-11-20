"""Comando de Flask para cargar datos de demo."""

import click
from flask import Flask
from flask.cli import with_appcontext


@click.command("seed")
@with_appcontext
def seed() -> None:
    """Carga datos de demo coherentes (idempotente). No corre con APP_ENV=production."""
    from app.seed import sembrar_demo

    credenciales = sembrar_demo()
    # Solo ASCII: algunas consolas (p. ej. cp932 o cp1252 en Windows) no codifican tildes.
    click.echo("\nDatos de demo listos. Credenciales:\n")
    click.echo(f"  {'ROL':<9} {'CORREO':<26} CLAVE")
    for credencial in credenciales:
        click.echo(f"  {credencial.rol:<9} {credencial.email:<26} {credencial.password}")
    click.echo("\nAbre http://localhost:5000/api/docs y usa Authorize con el token de /auth/login.")


def registrar_comandos(app: Flask) -> None:
    app.cli.add_command(seed)
