"""Agregar el video del tráiler subido por el artista.

Complementa a `youtube_url`: el artista puede enlazar un video de YouTube o subir su propio
archivo (MP4 o WebM). Si hay ambos, la interfaz muestra el video subido.

Revision ID: e3b5c7d9f1a2
Revises: d2a4f6b8c0e1
"""
from alembic import op
import sqlalchemy as sa


revision = "e3b5c7d9f1a2"
down_revision = "d2a4f6b8c0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("eventos", sa.Column("trailer_video_url", sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column("eventos", "trailer_video_url")
