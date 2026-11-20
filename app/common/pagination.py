"""Paginación uniforme (§13): `{items, page, page_size, total}`."""

from typing import Any

from sqlalchemy import Select, func, select

from app.extensions import db

PAGE_SIZE_MAX = 100


def paginar(consulta: Select, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), PAGE_SIZE_MAX)
    total = db.session.scalar(select(func.count()).select_from(consulta.order_by(None).subquery()))
    items = db.session.scalars(consulta.limit(page_size).offset((page - 1) * page_size)).all()
    return {"items": items, "page": page, "page_size": page_size, "total": total or 0}
