# AGENTS.md — Backend de la plataforma de transmisión en vivo de conciertos

> Guía operativa para agentes de IA y desarrolladores. **Léela completa antes de escribir código.**
> Origen: anteproyecto *"Diseño y evaluación de una plataforma digital de transmisión en vivo interactiva para ampliar el acceso a eventos"* (UCC, Grupo 01). Referencias de UX: Veeps y Vibenix.
> Alcance de este documento: **backend v1 (MVP)**. El frontend se construye aparte y consume esta API.

---

## 1. Qué estamos construyendo

API REST que permite a **artistas musicales independientes en Colombia** vender y transmitir conciertos virtuales puntuales, y a **espectadores** comprar la entrada y verla en vivo o en reproducción posterior (replay).

El anteproyecto define cuatro condiciones que el producto debe demostrar. **Toda decisión de diseño debe preservarlas:**

| Cód. | Condición | Cómo se cumple en el backend |
|------|-----------|------------------------------|
| C1 | Alta autoservicio para vender | Registro de artista sin verificación manual; el artista crea y publica eventos solo (controles mínimos en RN-02) |
| C2 | Operación repetible sin cuota fija | **No existen suscripciones ni cargos fijos al artista.** El único costo es una comisión por boleto vendido (RN-06) |
| C3 | Cobro local integrado al acceso | Un pago Wompi aprobado (simulado) crea el acceso **automáticamente**, sin pasos manuales (RN-08) |
| C4 | Replay restringido a compradores | El replay solo se entrega con compra `APROBADA` (RN-10) |

### Requisitos obligatorios del anteproyecto (RQ) → dónde se cubren

| RQ | Requisito | Módulo | Estado en este sprint |
|----|-----------|--------|-----------------------|
| RQ1 | Alta autoservicio | `auth` | ✅ |
| RQ2 | Evento con precio, sin cuota fija | `eventos` | ✅ |
| RQ3 | Compra con medio local y acceso automático | `compras` | ✅ (Wompi **simulado**) |
| RQ4 | Transmisión en vivo con chat | `transmision` | ⚠️ Transmisión simulada ✅; la interfaz tiene chat de demostración local, el chat real sigue diferido (ver §17) |
| RQ5 | Replay solo para compradores | `transmision` | ✅ (replay simulado) |
| RQ6 | Emisión solo con clave válida | `transmision` | ✅ |
| RQ7 | Registro con consentimiento de datos | `auth` | ✅ |

---

## 2. Alcance del sprint

**Dentro (MVP):**
- 3 roles: `USUARIO`, `ARTISTA`, `ADMIN`.
- Usuario: editar perfil, **foto de perfil**, **favoritos** (eventos), **artistas que sigue**, comprar entradas, ver/reproducir.
- Artista: crear/gestionar eventos, **transmitir (simulado)**, **ver estadísticas**, **consultar saldo y retirar dinero**.
- Pagos con **Wompi simulado** (misma forma de payload, firma y webhook que el real).
- **Transmisión simulada** (claves, URLs HLS/RTMP ficticias, contadores de audiencia).
- **Panel de administración**: usuarios, eventos, compras, retiros, métricas.
- **Máximo 6 entidades** (ver §5). No agregar tablas sin aprobación explícita.
- Flask + PostgreSQL + SQLAlchemy, arquitectura modular por capas, Swagger/OpenAPI.

**Fuera (no implementar ahora):** chat en tiempo real, grabación/ingesta real con MediaMTX, dinero real, confirmación de correo, recuperación de contraseña, OAuth/social login, notificaciones, tabla de auditoría, tabla de configuración, subida de portadas, múltiples monedas, internacionalización. Ver §17 para el plan de evolución.

### Supuestos adoptados (confírmalos o cámbialos antes de implementar)

1. **Favoritos = eventos** (como "Mis Favoritos" en las referencias); **seguidos = artistas**. Son dos tablas distintas.
2. **`ARTISTA` hereda las capacidades de espectador** (comprar, ver, favoritos, seguir), salvo comprar su propio evento. `ADMIN` no compra.
3. **El retiro lo resuelve un ADMIN** (aprobar → `PAGADO` simulado). Con `RETIRO_AUTO_APROBAR=true` se aprueba solo (útil para demos).
4. **El saldo no se guarda: se calcula** a partir de compras y retiros (evita inconsistencias).
5. La **comisión de plataforma es 10 %** (configurable). El anteproyecto modela una comisión de equilibrio de 8–10,5 % y usa 15 % como umbral interno; 10 % queda dentro del margen. La tarifa de Wompi (2,65 % + 700 COP + IVA) la absorbe la plataforma dentro de esa comisión.
6. **Dinero en pesos colombianos enteros (COP), nunca `float`.**

---

## 3. Stack tecnológico

| Capa | Tecnología | Notas |
|------|-----------|-------|
| Lenguaje | Python **3.12+** | Type hints en todo el código nuevo |
| Framework | **Flask 3.x** (application factory + Blueprints) | |
| API / Swagger | **flask-smorest** (+ apispec + marshmallow) | Swagger UI en `/api/docs`, JSON en `/api/openapi.json` |
| ORM | **SQLAlchemy 2.x** (estilo 2.0: `Mapped[]`, `mapped_column`, `select()`) vía **Flask-SQLAlchemy 3.1** | Prohibido el estilo legacy `Query` |
| Migraciones | **Flask-Migrate** (Alembic) | Toda alteración del esquema va en una migración |
| Base de datos | **PostgreSQL 16+** | Driver `psycopg` 3 → `postgresql+psycopg://` |
| Autenticación | **Flask-JWT-Extended** | Access (15 min) + refresh (7 días) |
| Hash de contraseñas | `werkzeug.security` (scrypt) | |
| Rate limiting | **Flask-Limiter** | Login, registro, webhook |
| CORS | **Flask-CORS** | Lista blanca de orígenes por env |
| Imágenes | **Pillow** | Validar y re-codificar la foto de perfil |
| Servidor WSGI | **gunicorn** | |
| Pruebas | **pytest** + pytest-cov | Contra PostgreSQL real, no SQLite |
| Calidad | **ruff** (lint + format) | |
| Contenedores | Docker + docker-compose (`db`, `api`) | |

**Versiones verificadas** (se resuelven e instalan juntas; una prueba de humo con modelo `Mapped` e índice parcial declarado, esquema marshmallow con `unknown=RAISE`, JWT, `/api/openapi.json` y `/api/docs` pasó correctamente. Esa prueba corrió sobre SQLite, así que el comportamiento real en PostgreSQL —índice parcial, `FOR UPDATE`, `ENUM`— se valida con las pruebas del proyecto): Flask 3.1, Flask-SQLAlchemy 3.1.1, SQLAlchemy 2.1, Flask-Migrate 4.1, Alembic 1.20, flask-smorest 0.47, apispec 6.10, marshmallow 4.3, Flask-JWT-Extended 4.7, Flask-Limiter 4.1, Flask-CORS 6.0, psycopg 3.3, Pillow 13. **Fíjalas en `requirements.txt` con `pip freeze` tras el primer `pip install`.**

> **marshmallow 4:** usa `load_default=` / `dump_default=` (ya no existen `missing=` / `default=`). Declara `class Meta: unknown = RAISE` en los esquemas de entrada.

---

## 4. Roles y permisos

Rol almacenado en `usuarios.rol`. El token JWT lleva el `rol` como claim **solo como pista**; el decorador de autorización **siempre recarga el usuario desde la BD** y verifica `estado = ACTIVO` y el rol real (así una suspensión surte efecto de inmediato).

| Capacidad | Público | USUARIO | ARTISTA | ADMIN |
|-----------|:-------:|:-------:|:-------:|:-----:|
| Registrarse / iniciar sesión | ✅ | | | |
| Ver catálogo de eventos y perfiles de artistas | ✅ | ✅ | ✅ | ✅ |
| Editar perfil propio y foto | | ✅ | ✅ | ✅ |
| Favoritos (eventos) y seguir artistas | | ✅ | ✅ | |
| Comprar entrada y ver evento/replay comprado | | ✅ | ✅ (no el propio) | ver cualquiera (moderación) |
| Crear / editar / publicar / cancelar eventos propios | | | ✅ | |
| Credenciales de emisión, iniciar/finalizar transmisión | | | ✅ (propios) | |
| Estadísticas, saldo y retiros propios | | | ✅ | |
| Panel admin (usuarios, eventos, compras, retiros, métricas) | | | | ✅ |

**Regla anti-IDOR:** cualquier recurso "propio" se verifica por `recurso.dueño_id == usuario_actual.id`. Si no coincide → `403` (o `404` si no debe revelar su existencia).

---

## 5. Modelo de datos (6 entidades)

```mermaid
erDiagram
  USUARIOS ||--o{ EVENTOS : "crea (artista)"
  USUARIOS ||--o{ COMPRAS : "compra"
  EVENTOS  ||--o{ COMPRAS : "se vende en"
  USUARIOS ||--o{ RETIROS : "solicita (artista)"
  USUARIOS ||--o{ FAVORITOS : "marca"
  EVENTOS  ||--o{ FAVORITOS : "es marcado"
  USUARIOS ||--o{ SEGUIDORES : "sigue"
  USUARIOS ||--o{ SEGUIDORES : "es seguido (artista)"
```

**Convenciones de BD:** tablas en español, plural, `snake_case`; PK `UUID` v4 (evita enumeración) salvo tablas puente con PK compuesta; fechas `TIMESTAMPTZ` en **UTC**; dinero `INTEGER` en COP; enums como `ENUM` de PostgreSQL (o `VARCHAR` + `CHECK`, pero consistente en todo el proyecto); `ON DELETE RESTRICT` por defecto (no se borra físicamente nada con historial financiero).

### 5.1 `usuarios`
| Columna | Tipo | Restricciones / notas |
|---------|------|-----------------------|
| `id` | UUID | PK |
| `email` | VARCHAR(254) | NOT NULL; **único por `lower(email)`**; se normaliza a minúsculas al guardar |
| `password_hash` | VARCHAR(255) | NOT NULL; nunca se expone |
| `nombre` | VARCHAR(100) | NOT NULL |
| `rol` | ENUM `USUARIO/ARTISTA/ADMIN` | NOT NULL |
| `estado` | ENUM `ACTIVO/SUSPENDIDO` | NOT NULL, default `ACTIVO` |
| `foto_url` | VARCHAR(255) | NULL; ruta relativa servida por la API |
| `nombre_artistico` | VARCHAR(100) | NULL; **obligatorio y único si `rol=ARTISTA`** (`CHECK` + índice único parcial) |
| `biografia` | TEXT | NULL; máx. 1000 caracteres (validado en esquema) |
| `consentimiento_datos_en` | TIMESTAMPTZ | NOT NULL (RQ7) |
| `version_politica_datos` | VARCHAR(20) | NOT NULL (RQ7) |
| `creado_en`, `actualizado_en` | TIMESTAMPTZ | NOT NULL |

### 5.2 `eventos`
| Columna | Tipo | Restricciones / notas |
|---------|------|-----------------------|
| `id` | UUID | PK |
| `artista_id` | UUID | FK → `usuarios`, NOT NULL, índice |
| `titulo` | VARCHAR(150) | NOT NULL |
| `descripcion` | TEXT | NULL |
| `categoria` | ENUM | `POP, ROCK, URBANO, ELECTRONICA, SALSA_TROPICAL, FOLCLOR, JAZZ_BLUES, CLASICA, OTRO` |
| `portada_url` | VARCHAR(255) | NULL; URL externa (no hay subida de portadas en el MVP) |
| `youtube_url` | VARCHAR(255) | NULL en datos existentes; nuevos eventos requieren un tráiler público HTTPS de YouTube |
| `precio_cop` | INTEGER | NOT NULL, `CHECK (precio_cop > 0)`; rango real en RN-04 |
| `fecha_inicio` | TIMESTAMPTZ | NOT NULL |
| `duracion_estimada_min` | INTEGER | NOT NULL, default 60, `CHECK` 15–240 |
| `estado` | ENUM `BORRADOR/PROGRAMADO/EN_VIVO/FINALIZADO/CANCELADO` | NOT NULL, default `BORRADOR` |
| `replay_habilitado` | BOOLEAN | default `true` (decisión del artista) |
| `replay_disponible` | BOOLEAN | default `false`; pasa a `true` al finalizar si `replay_habilitado` |
| `stream_key_hash` | VARCHAR(64) | NULL; SHA-256 de la clave de emisión (nunca la clave en claro) |
| `inicio_real`, `fin_real` | TIMESTAMPTZ | NULL |
| `espectadores_actuales` | INTEGER | default 0, `CHECK >= 0` |
| `pico_espectadores` | INTEGER | default 0 |
| `vistas_vivo`, `vistas_replay` | INTEGER | default 0 |
| `suspendido_admin` | BOOLEAN | default `false` (moderación, independiente del ciclo de vida) |
| `motivo_suspension` | VARCHAR(255) | NULL |
| `creado_en`, `actualizado_en` | TIMESTAMPTZ | NOT NULL |

Índices: `(estado, fecha_inicio)`, `(artista_id, estado)`, `(categoria)`.
Las URLs de vivo/replay **no se guardan**: las deriva el `StreamingProvider` (§11).

### 5.3 `compras`
| Columna | Tipo | Restricciones / notas |
|---------|------|-----------------------|
| `id` | UUID | PK |
| `usuario_id` | UUID | FK → `usuarios`, NOT NULL |
| `evento_id` | UUID | FK → `eventos`, NOT NULL |
| `referencia` | VARCHAR(64) | **UNIQUE**, NOT NULL; es la `reference` que se envía a Wompi |
| `monto_cop` | INTEGER | Precio **congelado** al comprar; `CHECK > 0` |
| `comision_pct` | NUMERIC(5,2) | % aplicado (auditoría) |
| `comision_cop` | INTEGER | Parte de la plataforma |
| `neto_artista_cop` | INTEGER | Parte del artista; `CHECK (monto_cop = comision_cop + neto_artista_cop)` |
| `metodo_pago` | ENUM `CARD/PSE/NEQUI` | NOT NULL |
| `estado` | ENUM `PENDIENTE/APROBADA/RECHAZADA/ANULADA/REEMBOLSADA` | NOT NULL, default `PENDIENTE` |
| `wompi_transaccion_id` | VARCHAR(64) | NULL; en simulación `sim-<uuid>` |
| `expira_en` | TIMESTAMPTZ | NOT NULL; las `PENDIENTE` vencidas pasan a `ANULADA` |
| `creado_en`, `aprobado_en`, `actualizado_en` | TIMESTAMPTZ | |

**Índice único parcial** (crítico, evita doble compra y condiciones de carrera):
`UNIQUE (usuario_id, evento_id) WHERE estado IN ('PENDIENTE','APROBADA')`
(en SQLAlchemy: `Index(..., unique=True, postgresql_where=text("estado IN ('PENDIENTE','APROBADA')"))`).

> **No existe tabla `accesos`.** El acceso a un evento *es* tener una compra `APROBADA` (C3: acceso automático).

### 5.4 `retiros`
| Columna | Tipo | Restricciones / notas |
|---------|------|-----------------------|
| `id` | UUID | PK |
| `artista_id` | UUID | FK → `usuarios`, NOT NULL, índice |
| `monto_cop` | INTEGER | `CHECK > 0` |
| `banco` | VARCHAR(60) | NOT NULL |
| `tipo_cuenta` | ENUM `AHORROS/CORRIENTE/NEQUI` | NOT NULL |
| `cuenta_ultimos4` | CHAR(4) | **Solo los últimos 4 dígitos.** Nunca guardar el número completo |
| `estado` | ENUM `PENDIENTE/PAGADO/RECHAZADO` | default `PENDIENTE` |
| `referencia_pago` | VARCHAR(64) | NULL; payout simulado `sim-payout-<uuid>` |
| `motivo_rechazo` | VARCHAR(255) | NULL |
| `resuelto_por` | UUID | FK → `usuarios` (admin), NULL |
| `solicitado_en`, `resuelto_en` | TIMESTAMPTZ | |

### 5.5 `favoritos` (tabla puente)
`usuario_id` FK, `evento_id` FK, `creado_en`. **PK compuesta `(usuario_id, evento_id)`**.

### 5.6 `seguidores` (tabla puente)
`usuario_id` FK (quien sigue), `artista_id` FK (el seguido), `creado_en`. **PK compuesta `(usuario_id, artista_id)`**, `CHECK (usuario_id <> artista_id)`. Que `artista_id` tenga `rol=ARTISTA` se valida en el servicio.

### 5.7 Lo que se calcula y NO se almacena
- **Acceso** a un evento → existe compra `APROBADA`.
- **Saldo del artista** → ver RN-14.
- **Estadísticas** → agregaciones sobre `compras`, `eventos`, `favoritos`, `seguidores`.
- **Conteo de seguidores** → `COUNT(*)` sobre `seguidores`.

---

## 6. Máquinas de estado

**Evento**
```
BORRADOR ──publicar──▶ PROGRAMADO ──iniciar (con clave)──▶ EN_VIVO ──finalizar──▶ FINALIZADO
    │                       │
    └────────cancelar───────┴──▶ CANCELADO        (EN_VIVO y FINALIZADO no se cancelan)
```

**Compra**
```
PENDIENTE ──Wompi APPROVED──▶ APROBADA ──(cancelar evento)──▶ REEMBOLSADA
    ├─────Wompi DECLINED / ERROR──▶ RECHAZADA
    └─────Wompi VOIDED o expira_en──▶ ANULADA
```
Los estados terminales **no retroceden** (un webhook tardío no puede pasar `APROBADA` a `RECHAZADA`).

**Retiro:** `PENDIENTE ──▶ PAGADO | RECHAZADO`.

---

## 7. Arquitectura: modular por capas

### 7.1 Principios
1. **Módulos por dominio** (vertical) y **capas dentro de cada módulo** (horizontal).
2. Flujo de dependencias **unidireccional**: `routes → service → repository → models`.
3. Las **integraciones externas** (pagos, streaming, almacenamiento) se acceden **solo** a través de interfaces en `infrastructure/` (patrón puerto/adaptador). Así el simulador se reemplaza por Wompi/MediaMTX reales sin tocar la lógica de negocio.

### 7.2 Responsabilidad de cada capa

| Capa (archivo) | Hace | **No** hace |
|----------------|------|-------------|
| `routes.py` (controlador) | Declara endpoints, aplica `@jwt_required` y `@requiere_roles`, valida entrada con esquemas, llama al servicio, serializa salida | Lógica de negocio, consultas, `db.session` |
| `schemas.py` (**DTOs**) | Esquemas marshmallow de entrada/salida; documentan Swagger | Lógica de negocio |
| `service.py` (negocio) | Reglas de negocio, permisos de propiedad, transiciones de estado, **frontera transaccional** (`commit`/`rollback`), orquesta repositorios e infraestructura | Importar `flask.request`, esquemas marshmallow, ni armar respuestas HTTP |
| `repository.py` (datos) | Consultas `select()`, `add`, `flush`, bloqueos `with_for_update()` | `commit`, reglas de negocio |
| `models.py` (ORM) | Tablas, relaciones, constraints, índices | Lógica |

**DTOs:** los `Schema` de marshmallow son los DTOs de la API. **Nunca se devuelve un modelo ORM sin esquema**, y nunca se expone `password_hash` ni `stream_key_hash`. Convención de nombres: `EventoCrearSchema`, `EventoActualizarSchema`, `EventoSchema` (detalle), `EventoResumenSchema` (listados). Entre capas internas se pasan modelos o `dataclass`, no esquemas.

### 7.3 Dependencias permitidas entre módulos
Un módulo puede llamar al **servicio** de otro (nunca a su repositorio para escribir). Sentido permitido (sin ciclos):

```
admin ──▶ (todos)
artistas ──▶ eventos, compras, usuarios   # usuarios: bloqueo de la fila del artista (RN-15) y perfil público
transmision ──▶ eventos, compras
eventos ──▶ compras, usuarios        # cancelar evento → compras.reembolsar_por_evento()
compras ──▶ usuarios                 # lee el Evento solo por su propio repositorio (solo lectura)
social ──▶ usuarios, eventos
auth ──▶ usuarios
```
El criterio de visibilidad del catálogo (RN-21) vive junto a la tabla, en `eventos/models.py` (`VISIBLE_EN_CATALOGO`), para que `social` y `eventos` lo reutilicen sin importar el repositorio ajeno. `tests/unit/test_arquitectura.py` vigila estas dependencias, la ausencia de ciclos y el resto de reglas de §18.
Los **modelos** pueden importarse entre módulos (para FKs y joins de solo lectura). Si una consulta cruza módulos (p. ej. `compras` necesita el estado y precio del evento, o `eventos` necesita saber si hay compras aprobadas), vive en el repositorio del módulo que la necesita, **solo lectura**; las escrituras pasan siempre por el servicio dueño de la entidad. **Nunca introduzcas un ciclo de imports entre servicios**: si lo necesitas, mueve la consulta a un repositorio de solo lectura.

### 7.4 Estructura de carpetas

```
backend/
├── AGENTS.md
├── README.md
├── wsgi.py                        # from app import create_app; app = create_app()
├── requirements.txt / requirements-dev.txt
├── Dockerfile · docker-compose.yml · Makefile · .env.example
├── migrations/                    # Alembic (versionado en git)
├── uploads/                       # fotos de perfil (en .gitignore)
├── tests/
│   ├── conftest.py                # app, BD de prueba, cliente, fixtures de roles/tokens
│   ├── factories.py               # helpers para crear usuarios/eventos/compras
│   ├── unit/                      # reglas de negocio (servicios)
│   └── integration/               # endpoints por módulo, contra PostgreSQL
└── app/
    ├── __init__.py                # create_app(): config, extensiones, módulos, errores, Swagger
    ├── config.py                  # clases Development/Testing/Production desde env
    ├── extensions.py              # db, migrate, jwt, limiter, cors, api (smorest)
    ├── cli.py                     # comando: flask seed (requiere un ADMIN activo preexistente)
    ├── common/
    │   ├── enums.py               # Rol, EstadoUsuario, EstadoEvento, EstadoCompra, ...
    │   ├── errors.py              # AppError y subclases + manejadores
    │   ├── security.py            # requiere_roles(), usuario_actual()
    │   ├── pagination.py          # paginar(select, page, page_size)
    │   ├── money.py               # calcular_comision() con Decimal, redondeo half-up
    │   ├── clock.py               # ahora_utc() (inyectable/mockeable en tests)
    │   └── schemas.py             # ErrorSchema, PaginaSchema base
    ├── infrastructure/
    │   ├── payments/   base.py (PaymentGateway) · wompi_simulado.py
    │   ├── streaming/  base.py (StreamingProvider) · simulado.py
    │   └── storage/    base.py (FileStorage) · local.py
    └── modules/
        ├── auth/        routes.py · schemas.py · service.py
        ├── usuarios/    routes.py · schemas.py · service.py · repository.py · models.py
        ├── social/      routes.py · schemas.py · service.py · repository.py · models.py   # Favorito, Seguidor
        ├── eventos/     routes.py · schemas.py · service.py · repository.py · models.py
        ├── transmision/ routes.py · schemas.py · service.py                                # sin modelo propio
        ├── compras/     routes.py · schemas.py · service.py · repository.py · models.py   # incluye webhook
        ├── artistas/    routes.py · schemas.py · service.py · repository.py · models.py   # Retiro, estadísticas, saldo
        └── admin/       routes.py · schemas.py · service.py                                # sin modelo propio
```
`app/modules/__init__.py` expone `registrar_modulos(api)`; `app/models.py` importa todos los modelos para que Alembic los detecte.

Añadidos durante el sprint: `app/seed.py` (datos de demo vía servicios), `app/common/headers.py` (cabeceras de seguridad), `app/common/sql.py` (patrón `ILIKE` con escape), `app/modules/sistema/` (`/health`), `docs/openapi.json` (especificación exportada) y `docs/trazabilidad.md` (matriz §15 → pruebas).

---

## 8. API REST

**Base:** `/api/v1` · **Formato:** JSON UTF-8 · **Auth:** `Authorization: Bearer <access_token>`
**Swagger UI:** `/api/docs` · **OpenAPI JSON:** `/api/openapi.json` · **Health:** `GET /api/v1/health` (verifica BD)

Prioridad: **P0** = imprescindible para el MVP · **P1** = importante · **P2** = deseable si sobra tiempo.
Códigos de éxito: `200` consulta/acción, `201` creación, `204` sin cuerpo (DELETE, PUT idempotentes).

### 8.1 Auth — tag `Auth`
| Método y ruta | Acceso | Descripción | P |
|---------------|--------|-------------|---|
| `POST /auth/registro` | Público | `{nombre, email, password, rol: USUARIO\|ARTISTA, nombre_artistico?, acepta_tratamiento_datos: true}`. Artista exige `nombre_artistico`. `ADMIN` no se puede auto-registrar | P0 |
| `POST /auth/login` | Público | `{email, password}` → `{access_token, refresh_token, usuario}` | P0 |
| `POST /auth/refresh` | Refresh token | Nuevo access token | P0 |
| `POST /auth/cambiar-password` | Autenticado | `{password_actual, password_nueva}` — **no implementado en este sprint** | P2 |

### 8.2 Usuario (perfil, favoritos, seguidos, entradas) — tag `Usuarios`
| Método y ruta | Acceso | Descripción | P |
|---------------|--------|-------------|---|
| `GET /usuarios/me` | Autenticado | Perfil propio | P0 |
| `PATCH /usuarios/me` | Autenticado | Editar `nombre`, `biografia`, `nombre_artistico` (artista) | P0 |
| `PUT /usuarios/me/foto` | Autenticado | `multipart/form-data` campo `foto` (RN-18) | P0 |
| `DELETE /usuarios/me/foto` | Autenticado | Quitar foto | P1 |
| `GET /usuarios/me/favoritos` | USUARIO, ARTISTA | Eventos favoritos (paginado) | P0 |
| `PUT /usuarios/me/favoritos/{evento_id}` | USUARIO, ARTISTA | Agregar (idempotente) → `204` | P0 |
| `DELETE /usuarios/me/favoritos/{evento_id}` | USUARIO, ARTISTA | Quitar (idempotente) → `204` | P0 |
| `GET /usuarios/me/seguidos` | USUARIO, ARTISTA | Artistas que sigo | P0 |
| `PUT /usuarios/me/seguidos/{artista_id}` | USUARIO, ARTISTA | Seguir (idempotente) → `204` | P0 |
| `DELETE /usuarios/me/seguidos/{artista_id}` | USUARIO, ARTISTA | Dejar de seguir → `204` | P0 |
| `GET /usuarios/me/compras` | USUARIO, ARTISTA | Mis entradas/eventos; filtros `estado`, `proximos=true` (equivale a "Mis Eventos / Mi Calendario") | P0 |

### 8.3 Artistas públicos — tag `Artistas`
| Método y ruta | Acceso | Descripción | P |
|---------------|--------|-------------|---|
| `GET /artistas` | Público | Lista paginada; filtro `q` (nombre artístico) | P1 |
| `GET /artistas/{artista_id}` | Público | Perfil: foto, bio, `seguidores`, `eventos_proximos`; `siguiendo` si hay sesión | P0 |

### 8.4 Panel del artista ("Gestor de Transmisión") — tag `Artista (panel)`
> Ojo con el enrutamiento: `/artistas/me/...` debe registrarse **antes** de `/artistas/<uuid:artista_id>`, o usar el conversor `uuid` (que no coincide con `me`).

| Método y ruta | Acceso | Descripción | P |
|---------------|--------|-------------|---|
| `GET /artistas/me/eventos` | ARTISTA | Todos mis eventos (cualquier estado); filtro `estado` | P0 |
| `GET /artistas/me/estadisticas` | ARTISTA | Resumen global (ver §8.4.1) | P0 |
| `GET /artistas/me/eventos/{evento_id}/estadisticas` | ARTISTA (dueño) | Métricas de un evento | P0 |
| `GET /artistas/me/saldo` | ARTISTA | `{disponible_cop, retenido_cop, en_proceso_cop, retirado_cop}` (RN-14) | P0 |
| `POST /artistas/me/retiros` | ARTISTA | `{monto_cop, banco, tipo_cuenta, cuenta_ultimos4}` (RN-15) → `201` | P0 |
| `GET /artistas/me/retiros` | ARTISTA | Historial paginado | P0 |

#### 8.4.1 Forma de `GET /artistas/me/estadisticas`
```json
{
  "resumen": {
    "eventos_por_estado": {"BORRADOR": 1, "PROGRAMADO": 2, "EN_VIVO": 0, "FINALIZADO": 4, "CANCELADO": 0},
    "entradas_vendidas": 212,
    "ingresos_brutos_cop": 4240000,
    "comision_plataforma_cop": 424000,
    "ingresos_netos_cop": 3816000,
    "seguidores": 87,
    "pico_espectadores_promedio": 41.5
  },
  "saldo": {"disponible_cop": 2900000, "retenido_cop": 400000, "en_proceso_cop": 300000, "retirado_cop": 216000},
  "ventas_por_dia": [{"fecha": "2026-11-01", "entradas": 12, "ingresos_netos_cop": 216000}]
}
```
Las estadísticas por evento incluyen: `entradas_vendidas`, ingresos brutos/netos, `pico_espectadores`, `vistas_vivo`, `vistas_replay`, `duracion_real_min`, `favoritos` (conteo) y `ventas_por_dia`. Solo cuentan compras `APROBADA`.

### 8.5 Eventos — tag `Eventos`
| Método y ruta | Acceso | Descripción | P |
|---------------|--------|-------------|---|
| `GET /eventos` | Público | Catálogo (RN-21). Filtros: `q`, `categoria`, `estado`, `artista_id`, `desde`, `hasta`; orden por `fecha_inicio`; paginado | P0 |
| `GET /eventos/{evento_id}` | Público | Detalle. Si hay sesión: `es_favorito`, `comprado`. Los `BORRADOR` solo los ve su dueño | P0 |
| `POST /eventos` | ARTISTA | Crea en `BORRADOR` (RN-04) → `201` | P0 |
| `PATCH /eventos/{evento_id}` | ARTISTA (dueño) | Edita campos permitidos según estado (RN-04) | P0 |
| `DELETE /eventos/{evento_id}` | ARTISTA (dueño) | Solo `BORRADOR` → `204` | P1 |
| `POST /eventos/{evento_id}/publicar` | ARTISTA (dueño) | `BORRADOR → PROGRAMADO` | P0 |
| `POST /eventos/{evento_id}/cancelar` | ARTISTA (dueño) | RN-13 | P1 |

### 8.6 Transmisión y reproducción (simuladas) — tag `Transmision`
| Método y ruta | Acceso | Descripción | P |
|---------------|--------|-------------|---|
| `POST /eventos/{evento_id}/transmision/credenciales` | ARTISTA (dueño) | Genera/rota la clave. Devuelve `{rtmp_url, stream_key}`; **la clave solo se muestra aquí, una vez** | P0 |
| `POST /eventos/{evento_id}/transmision/iniciar` | ARTISTA (dueño) | `{stream_key}` → valida (RQ6), `PROGRAMADO → EN_VIVO` | P0 |
| `POST /eventos/{evento_id}/transmision/finalizar` | ARTISTA (dueño) | `EN_VIVO → FINALIZADO` (RN-12) | P0 |
| `GET /eventos/{evento_id}/transmision` | ARTISTA (dueño) | Estado: `estado`, `espectadores_actuales`, `pico_espectadores`, `duracion_transcurrida_min` | P0 |
| `POST /eventos/{evento_id}/reproduccion/entrar` | USUARIO, ARTISTA, ADMIN | Verifica acceso (RN-10). Devuelve `{tipo: VIVO\|REPLAY, url, token, expira_en}`; incrementa contadores | P0 |
| `POST /eventos/{evento_id}/reproduccion/salir` | USUARIO, ARTISTA, ADMIN | Decrementa `espectadores_actuales` (nunca < 0) → `204` | P1 |

**Solo desarrollo** (`ENABLE_DEV_ENDPOINTS=true`, responde `404` si no): `POST /dev/eventos/{evento_id}/audiencia-simulada` `{espectadores:int}` (dueño o ADMIN) — fija contadores para poblar estadísticas en demos. **P2**

### 8.7 Compras y pagos (Wompi simulado) — tag `Compras`
| Método y ruta | Acceso | Descripción | P |
|---------------|--------|-------------|---|
| `POST /eventos/{evento_id}/compras` | USUARIO, ARTISTA | `{metodo_pago}` → crea compra `PENDIENTE` y devuelve el **checkout simulado** (§10) → `201` | P0 |
| `GET /compras/{compra_id}` | Dueño o ADMIN | Detalle y estado (el frontend la consulta hasta que cambie de `PENDIENTE`) | P0 |
| `POST /pagos/wompi/simulador/{referencia}` | Dueño de la compra | `{resultado: APPROVED\|DECLINED\|VOIDED\|ERROR}`. Simula que el usuario terminó el checkout; **internamente genera el evento firmado y lo procesa por el mismo camino que el webhook**. Solo si `PAYMENT_GATEWAY=wompi_simulado` | P0 |
| `POST /webhooks/wompi` | Público (firma) | Recibe `transaction.updated`. Verifica checksum, idempotente, siempre `200` salvo firma inválida (`401`) | P0 |

### 8.8 Administración — tag `Admin` (todo requiere `ADMIN`)
| Método y ruta | Descripción | P |
|---------------|-------------|---|
| `GET /admin/dashboard` | Usuarios por rol, eventos por estado, entradas vendidas, ingresos brutos, comisiones acumuladas, retiros pendientes (cantidad y monto) | P0 |
| `GET /admin/usuarios` | Lista; filtros `rol`, `estado`, `q` | P0 |
| `GET /admin/usuarios/{usuario_id}` | Detalle | P1 |
| `PATCH /admin/usuarios/{usuario_id}/estado` | `{estado: ACTIVO\|SUSPENDIDO}` (RN-20) | P0 |
| `PATCH /admin/usuarios/{usuario_id}/rol` | `{rol}` (RN-20) | P2 |
| `GET /admin/eventos` | Todos los eventos y estados; filtros | P0 |
| `POST /admin/eventos/{evento_id}/suspender` | `{motivo}` → `suspendido_admin=true`; corta venta y reproducción | P0 |
| `POST /admin/eventos/{evento_id}/reactivar` | Revierte la suspensión | P0 |
| `GET /admin/compras` | Filtros `estado`, `evento_id`, `usuario_id`, rango de fechas | P1 |
| `GET /admin/retiros` | Filtro `estado` | P0 |
| `POST /admin/retiros/{retiro_id}/aprobar` | `PENDIENTE → PAGADO` (payout simulado) | P0 |
| `POST /admin/retiros/{retiro_id}/rechazar` | `{motivo}` obligatorio | P0 |
| `GET /admin/configuracion` | Solo lectura: comisión, mínimos, flags (sin secretos) | P2 |

---

## 9. Reglas de negocio (fuente de verdad)

Cada regla debe tener **al menos una prueba** (ver §15). Si cambias una regla, cambia este documento en el mismo commit.

**Cuentas**
- **RN-01 Registro.** Solo `USUARIO` o `ARTISTA`. `acepta_tratamiento_datos` debe ser `true` o se rechaza (`422`); se guardan `consentimiento_datos_en` y `version_politica_datos` (RQ7, protección de datos personales, Ley 1581 de 2012). Las cuentas `ADMIN` se crean exclusivamente mediante inserción directa en PostgreSQL, con contraseña almacenada como hash Werkzeug; no hay vía de creación por API, CLI ni seed. El seed requiere un ADMIN activo preexistente.
- **RN-02 Alta de artista sin verificación manual (C1).** No hay aprobación humana. Controles mínimos: correo único, política de contraseña, *rate limit*, y un artista **sin ningún evento `FINALIZADO`** puede tener como máximo `MAX_EVENTOS_PROGRAMADOS_NUEVO` (3) eventos `PROGRAMADO` a la vez.
- **RN-03 Contraseña.** Mínimo 8 caracteres, con al menos una letra y un dígito. Máximo 128. Login con mensaje genérico (`CREDENCIALES_INVALIDAS`) para no revelar si el correo existe.

**Eventos**
- **RN-04 Creación y edición.** `precio_cop` entre `PRECIO_MIN_COP` (5.000) y `PRECIO_MAX_COP` (500.000). `fecha_inicio` debe ser ≥ ahora + 30 min al **publicar**. Crear requiere `youtube_url`, que debe ser un enlace HTTPS válido a un video promocional público de YouTube (no la transmisión completa). En `BORRADOR` se edita todo; en `PROGRAMADO` se edita `titulo`, `descripcion`, `portada_url`, `youtube_url`, `categoria`; `precio_cop`, `replay_habilitado` y `fecha_inicio` solo si el evento **no tiene compras `APROBADA`**. `EN_VIVO`/`FINALIZADO`/`CANCELADO` no se editan. **Cero cargos al artista por crear o publicar (C2/K1 = 0 COP).**
- **RN-05 Transiciones y emisión.** Solo las del diagrama §6, ejecutadas por el servicio con la fila bloqueada (`FOR UPDATE`). Un artista no puede tener dos eventos `EN_VIVO` a la vez. `iniciar` solo desde `fecha_inicio − START_WINDOW_MIN` (30 min) si `ENFORCE_START_WINDOW=true`.
- **RN-04 (precisiones de implementación).** `duracion_estimada_min` se trata como `precio_cop`/`fecha_inicio`/`replay_habilitado`: en `PROGRAMADO` solo se edita sin compras `APROBADA`. Editar `fecha_inicio` en `PROGRAMADO` exige también ≥ ahora + 30 min. Editar un campo restringido → `409 EVENTO_NO_EDITABLE`; fecha inválida → `422 FECHA_INICIO_INVALIDA`; precio fuera de rango → `422 MONTO_FUERA_DE_RANGO`.
- **RN-02 (precisión).** Superar el tope al publicar → `422 LIMITE_EVENTOS_PROGRAMADOS`. Se serializa bloqueando la fila del artista (`FOR UPDATE` sobre `usuarios`).
- **Detalle de evento.** Lo no visible según RN-21 (borrador, cancelado, suspendido, finalizado sin replay, artista suspendido) solo lo ve su dueño; para los demás es `404 EVENTO_NO_ENCONTRADO`. `es_favorito` y `comprado` se omiten sin sesión.
- **RN-21 Visibilidad del catálogo público.** Eventos `PROGRAMADO`, `EN_VIVO`, y `FINALIZADO` con `replay_disponible`; con `suspendido_admin=false` y artista `ACTIVO`. `BORRADOR` solo lo ve su dueño. `CANCELADO` no aparece.

**Compras y dinero**
- **RN-06 Comisión (C2).** `comision_cop = round_half_up(monto_cop × PLATFORM_COMMISSION_PCT / 100)` con `Decimal`; `neto_artista_cop = monto_cop − comision_cop`. El monto y el % se **congelan** en la compra. No existe cuota fija, suscripción ni cobro adicional al artista.
- **RN-07 Quién puede comprar.** Rol `USUARIO` o `ARTISTA`; no el dueño del evento; evento `PROGRAMADO` o `EN_VIVO` (o `FINALIZADO` con `replay_disponible`); no suspendido; sin otra compra `PENDIENTE`/`APROBADA` del mismo evento (`409 COMPRA_DUPLICADA`). Antes de validar, se anulan las `PENDIENTE` vencidas del usuario para ese evento.
- **RN-07 (precisiones de implementación).** Orden de rechazo al comprar: evento inexistente → `404 EVENTO_NO_ENCONTRADO`; es el dueño → `403 COMPRA_PROPIO_EVENTO`; `BORRADOR` o artista suspendido → `404` (no existen para el público); `suspendido_admin` → `403 EVENTO_SUSPENDIDO`; `CANCELADO` o `FINALIZADO` sin replay → `409 EVENTO_NO_DISPONIBLE`. `ADMIN` recibe `403 ROL_NO_PERMITIDO`. La lectura del evento toma `FOR SHARE` para serializarse con `cancelar` y con la edición de precio. La respuesta de `POST /eventos/{id}/compras` es `{compra, checkout}`; `compra` no expone comisión ni neto (son del artista).
- **RN-08 Acceso automático (C3).** Compra `APROBADA` ⇒ acceso inmediato. Ningún paso manual ni de administrador.
- **RN-09 Procesamiento del pago.** Un único método (`PagosService.procesar_evento_wompi`) sirve al webhook y al simulador. Debe: verificar firma; localizar la compra por `referencia` (si no existe → registrar y responder `200`); comparar `amount_in_cents == monto_cop × 100`; bloquear la fila de la compra; ser **idempotente** (mismo estado → no hace nada); no mover estados terminales; mapear `APPROVED→APROBADA`, `DECLINED/ERROR→RECHAZADA`, `VOIDED→ANULADA`.
- **RN-09 (precisiones de implementación).** Solo una compra `PENDIENTE` cambia de estado; el resto es idempotente o terminal. Moneda distinta de `COP` o monto distinto → se ignora con `200`. El checksum debe cubrir al menos `transaction.id`, `transaction.status` y `transaction.amount_in_cents`; un `event` distinto de `transaction.updated` o un cuerpo sin firma → `401 FIRMA_INVALIDA`. La cabecera `X-Event-Checksum`, si viene, prevalece sobre `signature.checksum`. La expiración (`expira_en`) solo se aplica de forma perezosa al comprar de nuevo (RN-07); un pago `APPROVED` sobre una `PENDIENTE` vencida que nadie ha anulado todavía sí la aprueba.
- **RN-13 Cancelar evento.** Solo `BORRADOR`/`PROGRAMADO`. En la misma transacción: compras `APROBADA → REEMBOLSADA` (reembolso simulado) y `PENDIENTE → ANULADA`.

**Transmisión y reproducción**
- **RN-10 Acceso a reproducción (C4).** Se entrega URL solo si el evento está `EN_VIVO` (tipo `VIVO`) o `FINALIZADO` con `replay_disponible` (tipo `REPLAY`) **y** el solicitante es: comprador con compra `APROBADA`, o el artista dueño, o `ADMIN`. Evento suspendido → `403 EVENTO_SUSPENDIDO`. Sin acceso → `403 ACCESO_NO_AUTORIZADO`.
- **RN-11 Clave de emisión (RQ6).** Se genera aleatoria (`secrets.token_urlsafe(32)`), se guarda **solo su hash SHA-256**, se muestra una única vez, se compara con `hmac.compare_digest`. Regenerarla invalida la anterior. Clave incorrecta → `401 CLAVE_EMISION_INVALIDA`.
- **RN-05 (precisiones de implementación).** `iniciar` comprueba, en este orden: dueño (`403 NO_ES_DUENO`), estado `PROGRAMADO` (`409 TRANSICION_INVALIDA`), que el evento no esté suspendido por el admin (`403 EVENTO_SUSPENDIDO`), clave (`401 CLAVE_EMISION_INVALIDA`), ventana (`409 VENTANA_DE_INICIO_NO_ABIERTA`, solo con `ENFORCE_START_WINDOW=true`; en producción el valor por defecto es `true`) y que el artista no tenga otro evento `EN_VIVO` (`409 YA_HAY_EVENTO_EN_VIVO`). Se serializa bloqueando primero la fila del artista y luego la del evento. `credenciales` solo con el evento `PROGRAMADO` (si no, `409 TRANSICION_INVALIDA`) y responde `200`.
- **RN-10 (precisiones de implementación).** Orden en `entrar`: evento inexistente → `404`; `suspendido_admin` → `403 EVENTO_SUSPENDIDO` (también para el dueño y el ADMIN); estado sin vivo ni replay o sin derecho de acceso → `403 ACCESO_NO_AUTORIZADO`. El derecho es: dueño, ADMIN o compra `APROBADA` (`ComprasService.tiene_acceso`). `salir` exige el mismo derecho, pero no depende del estado del evento ni de la suspensión.
- **Contadores (precisiones).** `entrar` en vivo suma `espectadores_actuales` y `vistas_vivo` y sube `pico_espectadores` en un único `UPDATE` con `GREATEST`; el `UPDATE` vuelve a exigir el estado (`EN_VIVO`, o `FINALIZADO` con replay) para que una entrada que llega tras finalizar no cuente. Una entrada en `REPLAY` solo suma `vistas_replay` (no toca `espectadores_actuales` ni el pico, que miden la audiencia en vivo). `salir` usa `GREATEST(espectadores_actuales - 1, 0)`. Sin tabla de sesiones de espectador, un comprador puede llamar a `salir` más veces que a `entrar`: el contador nunca baja de 0, pero no se distingue quién salió.
- **RN-12 Finalizar.** Fija `fin_real`, `espectadores_actuales = 0`, `replay_disponible = replay_habilitado`.
- **Contadores de audiencia.** `entrar` incrementa `espectadores_actuales` y `vistas_vivo`/`vistas_replay`, y actualiza `pico_espectadores = GREATEST(pico, actuales)`, todo con `UPDATE` atómico (sin leer-modificar-escribir en Python).

**Retiros**
- **RN-14 Saldo (derivado).**
  - `ingresos_liberados` = Σ `neto_artista_cop` de compras `APROBADA` de eventos **`FINALIZADO`** del artista.
  - `retenido` = Σ `neto_artista_cop` de compras `APROBADA` de eventos `PROGRAMADO`/`EN_VIVO` (el dinero se libera cuando el evento termina).
  - `en_proceso` = Σ retiros `PENDIENTE`; `retirado` = Σ retiros `PAGADO`.
  - **`disponible = ingresos_liberados − en_proceso − retirado`.**
- **RN-15 Solicitar retiro.** `monto_cop ≥ RETIRO_MIN_COP` (50.000) y `≤ disponible`. Se bloquea la fila del artista (`SELECT ... FOR UPDATE` sobre `usuarios`) mientras se calcula y se inserta, para impedir doble retiro concurrente. Solo se guardan `banco`, `tipo_cuenta` y `cuenta_ultimos4`.
- **RN-14/RN-15 (precisiones de implementación).** Todo el panel se calcula con agregaciones SQL (`SUM … FILTER`, `COUNT`, `AVG`), nunca trayendo filas a Python, y solo cuentan compras `APROBADA`. `retenido`, `liberado` y `ventas_por_dia` agrupan por el estado actual del evento. `ventas_por_dia` usa la fecha en **America/Bogota** (UTC−5, sin horario de verano) de `aprobado_en`, solo con los días que tuvieron ventas, de la más antigua a la más reciente. `pico_espectadores_promedio` es el único decimal (1 decimal) y promedia solo eventos con `inicio_real` (transmitidos); sin ninguno vale `0.0`. `duracion_real_min` es `null` si el evento no tiene `inicio_real` y `fin_real`. Hasta la Fase 6, `seguidores` y `favoritos` valen `0`. Un evento ajeno responde `403 NO_ES_DUENO` en todos los endpoints del panel. En `POST /artistas/me/retiros`, un monto menor que `RETIRO_MIN_COP` → `422 MONTO_FUERA_DE_RANGO` (se valida primero) y mayor que el disponible → `422 SALDO_INSUFICIENTE`. `cuenta_ultimos4` debe ser un texto de exactamente 4 dígitos `0-9` (también hay un `CHECK` en la BD); cualquier otro campo, como un número de cuenta completo, se rechaza (`unknown = RAISE`). Con `RETIRO_AUTO_APROBAR=true` el retiro nace `PAGADO` con `referencia_pago = sim-payout-<uuid>`, `resuelto_en = ahora` y `resuelto_por = NULL` (lo resolvió el sistema).
- **RN-16 Resolver retiro (admin).** Aprobar → `PAGADO` con `referencia_pago` simulada. Rechazar exige `motivo`; al quedar `RECHAZADO` deja de restar del saldo. Un retiro ya resuelto no se vuelve a resolver (`409`).

**Social y perfil**
- **RN-17 Favoritos y seguidos.** Favoritos: solo eventos visibles (RN-21). Seguir: solo `rol=ARTISTA` y `ACTIVO`; no a uno mismo (`422`). Todas las operaciones son idempotentes.
- **RN-17 (precisiones de implementación).** Las 6 entidades del modelo ya existen. Favoritos: solo eventos visibles según RN-21, **también para el dueño** (su propio borrador → `404 EVENTO_NO_ENCONTRADO`); el listado excluye los que dejaron de ser visibles (la fila se conserva, y reaparece si vuelve a serlo) y devuelve el resumen del evento. Seguir: `NO_SEGUIRSE_A_SI_MISMO` (422) se comprueba antes que la existencia del artista; un usuario inexistente, que no es `ARTISTA` o está suspendido → `404 USUARIO_NO_ENCONTRADO`. Dejar de seguir o quitar un favorito nunca falla (idempotente), tampoco con un artista suspendido. Los `PUT` usan `INSERT … ON CONFLICT DO NOTHING`. El listado de seguidos solo trae `id`, `nombre_artistico`, `foto_url` y `biografia` de artistas `ACTIVO`. `ADMIN` recibe `403` en favoritos, seguidos y «mis entradas».
- **Perfil público de artista (§8.3).** `GET /artistas` y `GET /artistas/{id}` son públicos y solo muestran artistas `ACTIVO` (suspendido, inexistente o que no es `ARTISTA` → `404 USUARIO_NO_ENCONTRADO`). `seguidores` es un `COUNT(*)` (nunca la lista); `eventos_proximos` son los eventos visibles `PROGRAMADO`/`EN_VIVO` por `fecha_inicio` (hasta 20); `siguiendo` solo aparece si la petición trae sesión. `GET /usuarios/me/compras` vive en el módulo `compras` (tag `Usuarios`); no filtra por visibilidad (una entrada comprada se sigue viendo aunque el evento se suspenda) y su monto es el precio pagado, sin comisión ni neto.
- **RN-18 Foto de perfil.** JPEG/PNG/WebP, ≤ `MAX_UPLOAD_MB` (2 MB). Se valida abriendo con Pillow (no confiar en la extensión ni en `Content-Type`), se corrige la orientación EXIF, se **elimina el EXIF**, se recorta a cuadrado y se re-codifica a WebP 512×512. Se guarda como `uploads/perfiles/<usuario_id>/<uuid>.webp` y se borra la foto anterior. Se sirve por una ruta estática de la API.

**Administración**
- **RN-19 Suspensión de usuario.** Un usuario `SUSPENDIDO` recibe `403 CUENTA_SUSPENDIDA` en todo endpoint autenticado (se verifica en cada petición) y sus eventos salen del catálogo.
- **RN-19 / RN-20 (precisiones de implementación).** Todo `/admin/*` exige `ADMIN` y se verifica contra la BD en cada petición. Suspender a un usuario es idempotente y no revoca tokens: `requiere_roles` y el login/refresh lo bloquean (`403 CUENTA_SUSPENDIDA`) y reactivarlo restablece el acceso con el mismo token. Un admin que intenta suspenderse o cambiar su propio rol a `USUARIO`/`ARTISTA` recibe `403 ADMIN_NO_PUEDE_AUTOMODIFICARSE`; el esquema de cambio de rol no acepta `ADMIN`. Antes de suspender o degradar a un admin se bloquean (`SELECT … FOR UPDATE`, en orden de `id`) todas las filas de admins activos y se cuentan: si no quedaría ninguno, `409 ULTIMO_ADMIN_ACTIVO`. Así dos admins que se suspenden entre sí a la vez no pueden vaciar el sistema: gana uno y el otro recibe 409.
- **Administración (precisiones de implementación).** Un `ADMIN` solo se crea mediante inserción directa en PostgreSQL con contraseña en hash Werkzeug; no se puede crear por API, CLI ni seed. El cambio de rol (P2) solo permite `USUARIO`/`ARTISTA`: pasar a `ARTISTA` exige `nombre_artistico` (único); pasar de `ARTISTA` a `USUARIO` da `409 ROL_NO_CAMBIABLE` si tiene eventos `PROGRAMADO`/`EN_VIVO` o retiros `PENDIENTE` (el `nombre_artistico` anterior se conserva). Suspender un evento exige `motivo` (1–255, no vacío), pone `suspendido_admin = true` y **no** finaliza ni cancela nada; suspender uno ya suspendido o reactivar uno que no lo está → `409 TRANSICION_INVALIDA`. Resolver un retiro (RN-16) bloquea la fila: aprobar → `PAGADO` con `sim-payout-<uuid>`, `resuelto_por` y `resuelto_en`; rechazar exige `motivo`; ya resuelto → `409 RETIRO_YA_RESUELTO`. `GET /admin/compras` muestra al comprador solo por nombre (sin correo). `GET /admin/configuracion` usa una lista explícita de valores públicos: nunca secretos, credenciales ni URLs de base de datos. El módulo `admin` no tiene modelo; solo orquesta servicios y tiene un repositorio de solo lectura para el dashboard. No hay tabla de auditoría en este sprint.
- **RN-20 Salvaguardas de admin.** Un admin no puede suspenderse ni cambiar su propio rol, y nunca puede quedar el sistema sin al menos un admin `ACTIVO`.

---

## 10. Pagos: Wompi simulado

Objetivo: que cambiar a Wompi sandbox/real sea **reemplazar un adaptador**, no reescribir el flujo. Se imita la forma real de Wompi (verifica los detalles contra https://docs.wompi.co antes de pasar a real).

### 10.1 Puerto (`infrastructure/payments/base.py`)
```python
class PaymentGateway(ABC):
    def crear_checkout(self, compra) -> CheckoutDTO: ...
    def verificar_evento(
        self, payload: dict, checksum_header: str | None
    ) -> TransaccionDTO: ...  # lanza FirmaInvalida
    def construir_evento_simulado(self, compra, resultado: str) -> dict: ...  # solo el simulador
```
`PAYMENT_GATEWAY=wompi_simulado` → `WompiSimuladoGateway`. Futuro: `WompiGateway` (sandbox/real). Los servicios dependen **solo** de `PaymentGateway`.

### 10.2 Flujo
```
1. Front → POST /eventos/{id}/compras {metodo_pago}
   Back crea Compra PENDIENTE (monto congelado, referencia, expira_en = ahora + 30 min)
   Back ← checkout = { referencia, monto_en_centavos, moneda: "COP", firma_integridad, checkout_url (simulada), expira_en }
2. (Real) El usuario paga en Wompi · (Simulado) Front → POST /pagos/wompi/simulador/{referencia} {resultado}
3. Gateway arma un evento "transaction.updated" firmado y se procesa con PagosService.procesar_evento_wompi()
   (idéntico al camino de POST /webhooks/wompi)
4. Compra → APROBADA | RECHAZADA | ANULADA  ⇒  si APROBADA, el acceso ya existe (C3)
5. Front consulta GET /compras/{id} hasta que deje de estar PENDIENTE
```

### 10.3 Firmas (imitando Wompi)
- **Firma de integridad** del checkout: `SHA256(referencia + monto_en_centavos + moneda + WOMPI_INTEGRITY_SECRET)` en hexadecimal.
- **Evento/webhook**:
  ```json
  {
    "event": "transaction.updated",
    "data": {"transaction": {"id": "sim-…", "reference": "…", "status": "APPROVED",
                              "amount_in_cents": 2000000, "currency": "COP", "payment_method_type": "NEQUI"}},
    "environment": "test",
    "signature": {"properties": ["transaction.id", "transaction.status", "transaction.amount_in_cents"],
                  "checksum": "<sha256>"},
    "timestamp": 1730000000,
    "sent_at": "2026-11-02T20:00:00Z"
  }
  ```
  `checksum = SHA256(valor(prop1) + valor(prop2) + valor(prop3) + str(timestamp) + WOMPI_EVENTS_SECRET)`. Comparar con `hmac.compare_digest`.
- Estados Wompi que se contemplan: `APPROVED`, `DECLINED`, `VOIDED`, `ERROR`, `PENDING`. `PENDING` no cambia la compra.
- Los montos que viajan a Wompi van **en centavos** (`monto_cop × 100`).

### 10.4 Reglas
- Nunca confiar en el monto que diga el cliente: el monto sale de la compra guardada.
- El webhook responde `200` rápido y es idempotente (Wompi reintenta).
- No guardar ni registrar en logs datos de tarjeta (en simulación ni existen).
- `WOMPI_*_SECRET` solo desde variables de entorno.

---

## 11. Transmisión simulada

### 11.1 Puerto (`infrastructure/streaming/base.py`)
```python
class StreamingProvider(ABC):
    def generar_credenciales(self, evento) -> Credenciales: ...  # {rtmp_url, stream_key}
    def url_vivo(self, evento, token: str) -> str: ...
    def url_replay(self, evento, token: str) -> str: ...
    def emitir_token_reproduccion(self, evento, usuario, tipo) -> TokenDTO: ...
```
`STREAMING_PROVIDER=simulado` → URLs ficticias:
- Emisión: `rtmp://localhost:1935/live/<evento_id>`
- Vivo: `{MEDIA_BASE_URL}/live/<evento_id>/index.m3u8?token=<jwt>`
- Replay: `{MEDIA_BASE_URL}/replay/<evento_id>/index.m3u8?token=<jwt>`

El **token de reproducción** es un JWT corto (`PLAYBACK_TOKEN_TTL_MIN`, 10 min) firmado con `PLAYBACK_TOKEN_SECRET` (distinto del secreto de sesión) con `evento_id`, `usuario_id` y `tipo`. Imita cómo MediaMTX delegará la autorización al backend.

### 11.2 Camino a la implementación real (anteproyecto)
El anteproyecto validó **MediaMTX** (ingesta RTMP desde OBS, HLS, grabación fMP4, autenticación delegada por HTTP/JWT). Cuando se integre: crear `MediaMtxProvider`, exponer `POST /media/auth` (MediaMTX consulta si una clave puede publicar y si un token puede leer), y reemplazar los contadores simulados por métricas reales. **No cambia ningún servicio de negocio.**

---

## 12. Seguridad

- **JWT:** access 15 min, refresh 7 días. Secretos fuertes desde env; la app **no arranca en `production`** si `SECRET_KEY`, `JWT_SECRET_KEY` o `PLAYBACK_TOKEN_SECRET` conservan el valor del `.env.example` o están vacíos, y en ese entorno fuerza `ENABLE_DEV_ENDPOINTS=false` y `ENFORCE_START_WINDOW=true`. Sin lista de revocación en el MVP (el logout lo hace el cliente descartando tokens; la suspensión sí es inmediata por la verificación en BD).
- **Autorización:** decorador `@requiere_roles(Rol.ARTISTA, ...)` + verificación de propiedad en el servicio (anti-IDOR).
- **Validación:** todo input pasa por un esquema marshmallow con `unknown = RAISE`; longitudes y rangos explícitos.
- **SQL:** solo ORM / `select()` con parámetros. Prohibido construir SQL con f-strings.
- **Rate limiting:** `login` 5/min por IP, `registro` 10/hora por IP, `webhooks/wompi` 120/min. Superarlos devuelve `429 DEMASIADAS_PETICIONES` con `Retry-After`. Los contadores son en memoria y por proceso: con varios workers o réplicas hace falta un almacenamiento compartido (Redis).
- **CORS:** lista blanca `CORS_ORIGINS` (sin `*` fuera de desarrollo: en `production` la app no arranca si la lista lo contiene).
- **Cabeceras:** `X-Content-Type-Options: nosniff` en todas las respuestas y `Cache-Control: no-store` en las autenticadas (con cabecera `Authorization`) y en `/auth/*`, que entrega tokens (`app/common/headers.py`).
- **Secretos y datos sensibles:** nunca en el repositorio ni en logs (contraseñas, tokens, `stream_key`, secretos Wompi). `.env` en `.gitignore`; solo `.env.example`.
- **Archivos:** validación por contenido (Pillow), límite de tamaño (`MAX_CONTENT_LENGTH`), nombre generado por el servidor (UUID), nunca el nombre del cliente.
- **Datos personales:** solo los necesarios (minimización). Consentimiento registrado (RN-01).
- **Logs y errores:** el motor de SQLAlchemy se crea con `hide_parameters=True`, así que ni los logs ni las trazas incluyen los valores enlazados (contraseñas, hashes, números de cuenta). Los errores 500 no devuelven trazas ni el mensaje de la excepción. Las pruebas `test_endurecimiento.py` y `test_e2e_demo.py` verifican que ningún log ni respuesta contenga contraseñas, tokens, `stream_key` ni secretos.
- **Respuestas:** nunca incluir `password_hash`, `stream_key_hash` ni datos de otros usuarios.

---

## 13. Convenciones de código y API

**Idioma:** dominio en **español** (tablas, modelos, campos JSON, rutas, mensajes, códigos de error, docstrings y comentarios); piezas técnicas genéricas en inglés (`Service`, `Repository`, `Gateway`, `create_app`). Ejemplos: `EventoService`, `precio_cop`, `/eventos`. **No mezclar** `price`/`precio` en el mismo concepto.

**Python:** PEP 8 vía `ruff` (línea 100), type hints, funciones cortas, sin lógica en `routes.py`, sin `import *`, sin estado global mutable. Tiempo siempre con `common.clock.ahora_utc()` (nunca `datetime.now()` suelto) para poder fijarlo en pruebas.

**Transacciones:** el **servicio** abre/cierra la transacción (`db.session.commit()` al final del caso de uso, `rollback()` ante excepción). Los repositorios solo hacen `add`/`flush`. Un caso de uso = una transacción.

**Concurrencia:** `with_for_update()` en transiciones de estado, procesamiento de pagos y retiros; contadores con `UPDATE ... SET x = x + 1`.

**Formato de error (único para toda la API):**
```json
{"codigo": "EVENTO_NO_ENCONTRADO", "mensaje": "El evento no existe.", "detalles": {}}
```
Se unifica también el error de validación de smorest (`422`) a esta forma, con `detalles` por campo.

| HTTP | Uso | Ejemplos de `codigo` |
|------|-----|----------------------|
| 400 | Petición mal formada | `PETICION_INVALIDA` |
| 401 | Sin autenticar / credenciales | `NO_AUTENTICADO`, `CREDENCIALES_INVALIDAS`, `CLAVE_EMISION_INVALIDA`, `FIRMA_INVALIDA` |
| 403 | Autenticado sin permiso | `ROL_NO_PERMITIDO`, `NO_ES_DUENO`, `ACCESO_NO_AUTORIZADO`, `CUENTA_SUSPENDIDA`, `EVENTO_SUSPENDIDO`, `COMPRA_PROPIO_EVENTO`, `ADMIN_NO_PUEDE_AUTOMODIFICARSE` |
| 404 | No existe | `EVENTO_NO_ENCONTRADO`, `USUARIO_NO_ENCONTRADO`, `COMPRA_NO_ENCONTRADA`, `RECURSO_NO_ENCONTRADO` (ruta inexistente), `RETIRO_NO_ENCONTRADO` |
| 405 | Método no permitido | `METODO_NO_PERMITIDO` |
| 409 | Conflicto de estado/unicidad | `EMAIL_YA_REGISTRADO`, `NOMBRE_ARTISTICO_EN_USO`, `COMPRA_DUPLICADA`, `EVENTO_NO_DISPONIBLE`, `YA_HAY_EVENTO_EN_VIVO`, `VENTANA_DE_INICIO_NO_ABIERTA`, `TRANSICION_INVALIDA`, `EVENTO_NO_EDITABLE`, `ULTIMO_ADMIN_ACTIVO`, `ROL_NO_CAMBIABLE`, `RETIRO_YA_RESUELTO` |
| 413 | Petición demasiado grande | `ARCHIVO_DEMASIADO_GRANDE` (tope duro de `MAX_CONTENT_LENGTH` = `MAX_UPLOAD_MB` + 1 MB) |
| 422 | Validación / regla de entrada | `VALIDACION`, `CONSENTIMIENTO_REQUERIDO`, `FOTO_INVALIDA`, `FECHA_INICIO_INVALIDA`, `LIMITE_EVENTOS_PROGRAMADOS`, `SALDO_INSUFICIENTE`, `MONTO_FUERA_DE_RANGO`, `NO_SEGUIRSE_A_SI_MISMO` |
| 429 | Rate limit | `DEMASIADAS_PETICIONES` |
| 500 | Error no controlado | `ERROR_INTERNO` (sin trazas al cliente) |
| 503 | Dependencia caída | `BASE_DE_DATOS_NO_DISPONIBLE` (`/health`) |

**Servicios lanzan `AppError`** (subclases: `NoEncontrado`, `Conflicto`, `NoAutorizado`, `Prohibido`, `ReglaDeNegocio`); un manejador global las traduce a HTTP. Los servicios **no** conocen códigos HTTP.

**Paginación:** `?page=1&page_size=20` (máx. 100). Respuesta: `{"items": [...], "page": 1, "page_size": 20, "total": 134}`.

**Fechas:** ISO 8601 con zona (`2026-11-02T20:00:00Z`). La API trabaja en UTC; el frontend convierte a `America/Bogota`.

**Dinero:** enteros `*_cop`. Nunca `float`. Redondeo solo en `common/money.py`.

**Rutas:** sustantivos en plural, `kebab-case` si hay varias palabras, acciones no-CRUD como `POST /{recurso}/{id}/{accion}`. Versionado en el prefijo `/api/v1`.

**Git:** ramas `feature/<módulo>-<descripción>`, commits en *Conventional Commits* (`feat(eventos): publicar evento`), un cambio lógico por commit. Migraciones en el mismo PR que el modelo.

---

## 14. Swagger / OpenAPI

- `flask-smorest` genera la especificación a partir de `Blueprint`, `@blp.arguments`, `@blp.response` y los esquemas.
- Configuración (en `config.py`): `API_TITLE`, `API_VERSION="v1"`, `OPENAPI_VERSION="3.0.3"`, `OPENAPI_URL_PREFIX="/api"`, `OPENAPI_SWAGGER_UI_PATH="/docs"`, `OPENAPI_SWAGGER_UI_URL` (CDN de `swagger-ui-dist`).
- Registrar el esquema de seguridad Bearer una sola vez en `create_app()`:
  ```python
  api.spec.components.security_scheme(
      "bearerAuth", {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
  )
  ```
  y marcar los endpoints protegidos con `@blp.doc(security=[{"bearerAuth": []}])`.
- **Cada endpoint debe tener:** `tag` por módulo (nombres de §8), `summary` en español, `description` con las reglas relevantes (`RN-xx`), esquemas de entrada/salida con `description` y ejemplos en campos clave, y las respuestas de error (`@blp.alt_response(404, schema=ErrorSchema)` en `401/403/404/409/422` aplicables).
- **Definición de terminado de un endpoint:** aparece en `/api/docs`, se puede probar con "Authorize" y su ejemplo funciona sin modificarlo.
- Exportar la especificación a `docs/openapi.json` (`make openapi` = `flask --app wsgi openapi write docs/openapi.json`) y versionarla; el frontend puede generar su cliente con ella. La prueba `test_docs_openapi_json_esta_al_dia_con_la_especificacion_viva` falla si el archivo queda desactualizado, y `test_openapi.py` audita que los 55 endpoints de §8 tengan tag, resumen, descripción con sus RN, seguridad y errores.

---

## 15. Pruebas

- **Herramientas:** `pytest`, `pytest-cov`. Base de datos **PostgreSQL real** (`TEST_DATABASE_URL`), porque se usan índices parciales, `UUID`, `FOR UPDATE` y `ENUM`.
- **Aislamiento:** cada prueba en una transacción con *rollback* (o esquema recreado por sesión + `TRUNCATE` por prueba).
- **Reloj:** fixture que fija `ahora_utc()`.
- **Pirámide:** reglas de negocio en `tests/unit` (servicios), flujos HTTP completos en `tests/integration`.
- **Cobertura mínima:** 80 % en `service.py` y 100 % de las reglas RN-xx críticas marcadas ★.

### Matriz de trazabilidad (pruebas obligatorias)

| Prueba | Valida |
|--------|--------|
| ★ Registro de artista crea la cuenta y permite crear/publicar evento sin intervención de admin | C1, RQ1 |
| ★ Registro sin `acepta_tratamiento_datos` → `422`; con él guarda fecha y versión | RQ7, RN-01 |
| ★ Crear y publicar un evento no genera ningún cargo al artista; no existe endpoint de suscripción | C2, RN-04 |
| ★ Comisión: 20.000 COP al 10 % → comisión 2.000 / neto 18.000; redondeo half-up; `monto = comision + neto` | RN-06 |
| ★ Pago `APPROVED` (simulador) → compra `APROBADA` y `reproduccion/entrar` funciona **sin pasos extra** | C3, RN-08 |
| ★ Pago `DECLINED`/`ERROR` → `RECHAZADA` y no hay acceso; `VOIDED` → `ANULADA` | RN-09 |
| ★ Webhook repetido 2 veces → un solo efecto; webhook tardío no retrocede un estado terminal | RN-09 |
| ★ Webhook con firma inválida → `401`; con monto distinto al de la compra → no aprueba | RN-09 |
| ★ Doble compra del mismo evento (secuencial y concurrente) → `409` | RN-07, índice parcial |
| ★ Replay sin compra → `403 ACCESO_NO_AUTORIZADO`; con compra `APROBADA` → URL con token | C4, RN-10 |
| ★ Evento `FINALIZADO` con `replay_habilitado=false` → `replay_disponible=false` y nadie (ni comprador, ni dueño, ni admin) obtiene replay | RN-10, RN-12 |
| ★ Iniciar con clave inválida → `401`; con la válida → `EN_VIVO`; rotar clave invalida la anterior | RQ6, RN-11 |
| Dos eventos `EN_VIVO` del mismo artista → `409` | RN-05 |
| Cancelar con compras aprobadas → `REEMBOLSADA` y el saldo las excluye | RN-13, RN-14 |
| ★ Saldo: retenido vs disponible según estado del evento; retiro > disponible → `422` | RN-14, RN-15 |
| ★ Dos retiros concurrentes que juntos exceden el saldo → solo uno pasa | RN-15 |
| Admin aprueba/rechaza retiro; rechazar sin motivo → `422`; resolver dos veces → `409` | RN-16 |
| Favoritos y seguir: idempotentes; no seguirse a sí mismo; no seguir a un no-artista | RN-17 |
| Foto: archivo no imagen/corrupto/> 2 MB → `422`; válida → WebP 512×512 sin EXIF; reemplazo borra la anterior | RN-18 |
| Usuario suspendido con token válido → `403 CUENTA_SUSPENDIDA` | RN-19 |
| Admin no puede suspenderse a sí mismo ni dejar el sistema sin admins | RN-20 |
| Cada endpoint protegido: sin token `401`; rol incorrecto `403`; recurso ajeno `403` | §4 |
| `/api/openapi.json` responde `200` y contiene todos los tags | §14 |

---

## 16. Configuración, entorno y comandos

### 16.1 Variables de entorno (`.env.example`)
```ini
APP_ENV=development                 # development | testing | production
SECRET_KEY=cambia-esto
DATABASE_URL=postgresql+psycopg://app:app@localhost:5433/streaming        # el contenedor publica 5433 para no chocar con un PostgreSQL local
TEST_DATABASE_URL=postgresql+psycopg://app:app@localhost:5433/streaming_test
CORS_ORIGINS=http://localhost:5500,http://localhost:3000

JWT_SECRET_KEY=cambia-esto-tambien
JWT_ACCESS_TTL_MIN=15
JWT_REFRESH_TTL_DAYS=7

PLATFORM_COMMISSION_PCT=10
PRECIO_MIN_COP=5000
PRECIO_MAX_COP=500000
RETIRO_MIN_COP=50000
RETIRO_AUTO_APROBAR=false
COMPRA_PENDIENTE_TTL_MIN=30
MAX_EVENTOS_PROGRAMADOS_NUEVO=3
POLITICA_DATOS_VERSION=v1

PAYMENT_GATEWAY=wompi_simulado
WOMPI_INTEGRITY_SECRET=sim-integrity-secret
WOMPI_EVENTS_SECRET=sim-events-secret

STREAMING_PROVIDER=simulado
MEDIA_BASE_URL=http://localhost:8888
RTMP_BASE_URL=rtmp://localhost:1935/live
PLAYBACK_TOKEN_SECRET=cambia-esto-3
PLAYBACK_TOKEN_TTL_MIN=10
ENFORCE_START_WINDOW=false          # true en producción
START_WINDOW_MIN=30

UPLOAD_DIR=uploads
MAX_UPLOAD_MB=2
ENABLE_DEV_ENDPOINTS=true           # false en producción

```

### 16.2 Comandos (Makefile)
```bash
make up          # docker compose up -d db api
make install     # pip install -r requirements-dev.txt
make migrate     # flask db upgrade
make migration m="mensaje"   # flask db migrate -m "mensaje"  (revisar el script generado)
make seed        # flask seed   → admin, 3 artistas, 5 usuarios, 8 eventos en distintos estados, compras, seguidores, favoritos y retiros (idempotente; no corre en production)
make run         # flask --app wsgi run --debug
make test        # pytest --cov=app --cov-report=term-missing
make lint        # ruff check . && ruff format --check .
make openapi     # flask openapi write docs/openapi.json
```
**Primer arranque:** `cp .env.example .env` → `docker compose up -d db` → `make install` → `make migrate` → `make seed` → `make run` → abrir `http://localhost:5000/api/docs`.

**Docker:** imagen `python:3.12-slim`, usuario no root, `gunicorn -w 3 -b 0.0.0.0:8000 wsgi:app`. `docker-compose.yml` con `db` (`postgres:16`, volumen, *healthcheck*) y `api` que depende de `db` saludable.

---

## 17. Orden de implementación y evolución

### 17.1 Fases (cada una termina con pruebas verdes + Swagger actualizado)
| Fase | Contenido | Resultado verificable |
|------|-----------|-----------------------|
| 0 | Esqueleto: factory, config, extensiones, errores, `common/*`, Docker, Alembic, `/health`, Swagger vacío | `/api/docs` carga; `make test` corre |
| 1 | `usuarios` + `auth` (registro, login, refresh, `/me`, `requiere_roles`), foto de perfil | RQ1, RQ7 |
| 2 | `eventos` (CRUD, publicar, cancelar, catálogo público) | RQ2, C1/C2 |
| 3 | `compras` + puerto de pagos + Wompi simulado + webhook | RQ3, C3 |
| 4 | `transmision` (credenciales, iniciar/finalizar, reproducción, contadores) | RQ5, RQ6, C4 |
| 5 | `artistas` (estadísticas, saldo, retiros) | Panel del artista |
| 6 | `social` (favoritos, seguidos) + `GET /usuarios/me/compras` | Funciones del usuario |
| 7 | `admin` (dashboard, usuarios, eventos, retiros, compras) | Panel de administración |
| 8 | Endurecimiento: rate limits, cabeceras, seeds, `docs/openapi.json`, README, revisión de la matriz §15 | Listo para demo |

Si el tiempo aprieta, recorta por prioridad (**P2 → P1**), nunca por capas: mejor menos endpoints completos y probados que muchos a medias.

**Estado del sprint:** las fases 0 a 8 están implementadas. Quedan fuera, por decisión de alcance, `POST /auth/cambiar-password` (P2) y todo lo de §17.2.

### 17.2 Siguientes entidades (fuera de este sprint, en este orden sugerido)
1. `mensajes_chat` — completa RQ4 (WebSocket/SSE; idealmente Redis para fan-out).
2. `refresh_tokens` — revocación real y logout.
3. `auditoria` — quién hizo qué en el panel admin.
4. `configuracion` — comisión y límites editables desde el panel.
5. `grabaciones` — metadatos del replay cuando se integre MediaMTX.

Integraciones: `MediaMtxProvider` (§11.2), `WompiGateway` real (sandbox → producción), confirmación de correo y recuperación de contraseña, almacenamiento de fotos en S3-compatible (basta un nuevo adaptador de `FileStorage`).

---

## 18. Reglas para agentes de IA

**Haz siempre**
1. Lee §5, §7 y §9 antes de tocar código. Las reglas RN-xx mandan sobre tu criterio.
2. Para cada funcionalidad nueva, en este orden: **modelo → migración → repositorio → servicio → esquemas (DTOs) → ruta → pruebas → documentación Swagger**.
3. Respeta el flujo `routes → service → repository`. Si dudas de dónde va algo: lógica de negocio = servicio; consulta = repositorio; forma de datos HTTP = esquema.
4. Usa transacciones en el servicio y bloqueos (`FOR UPDATE`) en todo lo que mueva dinero o cambie estados.
5. Usa `AppError` con `codigo` del catálogo de §13; si hace falta uno nuevo, agrégalo a la tabla.
6. Escribe la prueba que demuestra la regla antes de dar la tarea por terminada, y corre `make lint test`.
7. Revisa el SQL generado por las migraciones de Alembic; renombrar o borrar columnas requiere migración manual.
8. Mantén este archivo actualizado cuando cambie una regla, endpoint o entidad.

**Nunca hagas**
- ❌ Crear una **séptima tabla** o columnas que dupliquen datos derivados (saldo, acceso, conteos).
- ❌ Poner lógica de negocio en `routes.py` o `models.py`; usar `request`/esquemas dentro de servicios.
- ❌ Hacer `commit` en repositorios.
- ❌ Usar `float` para dinero, ni calcular la comisión fuera de `common/money.py`.
- ❌ Confiar en el monto, el rol o el `usuario_id` que llegue en el cuerpo de la petición.
- ❌ Devolver modelos ORM crudos, `password_hash`, `stream_key_hash` o secretos.
- ❌ Llamar a Wompi o al servidor de medios directamente desde un servicio: siempre por `PaymentGateway` / `StreamingProvider`.
- ❌ Implementar cuotas, suscripciones o cualquier cargo fijo al artista (rompe C2).
- ❌ Dar acceso a reproducción sin comprobar compra `APROBADA` (rompe C4).
- ❌ Registrar en logs contraseñas, tokens, claves de emisión o secretos.
- ❌ Usar SQLite en pruebas ni construir SQL con f-strings.
- ❌ Ampliar el alcance (chat, correo, dinero real) sin que el usuario lo pida.

**Si algo es ambiguo:** elige la opción más simple que respete C1–C4 y los límites del MVP, anótala como supuesto al final de tu respuesta y sigue; no bloquees la entrega por una duda menor.

**Definición de terminado (DoD) de una tarea:** código en su capa correcta · migración incluida · pruebas verdes con la regla RN correspondiente · endpoint visible y probable en Swagger con errores documentados · `ruff` sin avisos · AGENTS.md actualizado si cambió el contrato.
