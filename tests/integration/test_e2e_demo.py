"""Flujo completo de la demo, solo por HTTP (como lo haría el frontend o Swagger)."""

import logging
from datetime import timedelta

from app.common import clock
from app.common.enums import Rol
from tests.factories import PASSWORD, crear_usuario, encabezados

PASSWORD_ARTISTA = "ClaveArtista1"
PASSWORD_USUARIO = "ClaveUsuario2"
CUENTA_ULTIMOS4 = "7391"


def test_flujo_completo_de_la_demo(app, client, caplog, monkeypatch):
    """Artista y usuario se registran → evento → compra → emisión → replay → retiro → admin."""
    monkeypatch.setitem(app.config, "RETIRO_AUTO_APROBAR", False)  # el retiro nace PENDIENTE
    with app.app_context():
        crear_usuario("admin@example.com", Rol.ADMIN)
    secretos: list[str] = [PASSWORD_ARTISTA, PASSWORD_USUARIO, PASSWORD]
    cuerpos: list[str] = []

    def llamar(metodo, ruta, sesion=None, **kwargs):
        respuesta = getattr(client, metodo)(ruta, headers=sesion or {}, **kwargs)
        cuerpos.append(respuesta.get_data(as_text=True))
        return respuesta

    def ok(respuesta, estado=200):
        assert respuesta.status_code == estado, respuesta.get_json()
        return respuesta.get_json() if respuesta.status_code != 204 else None

    with caplog.at_level(logging.DEBUG):
        # 1) Registro de un artista y de un usuario, con consentimiento de datos (RQ1, RQ7).
        artista = ok(
            llamar(
                "post",
                "/api/v1/auth/registro",
                json={
                    "nombre": "Marta",
                    "apellido": "Rojas",
                    "email": "marta@demo.co",
                    "password": PASSWORD_ARTISTA,
                    "rol": "ARTISTA",
                    "nombre_artistico": "Marta en Vivo",
                    "acepta_tratamiento_datos": True,
                },
            ),
            201,
        )
        usuario = ok(
            llamar(
                "post",
                "/api/v1/auth/registro",
                json={
                    "nombre": "Pablo",
                    "apellido": "Vega",
                    "email": "pablo@demo.co",
                    "password": PASSWORD_USUARIO,
                    "rol": "USUARIO",
                    "acepta_tratamiento_datos": True,
                },
            ),
            201,
        )
        assert artista["rol"] == "ARTISTA" and usuario["rol"] == "USUARIO"

        sesion_artista_json = ok(
            llamar(
                "post",
                "/api/v1/auth/login",
                json={"email": "marta@demo.co", "password": PASSWORD_ARTISTA},
            )
        )
        sesion_usuario_json = ok(
            llamar(
                "post",
                "/api/v1/auth/login",
                json={"email": "pablo@demo.co", "password": PASSWORD_USUARIO},
            )
        )
        secretos += [
            sesion_artista_json["access_token"],
            sesion_artista_json["refresh_token"],
            sesion_usuario_json["access_token"],
            sesion_usuario_json["refresh_token"],
        ]
        a = encabezados(sesion_artista_json["access_token"])
        u = encabezados(sesion_usuario_json["access_token"])

        # 2) Crear y publicar un evento (RQ2, C1, C2): sin intervención de un admin.
        fecha = (clock.ahora_utc() + timedelta(days=7)).isoformat()
        evento = ok(
            llamar(
                "post",
                "/api/v1/eventos",
                a,
                json={
                    "titulo": "Concierto de lanzamiento",
                    "precio_cop": 60_000,
                    "fecha_inicio": fecha,
                    "youtube_url": "https://youtu.be/AbC123_deFg",
                },
            ),
            201,
        )
        evento_id = evento["id"]
        assert evento["estado"] == "BORRADOR"
        publicado = ok(llamar("post", f"/api/v1/eventos/{evento_id}/publicar", a))
        assert publicado["estado"] == "PROGRAMADO"
        catalogo = ok(llamar("get", "/api/v1/eventos"))
        assert evento_id in {e["id"] for e in catalogo["items"]}

        # 3) Comprar y simular el pago APPROVED: el acceso se crea solo (C3, RQ3).
        compra = ok(
            llamar(
                "post", f"/api/v1/eventos/{evento_id}/compras", u, json={"metodo_pago": "NEQUI"}
            ),
            201,
        )
        assert compra["compra"]["estado"] == "PENDIENTE" and compra["compra"]["monto_cop"] == 60_000
        referencia = compra["compra"]["referencia"]
        pagada = ok(
            llamar(
                "post",
                f"/api/v1/pagos/wompi/simulador/{referencia}",
                u,
                json={"resultado": "APPROVED"},
            )
        )
        assert pagada["estado"] == "APROBADA"
        assert ok(llamar("get", f"/api/v1/compras/{pagada['id']}", u))["estado"] == "APROBADA"
        assert ok(llamar("get", f"/api/v1/eventos/{evento_id}", u))["comprado"] is True

        # 4) Credenciales de emisión e inicio de la transmisión (RQ6).
        credenciales = ok(
            llamar("post", f"/api/v1/eventos/{evento_id}/transmision/credenciales", a)
        )
        secretos.append(credenciales["stream_key"])
        iniciada = ok(
            llamar(
                "post",
                f"/api/v1/eventos/{evento_id}/transmision/iniciar",
                a,
                json={"stream_key": credenciales["stream_key"]},
            )
        )
        assert iniciada["estado"] == "EN_VIVO"

        # 5) El espectador entra al vivo.
        vivo = ok(llamar("post", f"/api/v1/eventos/{evento_id}/reproduccion/entrar", u))
        secretos.append(vivo["token"])
        assert vivo["tipo"] == "VIVO" and "/live/" in vivo["url"]
        en_curso = ok(llamar("get", f"/api/v1/eventos/{evento_id}/transmision", a))
        assert (en_curso["espectadores_actuales"], en_curso["pico_espectadores"]) == (1, 1)

        # 6) Se finaliza y el comprador vuelve a verlo en REPLAY (RQ5, C4).
        final = ok(llamar("post", f"/api/v1/eventos/{evento_id}/transmision/finalizar", a))
        assert final["estado"] == "FINALIZADO" and final["espectadores_actuales"] == 0
        replay = ok(llamar("post", f"/api/v1/eventos/{evento_id}/reproduccion/entrar", u))
        secretos.append(replay["token"])
        assert replay["tipo"] == "REPLAY" and "/replay/" in replay["url"]

        # 7) Estadísticas del artista, calculadas a mano: 1 × 60.000, comisión 6.000 (10 %).
        resumen = ok(llamar("get", "/api/v1/artistas/me/estadisticas", a))
        assert resumen["resumen"]["entradas_vendidas"] == 1
        assert resumen["resumen"]["ingresos_brutos_cop"] == 60_000
        assert resumen["resumen"]["comision_plataforma_cop"] == 6_000
        assert resumen["resumen"]["ingresos_netos_cop"] == 54_000
        assert resumen["saldo"]["disponible_cop"] == 54_000  # el evento ya terminó
        por_evento = ok(llamar("get", f"/api/v1/artistas/me/eventos/{evento_id}/estadisticas", a))
        assert (por_evento["vistas_vivo"], por_evento["vistas_replay"]) == (1, 1)
        assert por_evento["pico_espectadores"] == 1 and por_evento["duracion_real_min"] is not None

        # 8) Solicita un retiro (RN-15) y un admin lo aprueba (RN-16).
        retiro = ok(
            llamar(
                "post",
                "/api/v1/artistas/me/retiros",
                a,
                json={
                    "monto_cop": 50_000,
                    "banco": "Bancolombia",
                    "tipo_cuenta": "AHORROS",
                    "cuenta_ultimos4": CUENTA_ULTIMOS4,
                },
            ),
            201,
        )
        assert retiro["estado"] == "PENDIENTE" and retiro["cuenta_ultimos4"] == CUENTA_ULTIMOS4
        saldo = ok(llamar("get", "/api/v1/artistas/me/saldo", a))
        assert (saldo["disponible_cop"], saldo["en_proceso_cop"]) == (4_000, 50_000)

        sesion_admin_json = ok(
            llamar(
                "post",
                "/api/v1/auth/login",
                json={"email": "admin@example.com", "password": PASSWORD},
            )
        )
        secretos += [sesion_admin_json["access_token"], sesion_admin_json["refresh_token"]]
        admin = encabezados(sesion_admin_json["access_token"])

        pendientes = ok(
            llamar("get", "/api/v1/admin/retiros", admin, query_string={"estado": "PENDIENTE"})
        )
        assert [r["id"] for r in pendientes["items"]] == [retiro["id"]]
        aprobado = ok(llamar("post", f"/api/v1/admin/retiros/{retiro['id']}/aprobar", admin))
        assert aprobado["estado"] == "PAGADO" and aprobado["referencia_pago"].startswith(
            "sim-payout-"
        )

        saldo_final = ok(llamar("get", "/api/v1/artistas/me/saldo", a))
        assert saldo_final == {
            "disponible_cop": 4_000,
            "retenido_cop": 0,
            "en_proceso_cop": 0,
            "retirado_cop": 50_000,
        }
        tablero = ok(llamar("get", "/api/v1/admin/dashboard", admin))
        assert tablero["entradas_vendidas"] == 1 and tablero["ingresos_brutos_cop"] == 60_000
        assert tablero["comisiones_acumuladas_cop"] == 6_000
        assert tablero["retiros_pendientes"] == {"cantidad": 0, "monto_cop": 0}

    # Auditoría de logs y respuestas de todo el recorrido (§12).
    registro = caplog.text
    for secreto in secretos:
        assert secreto not in registro, "un secreto llegó a los logs"
    secretos_de_wompi = [app.config["WOMPI_INTEGRITY_SECRET"], app.config["WOMPI_EVENTS_SECRET"]]
    for secreto in [*secretos_de_wompi, app.config["JWT_SECRET_KEY"], app.config["SECRET_KEY"]]:
        assert secreto not in registro
        assert not any(secreto in cuerpo for cuerpo in cuerpos)
    assert CUENTA_ULTIMOS4 not in registro
    todo = "\n".join(cuerpos)
    assert "password_hash" not in todo and "stream_key_hash" not in todo
    for contrasena in (PASSWORD_ARTISTA, PASSWORD_USUARIO, PASSWORD):
        assert contrasena not in todo
