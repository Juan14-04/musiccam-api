# Backend — plataforma de transmisión en vivo de conciertos

API REST para que **artistas musicales independientes en Colombia** vendan y transmitan conciertos
virtuales puntuales, y para que los **espectadores** compren la entrada y vean el evento en vivo o
en reproducción posterior (replay). Es el backend v1 (MVP) del anteproyecto *«Diseño y evaluación
de una plataforma digital de transmisión en vivo interactiva para ampliar el acceso a eventos»*
(UCC, Grupo 01). El frontend se construye aparte y consume esta API.

> La guía operativa y las reglas de negocio (RN-xx) viven en [AGENTS.md](AGENTS.md): es la fuente
> de verdad. La trazabilidad requisito → prueba está en [docs/trazabilidad.md](docs/trazabilidad.md).

Las cuatro condiciones que el producto demuestra:

| Cód. | Condición | Cómo se cumple |
| --- | --- | --- |
| C1 | Alta autoservicio para vender | El artista se registra, crea y publica eventos solo |
| C2 | Sin cuota fija | Solo hay una comisión (10 %) por boleto vendido; no existen suscripciones |
| C3 | Cobro local integrado al acceso | Un pago aprobado (Wompi simulado) crea el acceso automáticamente |
| C4 | Replay solo para compradores | El replay exige una compra `APROBADA` (o ser el dueño / un admin) |

## Requisitos

- **Python 3.12+** (se desarrolló con 3.13)
- **Docker** (para PostgreSQL 16; también corre la API en contenedor)
- `make` (opcional: en Windows puedes ejecutar los comandos de la tabla de abajo directamente)

## Primer arranque

```bash
# 1. Variables de entorno
cp .env.example .env                 # (Windows PowerShell: Copy-Item .env.example .env)

# 2. Base de datos (PostgreSQL 16 en localhost:5433, con la base de pruebas incluida)
make db-up                           # = docker compose up -d db

# 3. Entorno virtual y dependencias
python -m venv .venv
source .venv/bin/activate            # (Windows: .venv\Scripts\activate)
make install                         # = pip install -r requirements-dev.txt

# 4. Esquema, datos de demo y servidor
make migrate                         # = flask --app wsgi db upgrade
make seed                            # = flask --app wsgi seed   (imprime las credenciales)
make run                             # = flask --app wsgi run --debug
```

Abre **<http://localhost:5000/api/docs>** (Swagger UI) y comprueba <http://localhost:5000/api/v1/health>.

> El contenedor de PostgreSQL publica el puerto **5433** (no el 5432) para no chocar con un
> PostgreSQL instalado en la máquina. Puedes cambiarlo con `POSTGRES_PORT` en `.env`; para ejecutar
> el backend fuera de Docker, ajusta también el puerto de `DATABASE_URL` y `TEST_DATABASE_URL`.
> `POSTGRES_PASSWORD` se usa para la base dentro de Docker. Si la cambias, actualiza también las
> credenciales en ambas URLs para poder conectarte desde el host.

### Base de datos y migraciones

- El volumen Docker `pgdata` conserva los datos aunque detengas o recrees los contenedores.
- `docker compose down` detiene y elimina contenedores, pero **conserva** los datos.
- `docker compose down -v` elimina también el volumen y **borra permanentemente** la base local.
- Las migraciones Alembic son la fuente del esquema; `docker/initdb/01-bd-pruebas.sql` solo crea
  `streaming_test` la primera vez que se inicializa el volumen.
- Para inspeccionar la base: `make db-shell`; para ver el arranque de PostgreSQL: `make db-logs`.

### Todo en contenedores

```bash
cp .env.example .env
docker compose up -d --build        # = make up   (base de datos, API y frontend)
docker compose exec api flask --app wsgi seed
docker compose watch --no-up        # en otra terminal: sincroniza los cambios del frontend
```

Abre la aplicación en <http://localhost:5173> y la documentación de la API en
<http://localhost:8000/api/docs>. El frontend se conecta a la API del contenedor y recarga los
cambios locales; la API corre con un usuario sin privilegios y `gunicorn -w 3`.

## Comandos

| `make …` | Equivale a | Qué hace |
| --- | --- | --- |
| `up` | `docker compose up -d --build` | Levanta la base de datos, la API y el frontend |
| `db-up` | `docker compose up -d db` | Levanta PostgreSQL y crea las bases iniciales |
| `db-down` | `docker compose stop db` | Detiene PostgreSQL sin borrar sus datos |
| `db-logs` | `docker compose logs -f db` | Sigue los logs del contenedor PostgreSQL |
| `db-shell` | `docker compose exec db psql -U app -d streaming` | Abre una consola SQL en la base principal |
| `install` | `pip install -r requirements-dev.txt` | Instala dependencias (producción + desarrollo) |
| `migrate` | `flask --app wsgi db upgrade` | Aplica las migraciones |
| `migration m="texto"` | `flask --app wsgi db migrate -m "texto"` | Genera una migración (revísala siempre) |
| `seed` | `flask --app wsgi seed` | Carga datos de demo (idempotente; no corre en producción) |
| `run` | `flask --app wsgi run --debug` | Servidor de desarrollo en el puerto 5000 |
| `test` | `pytest --cov=app --cov-report=term-missing` | Pruebas contra PostgreSQL real, con cobertura |
| `lint` | `ruff check . && ruff format --check .` | Calidad de código |
| `openapi` | `flask --app wsgi openapi write docs/openapi.json` | Exporta la especificación OpenAPI versionada |

Tras cambiar rutas o esquemas ejecuta `make openapi` y versiona `docs/openapi.json` (una prueba avisa
si queda desactualizado).

Los administradores se crean únicamente mediante una inserción directa en PostgreSQL. Guarda
siempre el hash de contraseña generado con Werkzeug, nunca la contraseña en texto plano. El comando
`make seed` requiere que ya exista un administrador activo y no crea ni imprime credenciales ADMIN.

## Usuarios de demo

`make seed` crea cuentas demo de artistas y usuarios (todas las contraseñas son `Demo1234`); requiere
un ADMIN activo ya existente en PostgreSQL para ejecutar las operaciones administrativas:

| Rol | Correo | Qué tiene |
| --- | --- | --- |
| ARTISTA | `artista1@demo.co` | Eventos en borrador, programado y finalizado con replay; saldo y un retiro pagado y otro pendiente |
| ARTISTA | `artista2@demo.co` | Un evento programado y otro suspendido por moderación |
| ARTISTA | `artista3@demo.co` | Un evento **en vivo**, uno cancelado (con compra reembolsada) y uno programado |
| USUARIO | `usuario1@demo.co` … `usuario5@demo.co` | Compras aprobadas, pendiente, rechazada y anulada; favoritos y artistas seguidos |

Son 8 eventos en todos los estados (borrador, programado, en vivo, finalizado con replay, cancelado
y uno suspendido). Los datos se crean pasando por los servicios, así que saldos, estadísticas y el
dashboard son coherentes. Ejecutar `make seed` de nuevo no duplica nada.

## Probar el flujo completo con Swagger

En <http://localhost:5000/api/docs>: haz `POST /auth/login`, copia el `access_token` y pulsa
**Authorize** (pega solo el token). Para cambiar de rol, vuelve a autorizar con otro token.

1. **Registro** — `POST /auth/registro` dos veces: un `ARTISTA` (con `nombre_artistico`) y un
   `USUARIO`, ambos con `acepta_tratamiento_datos: true`. Luego `POST /auth/login` con cada uno.
2. **Evento** (como artista) — `POST /eventos` con título, `precio_cop` (5.000–500.000) y una
   `fecha_inicio` futura; después `POST /eventos/{id}/publicar`. Aparece en `GET /eventos`.
3. **Compra** (como usuario) — `POST /eventos/{id}/compras` con `{"metodo_pago": "NEQUI"}`. Copia la
   `referencia` y llama a `POST /pagos/wompi/simulador/{referencia}` con `{"resultado": "APPROVED"}`:
   la compra queda `APROBADA` y el acceso existe, sin más pasos.
4. **Emisión** (como artista) — `POST /eventos/{id}/transmision/credenciales` (la `stream_key` se
   muestra una sola vez), luego `POST …/transmision/iniciar` con esa clave.
5. **Ver en vivo** (como usuario) — `POST /eventos/{id}/reproduccion/entrar` devuelve
   `{tipo: "VIVO", url, token}`. Para poblar la audiencia en una demo: `POST /dev/eventos/{id}/audiencia-simulada`.
6. **Finalizar y replay** — el artista llama a `POST …/transmision/finalizar`; el usuario vuelve a
   `…/reproduccion/entrar` y recibe `tipo: "REPLAY"`. Quien no compró recibe `403 ACCESO_NO_AUTORIZADO`.
7. **Dinero** (como artista) — `GET /artistas/me/estadisticas` y `GET /artistas/me/saldo`; con el evento
   finalizado el neto (90 %) está disponible. `POST /artistas/me/retiros` (mínimo 50.000 COP).
8. **Admin** — autoriza con el admin: `GET /admin/retiros` y `POST /admin/retiros/{id}/aprobar`
   (queda `PAGADO`); `GET /admin/dashboard` para las métricas; `POST /admin/eventos/{id}/suspender`
   para moderar.

La prueba [`tests/integration/test_e2e_demo.py`](tests/integration/test_e2e_demo.py) ejecuta
exactamente este recorrido.

## Estructura de carpetas

```text
backend/
├── AGENTS.md · README.md · Makefile · wsgi.py · pyproject.toml
├── requirements.txt / requirements-dev.txt   # versiones fijadas (pip freeze)
├── Dockerfile · docker-compose.yml · .env.example
├── docker/initdb/                 # crea la base de pruebas dentro del contenedor
├── docs/                          # openapi.json (versionado) y trazabilidad.md
├── migrations/                    # Alembic
├── uploads/                       # fotos de perfil (fuera de git)
├── tests/
│   ├── conftest.py · factories.py
│   ├── unit/                      # comisión, reloj, gateways, y guardas de arquitectura
│   └── integration/               # endpoints por módulo, contra PostgreSQL
└── app/
    ├── __init__.py                # create_app()
    ├── config.py · extensions.py · cli.py · seed.py
    ├── common/                    # errores, seguridad, dinero, reloj, paginación, cabeceras
    ├── infrastructure/            # puertos y adaptadores: payments/ streaming/ storage/
    └── modules/                   # un módulo por dominio, en capas routes→service→repository→models
        ├── auth/ usuarios/ social/ eventos/ transmision/
        └── compras/ artistas/ admin/ sistema/
```

Flujo de dependencias: `routes → service → repository → models`. Las integraciones externas
(pagos, streaming, almacenamiento) se usan solo a través de interfaces en `infrastructure/`, de modo
que reemplazar el simulador por Wompi o MediaMTX reales es cambiar un adaptador.

## Seguridad (resumen)

- JWT: acceso 15 min, refresh 7 días. El rol del token es solo una pista: cada petición recarga al
  usuario desde la BD, así que una suspensión surte efecto de inmediato.
- Límites de peticiones por IP: login 5/min, registro 10/hora, webhook de Wompi 120/min (`429`).
- Cabeceras: `X-Content-Type-Options: nosniff` siempre y `Cache-Control: no-store` en respuestas
  autenticadas y en las que entregan tokens.
- Con `APP_ENV=production` la app **no arranca** si `SECRET_KEY`, `JWT_SECRET_KEY` o
  `PLAYBACK_TOKEN_SECRET` conservan el valor del `.env.example`, ni con `CORS_ORIGINS` con `*`;
  además fuerza `ENABLE_DEV_ENDPOINTS=false` y `ENFORCE_START_WINDOW=true`.
- La clave de emisión se guarda solo como hash SHA-256; de las cuentas bancarias solo los últimos
  4 dígitos; los errores de SQL no incluyen los valores enlazados y ningún log contiene contraseñas,
  tokens, claves ni secretos (hay pruebas que lo verifican).

## Pruebas y calidad

```bash
make lint            # ruff
make test            # 700+ pruebas contra PostgreSQL real + informe de cobertura
```

Las pruebas usan la base `streaming_test` (se crea sola con `docker compose up -d db`) y recrean su
esquema ejecutando las migraciones. Incluyen guardas de arquitectura (sin SQL con f-strings,
sin `float` para dinero, sin `datetime.now()` fuera de `common/clock.py`, sin ciclos de imports,
solo las 6 tablas…) y la auditoría de Swagger de todos los endpoints.

## Limitaciones conocidas

- **Pagos simulados.** Wompi está simulado: misma forma de checkout, firma y webhook que el real,
  pero no mueve dinero. Los reembolsos y los pagos de retiros también son simulados.
- **Transmisión simulada.** Las URLs HLS/RTMP son ficticias (no hay MediaMTX) y la audiencia se
  cuenta solo con `reproduccion/entrar|salir`. El endpoint `/dev/...` solo existe fuera de producción.
- **Chat diferido.** RQ4 queda parcial: no hay chat en vivo (sería la séptima entidad, `mensajes_chat`).
- **Sin revocación de tokens.** El «cierre de sesión» lo hace el cliente descartando los tokens
  (una cuenta suspendida sí queda bloqueada al instante).
- **Límites de peticiones en memoria.** Cada proceso de gunicorn lleva sus propios contadores; para
  varios workers o réplicas hace falta un almacenamiento compartido (p. ej. Redis).
- **`salir` de la reproducción** no distingue quién salió (no hay tabla de sesiones de espectador).
- **No incluido en este sprint:** confirmación de correo, recuperación de contraseña,
  `POST /auth/cambiar-password` (P2), OAuth, notificaciones, auditoría, subida de portadas,
  fotos en almacenamiento externo (S3) y múltiples monedas.
