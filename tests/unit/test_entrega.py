"""Guardas de entrega (Fase 8): entorno, Docker, Makefile, dependencias, README y trazabilidad."""

import re
from importlib import metadata
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]


def leer(ruta: str) -> str:
    return (RAIZ / ruta).read_text(encoding="utf-8")


def variables(texto: str) -> set[str]:
    return set(re.findall(r"^([A-Z][A-Z0-9_]+)=", texto, re.MULTILINE))


# --- .env.example ------------------------------------------------------------------------------


def test_env_example_declara_exactamente_las_variables_de_la_seccion_16_1():
    bloque = leer("AGENTS.md").split("### 16.1")[1].split("### 16.2")[0]
    assert variables(leer(".env.example")) == variables(bloque)
    assert len(variables(bloque)) == 31


def test_la_configuracion_solo_lee_variables_documentadas_en_env_example():
    patron = r'_(?:texto|entero|booleano)\(\s*"([A-Z][A-Z0-9_]+)"'
    leidas = set(re.findall(patron, leer("app/config.py")))
    assert leidas
    assert leidas <= variables(leer(".env.example")), leidas - variables(leer(".env.example"))


def test_env_example_conserva_los_secretos_de_ejemplo_que_produccion_rechaza():
    from app.config import SECRETOS_POR_DEFECTO

    ejemplo = leer(".env.example")
    for nombre in ("SECRET_KEY", "JWT_SECRET_KEY", "PLAYBACK_TOKEN_SECRET"):
        valor = re.search(rf"^{nombre}=(\S+)", ejemplo, re.MULTILINE).group(1)
        assert valor in SECRETOS_POR_DEFECTO


def test_gitignore_excluye_secretos_cargas_y_cache():
    lineas = set(leer(".gitignore").splitlines())
    assert {".env", "uploads/*", "__pycache__/"} <= lineas


# --- Docker ------------------------------------------------------------------------------------


def test_dockerfile_usa_python_312_usuario_no_root_y_gunicorn():
    docker = leer("Dockerfile")
    assert docker.splitlines()[0].strip() == "FROM python:3.12-slim"
    usuarios = re.findall(r"^USER\s+(\S+)", docker, re.MULTILINE)
    assert usuarios and usuarios[-1] not in {"root", "0"}
    assert re.search(r'CMD \["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "wsgi:app"\]', docker)


def test_compose_tiene_db_con_healthcheck_y_api_que_la_espera():
    compose = leer("docker-compose.yml")
    assert "image: postgres:16" in compose
    assert "healthcheck:" in compose and "pg_isready" in compose
    assert re.search(r"depends_on:\s+db:\s+condition: service_healthy", compose)
    assert "volumes:" in compose and "pgdata" in compose


# --- Makefile ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "objetivo", ["up", "install", "migrate", "migration", "seed", "run", "test", "lint", "openapi"]
)
def test_makefile_tiene_todos_los_objetivos(objetivo):
    assert re.search(rf"^{objetivo}:", leer("Makefile"), re.MULTILINE)


def test_make_test_mide_cobertura_y_make_openapi_exporta_a_docs():
    makefile = leer("Makefile")
    assert "pytest --cov=app" in makefile
    assert "openapi write docs/openapi.json" in makefile


# --- Dependencias fijadas ----------------------------------------------------------------------


def paquetes_fijados(archivo: str) -> dict[str, str]:
    fijados = {}
    for linea in leer(archivo).splitlines():
        linea = linea.split("#")[0].strip()
        if not linea or linea.startswith("-r"):
            continue
        assert "==" in linea, f"{archivo}: {linea!r} no está fijada con =="
        nombre, version = linea.split("==")
        fijados[nombre.split("[")[0].lower().replace("_", "-")] = version
    return fijados


def test_todas_las_dependencias_estan_fijadas_y_coinciden_con_lo_instalado():
    produccion = paquetes_fijados("requirements.txt")
    desarrollo = paquetes_fijados("requirements-dev.txt")
    assert not set(produccion) & set(desarrollo)
    assert {"flask", "sqlalchemy", "flask-smorest", "pillow", "gunicorn"} <= set(produccion)
    assert {"pytest", "pytest-cov", "ruff"} <= set(desarrollo)
    for nombre, version in {**produccion, **desarrollo}.items():
        assert metadata.version(nombre) == version, nombre


# --- Documentación -----------------------------------------------------------------------------


def test_el_readme_tiene_las_secciones_pedidas():
    readme = leer("README.md")
    for seccion in (
        "## Requisitos",
        "## Primer arranque",
        "## Comandos",
        "## Estructura de carpetas",
        "## Probar el flujo completo con Swagger",
        "## Usuarios de demo",
        "## Limitaciones conocidas",
    ):
        assert seccion in readme, seccion
    for dato in ("Demo1234", "artista1@demo.co", "make seed", "make openapi", "mensajes_chat"):
        assert dato in readme


def test_la_trazabilidad_cubre_las_23_filas_de_la_matriz():
    filas = [
        linea
        for linea in leer("docs/trazabilidad.md").splitlines()
        if re.match(r"^\| \d+ \|", linea)
    ]
    assert [int(re.match(r"^\| (\d+)", f).group(1)) for f in filas] == list(range(1, 24))
    matriz = leer("AGENTS.md").split("### Matriz de trazabilidad")[1].split("\n---")[0]
    filas_agents = [
        ln for ln in matriz.splitlines() if ln.startswith("| ") and not ln.startswith("| Prueba")
    ]
    assert len(filas_agents) == 23  # la tabla de §15 y este documento tienen las mismas filas


def test_la_trazabilidad_apunta_a_pruebas_que_existen():
    referencias = re.findall(r"`(tests/[\w/]+\.py)::(\w+)`", leer("docs/trazabilidad.md"))
    assert len(referencias) >= 60
    for archivo, funcion in referencias:
        assert re.search(rf"^def {funcion}\(", leer(archivo), re.MULTILINE), f"{archivo}::{funcion}"
    codigo = re.findall(r"`(app/[\w/.*]+\.py)`", leer("docs/trazabilidad.md"))
    for archivo in codigo:
        if "*" not in archivo:
            assert (RAIZ / archivo).exists(), archivo
