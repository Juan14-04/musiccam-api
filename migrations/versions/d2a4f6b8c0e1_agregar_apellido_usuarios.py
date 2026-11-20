"""Agregar el apellido de los usuarios.

Es opcional en la base: las cuentas existentes y los administradores creados por SQL quedan sin
apellido hasta que lo completen en su perfil. El registro público lo exige (validación de la API).

Revision ID: d2a4f6b8c0e1
Revises: c6e1d7b4a920
"""
from alembic import op
import sqlalchemy as sa


revision = "d2a4f6b8c0e1"
down_revision = "c6e1d7b4a920"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("usuarios", sa.Column("apellido", sa.String(length=100), nullable=True))


def downgrade():
    op.drop_column("usuarios", "apellido")
