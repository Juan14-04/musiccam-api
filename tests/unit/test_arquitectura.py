"""Guardas automáticas de las reglas de §7 y §18 (análisis estático del código, sin BD)."""

import ast
import importlib
import pkgutil
import re
from collections import defaultdict
from pathlib import Path

import marshmallow
import pytest

RAIZ = Path(__file__).resolve().parents[2]
APP = RAIZ / "app"
TESTS = RAIZ / "tests"
MODULOS = APP / "modules"
METODOS_HTTP = {"get", "post", "put", "patch", "delete"}


def python_de(base: Path):
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def arbol(ruta: Path) -> ast.AST:
    return ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))


def relativa(ruta: Path) -> str:
    return ruta.relative_to(RAIZ).as_posix()


def llamadas(nodo: ast.AST):
    return (n for n in ast.walk(nodo) if isinstance(n, ast.Call))


def nombre_llamada(llamada: ast.Call) -> str:
    funcion = llamada.func
    if isinstance(funcion, ast.Attribute):
        return funcion.attr
    return funcion.id if isinstance(funcion, ast.Name) else ""


def importados(ruta: Path) -> list[tuple[str, list[str]]]:
    """`(módulo, nombres)` de cada `from … import …` (incluidos los imports dentro de funciones)."""
    return [
        (n.module or "", [a.name for a in n.names])
        for n in ast.walk(arbol(ruta))
        if isinstance(n, ast.ImportFrom) and n.level == 0
    ]


# --- §18: SQL, tiempo y dinero ----------------------------------------------------------------

PATRON_SQL = re.compile(
    r"(SELECT\s.+\sFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|TRUNCATE\s|"
    r"DROP\s+(TABLE|SCHEMA)|ALTER\s+TABLE)",
    re.IGNORECASE | re.DOTALL,
)


def test_no_hay_sql_construido_con_f_strings_ni_concatenacion():
    """§12 / §18: solo ORM o `select()` con parámetros."""
    sospechosos = []
    for ruta in [*python_de(APP), *python_de(TESTS)]:
        for nodo in ast.walk(arbol(ruta)):
            if isinstance(nodo, ast.JoinedStr):
                literal = "".join(
                    v.value
                    for v in nodo.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                )
                if PATRON_SQL.search(literal):
                    sospechosos.append(f"{relativa(ruta)}:{nodo.lineno} f-string con SQL")
            if isinstance(nodo, ast.Call) and nombre_llamada(nodo) in {"text", "execute"}:
                if nodo.args and isinstance(nodo.args[0], ast.BinOp):
                    sospechosos.append(f"{relativa(ruta)}:{nodo.lineno} SQL concatenado")
            if (
                isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Attribute)
                and nodo.func.attr == "format"
                and isinstance(nodo.func.value, ast.Constant)
                and isinstance(nodo.func.value.value, str)
                and PATRON_SQL.search(nodo.func.value.value)
            ):
                sospechosos.append(f"{relativa(ruta)}:{nodo.lineno} .format() con SQL")
    assert not sospechosos, sospechosos


def test_el_tiempo_siempre_sale_de_common_clock():
    """§13: nada de `datetime.now()` / `utcnow()` suelto (ni en el código ni en las pruebas)."""
    permitido = APP / "common" / "clock.py"
    sospechosos = []
    for ruta in [*python_de(APP), *python_de(TESTS)]:
        if ruta == permitido:
            continue
        for nodo in llamadas(arbol(ruta)):
            f = nodo.func
            if (
                isinstance(f, ast.Attribute)
                and f.attr in {"now", "utcnow", "today"}
                and isinstance(f.value, ast.Name | ast.Attribute)
                and getattr(f.value, "id", getattr(f.value, "attr", "")) in {"datetime", "date"}
            ):
                sospechosos.append(f"{relativa(ruta)}:{nodo.lineno}")
    assert not sospechosos, sospechosos


# Únicos usos legítimos de `float`: no son dinero (promedio de picos y % de comisión).
FLOAT_PERMITIDO = {
    "app/modules/admin/schemas.py",
    "app/modules/admin/service.py",
    "app/modules/artistas/schemas.py",
    "app/modules/artistas/service.py",
}


def test_el_dinero_nunca_usa_float():
    usos = defaultdict(list)
    for ruta in python_de(APP):
        for nodo in ast.walk(arbol(ruta)):
            es_llamada = (
                isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Name)
                and nodo.func.id == "float"
            )
            es_campo = isinstance(nodo, ast.Attribute) and nodo.attr == "Float"
            es_anotacion = isinstance(nodo, ast.Name) and nodo.id == "float"
            if es_llamada or es_campo or es_anotacion:
                usos[relativa(ruta)].append(nodo.lineno)
    assert set(usos) <= FLOAT_PERMITIDO, {k: v for k, v in usos.items() if k not in FLOAT_PERMITIDO}


def test_ningun_campo_de_dinero_es_decimal_en_la_api():
    """Los campos `*_cop` son siempre enteros en los esquemas."""
    from marshmallow import fields

    _importar_todos_los_esquemas()
    erroneos = []
    for esquema in _esquemas():
        for nombre, campo in esquema().fields.items():
            if nombre.endswith("_cop") and not isinstance(campo, fields.Int):
                erroneos.append(f"{esquema.__name__}.{nombre}")
    assert not erroneos, erroneos


def test_la_comision_solo_se_calcula_en_money_py():
    permitido = APP / "common" / "money.py"
    fuera = []
    for ruta in python_de(APP):
        if ruta == permitido:
            continue
        texto = ruta.read_text(encoding="utf-8")
        if re.search(r"ROUND_HALF|\.quantize\(", texto):
            fuera.append(relativa(ruta))
    assert not fuera, fuera
    # y quien necesita la comisión llama a `calcular_comision`
    quienes = [
        relativa(r)
        for r in python_de(MODULOS)
        if "calcular_comision" in r.read_text(encoding="utf-8")
    ]
    assert quienes == ["app/modules/compras/service.py"]


# --- §7.2: responsabilidades de cada capa -----------------------------------------------------


def test_los_repositorios_no_hacen_commit_ni_rollback():
    sospechosos = [
        f"{relativa(r)}:{n.lineno}"
        for r in python_de(MODULOS)
        if r.name == "repository.py"
        for n in llamadas(arbol(r))
        if nombre_llamada(n) in {"commit", "rollback"}
    ]
    assert not sospechosos, sospechosos


def test_los_servicios_no_conocen_http_ni_esquemas():
    """Un servicio no importa `request`, esquemas ni marshmallow/smorest, ni arma respuestas."""
    sospechosos = []
    for ruta in python_de(MODULOS):
        if ruta.name != "service.py":
            continue
        for modulo, nombres in importados(ruta):
            if modulo.endswith(".schemas") or modulo.startswith(("marshmallow", "flask_smorest")):
                sospechosos.append(f"{relativa(ruta)} importa {modulo}")
            if modulo == "flask" and {"request", "jsonify", "abort", "make_response"} & set(
                nombres
            ):
                sospechosos.append(f"{relativa(ruta)} importa {nombres} de flask")
    assert not sospechosos, sospechosos


def test_routes_py_no_tiene_logica_ni_toca_la_sesion():
    sospechosos = []
    for ruta in python_de(MODULOS):
        if ruta.name != "routes.py":
            continue
        for nodo in ast.walk(arbol(ruta)):
            if isinstance(nodo, ast.If | ast.For | ast.While | ast.Try | ast.With | ast.IfExp):
                sospechosos.append(f"{relativa(ruta)}:{nodo.lineno} lógica de control")
        for modulo, nombres in importados(ruta):
            if modulo == "app.extensions" and "db" in nombres:
                sospechosos.append(f"{relativa(ruta)} importa db.session")
            if modulo.endswith(".repository") or modulo.endswith(".models"):
                sospechosos.append(f"{relativa(ruta)} importa {modulo}")
    assert not sospechosos, sospechosos


def test_models_py_no_tiene_logica():
    sospechosos = [
        f"{relativa(r)}:{n.lineno}"
        for r in python_de(MODULOS)
        if r.name == "models.py"
        for n in ast.walk(arbol(r))
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
    ]
    assert not sospechosos, sospechosos


def test_todo_endpoint_serializa_su_respuesta_con_un_esquema():
    """Nunca se devuelve un modelo ORM sin esquema: cada handler lleva `@blp.response(...)`."""
    sin_esquema = []
    for ruta in python_de(MODULOS):
        if ruta.name != "routes.py":
            continue
        for nodo in ast.walk(arbol(ruta)):
            if not isinstance(nodo, ast.FunctionDef):
                continue
            decoradores = {
                d.func.attr if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) else ""
                for d in nodo.decorator_list
            }
            es_handler = "route" in decoradores or nodo.name in METODOS_HTTP
            if es_handler and "response" not in decoradores:
                sin_esquema.append(f"{relativa(ruta)}:{nodo.lineno} {nodo.name}")
    assert not sin_esquema, sin_esquema


# --- §7.3: dependencias entre módulos ----------------------------------------------------------

# Qué servicios puede invocar cada servicio (§7.3, más `artistas → usuarios`: bloqueo de la fila
# del artista en RN-15, igual que `eventos → usuarios`).
SERVICIOS_PERMITIDOS = {
    "admin": {"usuarios", "eventos", "compras", "artistas"},
    "artistas": {"eventos", "compras", "usuarios"},
    "transmision": {"eventos", "compras"},
    "eventos": {"compras", "usuarios"},
    "compras": {"usuarios"},
    "social": {"usuarios", "eventos"},
    "auth": {"usuarios"},
    "usuarios": set(),
    "sistema": set(),
}


def test_los_servicios_solo_llaman_a_los_servicios_permitidos():
    excesos = []
    for ruta in python_de(MODULOS):
        if ruta.name != "service.py":
            continue
        propio = ruta.parent.name
        for modulo, _ in importados(ruta):
            partes = modulo.split(".")
            if len(partes) == 4 and partes[:2] == ["app", "modules"] and partes[3] == "service":
                if partes[2] != propio and partes[2] not in SERVICIOS_PERMITIDOS[propio]:
                    excesos.append(f"{propio} -> {partes[2]}")
    assert not excesos, excesos


def test_ningun_repositorio_importa_el_repositorio_de_otro_modulo():
    """Las lecturas cruzadas se hacen sobre modelos; el repositorio ajeno nunca se toca."""
    excesos = []
    for ruta in python_de(MODULOS):
        if ruta.name not in {"repository.py", "service.py"}:
            continue
        propio = ruta.parent.name
        for modulo, _ in importados(ruta):
            partes = modulo.split(".")
            if len(partes) == 4 and partes[2] != propio and partes[3] == "repository":
                excesos.append(f"{propio}.{ruta.stem} -> {partes[2]}.repository")
    assert not excesos, excesos


def test_no_hay_ciclos_de_imports():
    """Ciclos entre archivos de `app` (sin los agregadores que importan todo a propósito)."""
    agregadores = {
        APP / "__init__.py",
        APP / "models.py",
        APP / "cli.py",
        APP / "seed.py",
        MODULOS / "__init__.py",
    }
    archivos = {
        relativa(r).removesuffix(".py").replace("/", ".").removesuffix(".__init__"): r
        for r in python_de(APP)
        if r not in agregadores
    }
    grafo = {nombre: set() for nombre in archivos}
    for nombre, ruta in archivos.items():
        for modulo, _ in importados(ruta):
            if modulo in archivos and modulo != nombre:
                grafo[nombre].add(modulo)

    ciclo = _buscar_ciclo(grafo)
    assert ciclo is None, " -> ".join(ciclo or [])


def _buscar_ciclo(grafo: dict[str, set[str]]) -> list[str] | None:
    estado: dict[str, int] = {}
    pila: list[str] = []

    def visitar(nodo: str) -> list[str] | None:
        estado[nodo] = 1
        pila.append(nodo)
        for vecino in sorted(grafo[nodo]):
            if estado.get(vecino) == 1:
                return [*pila[pila.index(vecino) :], vecino]
            if vecino not in estado and (ciclo := visitar(vecino)):
                return ciclo
        pila.pop()
        estado[nodo] = 2
        return None

    for nodo in sorted(grafo):
        if nodo not in estado and (ciclo := visitar(nodo)):
            return ciclo
    return None


def test_el_detector_de_ciclos_detecta_un_ciclo():
    assert _buscar_ciclo({"a": {"b"}, "b": {"c"}, "c": {"a"}}) is not None
    assert _buscar_ciclo({"a": {"b"}, "b": {"c"}, "c": set()}) is None


# --- §5: seis entidades -------------------------------------------------------------------------

TABLAS = {"usuarios", "eventos", "compras", "retiros", "favoritos", "seguidores"}


def test_las_migraciones_crean_exactamente_las_6_tablas():
    creadas, borradas = [], []
    for ruta in sorted((RAIZ / "migrations" / "versions").glob("*.py")):
        for nodo in llamadas(arbol(ruta)):
            if nombre_llamada(nodo) in {"create_table", "drop_table"} and nodo.args:
                destino = creadas if nombre_llamada(nodo) == "create_table" else borradas
                destino.append(nodo.args[0].value)
    assert len(creadas) == 6 and set(creadas) == TABLAS, creadas
    assert set(borradas) == TABLAS  # cada `upgrade` tiene su `downgrade`


def test_el_metadata_del_orm_tiene_solo_las_6_tablas(app):
    from app.extensions import db

    assert {t.name for t in db.metadata.sorted_tables} == TABLAS


# --- Esquemas: nada de credenciales ------------------------------------------------------------


def _importar_todos_los_esquemas() -> None:
    import app.modules as paquete

    for info in pkgutil.walk_packages(paquete.__path__, "app.modules."):
        if info.name.endswith(".schemas"):
            importlib.import_module(info.name)
    importlib.import_module("app.common.schemas")


def _esquemas() -> list[type[marshmallow.Schema]]:
    pendientes, vistos = list(marshmallow.Schema.__subclasses__()), []
    while pendientes:
        clase = pendientes.pop()
        pendientes.extend(clase.__subclasses__())
        if clase.__module__.startswith("app."):
            vistos.append(clase)
    return vistos


def test_ningun_esquema_expone_hashes_ni_contrasenas():
    _importar_todos_los_esquemas()
    esquemas = _esquemas()
    assert len(esquemas) > 30
    for esquema in esquemas:
        campos = esquema().fields
        assert "password_hash" not in campos, esquema.__name__
        assert "stream_key_hash" not in campos, esquema.__name__
        if "password" in campos:  # solo de entrada, nunca en una respuesta
            assert campos["password"].load_only, esquema.__name__
        assert not getattr(getattr(esquema, "Meta", None), "fields", None), esquema.__name__


@pytest.mark.parametrize("archivo", ["app/seed.py", "app/cli.py"])
def test_seed_y_cli_usan_los_servicios_y_no_la_sesion_directamente(archivo):
    """El seed pasa por los servicios (§ Fase 8): sin `db.session` ni INSERT directos."""
    texto = (RAIZ / archivo).read_text(encoding="utf-8")
    assert "db.session" not in texto
    assert "session.add" not in texto
