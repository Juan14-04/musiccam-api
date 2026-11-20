"""Comandos disponibles que no crean administradores fuera de PostgreSQL."""


def test_no_hay_comando_para_crear_admin(app):
    resultado = app.test_cli_runner().invoke(args=["crear-admin"])
    assert resultado.exit_code != 0
    assert "No such command" in resultado.output
