"""Helpers para crear datos de prueba."""

from io import BytesIO

from PIL import Image
from werkzeug.security import generate_password_hash

from app.common.clock import ahora_utc
from app.common.enums import Rol
from app.extensions import db
from app.modules.usuarios.models import Usuario

PASSWORD = "Clave1234"


def crear_usuario(
    email: str = "ana@example.com",
    rol: Rol = Rol.USUARIO,
    nombre_artistico: str | None = None,
    **extra,
) -> Usuario:
    """Inserta un usuario directo en la BD (hash rápido: solo para pruebas)."""
    if rol == Rol.ARTISTA and nombre_artistico is None:
        nombre_artistico = f"Artista {email}"
    datos = {"nombre": "Ana", **extra}
    usuario = Usuario(
        email=email,
        password_hash=generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000"),
        rol=rol,
        nombre_artistico=nombre_artistico,
        consentimiento_datos_en=ahora_utc(),
        version_politica_datos="v1",
        **datos,
    )
    db.session.add(usuario)
    db.session.commit()
    return usuario


def iniciar_sesion(client, email: str = "ana@example.com", password: str = PASSWORD) -> dict:
    respuesta = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert respuesta.status_code == 200, respuesta.get_json()
    return respuesta.get_json()


def encabezados(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def imagen_bytes(formato: str = "PNG", tamano=(800, 400), **opciones) -> bytes:
    salida = BytesIO()
    Image.new("RGB", tamano, (200, 30, 30)).save(salida, format=formato, **opciones)
    return salida.getvalue()


def crear_evento(artista, **extra):
    """Inserta un evento directo en la BD (por defecto PROGRAMADO dentro de una semana)."""
    from datetime import timedelta

    from app.common.enums import EstadoEvento
    from app.modules.eventos.models import Evento

    datos = {
        "titulo": "Concierto",
        "precio_cop": 20_000,
        "fecha_inicio": ahora_utc() + timedelta(days=7),
        "estado": EstadoEvento.PROGRAMADO,
        **extra,
    }
    evento = Evento(artista_id=artista.id, **datos)
    db.session.add(evento)
    db.session.commit()
    return evento


def datos_evento(**cambios) -> dict:
    """Cuerpo JSON válido para `POST /eventos` (empieza dentro de una semana)."""
    from datetime import timedelta

    base = {
        "titulo": "Mi primer concierto",
        "youtube_url": "https://youtu.be/AbC123_deFg",
        "precio_cop": 20_000,
        "fecha_inicio": (ahora_utc() + timedelta(days=7)).isoformat(),
    }
    return {**base, **cambios}


def crear_compra(usuario_id, evento_id, **extra):
    """Inserta una compra directo en la BD (PENDIENTE por defecto, 20.000 COP al 10 %)."""
    from datetime import timedelta
    from decimal import Decimal
    from uuid import uuid4

    from app.common.enums import EstadoCompra, MetodoPago
    from app.modules.compras.models import Compra

    datos = {
        "referencia": f"conc-{uuid4().hex}",
        "monto_cop": 20_000,
        "comision_pct": Decimal("10"),
        "comision_cop": 2_000,
        "neto_artista_cop": 18_000,
        "metodo_pago": MetodoPago.NEQUI,
        "estado": EstadoCompra.PENDIENTE,
        "expira_en": ahora_utc() + timedelta(minutes=30),
        **extra,
    }
    compra = Compra(usuario_id=usuario_id, evento_id=evento_id, **datos)
    db.session.add(compra)
    db.session.commit()
    return compra


def crear_retiro(artista_id, **extra):
    """Inserta un retiro directo en la BD (PENDIENTE de 10.000 COP por defecto)."""
    from app.common.enums import EstadoRetiro, TipoCuenta
    from app.modules.artistas.models import Retiro

    datos = {
        "monto_cop": 10_000,
        "banco": "Bancolombia",
        "tipo_cuenta": TipoCuenta.AHORROS,
        "cuenta_ultimos4": "1234",
        "estado": EstadoRetiro.PENDIENTE,
        **extra,
    }
    retiro = Retiro(artista_id=artista_id, **datos)
    db.session.add(retiro)
    db.session.commit()
    return retiro
