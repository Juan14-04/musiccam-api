"""Agregar el tráiler promocional de YouTube a eventos.

Revision ID: c6e1d7b4a920
Revises: b877ffcf2bfd
"""
from alembic import op
import sqlalchemy as sa


revision = "c6e1d7b4a920"
down_revision = "b877ffcf2bfd"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("eventos", sa.Column("youtube_url", sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column("eventos", "youtube_url")
