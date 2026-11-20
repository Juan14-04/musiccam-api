"""`flask seed`: datos de demo coherentes, idempotentes y vía servicios."""

from sqlalchemy import func, select

from app.common import clock
from app.common.enums import EstadoCompra, EstadoEvento, EstadoRetiro, EstadoUsuario, Rol
from app.extensions import db
from app.modules.artistas.models import Retiro
from app.modules.compras.models import Compra
from app.modules.eventos.models import Evento
from app.modules.social.models import Favorito, Seguidor
from app.modules.usuarios.models import Usuario
from app.seed import CLAVE_DEMO
from tests.factories import PASSWORD, crear_usuario, encabezados, iniciar_sesion

MODELOS = (Usuario, Evento, Compra, Retiro, Favorito, Seguidor)


def sembrar(app, esperado: int = 0):
    if esperado == 0:
        with app.app_context():
            admin = db.session.scalar(
                select(Usuario.id)
                .where(Usuario.rol == Rol.ADMIN, Usuario.estado == EstadoUsuario.ACTIVO)
                .limit(1)
            )
            if admin is None:
                crear_usuario("seed-admin@example.com", Rol.ADMIN)
    resultado = app.test_cli_runner().invoke(args=["seed"])
    assert resultado.exit_code == esperado, resultado.output
    return resultado


def conteos(app) -> dict[str, int]:
    with app.app_context():
        return {
            m.__tablename__: db.session.scalar(select(func.count()).select_from(m)) for m in MODELOS
        }


def estados(app, modelo, columna) -> list:
    with app.app_context():
        return list(db.session.scalars(select(columna)))


def credenciales_impresas(salida: str) -> list[tuple[str, str, str]]:
    filas = []
    for linea in salida.splitlines():
        partes = linea.split()
        if len(partes) == 3 and "@" in partes[1]:
            filas.append(tuple(partes))
    return filas


def sesion_de(client, email, clave=CLAVE_DEMO):
    return encabezados(iniciar_sesion(client, email, clave)["access_token"])


def test_el_seed_crea_los_datos_de_demo_pedidos(app, client):
    salida = sembrar(app).output

    assert conteos(app) == {
        "usuarios": 9,  # 1 admin + 3 artistas + 5 usuarios
        "eventos": 8,
        "compras": 16,
        "retiros": 2,
        "favoritos": 6,
        "seguidores": 7,
    }
    with app.app_context():
        roles = sorted(r.value for r in db.session.scalars(select(Usuario.rol)))
        assert roles == ["ADMIN"] + ["ARTISTA"] * 3 + ["USUARIO"] * 5
        eventos = list(db.session.scalars(select(Evento)))
        assert {e.estado for e in eventos} == set(EstadoEvento)  # los 5 estados
        assert any(e.estado == EstadoEvento.FINALIZADO and e.replay_disponible for e in eventos)
        assert any(e.suspendido_admin and e.motivo_suspension for e in eventos)
        assert sum(e.estado == EstadoEvento.EN_VIVO for e in eventos) == 1
        assert set(db.session.scalars(select(Compra.estado))) == set(EstadoCompra)  # los 5
        retiros = sorted(r.estado.value for r in db.session.scalars(select(Retiro)))
        assert retiros == ["PAGADO", "PENDIENTE"]
        pagado = db.session.scalar(select(Retiro).where(Retiro.estado == EstadoRetiro.PAGADO))
        assert pagado.referencia_pago.startswith("sim-payout-") and pagado.resuelto_por is not None
        assert all(
            r.cuenta_ultimos4.isdigit() and len(r.cuenta_ultimos4) == 4
            for r in db.session.scalars(select(Retiro))
        )
        # La siembra crea las cuentas de demo con hash real y consentimiento registrado.
        usuarios = list(db.session.scalars(select(Usuario)))
        assert all(u.password_hash.startswith(("scrypt:", "pbkdf2:")) for u in usuarios)
        assert all(u.consentimiento_datos_en and u.version_politica_datos == "v1" for u in usuarios)

    # El catálogo público solo ve lo visible: 3 programados + el en vivo + el finalizado con replay.
    assert client.get("/api/v1/eventos").get_json()["total"] == 5

    # Las credenciales impresas son reales: cada una inicia sesión.
    filas = credenciales_impresas(salida)
    assert len(filas) == 8 and {f[0] for f in filas} == {"ARTISTA", "USUARIO"}
    for rol, email, clave in filas:
        usuario = iniciar_sesion(client, email, clave)["usuario"]
        assert usuario["rol"] == rol and usuario["estado"] == "ACTIVO"


def test_los_numeros_del_seed_son_coherentes_a_mano(app, client):
    """Saldo, estadísticas y dashboard salen de las reglas, no de INSERT sueltos."""
    sembrar(app)

    # Artista 1: 5 entradas de 30.000 del evento finalizado + 2 de 25.000 del programado.
    artista = sesion_de(client, "artista1@demo.co")
    saldo = client.get("/api/v1/artistas/me/saldo", headers=artista).get_json()
    assert saldo == {
        "disponible_cop": 135_000 - 50_000 - 50_000,  # 5 × 27.000 liberados − PAGADO − PENDIENTE
        "retenido_cop": 2 * 22_500,  # 2 × 22.500 del evento aún PROGRAMADO
        "en_proceso_cop": 50_000,
        "retirado_cop": 50_000,
    }
    resumen = client.get("/api/v1/artistas/me/estadisticas", headers=artista).get_json()
    assert resumen["resumen"]["eventos_por_estado"] == {
        "BORRADOR": 1,
        "PROGRAMADO": 1,
        "EN_VIVO": 0,
        "FINALIZADO": 1,
        "CANCELADO": 0,
    }
    assert resumen["resumen"]["entradas_vendidas"] == 7
    assert resumen["resumen"]["ingresos_brutos_cop"] == 5 * 30_000 + 2 * 25_000
    assert resumen["resumen"]["ingresos_netos_cop"] == 5 * 27_000 + 2 * 22_500
    assert resumen["resumen"]["comision_plataforma_cop"] == 5 * 3_000 + 2 * 2_500
    assert resumen["resumen"]["seguidores"] == 3  # usuario1, usuario2 y usuario4
    assert resumen["resumen"]["pico_espectadores_promedio"] == 5.0
    assert resumen["saldo"] == saldo
    assert sum(d["entradas"] for d in resumen["ventas_por_dia"]) == 7
    assert len(resumen["ventas_por_dia"]) == 2  # la venta histórica y la de hoy

    # Dashboard del admin contra los datos de la BD y contra el cálculo manual.
    admin = sesion_de(client, "seed-admin@example.com", PASSWORD)
    tablero = client.get("/api/v1/admin/dashboard", headers=admin).get_json()
    assert tablero["usuarios"]["por_rol"] == {"USUARIO": 5, "ARTISTA": 3, "ADMIN": 1}
    assert tablero["eventos_por_estado"] == {
        "BORRADOR": 1,
        "PROGRAMADO": 4,  # el suspendido sigue PROGRAMADO
        "EN_VIVO": 1,
        "FINALIZADO": 1,
        "CANCELADO": 1,
    }
    assert tablero["entradas_vendidas"] == 12
    assert tablero["ingresos_brutos_cop"] == 310_000
    assert tablero["comisiones_acumuladas_cop"] == 31_000
    assert tablero["retiros_pendientes"] == {"cantidad": 1, "monto_cop": 50_000}
    with app.app_context():
        aprobadas = db.session.scalar(
            select(func.count()).where(Compra.estado == EstadoCompra.APROBADA)
        )
        bruto = db.session.scalar(
            select(func.sum(Compra.monto_cop)).where(Compra.estado == EstadoCompra.APROBADA)
        )
    assert (aprobadas, bruto) == (12, 310_000)

    # El evento en vivo tiene público; el finalizado tiene vistas de vivo y de replay.
    publico = client.get("/api/v1/eventos", query_string={"estado": "EN_VIVO"}).get_json()["items"]
    en_vivo = sesion_de(client, "artista3@demo.co")
    transmision = client.get(
        f"/api/v1/eventos/{publico[0]['id']}/transmision", headers=en_vivo
    ).get_json()
    assert transmision["estado"] == "EN_VIVO" and transmision["espectadores_actuales"] == 2


def test_el_seed_es_idempotente(app):
    primero = sembrar(app)
    antes = conteos(app)
    segundo = sembrar(app)
    assert conteos(app) == antes  # ninguna fila duplicada, ni compras ni retiros
    assert credenciales_impresas(segundo.output) == credenciales_impresas(primero.output)
    with app.app_context():
        correos = list(db.session.scalars(select(Usuario.email)))
        assert len(correos) == len(set(correos))


def test_el_seed_se_niega_a_correr_en_produccion(app, monkeypatch):
    monkeypatch.setitem(app.config, "APP_ENV", "production")
    resultado = sembrar(app, esperado=1)
    assert "production" in resultado.output
    assert all(total == 0 for total in conteos(app).values())


def test_el_seed_exige_un_admin_activo_preexistente(app):
    resultado = app.test_cli_runner().invoke(args=["seed"])
    assert resultado.exit_code != 0
    assert "Crea un administrador activo directamente en la base de datos" in resultado.output
    assert all(total == 0 for total in conteos(app).values())


def test_el_seed_restablece_el_reloj_y_los_ajustes_que_toca(app, monkeypatch):
    monkeypatch.setitem(app.config, "RETIRO_AUTO_APROBAR", True)
    monkeypatch.setitem(app.config, "ENFORCE_START_WINDOW", True)
    sembrar(app)
    assert app.config["RETIRO_AUTO_APROBAR"] is True and app.config["ENFORCE_START_WINDOW"] is True
    assert clock._Reloj.fijo is None  # el reloj de pruebas del seed se soltó
    # Aun con auto-aprobación activa, el seed deja un retiro PENDIENTE y uno PAGADO.
    assert sorted(estados(app, Retiro, Retiro.estado), key=str) == [
        EstadoRetiro.PAGADO,
        EstadoRetiro.PENDIENTE,
    ]


def test_el_seed_no_crea_a_nadie_suspendido(app):
    sembrar(app)
    assert set(estados(app, Usuario, Usuario.estado)) == {EstadoUsuario.ACTIVO}
    assert Rol.ADMIN in set(estados(app, Usuario, Usuario.rol))
