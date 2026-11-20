"""Admin (Fase 7): RN-16, RN-19, RN-20 y la parte administrativa de RN-07, RN-10 y RN-21."""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy import func, select

from app.common.clock import fijar_ahora
from app.common.enums import (
    CategoriaEvento,
    EstadoCompra,
    EstadoEvento,
    EstadoRetiro,
    EstadoUsuario,
    Rol,
)
from app.common.errors import Conflicto, ReglaDeNegocio
from app.extensions import db
from app.modules.artistas.models import Retiro
from app.modules.artistas.repository import ArtistasRepository
from app.modules.eventos.models import Evento
from app.modules.usuarios.models import Usuario
from app.modules.usuarios.repository import UsuarioRepository
from app.modules.usuarios.service import UsuarioService
from tests.factories import (
    PASSWORD,
    crear_compra,
    crear_evento,
    crear_retiro,
    crear_usuario,
    encabezados,
    iniciar_sesion,
)

ADMIN = "/api/v1/admin"
EVENTOS = "/api/v1/eventos"
AHORA = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
INEXISTENTE = "00000000-0000-4000-8000-000000000000"


def entrar_sesion(client, email):
    return encabezados(iniciar_sesion(client, email)["access_token"])


# --- Fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def admin(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("admin@example.com", Rol.ADMIN).id)


@pytest.fixture
def sesion_admin(client, admin):
    return entrar_sesion(client, "admin@example.com")


@pytest.fixture
def artista(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("luz@example.com", Rol.ARTISTA, "Luz Band").id)


@pytest.fixture
def sesion_artista(client, artista):
    return entrar_sesion(client, "luz@example.com")


@pytest.fixture
def ana(app):
    with app.app_context():
        return SimpleNamespace(id=crear_usuario("ana@example.com", nombre="Ana Gómez").id)


@pytest.fixture
def sesion_ana(client, ana):
    return entrar_sesion(client, "ana@example.com")


def nuevo_evento(app, artista, **campos) -> str:
    with app.app_context():
        return str(crear_evento(artista, **campos).id)


def usuario_en_bd(app, usuario_id) -> SimpleNamespace:
    with app.app_context():
        fila = db.session.get(Usuario, uuid.UUID(str(usuario_id)))
        return SimpleNamespace(**{c.name: getattr(fila, c.name) for c in Usuario.__table__.columns})


def evento_en_bd(app, evento_id) -> SimpleNamespace:
    with app.app_context():
        fila = db.session.get(Evento, uuid.UUID(str(evento_id)))
        return SimpleNamespace(**{c.name: getattr(fila, c.name) for c in Evento.__table__.columns})


def retiro_en_bd(app, retiro_id) -> SimpleNamespace:
    with app.app_context():
        fila = db.session.get(Retiro, uuid.UUID(str(retiro_id)))
        return SimpleNamespace(**{c.name: getattr(fila, c.name) for c in Retiro.__table__.columns})


def estado_usuario(client, sesion_admin, usuario_id, estado):
    return client.patch(
        f"{ADMIN}/usuarios/{usuario_id}/estado", headers=sesion_admin, json={"estado": estado}
    )


# --- Acceso: solo ADMIN ------------------------------------------------------------------------

ENDPOINTS = [
    ("get", "/dashboard"),
    ("get", "/usuarios"),
    ("get", f"/usuarios/{INEXISTENTE}"),
    ("patch", f"/usuarios/{INEXISTENTE}/estado"),
    ("patch", f"/usuarios/{INEXISTENTE}/rol"),
    ("get", "/eventos"),
    ("post", f"/eventos/{INEXISTENTE}/suspender"),
    ("post", f"/eventos/{INEXISTENTE}/reactivar"),
    ("get", "/compras"),
    ("get", "/retiros"),
    ("post", f"/retiros/{INEXISTENTE}/aprobar"),
    ("post", f"/retiros/{INEXISTENTE}/rechazar"),
    ("get", "/configuracion"),
]


@pytest.mark.parametrize(("metodo", "ruta"), ENDPOINTS)
def test_sin_token_es_401(client, metodo, ruta):
    respuesta = getattr(client, metodo)(ADMIN + ruta)
    assert respuesta.status_code == 401 and respuesta.get_json()["codigo"] == "NO_AUTENTICADO"


@pytest.mark.parametrize(("metodo", "ruta"), ENDPOINTS)
def test_usuario_y_artista_reciben_403(client, sesion_ana, sesion_artista, metodo, ruta):
    """★ §4: el panel es solo de ADMIN."""
    for sesion in (sesion_ana, sesion_artista):
        respuesta = getattr(client, metodo)(ADMIN + ruta, headers=sesion, json={})
        assert respuesta.status_code == 403
        assert respuesta.get_json()["codigo"] == "ROL_NO_PERMITIDO"


def test_el_rol_se_verifica_contra_la_bd_no_contra_el_token(app, client, admin, sesion_admin):
    """Un admin degradado en la BD pierde el acceso con su token aún vigente."""
    assert client.get(f"{ADMIN}/dashboard", headers=sesion_admin).status_code == 200
    with app.app_context():
        db.session.get(Usuario, admin.id).rol = Rol.USUARIO
        db.session.commit()
    assert client.get(f"{ADMIN}/dashboard", headers=sesion_admin).status_code == 403


def test_un_admin_suspendido_pierde_el_acceso(app, client, admin, sesion_admin):
    with app.app_context():
        db.session.get(Usuario, admin.id).estado = EstadoUsuario.SUSPENDIDO
        db.session.commit()
    respuesta = client.get(f"{ADMIN}/dashboard", headers=sesion_admin)
    assert respuesta.status_code == 403 and respuesta.get_json()["codigo"] == "CUENTA_SUSPENDIDA"


# --- Usuarios: listado y detalle ---------------------------------------------------------------


def test_listado_de_usuarios_filtra_por_rol_estado_y_q(
    app, client, admin, sesion_admin, artista, ana
):
    with app.app_context():
        crear_usuario("beto@example.com", nombre="Beto Pérez")
        crear_usuario("susp@example.com", nombre="Susana", estado=EstadoUsuario.SUSPENDIDO)

    def emails(**params):
        respuesta = client.get(f"{ADMIN}/usuarios", headers=sesion_admin, query_string=params)
        assert respuesta.status_code == 200, respuesta.get_json()
        return {u["email"] for u in respuesta.get_json()["items"]}

    assert len(emails()) == 5
    assert emails(rol="ARTISTA") == {"luz@example.com"}
    assert emails(rol="ADMIN") == {"admin@example.com"}
    assert emails(estado="SUSPENDIDO") == {"susp@example.com"}
    assert emails(rol="USUARIO", estado="ACTIVO") == {"ana@example.com", "beto@example.com"}
    assert emails(q="pérez") == {"beto@example.com"}  # nombre
    assert emails(q="LUZ@") == {"luz@example.com"}  # email
    assert emails(q="luz band") == {"luz@example.com"}  # nombre artístico
    assert emails(q="%") == set()  # el comodín es texto


def test_listado_de_usuarios_nunca_trae_la_contrasena_y_pagina(client, sesion_admin, ana):
    respuesta = client.get(f"{ADMIN}/usuarios", headers=sesion_admin, query_string={"page_size": 1})
    cuerpo = respuesta.get_json()
    assert (cuerpo["page"], cuerpo["page_size"], cuerpo["total"], len(cuerpo["items"])) == (
        1,
        1,
        2,
        1,
    )
    texto = respuesta.get_data(as_text=True)
    assert "password" not in texto and "hash" not in texto
    segunda = client.get(
        f"{ADMIN}/usuarios", headers=sesion_admin, query_string={"page": 2, "page_size": 1}
    )
    assert len(segunda.get_json()["items"]) == 1


@pytest.mark.parametrize(
    "params", [{"rol": "REY"}, {"estado": "X"}, {"q": ""}, {"page": 0}, {"orden": "x"}]
)
def test_listado_de_usuarios_rechaza_parametros_invalidos(client, sesion_admin, params):
    assert (
        client.get(f"{ADMIN}/usuarios", headers=sesion_admin, query_string=params).status_code
        == 422
    )


def test_detalle_de_usuario(client, sesion_admin, ana):
    respuesta = client.get(f"{ADMIN}/usuarios/{ana.id}", headers=sesion_admin)
    assert respuesta.status_code == 200
    cuerpo = respuesta.get_json()
    assert (cuerpo["email"], cuerpo["rol"], cuerpo["estado"]) == (
        "ana@example.com",
        "USUARIO",
        "ACTIVO",
    )
    assert cuerpo["version_politica_datos"] == "v1" and cuerpo["consentimiento_datos_en"]
    # Sin credenciales ni datos financieros en bruto.
    assert not {"password_hash", "password", "saldo", "compras", "retiros"} & set(cuerpo)
    assert client.get(f"{ADMIN}/usuarios/{INEXISTENTE}", headers=sesion_admin).status_code == 404


def test_admin_no_se_crea_por_api(client, sesion_admin):
    assert client.post(f"{ADMIN}/usuarios", headers=sesion_admin, json={}).status_code == 405


def test_usuario_service_no_crea_admin(app):
    with app.app_context(), pytest.raises(ReglaDeNegocio) as error:
        UsuarioService().crear(
            nombre="Root",
            email="root@example.com",
            password_hash="hash",
            rol=Rol.ADMIN,
            nombre_artistico=None,
            version_politica="v1",
        )
    assert error.value.codigo == "ADMIN_SOLO_BD"


def test_el_registro_publico_sigue_sin_poder_crear_admins(client):
    respuesta = client.post(
        "/api/v1/auth/registro",
        json={
            "nombre": "Intruso",
            "apellido": "Pérez",
            "email": "intruso@example.com",
            "password": "Clave1234",
            "rol": "ADMIN",
            "acepta_tratamiento_datos": True,
        },
    )
    assert respuesta.status_code == 422


# --- Suspender usuarios (RN-19) ----------------------------------------------------------------


def test_usuario_suspendido_con_token_valido_recibe_403_y_al_reactivarlo_vuelve(
    client, sesion_admin, ana, sesion_ana
):
    """★ RN-19."""
    sesion = iniciar_sesion(client, "ana@example.com")
    assert client.get("/api/v1/usuarios/me", headers=sesion_ana).status_code == 200

    respuesta = estado_usuario(client, sesion_admin, ana.id, "SUSPENDIDO")
    assert respuesta.status_code == 200 and respuesta.get_json()["estado"] == "SUSPENDIDO"

    for ruta in (
        "/api/v1/usuarios/me",
        "/api/v1/usuarios/me/favoritos",
        "/api/v1/usuarios/me/compras",
    ):
        bloqueado = client.get(ruta, headers=sesion_ana)
        assert bloqueado.status_code == 403, ruta
        assert bloqueado.get_json()["codigo"] == "CUENTA_SUSPENDIDA"
    login = client.post(
        "/api/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD}
    )
    assert login.status_code == 403 and login.get_json()["codigo"] == "CUENTA_SUSPENDIDA"
    refresco = client.post("/api/v1/auth/refresh", headers=encabezados(sesion["refresh_token"]))
    assert refresco.status_code == 403

    reactivada = estado_usuario(client, sesion_admin, ana.id, "ACTIVO")
    assert reactivada.status_code == 200 and reactivada.get_json()["estado"] == "ACTIVO"
    # El mismo token de antes vuelve a funcionar.
    assert client.get("/api/v1/usuarios/me", headers=sesion_ana).status_code == 200


def test_un_artista_suspendido_desaparece_del_catalogo_y_de_los_perfiles(
    app, client, sesion_admin, artista
):
    """★ RN-19 / RN-21."""
    evento_id = nuevo_evento(app, artista)

    def ids_catalogo():
        return [e["id"] for e in client.get(EVENTOS).get_json()["items"]]

    assert evento_id in ids_catalogo()
    assert client.get(f"/api/v1/artistas/{artista.id}").status_code == 200

    estado_usuario(client, sesion_admin, artista.id, "SUSPENDIDO")

    assert evento_id not in ids_catalogo()
    assert client.get(f"{EVENTOS}/{evento_id}").status_code == 404
    assert client.get(f"/api/v1/artistas/{artista.id}").status_code == 404
    lista = client.get("/api/v1/artistas").get_json()
    assert str(artista.id) not in {a["id"] for a in lista["items"]}

    estado_usuario(client, sesion_admin, artista.id, "ACTIVO")
    assert evento_id in ids_catalogo()
    assert client.get(f"/api/v1/artistas/{artista.id}").status_code == 200


def test_cambiar_estado_valida_el_cuerpo_y_el_usuario(client, sesion_admin, ana):
    ruta = f"{ADMIN}/usuarios/{ana.id}/estado"
    for cuerpo in ({}, {"estado": "BANEADO"}, {"estado": "ACTIVO", "rol": "ADMIN"}):
        assert client.patch(ruta, headers=sesion_admin, json=cuerpo).status_code == 422
    respuesta = estado_usuario(client, sesion_admin, INEXISTENTE, "SUSPENDIDO")
    assert (
        respuesta.status_code == 404 and respuesta.get_json()["codigo"] == "USUARIO_NO_ENCONTRADO"
    )


def test_cambiar_estado_es_idempotente(client, sesion_admin, ana):
    assert estado_usuario(client, sesion_admin, ana.id, "ACTIVO").status_code == 200
    assert estado_usuario(client, sesion_admin, ana.id, "SUSPENDIDO").status_code == 200
    assert estado_usuario(client, sesion_admin, ana.id, "SUSPENDIDO").status_code == 200


# --- RN-20: salvaguardas de admin --------------------------------------------------------------


def test_un_admin_no_puede_suspenderse_a_si_mismo(app, client, admin, sesion_admin):
    """★ RN-20."""
    respuesta = estado_usuario(client, sesion_admin, admin.id, "SUSPENDIDO")
    assert respuesta.status_code == 403
    assert respuesta.get_json()["codigo"] == "ADMIN_NO_PUEDE_AUTOMODIFICARSE"
    assert usuario_en_bd(app, admin.id).estado == EstadoUsuario.ACTIVO


def test_un_admin_no_puede_cambiar_su_propio_rol(app, client, admin, sesion_admin):
    """★ RN-20."""
    for cuerpo in (
        {"rol": "USUARIO"},
        {"rol": "ARTISTA", "nombre_artistico": "Yo"},
    ):
        respuesta = client.patch(
            f"{ADMIN}/usuarios/{admin.id}/rol", headers=sesion_admin, json=cuerpo
        )
        assert respuesta.status_code == 403, cuerpo
        assert respuesta.get_json()["codigo"] == "ADMIN_NO_PUEDE_AUTOMODIFICARSE"
    assert usuario_en_bd(app, admin.id).rol == Rol.ADMIN


def test_un_admin_puede_suspender_a_otro_admin_y_este_pierde_el_acceso(
    app, client, sesion_admin, admin
):
    with app.app_context():
        otro = crear_usuario("admin2@example.com", Rol.ADMIN).id
    sesion_otro = entrar_sesion(client, "admin2@example.com")
    assert client.get(f"{ADMIN}/dashboard", headers=sesion_otro).status_code == 200

    assert estado_usuario(client, sesion_admin, otro, "SUSPENDIDO").status_code == 200
    bloqueado = client.get(f"{ADMIN}/dashboard", headers=sesion_otro)
    assert bloqueado.status_code == 403 and bloqueado.get_json()["codigo"] == "CUENTA_SUSPENDIDA"
    # Y el que queda ya no puede suspenderse: seguirá habiendo un admin activo.
    assert estado_usuario(client, sesion_admin, admin.id, "SUSPENDIDO").status_code == 403
    assert usuario_en_bd(app, admin.id).estado == EstadoUsuario.ACTIVO


def test_no_se_deja_el_sistema_sin_admins_activos(app, admin):
    """★ RN-20: aunque un actor ajeno (p. ej. ya suspendido) lo intente, el conteo lo impide."""
    with app.app_context():
        fantasma = SimpleNamespace(id=uuid.uuid4())  # no es el objetivo y no es admin activo
        with pytest.raises(Conflicto) as error:
            UsuarioService().cambiar_estado(fantasma, admin.id, EstadoUsuario.SUSPENDIDO)
        assert error.value.codigo == "ULTIMO_ADMIN_ACTIVO"
        db.session.rollback()
    assert usuario_en_bd(app, admin.id).estado == EstadoUsuario.ACTIVO


def test_dos_admins_que_se_suspenden_entre_si_no_dejan_el_sistema_vacio(app, client, monkeypatch):
    """★ RN-20, concurrente: A suspende a B y B suspende a A a la vez."""
    with app.app_context():
        a = crear_usuario("a@example.com", Rol.ADMIN).id
        b = crear_usuario("b@example.com", Rol.ADMIN).id
    sesion_a, sesion_b = (
        entrar_sesion(client, "a@example.com"),
        entrar_sesion(client, "b@example.com"),
    )

    barrera = threading.Barrier(2, timeout=20)
    original = UsuarioRepository.bloquear_admins_activos

    def sincronizado(self):
        barrera.wait()  # los dos llegan juntos al bloqueo
        return original(self)

    monkeypatch.setattr(UsuarioRepository, "bloquear_admins_activos", sincronizado)
    resultados = []

    def suspender(sesion, objetivo):
        respuesta = estado_usuario(app.test_client(), sesion, objetivo, "SUSPENDIDO")
        resultados.append((respuesta.status_code, respuesta.get_json().get("codigo")))

    hilos = [
        threading.Thread(target=suspender, args=(sesion_a, b)),
        threading.Thread(target=suspender, args=(sesion_b, a)),
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(40)

    assert sorted(codigo for codigo, _ in resultados) == [200, 409]
    assert (409, "ULTIMO_ADMIN_ACTIVO") in resultados
    with app.app_context():
        activos = db.session.scalar(
            select(func.count()).where(
                Usuario.rol == Rol.ADMIN, Usuario.estado == EstadoUsuario.ACTIVO
            )
        )
    assert activos == 1


def test_con_tres_admins_se_pueden_suspender_dos_pero_no_los_tres(app, client):
    with app.app_context():
        ids = [crear_usuario(f"adm{i}@example.com", Rol.ADMIN).id for i in range(3)]
    sesion = entrar_sesion(client, "adm0@example.com")
    assert estado_usuario(client, sesion, ids[1], "SUSPENDIDO").status_code == 200
    assert estado_usuario(client, sesion, ids[2], "SUSPENDIDO").status_code == 200
    assert estado_usuario(client, sesion, ids[0], "SUSPENDIDO").status_code == 403  # a sí mismo


# --- Cambio de rol (P2) ------------------------------------------------------------------------


def cambiar_rol(client, sesion_admin, usuario_id, **cuerpo):
    return client.patch(f"{ADMIN}/usuarios/{usuario_id}/rol", headers=sesion_admin, json=cuerpo)


def test_pasar_a_artista_exige_nombre_artistico(app, client, sesion_admin, ana):
    sin = cambiar_rol(client, sesion_admin, ana.id, rol="ARTISTA")
    assert sin.status_code == 422 and "nombre_artistico" in sin.get_json()["detalles"]
    assert usuario_en_bd(app, ana.id).rol == Rol.USUARIO

    con = cambiar_rol(client, sesion_admin, ana.id, rol="ARTISTA", nombre_artistico="Ana Sol")
    assert con.status_code == 200
    assert (con.get_json()["rol"], con.get_json()["nombre_artistico"]) == ("ARTISTA", "Ana Sol")


def test_no_se_puede_promover_a_admin_por_api(app, client, sesion_admin, ana):
    respuesta = cambiar_rol(client, sesion_admin, ana.id, rol="ADMIN")
    assert respuesta.status_code == 422
    assert usuario_en_bd(app, ana.id).rol == Rol.USUARIO


def test_usuario_service_no_promueve_a_admin(app, admin, ana):
    with app.app_context(), pytest.raises(ReglaDeNegocio) as error:
        actor = db.session.get(Usuario, admin.id)
        UsuarioService().cambiar_rol(actor, ana.id, Rol.ADMIN, None, lambda _: None)
    assert error.value.codigo == "ADMIN_SOLO_BD"


def test_pasar_a_artista_con_nombre_en_uso_es_409(client, sesion_admin, ana, artista):
    respuesta = cambiar_rol(
        client, sesion_admin, ana.id, rol="ARTISTA", nombre_artistico="luz band"
    )
    assert respuesta.status_code == 409
    assert respuesta.get_json()["codigo"] == "NOMBRE_ARTISTICO_EN_USO"


@pytest.mark.parametrize("estado", [EstadoEvento.PROGRAMADO, EstadoEvento.EN_VIVO])
def test_un_artista_con_eventos_en_curso_no_cambia_de_rol(
    app, client, sesion_admin, artista, estado
):
    nuevo_evento(app, artista, estado=estado)
    respuesta = cambiar_rol(client, sesion_admin, artista.id, rol="USUARIO")
    assert respuesta.status_code == 409 and respuesta.get_json()["codigo"] == "ROL_NO_CAMBIABLE"
    assert "eventos" in respuesta.get_json()["detalles"]
    assert usuario_en_bd(app, artista.id).rol == Rol.ARTISTA


def test_un_artista_con_retiros_pendientes_no_cambia_de_rol(app, client, sesion_admin, artista):
    with app.app_context():
        crear_retiro(artista.id, estado=EstadoRetiro.PENDIENTE)
    respuesta = cambiar_rol(client, sesion_admin, artista.id, rol="USUARIO")
    assert respuesta.status_code == 409 and "retiros" in respuesta.get_json()["detalles"]


def test_un_artista_sin_eventos_en_curso_ni_retiros_pendientes_si_cambia(
    app, client, sesion_admin, artista
):
    for estado in (EstadoEvento.BORRADOR, EstadoEvento.FINALIZADO, EstadoEvento.CANCELADO):
        nuevo_evento(app, artista, estado=estado)
    with app.app_context():
        crear_retiro(artista.id, estado=EstadoRetiro.PAGADO)
        crear_retiro(artista.id, estado=EstadoRetiro.RECHAZADO)
    respuesta = cambiar_rol(client, sesion_admin, artista.id, rol="USUARIO")
    assert respuesta.status_code == 200 and respuesta.get_json()["rol"] == "USUARIO"


def test_cambio_de_rol_a_si_mismo_igual_o_inexistente(client, sesion_admin, ana):
    igual = cambiar_rol(client, sesion_admin, ana.id, rol="USUARIO")
    assert igual.status_code == 200 and igual.get_json()["rol"] == "USUARIO"
    inexistente = cambiar_rol(client, sesion_admin, INEXISTENTE, rol="USUARIO")
    assert inexistente.status_code == 404
    for cuerpo in ({}, {"rol": "REY"}, {"rol": "USUARIO", "estado": "ACTIVO"}):
        assert cambiar_rol(client, sesion_admin, ana.id, **cuerpo).status_code == 422


def test_un_admin_puede_degradar_a_otro_admin(app, client, sesion_admin):
    with app.app_context():
        otro = crear_usuario("admin2@example.com", Rol.ADMIN).id
    respuesta = cambiar_rol(client, sesion_admin, otro, rol="USUARIO")
    assert respuesta.status_code == 200 and respuesta.get_json()["rol"] == "USUARIO"


# --- Eventos: moderación -----------------------------------------------------------------------


def suspender(client, sesion_admin, evento_id, **cuerpo):
    return client.post(f"{ADMIN}/eventos/{evento_id}/suspender", headers=sesion_admin, json=cuerpo)


def reactivar(client, sesion_admin, evento_id):
    return client.post(f"{ADMIN}/eventos/{evento_id}/reactivar", headers=sesion_admin)


def test_suspender_un_evento_tiene_todos_los_efectos_y_reactivar_los_revierte(
    app, client, artista, sesion_artista, sesion_admin, sesion_ana
):
    """★ RN-21 + RN-07 + RN-10 + iniciar transmisión, y la reversión completa."""
    with app.app_context():
        otra = SimpleNamespace(id=crear_usuario("otra@example.com", Rol.ARTISTA, "Otra Banda").id)
    programado = nuevo_evento(app, artista)  # catálogo, compra e inicio
    en_vivo = nuevo_evento(app, otra, estado=EstadoEvento.EN_VIVO)  # reproducción
    clave = client.post(
        f"{EVENTOS}/{programado}/transmision/credenciales", headers=sesion_artista
    ).get_json()["stream_key"]
    with app.app_context():
        crear_compra(
            usuario_id_de(app, "ana@example.com"), uuid.UUID(en_vivo), estado=EstadoCompra.APROBADA
        )

    def catalogo():
        return {e["id"] for e in client.get(EVENTOS).get_json()["items"]}

    def comprar():
        return client.post(
            f"{EVENTOS}/{programado}/compras", headers=sesion_ana, json={"metodo_pago": "NEQUI"}
        )

    def iniciar():
        return client.post(
            f"{EVENTOS}/{programado}/transmision/iniciar",
            headers=sesion_artista,
            json={"stream_key": clave},
        )

    def entrar():
        return client.post(f"{EVENTOS}/{en_vivo}/reproduccion/entrar", headers=sesion_ana)

    assert {programado, en_vivo} <= catalogo()
    assert entrar().status_code == 200

    # --- Suspender ---
    respuesta = suspender(client, sesion_admin, programado, motivo="  Contenido no permitido ")
    assert respuesta.status_code == 200
    cuerpo = respuesta.get_json()
    assert (
        cuerpo["suspendido_admin"] is True
        and cuerpo["motivo_suspension"] == "Contenido no permitido"
    )
    assert cuerpo["estado"] == "PROGRAMADO"  # no se cancela ni se finaliza
    assert suspender(client, sesion_admin, en_vivo, motivo="Denuncia").status_code == 200

    assert programado not in catalogo() and en_vivo not in catalogo()  # RN-21
    assert client.get(f"{EVENTOS}/{programado}").status_code == 404
    rechazo = comprar()  # RN-07
    assert rechazo.status_code == 403 and rechazo.get_json()["codigo"] == "EVENTO_SUSPENDIDO"
    visto = entrar()  # RN-10
    assert visto.status_code == 403 and visto.get_json()["codigo"] == "EVENTO_SUSPENDIDO"
    inicio = iniciar()  # el artista no puede emitir
    assert inicio.status_code == 403 and inicio.get_json()["codigo"] == "EVENTO_SUSPENDIDO"
    assert evento_en_bd(app, programado).estado == EstadoEvento.PROGRAMADO
    assert evento_en_bd(app, en_vivo).estado == EstadoEvento.EN_VIVO  # sigue en vivo
    assert evento_en_bd(app, en_vivo).fin_real is None

    # --- Reactivar ---
    reactivado = reactivar(client, sesion_admin, programado)
    assert reactivado.status_code == 200
    assert reactivado.get_json()["suspendido_admin"] is False
    assert reactivado.get_json()["motivo_suspension"] is None
    assert reactivar(client, sesion_admin, en_vivo).status_code == 200

    assert {programado, en_vivo} <= catalogo()
    assert comprar().status_code == 201
    assert entrar().status_code == 200
    assert iniciar().status_code == 200
    assert evento_en_bd(app, programado).estado == EstadoEvento.EN_VIVO


def usuario_id_de(app, email):
    with app.app_context():
        return db.session.scalar(select(Usuario.id).where(Usuario.email == email))


@pytest.mark.parametrize(
    "cuerpo",
    [{}, {"motivo": ""}, {"motivo": "   "}, {"motivo": "x" * 256}, {"motivo": "ok", "x": 1}],
)
def test_suspender_exige_un_motivo_valido(app, client, artista, sesion_admin, cuerpo):
    evento_id = nuevo_evento(app, artista)
    respuesta = suspender(client, sesion_admin, evento_id, **cuerpo)
    assert respuesta.status_code == 422
    assert evento_en_bd(app, evento_id).suspendido_admin is False


def test_suspender_dos_veces_o_reactivar_sin_suspender_es_409(app, client, artista, sesion_admin):
    evento_id = nuevo_evento(app, artista)
    no_suspendido = reactivar(client, sesion_admin, evento_id)
    assert no_suspendido.status_code == 409
    assert no_suspendido.get_json()["codigo"] == "TRANSICION_INVALIDA"

    assert suspender(client, sesion_admin, evento_id, motivo="a").status_code == 200
    doble = suspender(client, sesion_admin, evento_id, motivo="b")
    assert doble.status_code == 409 and doble.get_json()["codigo"] == "TRANSICION_INVALIDA"
    assert evento_en_bd(app, evento_id).motivo_suspension == "a"  # no se pisó


def test_suspender_o_reactivar_un_evento_inexistente_es_404(client, sesion_admin):
    for respuesta in (
        suspender(client, sesion_admin, INEXISTENTE, motivo="x"),
        reactivar(client, sesion_admin, INEXISTENTE),
    ):
        assert (
            respuesta.status_code == 404
            and respuesta.get_json()["codigo"] == "EVENTO_NO_ENCONTRADO"
        )


def test_se_puede_suspender_un_evento_en_cualquier_estado(app, client, artista, sesion_admin):
    for estado in EstadoEvento:
        evento_id = nuevo_evento(app, artista, estado=estado)
        assert suspender(client, sesion_admin, evento_id, motivo="m").status_code == 200
        assert evento_en_bd(app, evento_id).estado == estado


def test_listado_de_eventos_muestra_todos_los_estados_y_filtra(app, client, artista, sesion_admin):
    with app.app_context():
        otro = SimpleNamespace(id=crear_usuario("otro@example.com", Rol.ARTISTA, "Orquesta Sol").id)
    borrador = nuevo_evento(app, artista, estado=EstadoEvento.BORRADOR, titulo="Borrador X")
    cancelado = nuevo_evento(app, artista, estado=EstadoEvento.CANCELADO, titulo="Cancelado X")
    suspendido = nuevo_evento(app, artista, suspendido_admin=True, titulo="Suspendido X")
    ajeno = nuevo_evento(app, otro, titulo="De otro", categoria=CategoriaEvento.ROCK)

    def ids(**params):
        respuesta = client.get(f"{ADMIN}/eventos", headers=sesion_admin, query_string=params)
        assert respuesta.status_code == 200, respuesta.get_json()
        return {e["id"] for e in respuesta.get_json()["items"]}

    assert ids() == {borrador, cancelado, suspendido, ajeno}  # también los que el catálogo oculta
    assert ids(estado="BORRADOR") == {borrador}
    assert ids(suspendido="true") == {suspendido}
    assert ids(suspendido="false") == {borrador, cancelado, ajeno}
    assert ids(artista_id=str(otro.id)) == {ajeno}
    assert ids(categoria="ROCK") == {ajeno}
    assert ids(q="cancelado") == {cancelado}
    assert ids(q="orquesta") == {ajeno}  # nombre artístico
    assert ids(desde="2099-01-01T00:00:00+00:00") == set()

    item = client.get(f"{ADMIN}/eventos", headers=sesion_admin, query_string={"suspendido": "true"})
    evento = item.get_json()["items"][0]
    assert evento["suspendido_admin"] is True and "motivo_suspension" in evento
    assert "luz@example.com" not in item.get_data(as_text=True)


@pytest.mark.parametrize(
    "params", [{"estado": "X"}, {"suspendido": "quizas"}, {"desde": "ayer"}, {"x": 1}]
)
def test_listado_de_eventos_rechaza_parametros_invalidos(client, sesion_admin, params):
    assert (
        client.get(f"{ADMIN}/eventos", headers=sesion_admin, query_string=params).status_code == 422
    )


# --- Retiros: resolución (RN-16) ---------------------------------------------------------------


def dar_saldo(app, artista, compras=6) -> None:
    """108.000 disponibles (6 × neto 18.000) en un evento FINALIZADO."""
    with app.app_context():
        evento = crear_evento(artista, estado=EstadoEvento.FINALIZADO)
        for _ in range(compras):
            comprador = crear_usuario(f"comp{uuid.uuid4().hex[:8]}@example.com").id
            crear_compra(comprador, evento.id, estado=EstadoCompra.APROBADA)


def pedir_retiro(client, sesion_artista, monto=60_000) -> str:
    respuesta = client.post(
        "/api/v1/artistas/me/retiros",
        headers=sesion_artista,
        json={
            "monto_cop": monto,
            "banco": "Bancolombia",
            "tipo_cuenta": "AHORROS",
            "cuenta_ultimos4": "0042",
        },
    )
    assert respuesta.status_code == 201, respuesta.get_json()
    return respuesta.get_json()["id"]


def disponible(client, sesion_artista) -> int:
    return client.get("/api/v1/artistas/me/saldo", headers=sesion_artista).get_json()[
        "disponible_cop"
    ]


def resolver(client, sesion_admin, retiro_id, accion, **cuerpo):
    return client.post(
        f"{ADMIN}/retiros/{retiro_id}/{accion}", headers=sesion_admin, json=cuerpo or None
    )


def test_aprobar_un_retiro_lo_deja_pagado_con_referencia(
    app, client, admin, artista, sesion_artista, sesion_admin
):
    """★ RN-16."""
    dar_saldo(app, artista)
    retiro_id = pedir_retiro(client, sesion_artista)
    fijar_ahora(AHORA)

    respuesta = resolver(client, sesion_admin, retiro_id, "aprobar")

    assert respuesta.status_code == 200
    cuerpo = respuesta.get_json()
    assert cuerpo["estado"] == "PAGADO"
    assert cuerpo["referencia_pago"].startswith("sim-payout-")
    assert len(cuerpo["referencia_pago"]) == len("sim-payout-") + 36
    assert cuerpo["resuelto_por"] == str(admin.id)
    assert cuerpo["resuelto_en"].startswith("2026-11-10T15:00:00")
    assert cuerpo["motivo_rechazo"] is None
    saldo = client.get("/api/v1/artistas/me/saldo", headers=sesion_artista).get_json()
    assert (saldo["en_proceso_cop"], saldo["retirado_cop"], saldo["disponible_cop"]) == (
        0,
        60_000,
        48_000,
    )


def test_rechazar_un_retiro_recupera_el_disponible(
    app, client, admin, artista, sesion_artista, sesion_admin
):
    """★ RN-16 / RN-14."""
    dar_saldo(app, artista)
    retiro_id = pedir_retiro(client, sesion_artista, 100_000)
    assert disponible(client, sesion_artista) == 8_000
    fijar_ahora(AHORA)

    respuesta = resolver(
        client, sesion_admin, retiro_id, "rechazar", motivo="Cuenta no verificable"
    )

    assert respuesta.status_code == 200
    cuerpo = respuesta.get_json()
    assert (cuerpo["estado"], cuerpo["motivo_rechazo"]) == ("RECHAZADO", "Cuenta no verificable")
    assert cuerpo["referencia_pago"] is None
    assert cuerpo["resuelto_por"] == str(admin.id) and cuerpo["resuelto_en"].startswith(
        "2026-11-10T15:00:00"
    )
    assert disponible(client, sesion_artista) == 108_000  # el RECHAZADO deja de restar
    saldo = client.get("/api/v1/artistas/me/saldo", headers=sesion_artista).get_json()
    assert (saldo["en_proceso_cop"], saldo["retirado_cop"]) == (0, 0)


@pytest.mark.parametrize(
    "cuerpo",
    [{}, {"motivo": ""}, {"motivo": "   "}, {"motivo": "x" * 256}, {"motivo": "ok", "x": 1}],
)
def test_rechazar_sin_motivo_valido_es_422_y_no_cambia_nada(
    app, client, artista, sesion_artista, sesion_admin, cuerpo
):
    """★ RN-16."""
    dar_saldo(app, artista)
    retiro_id = pedir_retiro(client, sesion_artista)
    respuesta = resolver(client, sesion_admin, retiro_id, "rechazar", **cuerpo)
    assert respuesta.status_code == 422
    assert retiro_en_bd(app, retiro_id).estado == EstadoRetiro.PENDIENTE


@pytest.mark.parametrize(
    ("primero", "segundo"),
    [
        ("aprobar", "aprobar"),
        ("rechazar", "rechazar"),
        ("aprobar", "rechazar"),
        ("rechazar", "aprobar"),
    ],
)
def test_resolver_dos_veces_es_409(
    app, client, artista, sesion_artista, sesion_admin, primero, segundo
):
    """★ RN-16."""
    dar_saldo(app, artista)
    retiro_id = pedir_retiro(client, sesion_artista)
    cuerpo = {"motivo": "m"}
    assert resolver(client, sesion_admin, retiro_id, primero, **cuerpo).status_code == 200
    antes = retiro_en_bd(app, retiro_id)
    repetido = resolver(client, sesion_admin, retiro_id, segundo, **cuerpo)
    assert repetido.status_code == 409 and repetido.get_json()["codigo"] == "RETIRO_YA_RESUELTO"
    despues = retiro_en_bd(app, retiro_id)
    assert (despues.estado, despues.referencia_pago, despues.motivo_rechazo) == (
        antes.estado,
        antes.referencia_pago,
        antes.motivo_rechazo,
    )


def test_resolver_un_retiro_inexistente_es_404(client, sesion_admin):
    for accion, cuerpo in (("aprobar", {}), ("rechazar", {"motivo": "m"})):
        respuesta = resolver(client, sesion_admin, INEXISTENTE, accion, **cuerpo)
        assert (
            respuesta.status_code == 404
            and respuesta.get_json()["codigo"] == "RETIRO_NO_ENCONTRADO"
        )


def test_resolver_de_forma_concurrente_solo_deja_ganar_a_uno(
    app, client, admin, artista, sesion_artista, sesion_admin, monkeypatch
):
    """★ RN-16: 3 aprobaciones y 3 rechazos a la vez sobre el mismo retiro."""
    dar_saldo(app, artista)
    retiro_id = pedir_retiro(client, sesion_artista)
    barrera = threading.Barrier(6, timeout=30)
    original = ArtistasRepository.obtener_retiro

    def sincronizado(self, retiro, *, bloquear=False):
        if bloquear:
            barrera.wait()  # los seis llegan juntos al SELECT … FOR UPDATE
        return original(self, retiro, bloquear=bloquear)

    monkeypatch.setattr(ArtistasRepository, "obtener_retiro", sincronizado)
    resultados = []

    def intentar(accion):
        respuesta = resolver(app.test_client(), sesion_admin, retiro_id, accion, motivo="m")
        resultados.append((accion, respuesta.status_code, respuesta.get_json().get("codigo")))

    hilos = [threading.Thread(target=intentar, args=(a,)) for a in ["aprobar", "rechazar"] * 3]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(60)

    ganadores = [r for r in resultados if r[1] == 200]
    assert len(resultados) == 6 and len(ganadores) == 1
    assert all(r[1:] == (409, "RETIRO_YA_RESUELTO") for r in resultados if r[1] != 200)
    final = retiro_en_bd(app, retiro_id)
    if ganadores[0][0] == "aprobar":
        assert (
            final.estado == EstadoRetiro.PAGADO
            and final.referencia_pago
            and final.motivo_rechazo is None
        )
    else:
        assert (
            final.estado == EstadoRetiro.RECHAZADO
            and final.motivo_rechazo
            and final.referencia_pago is None
        )
    assert final.resuelto_por == admin.id and final.resuelto_en is not None


def test_listado_de_retiros(app, client, artista, sesion_admin):
    with app.app_context():
        otro = SimpleNamespace(id=crear_usuario("otro@example.com", Rol.ARTISTA, "Orquesta Sol").id)
        pendiente = str(crear_retiro(artista.id, monto_cop=60_000).id)
        pagado = str(crear_retiro(otro.id, monto_cop=70_000, estado=EstadoRetiro.PAGADO).id)
        rechazado = str(crear_retiro(otro.id, estado=EstadoRetiro.RECHAZADO).id)

    def ids(**params):
        respuesta = client.get(f"{ADMIN}/retiros", headers=sesion_admin, query_string=params)
        assert respuesta.status_code == 200, respuesta.get_json()
        return {r["id"] for r in respuesta.get_json()["items"]}

    assert ids() == {pendiente, pagado, rechazado}
    assert ids(estado="PENDIENTE") == {pendiente}
    assert ids(estado="PAGADO") == {pagado}
    item = next(
        r
        for r in client.get(f"{ADMIN}/retiros", headers=sesion_admin).get_json()["items"]
        if r["id"] == pendiente
    )
    assert item["artista"]["nombre_artistico"] == "Luz Band" and item["cuenta_ultimos4"] == "1234"
    assert set(item["artista"]) == {"id", "nombre_artistico", "foto_url"}
    pagina = client.get(
        f"{ADMIN}/retiros", headers=sesion_admin, query_string={"page_size": 2}
    ).get_json()
    assert (pagina["total"], len(pagina["items"])) == (3, 2)
    for params in ({"estado": "X"}, {"x": 1}, {"page": 0}):
        assert (
            client.get(f"{ADMIN}/retiros", headers=sesion_admin, query_string=params).status_code
            == 422
        )


# --- Compras: listado --------------------------------------------------------------------------


def test_listado_de_compras_con_filtros_y_sin_correos(app, client, artista, sesion_admin):
    base = datetime(2030, 1, 10, 12, 0, tzinfo=UTC)
    with app.app_context():
        e1 = crear_evento(artista, titulo="Concierto Uno")
        e2 = crear_evento(artista, titulo="Concierto Dos")
        ana = crear_usuario("ana@example.com", nombre="Ana Gómez").id
        beto = crear_usuario("beto@example.com", nombre="Beto Pérez").id
        c1 = crear_compra(ana, e1.id, estado=EstadoCompra.APROBADA, creado_en=base)
        c2 = crear_compra(
            ana, e2.id, estado=EstadoCompra.PENDIENTE, creado_en=base + timedelta(days=1)
        )
        c3 = crear_compra(
            beto, e1.id, estado=EstadoCompra.RECHAZADA, creado_en=base + timedelta(days=2)
        )
        ids = {"c1": str(c1.id), "c2": str(c2.id), "c3": str(c3.id)}
        ana_id, e1_id = str(ana), str(e1.id)

    def listar(**params):
        respuesta = client.get(f"{ADMIN}/compras", headers=sesion_admin, query_string=params)
        assert respuesta.status_code == 200, respuesta.get_json()
        return respuesta

    todas = listar().get_json()
    assert [c["id"] for c in todas["items"]] == [
        ids["c3"],
        ids["c2"],
        ids["c1"],
    ]  # recientes primero
    assert [c["id"] for c in listar(estado="PENDIENTE").get_json()["items"]] == [ids["c2"]]
    assert {c["id"] for c in listar(usuario_id=ana_id).get_json()["items"]} == {
        ids["c1"],
        ids["c2"],
    }
    assert {c["id"] for c in listar(evento_id=e1_id).get_json()["items"]} == {ids["c1"], ids["c3"]}
    rango = listar(desde="2030-01-11T00:00:00+00:00", hasta="2030-01-11T23:59:59+00:00")
    assert [c["id"] for c in rango.get_json()["items"]] == [ids["c2"]]
    assert listar(desde="2031-01-01T00:00:00+00:00").get_json()["items"] == []

    primera = next(c for c in todas["items"] if c["id"] == ids["c1"])
    assert primera["usuario"] == {"id": ana_id, "nombre": "Ana Gómez"}
    assert primera["evento"]["titulo"] == "Concierto Uno"
    assert (primera["monto_cop"], primera["comision_cop"], primera["neto_artista_cop"]) == (
        20_000,
        2_000,
        18_000,
    )
    assert primera["estado"] == "APROBADA" and primera["referencia"].startswith("conc-")
    assert "@" not in listar().get_data(as_text=True)  # ningún correo en todo el listado

    pagina = listar(page_size=2).get_json()
    assert (pagina["total"], len(pagina["items"])) == (3, 2)


@pytest.mark.parametrize(
    "params", [{"estado": "X"}, {"evento_id": "no"}, {"desde": "ayer"}, {"x": 1}, {"page": 0}]
)
def test_listado_de_compras_rechaza_parametros_invalidos(client, sesion_admin, params):
    assert (
        client.get(f"{ADMIN}/compras", headers=sesion_admin, query_string=params).status_code == 422
    )


# --- Dashboard ---------------------------------------------------------------------------------


@pytest.fixture
def escenario(app, admin, artista):
    """Datos conocidos; los totales esperados están calculados a mano en el test."""
    with app.app_context():
        crear_usuario("admin2@example.com", Rol.ADMIN, estado=EstadoUsuario.SUSPENDIDO)
        crear_usuario("art2@example.com", Rol.ARTISTA, "Art Dos")
        crear_usuario("art3@example.com", Rol.ARTISTA, "Art Tres", estado=EstadoUsuario.SUSPENDIDO)
        u = [crear_usuario(f"u{i}@example.com").id for i in range(1, 5)]

        e = [
            crear_evento(artista, estado=EstadoEvento.BORRADOR),
            crear_evento(artista, estado=EstadoEvento.BORRADOR),
            crear_evento(artista, estado=EstadoEvento.PROGRAMADO),
            crear_evento(artista, estado=EstadoEvento.PROGRAMADO),
            crear_evento(artista, estado=EstadoEvento.PROGRAMADO),
            crear_evento(artista, estado=EstadoEvento.EN_VIVO),
            crear_evento(artista, estado=EstadoEvento.FINALIZADO),
            crear_evento(artista, estado=EstadoEvento.FINALIZADO),
            crear_evento(artista, estado=EstadoEvento.CANCELADO),
        ]

        def compra(usuario, evento, estado, monto, comision):
            crear_compra(
                usuario,
                evento.id,
                estado=estado,
                monto_cop=monto,
                comision_cop=comision,
                neto_artista_cop=monto - comision,
            )

        aprobada, otras = EstadoCompra.APROBADA, EstadoCompra
        compra(u[0], e[2], aprobada, 20_000, 2_000)
        compra(u[0], e[3], aprobada, 20_000, 2_000)
        compra(u[1], e[4], aprobada, 20_000, 2_000)
        compra(u[1], e[6], aprobada, 50_000, 5_000)
        # Ninguna de estas suma: con importes grandes para detectar cualquier fuga.
        compra(u[2], e[2], otras.PENDIENTE, 20_000, 2_000)
        compra(u[3], e[2], otras.RECHAZADA, 99_999, 9_999)
        compra(u[3], e[2], otras.ANULADA, 99_999, 9_999)
        compra(u[3], e[8], otras.REEMBOLSADA, 99_999, 9_999)

        crear_retiro(artista.id, monto_cop=60_000, estado=EstadoRetiro.PENDIENTE)
        crear_retiro(artista.id, monto_cop=40_000, estado=EstadoRetiro.PENDIENTE)
        crear_retiro(artista.id, monto_cop=70_000, estado=EstadoRetiro.PAGADO)
        crear_retiro(artista.id, monto_cop=80_000, estado=EstadoRetiro.RECHAZADO)


def test_dashboard_con_datos_conocidos(client, sesion_admin, escenario):
    """★ Totales calculados a mano."""
    respuesta = client.get(f"{ADMIN}/dashboard", headers=sesion_admin)
    assert respuesta.status_code == 200
    assert respuesta.get_json() == {
        "usuarios": {
            # admin + admin2 · luz + art2 + art3 · u1..u4
            "por_rol": {"USUARIO": 4, "ARTISTA": 3, "ADMIN": 2},
            # activos: admin, luz, art2, u1..u4 = 7 · suspendidos: admin2, art3 = 2
            "por_estado": {"ACTIVO": 7, "SUSPENDIDO": 2},
        },
        "eventos_por_estado": {
            "BORRADOR": 2,
            "PROGRAMADO": 3,
            "EN_VIVO": 1,
            "FINALIZADO": 2,
            "CANCELADO": 1,
        },
        "entradas_vendidas": 4,  # solo las APROBADA
        "ingresos_brutos_cop": 110_000,  # 3 × 20.000 + 50.000
        "comisiones_acumuladas_cop": 11_000,  # 3 × 2.000 + 5.000
        "retiros_pendientes": {"cantidad": 2, "monto_cop": 100_000},  # 60.000 + 40.000
    }


def test_dashboard_vacio_es_todo_ceros(client, sesion_admin, admin):
    cuerpo = client.get(f"{ADMIN}/dashboard", headers=sesion_admin).get_json()
    assert cuerpo["usuarios"]["por_rol"] == {"USUARIO": 0, "ARTISTA": 0, "ADMIN": 1}
    assert cuerpo["eventos_por_estado"] == dict.fromkeys(
        ("BORRADOR", "PROGRAMADO", "EN_VIVO", "FINALIZADO", "CANCELADO"), 0
    )
    assert (
        cuerpo["entradas_vendidas"],
        cuerpo["ingresos_brutos_cop"],
        cuerpo["comisiones_acumuladas_cop"],
    ) == (0, 0, 0)
    assert cuerpo["retiros_pendientes"] == {"cantidad": 0, "monto_cop": 0}


def test_el_dashboard_son_solo_agregaciones_sql(app, client, sesion_admin, escenario):
    sentencias = []

    def capturar(conexion, cursor, statement, parameters, context, executemany):
        sentencias.append(" ".join(statement.split()).lower())

    with app.app_context():
        motor = db.engine
    sa_event.listen(motor, "before_cursor_execute", capturar)
    try:
        assert client.get(f"{ADMIN}/dashboard", headers=sesion_admin).status_code == 200
    finally:
        sa_event.remove(motor, "before_cursor_execute", capturar)

    # Salvo la carga del propio admin (`WHERE usuarios.id = …`), todo es agregado.
    datos = [
        s
        for s in sentencias
        if s.startswith("select")
        and any(t in s for t in ("from usuarios", "from eventos", "from compras", "from retiros"))
        and "where usuarios.id =" not in s
    ]
    assert len(datos) == 5
    for sql in datos:
        assert "count(" in sql or "sum(" in sql, sql
        assert "limit" not in sql


# --- Configuración -----------------------------------------------------------------------------


def test_configuracion_expone_los_valores_publicos(app, client, sesion_admin):
    respuesta = client.get(f"{ADMIN}/configuracion", headers=sesion_admin)
    assert respuesta.status_code == 200
    cuerpo = respuesta.get_json()
    assert cuerpo["comision_plataforma_pct"] == 10.0
    assert (cuerpo["precio_min_cop"], cuerpo["precio_max_cop"], cuerpo["retiro_min_cop"]) == (
        5_000,
        500_000,
        50_000,
    )
    assert (
        cuerpo["pasarela_pagos"] == "wompi_simulado" and cuerpo["proveedor_streaming"] == "simulado"
    )
    assert (
        cuerpo["jwt_access_ttl_min"],
        cuerpo["jwt_refresh_ttl_dias"],
        cuerpo["playback_token_ttl_min"],
    ) == (15, 7, 10)
    assert isinstance(cuerpo["retiro_auto_aprobar"], bool) and isinstance(
        cuerpo["enable_dev_endpoints"], bool
    )
    assert cuerpo["entorno"] == "testing"


def test_configuracion_no_contiene_ningun_secreto(app, client, sesion_admin):
    """★ Se revisa la respuesta completa: ni valores ni nombres de secretos."""
    texto = client.get(f"{ADMIN}/configuracion", headers=sesion_admin).get_data(as_text=True)
    secretos = [
        valor
        for clave, valor in app.config.items()
        if isinstance(valor, str)
        and valor
        and any(marca in clave for marca in ("SECRET", "PASSWORD", "KEY", "URI"))
    ]
    assert secretos  # (hay secretos reales que buscar)
    for valor in [*secretos, "Cambiar123", "postgresql", "app:app"]:
        assert valor not in texto, valor
    minusculas = texto.lower()
    for palabra in ("secret", "password", "jwt_secret", "database", "uri"):
        assert palabra not in minusculas, palabra


def test_la_configuracion_refleja_los_cambios(app, client, sesion_admin, monkeypatch):
    monkeypatch.setitem(app.config, "RETIRO_AUTO_APROBAR", True)
    monkeypatch.setitem(app.config, "PLATFORM_COMMISSION_PCT", "12.5")
    cuerpo = client.get(f"{ADMIN}/configuracion", headers=sesion_admin).get_json()
    assert cuerpo["retiro_auto_aprobar"] is True and cuerpo["comision_plataforma_pct"] == 12.5


# --- Creación de ADMIN solo por base de datos --------------------------------------------------


def test_el_comando_crear_admin_no_existe(app):
    resultado = app.test_cli_runner().invoke(args=["crear-admin"])
    assert resultado.exit_code != 0
    assert "No such command" in resultado.output


# --- Swagger -----------------------------------------------------------------------------------


def test_swagger_contiene_el_tag_admin_con_todos_sus_endpoints(client):
    spec = client.get("/api/openapi.json").get_json()
    assert "Admin" in {t["name"] for t in spec["tags"]}
    esperados = [
        ("/dashboard", "get", {"200", "401", "403"}),
        ("/usuarios", "get", {"200", "401", "403", "422"}),
        ("/usuarios/{usuario_id}", "get", {"200", "401", "403", "404"}),
        ("/usuarios/{usuario_id}/estado", "patch", {"200", "401", "403", "404", "409", "422"}),
        ("/usuarios/{usuario_id}/rol", "patch", {"200", "401", "403", "404", "409", "422"}),
        ("/eventos", "get", {"200", "401", "403", "422"}),
        ("/eventos/{evento_id}/suspender", "post", {"200", "401", "403", "404", "409", "422"}),
        ("/eventos/{evento_id}/reactivar", "post", {"200", "401", "403", "404", "409"}),
        ("/compras", "get", {"200", "401", "403", "422"}),
        ("/retiros", "get", {"200", "401", "403", "422"}),
        ("/retiros/{retiro_id}/aprobar", "post", {"200", "401", "403", "404", "409"}),
        ("/retiros/{retiro_id}/rechazar", "post", {"200", "401", "403", "404", "409", "422"}),
        ("/configuracion", "get", {"200", "401", "403"}),
    ]
    assert len(esperados) == 13
    for ruta, metodo, codigos in esperados:
        operacion = spec["paths"][ADMIN + ruta][metodo]
        assert operacion["tags"] == ["Admin"], (ruta, metodo)
        assert operacion["summary"] and operacion["description"], (ruta, metodo)
        assert operacion["security"] == [{"bearerAuth": []}], (ruta, metodo)
        assert codigos <= set(operacion["responses"]), (ruta, metodo, set(operacion["responses"]))
    admin_paths = {p for p in spec["paths"] if p.startswith(ADMIN)}
    assert admin_paths == {ADMIN + r for r, _, _ in esperados}

    esquemas = spec["components"]["schemas"]
    for esquema, campo in (
        ("Motivo", "motivo"),
        ("EstadoUsuario", "estado"),
        ("Dashboard", "entradas_vendidas"),
        ("Configuracion", "comision_plataforma_pct"),
        ("RolUsuario", "nombre_artistico"),
    ):
        assert "example" in esquemas[esquema]["properties"][campo], (esquema, campo)
    assert "password_hash" not in str(esquemas)
