# Converge — Enterprise/Escalado (Plan Ejecutable 8 Semanas)

## Objetivo de etapa
Llevar Converge de "MVP robusto" a "plataforma enterprise operable": persistencia concurrente, servidor de produccion, observabilidad, integracion bidireccional con GitHub, y escala validada bajo carga realista.

## Invariantes (no negociables)
- El motor mantiene las 3 invariantes de convergencia.
- Todo write produce eventos auditables con trace_id correlacionado.
- Cualquier cambio de diseno reduce o mantiene entropia estructural.
- API externa siempre sobre servicios internos (separacion de concerns).
- Observabilidad desde el dia 1: si no se puede medir, no se puede operar.

## Decisiones arquitectonicas

### Persistencia sin ORM
Los ports de almacenamiento usan SQL raw (sqlite3, psycopg) — sin SQLAlchemy.
Migraciones via scripts SQL versionados (no Alembic).
Razon: el modelo de datos es append-only y simple (7 tablas). Un ORM agrega complejidad sin retorno en este dominio.

### Ports sincronos, servidor async
Los ports se definen con interfaz **sincrona**. Razon: la logica de negocio (engine, risk, policy) es CPU-bound y sincrona; forzar async contamina todo el stack sin beneficio.
- `SqliteStore`: implementacion directa (sqlite3 es sync).
- `PostgresStore`: usa psycopg 3 (sync mode) o psycopg_pool para connection pooling.
- FastAPI ejecuta handlers sync en threadpool (comportamiento por defecto de FastAPI con `def` handlers).
- El worker corre en su propio proceso; no comparte event loop con el servidor.
- Unica excepcion async: llamadas HTTP salientes a GitHub API (httpx.AsyncClient), aisladas en el modulo de integracion.

## Estado actual al inicio de etapa
- 20 modulos, 5,158 LOC, 194 tests (169 unit + 25 integration).
- Seguridad: API keys con SHA-256, tenant isolation en GETs, webhook HMAC en produccion.
- trace_id end-to-end en todo el flujo de validacion.
- Persistencia: SQLite con WAL, 7 tablas, SQL embebido en event_log.py.
- Servidor: http.server stdlib, sincrono, single-process.
- Cola: process_queue sincrono invocado desde CLI, advisory lock SQLite.
- Gobernanza: analytics (arqueologia, calibracion, export), projections (health, trends, predictions, compliance) implementados y testeados.

---

## Semana 1 — Ports de almacenamiento + adapter SQLite

**Objetivo:** Abstraer la persistencia sin cambiar comportamiento.

**Entregables:**
- Interfaces (Protocol classes, sincronas):
  - `EventStorePort`: append, query, count, prune_events.
  - `IntentStorePort`: upsert, get, list, update_status.
  - `PolicyStorePort`: risk policies, agent policies, compliance thresholds (CRUD + list).
  - `LockPort`: acquire, release, force_release, get_info.
  - `DeliveryPort`: is_duplicate, record.
- Adapter `SqliteStore` implementando todos los ports.
  - Extraer SQL de event_log.py a metodos del adapter.
  - event_log.py se convierte en fachada que delega al adapter.
- engine.py, server.py, projections/ reciben store via inyeccion (parametro o factory).
- Tests parametrizados con fixture `store` que retorna `SqliteStore`.

**Criterio de salida:**
- 194 tests pasan sin cambio funcional.
- Ningun modulo importa sqlite3 directamente excepto el adapter.
- Tests listos para recibir un segundo adapter (parametrize por backend).

**Tests nuevos:** >= 10 (ports contract tests).
**Tests acumulados:** >= 204.

---

## Semana 2 — Servidor ASGI + observabilidad + Dockerfile

**Objetivo:** Servidor de produccion con metricas desde el primer dia.

**Entregables:**
- Reemplazo de `http.server` por FastAPI/uvicorn.
  - Routers por dominio: `intents`, `queue`, `risk`, `agents`, `compliance`, `webhooks`.
  - Handlers sync (`def`, no `async def`) — FastAPI los ejecuta en threadpool.
  - Migrar endpoints existentes manteniendo contratos.
  - Health checks: `/health/ready` (DB accesible) y `/health/live` (proceso vivo).
- API versionada: prefijo `/v1/` en todos los endpoints.
- Observabilidad base:
  - Structured logging (JSON) con correlation via trace_id.
  - OpenTelemetry SDK: spans en validate_intent, process_queue, simulate.
  - Endpoint `/metrics` Prometheus (latencia, throughput, error rate).
- Dockerfile multi-stage basico (uvicorn).
- Baseline de rendimiento: medir throughput y latencia P95 con adapter SQLite.
  Registrar como referencia para KPIs de S8.
- CLI: se mantiene como acceso directo a la logica (ops/debug). No llama a la API.
  Documentar que para multi-nodo, el CLI es local al nodo; operaciones cross-nodo van por API.
- Rollback: server.py original se preserva como `server_legacy.py` hasta S4.
  Si FastAPI falla en produccion, se puede revertir con un cambio de entrypoint.

**Criterio de salida:**
- Tests de contrato: mismas respuestas HTTP que el server anterior.
- `docker build && docker run` levanta el server funcional.
- Logs en JSON, spans visibles en consola OTLP.
- Baseline de throughput registrado.

**Tests nuevos:** >= 15 (router tests + health checks + contract parity).
**Tests acumulados:** >= 219.

---

## Semana 3 — Persistencia enterprise: Postgres

**Objetivo:** Adapter Postgres funcional en paralelo a SQLite.

**Entregables:**
- Adapter `PostgresStore` implementando los mismos ports (psycopg 3, sync, SQL raw).
- Connection pooling via `psycopg_pool.ConnectionPool`.
- Migraciones SQL versionadas (directorio `migrations/`, scripts numerados).
  - Script de rollback por cada migracion.
- Estrategia de backfill SQLite -> Postgres (script + validacion de paridad).
- Queue lock migrado a `pg_advisory_lock` o `SELECT FOR UPDATE SKIP LOCKED`.
- Dual-run en CI: tests parametrizados corren contra SQLite y Postgres.
- Docker Compose para desarrollo local: Converge + Postgres + OTLP collector.
- Selector de backend via variable de entorno (`CONVERGE_DB_BACKEND=postgres|sqlite`).

**Criterio de salida:**
- Parity tests verdes en ambos backends.
- Documento de rollback escrito y probado (Postgres -> SQLite).
- `docker compose up` levanta stack completo.

**Tests nuevos:** >= 10 (Postgres-specific: connection pool, migration up/down, backfill verification).
**Tests acumulados:** >= 229.

---

## Semana 4 — Hardening API, seguridad y resiliencia

**Objetivo:** Cerrar superficie de riesgo y proteger ante fallos.

**Entregables:**
- Seguridad:
  - Scopes finos en API keys: `read`, `write`, `admin` por recurso (intents, policies, queue).
  - Rotacion de claves: endpoint de rotacion + periodo de gracia para clave anterior.
  - Auditoria de acceso: evento `access.denied` y `access.granted` en event log.
  - Validacion estricta de inputs con Pydantic models en FastAPI.
- Resiliencia:
  - Rate limiting por tenant: in-process sliding window para single-instance.
    Documentar que multi-instance requiere Redis (fuera de scope de esta etapa).
  - Circuit breakers en llamadas a git y checks externos (tenacity o pybreaker).
  - Timeouts configurables por operacion (simulate, run_checks, webhook processing).
  - Retry con backoff exponencial acotado (max_retries ya existe, agregar delay con time.sleep en worker).
- Observabilidad:
  - Eventos de auditoria (granted/denied) en event log.
  - Metricas de rate limiting (requests throttled por tenant).
- Eliminar server_legacy.py (rollback ASGI ya no necesario).

**Criterio de salida:**
- Suite de seguridad con casos de abuso bloqueados (inyeccion, escalacion, cross-tenant, rate limit).
- Circuit breakers activados en tests de fault injection (timeout git, timeout DB).

**Tests nuevos:** >= 20 (scopes, rotacion, rate limiting, circuit breakers, fault injection).
**Tests acumulados:** >= 249.

---

## Semana 5 — GitHub App bidireccional + worker async

**Objetivo:** Cerrar circuito con GitHub y automatizar la cola.

**Entregables:**
- GitHub App MVP:
  - Manifest de instalacion con permisos minimos (pull_requests: read, checks: write, statuses: write).
  - Autenticacion via JWT + installation token.
  - Private key via secret mount (K8s secret o archivo local). Documentar: nunca en env var visible en `ps`.
  - Webhook receiver existente conectado a App.
- Publicacion de resultados (unico modulo async — httpx.AsyncClient):
  - Check-run por intent con status (queued/in_progress/completed) y conclusion (success/failure/neutral).
  - Commit status en PR reflejando decision del motor (validated/blocked/rejected).
  - Summary con trace_id, risk score, y motivo de decision.
- Sincronizacion de estado:
  - PR cerrado/mergeado -> intent actualizado automaticamente.
  - Re-push en branch -> revalidacion automatica.
- Worker async para process_queue:
  - Proceso separado con loop de scheduling (polling interval, batch size configurables).
  - Lock distribuido sobre Postgres (`pg_advisory_lock`).
  - Graceful shutdown: captura SIGTERM, drain de batch actual, liberacion de lock.
  - No comparte event loop — es un proceso independiente.
- Observabilidad:
  - Spans en llamadas a GitHub API (latencia, errores).
  - Metricas de webhook processing (received, processed, failed).
  - Metricas de cola (depth, processing time, retry rate).

**Criterio de salida:**
- PR real en repo de prueba muestra check-runs de Converge.
- Decisiones del motor visibles en la UI de GitHub.
- Idempotencia end-to-end verificada con delivery_id + trace_id.
- Worker procesa cola autonomamente sin invocacion CLI.

**Tests nuevos:** >= 20 (GitHub API mocks, JWT auth, sync events, worker lifecycle, graceful shutdown, lock contention).
**Tests acumulados:** >= 269.

---

## Semana 6 — Reportes operativos y deployment completo

**Objetivo:** Dashboard, deployment containerizado, documentacion operativa.

**Entregables:**
- Reportes accionables (sobre analitica ya implementada):
  - Dashboard endpoint `/v1/api/dashboard` con health, risk trends, queue state, compliance.
  - Alertas de compliance por tenant cuando SLO se degrada.
  - Export de decisiones via endpoint HTTP (JSONL/CSV ya existen).
- Deployment completo:
  - Dockerfile multi-stage produccion (uvicorn + worker como entrypoints separados).
  - Docker Compose actualizado (Converge API + Worker + Postgres + OTLP collector).
  - Manifests K8s: Deployment (API), Deployment (Worker), Service, ConfigMap, Secret, HPA.
  - Variables de entorno documentadas con defaults sensatos.
  - Secrets: API keys, webhook secret, GitHub App private key via K8s secrets.
    Documentar path a Vault (sin implementar adapter).
- Documentacion operativa:
  - Runbook: arranque, parada, rollback Postgres->SQLite, rotacion de keys, troubleshooting.
  - Diagrama de arquitectura (componentes, flujo de datos, dependencias).

**Criterio de salida:**
- `docker compose up` levanta API + Worker + Postgres + collector.
- K8s manifests deployables en cluster de prueba.
- Dashboard endpoint retorna datos reales.
- Runbook revisado.

**Tests nuevos:** >= 10 (dashboard endpoint, export HTTP, deployment smoke tests).
**Tests acumulados:** >= 279.

---

## Semana 7 — Modularizacion y deuda estructural

**Objetivo:** Bajar entropia interna en los modulos no tocados por S1-S6.

**Contexto:** S1 ya extrajo event_log.py a ports. S2 ya partio server.py en routers FastAPI.
Los modulos que quedan por modularizar son cli.py (901 LOC) y risk.py (679 LOC).
engine.py (524 LOC) se evalua: split solo si mejora cohesion.

**Entregables:**
- Split de cli.py por bounded context:
  - `cli/intents.py`: cmd_intent_create, cmd_intent_list, cmd_intent_status, cmd_validate, cmd_simulate.
  - `cli/queue.py`: cmd_queue_run, cmd_queue_inspect, cmd_queue_reset, cmd_merge_confirm.
  - `cli/risk.py`: cmd_risk_eval, cmd_risk_gate, cmd_risk_review, cmd_risk_shadow, cmd_risk_policy_*.
  - `cli/admin.py`: cmd_serve, cmd_audit_*, cmd_export_*, cmd_policy_calibrate, cmd_archaeology.
  - `cli/__init__.py`: build_parser, main (re-exports).
- Split de risk.py por dominio:
  - `risk/signals.py`: entropic_load, contextual_value, complexity_delta, path_dependence.
  - `risk/graph.py`: build_dependency_graph, graph_metrics, build_impact_edges, propagation_score, containment_score.
  - `risk/bombs.py`: detect_bombs.
  - `risk/eval.py`: evaluate_risk, analyze_findings, build_diagnostics.
  - `risk/__init__.py`: re-exports publicos.
- Quality gates en CI:
  - Max 400 LOC por modulo.
  - Complejidad ciclomatica max 10 por funcion (radon o flake8-cognitive-complexity).
  - Verificacion automatica en pre-commit o CI.
- Limpieza menor:
  - Eliminar 6 imports sin usar en tests.
  - Reemplazar `os.chdir` manual por `monkeypatch.chdir` en test_analytics.py.
  - Documentar los 4 `except Exception: pass` en risk.py con comentarios de intencion.

**Criterio de salida:**
- Ningun modulo supera 400 LOC.
- CI valida limites automaticamente.
- Todos los tests existentes pasan sin cambio.

**Tests nuevos:** >= 5 (import sanity checks, CI limit validation).
**Tests acumulados:** >= 284.

---

## Semana 8 — Validacion de escala y readiness

**Objetivo:** Certificar que Converge es operable en produccion enterprise.

**Entregables:**
- Pruebas de carga multi-tenant:
  - k6 o Locust simulando carga concurrente.
  - Multiples tenants con politicas distintas.
  - Medir P95/P99 latencia, throughput, error rate bajo carga.
  - Comparar resultados vs baseline de S2.
- Pruebas de recuperacion:
  - Fallo parcial de Postgres (replica down, timeout).
  - Burst de webhooks (100+ simultaneos).
  - Worker crash y recovery (lock liberado, cola retomada).
  - Rollback de Postgres a SQLite.
- Pruebas de disponibilidad:
  - Health checks validados bajo carga.
  - Documentar path a HA: replicas API (HPA), failover Postgres (managed o Patroni), load balancer.
- Informe final de readiness:
  - Resultados de carga vs KPIs objetivo.
  - Delta vs baseline S2.
  - Riesgos residuales con plan de mitigacion.
  - Go/No-Go documentado.
  - Roadmap de siguiente etapa.

**Criterio de salida:**
- KPIs cumplidos bajo carga realista.
- Recovery time < 30s en todos los escenarios de fallo probados.
- Informe aprobado.

**Tests nuevos:** >= 10 (load test scripts, recovery scenarios).
**Tests acumulados:** >= 294.

---

## Eje transversal: Observabilidad

No es una semana — es una disciplina que crece con cada entregable.

| Semana | Adicion de observabilidad |
|--------|--------------------------|
| S1 | — (fundacion de ports, sin server aun) |
| S2 | Structured logging JSON + OTLP spans + /metrics base + baseline rendimiento |
| S3 | Metricas de persistencia (query time, connection pool) |
| S4 | Eventos de auditoria de acceso, metricas de rate limiting |
| S5 | Spans en GitHub API, metricas de webhook y cola |
| S6 | Dashboard endpoint consolidado, alertas de compliance |
| S7 | — (quality gates en CI, no observabilidad de produccion) |
| S8 | Dashboards finales, SLO tracking, runbook de incidentes |

---

## Eje transversal: Testing

Cada semana produce tests nuevos. Objetivo: ~50% de crecimiento al cierre.

| Semana | Tests nuevos | Acumulado | Tipo principal |
|--------|-------------|-----------|----------------|
| S1 | >= 10 | >= 204 | Contract tests de ports |
| S2 | >= 15 | >= 219 | Router tests, health checks, contract parity |
| S3 | >= 10 | >= 229 | Postgres adapter, migration, backfill |
| S4 | >= 20 | >= 249 | Seguridad, rate limiting, fault injection |
| S5 | >= 20 | >= 269 | GitHub API mocks, worker lifecycle, lock contention |
| S6 | >= 10 | >= 279 | Dashboard, export HTTP, deployment smoke |
| S7 | >= 5 | >= 284 | Import sanity, CI limits |
| S8 | >= 10 | >= 294 | Load tests, recovery scenarios |

Criterio: la suite no puede decrecer semana a semana. Cada parity test debe correr en ambos backends (SQLite + Postgres) desde S3.

---

## KPIs de etapa

| KPI | Baseline (S2) | Objetivo (S8) | Medicion |
|-----|--------------|---------------|----------|
| P95 validacion por intent | medir en S2 | < 2s | OTLP spans |
| Throughput de cola | medir en S2 | >= 5x baseline | /metrics |
| Throughput API | medir en S2 | > 50 req/s por instancia | pruebas de carga S8 |
| Tasa de requeue/reject | — | < 15% por perfil medio | event log query |
| Error rate API (5xx) | — | < 0.1% | /metrics |
| Latencia API P99 | medir en S2 | < 500ms | /metrics |
| Drift de entropia semanal | — | decreciente o estable | projections |
| Recovery time | — | < 30s | pruebas S8 |
| Test count al cierre | 194 | >= 294 | CI |

---

## Riesgos principales y mitigacion

| Riesgo | Probabilidad | Impacto | Mitigacion |
|--------|-------------|---------|------------|
| Migracion de persistencia rompe consistencia | Media | Alto | Dual-run + parity tests + rollback probado |
| FastAPI migration rompe contratos existentes | Baja | Alto | Tests de contrato + server_legacy.py hasta S4 |
| Worker async introduce race conditions | Media | Medio | Lock distribuido Postgres, tests de concurrencia |
| GitHub App private key expuesta | Baja | Alto | Secret mount, nunca en env var, rotacion documentada |
| Rate limiting insuficiente en multi-instance | Media | Medio | Documentado como limitacion; Redis en etapa siguiente |
| Sobreingenieria en hardening | Media | Bajo | Priorizar por SLO y evidencia, no completitud |
| Complejidad post-split genera mas entropia | Baja | Medio | Quality gates en CI, tests de regresion |
| S1 se alarga y bloquea todo | Media | Alto | S1 es solo ports + adapter; ASGI diferido a S2 |

---

## Dependencias entre semanas

```
S1 (ports + adapter SQLite)
 └── S2 (ASGI + observabilidad + Dockerfile + baseline)
      ├── S3 (Postgres + Docker Compose)
      ├── S4 (hardening + resiliencia)
      │
      │   S3 + S4 convergen en:
      │    └── S5 (GitHub App + worker async)
      │         └── S6 (reportes + deployment K8s)
      │              └── S7 (modularizacion cli + risk)
      │                   └── S8 (validacion de escala)
      │
      └── S7 puede adelantarse parcialmente (cli.py y risk.py no dependen de S3-S6)
```

**Ruta critica:** S1 → S2 → max(S3, S4) → S5 → S6 → S7 → S8.
**Paralelizables:** S3 y S4 pueden ejecutarse en paralelo despues de S2.
S7 (cli.py + risk.py split) puede comenzar en cualquier momento despues de S1.

---

## Rol del CLI

El CLI se mantiene como herramienta de operaciones locales y debug.
- Invoca la logica directamente (engine, event_log via store).
- No llama a la API HTTP.
- Para operaciones multi-nodo o remotas, usar la API.
- En S7 se modulariza (901 LOC -> 4 submodulos).

---

## Limitaciones conocidas al cierre de etapa

Estos items quedan explicitamente fuera de scope. Se documentan para la siguiente etapa:
- Rate limiting distribuido (requiere Redis o similar).
- HA automatico (requiere Postgres managed + replicas API).
- Adapter Vault para secrets (se documenta path, no se implementa).
- CLI via API (el CLI sigue siendo local; no hay cliente HTTP para operaciones remotas).
- Multi-region (deployment single-region en esta etapa).

---

## Definicion de "etapa cerrada"

- [ ] Persistencia Postgres operativa con rollback probado.
- [ ] Servidor ASGI en produccion con health checks y /metrics.
- [ ] Observabilidad: logs JSON, traces OTLP, metricas Prometheus, dashboard.
- [ ] Seguridad: scopes finos, rotacion, rate limiting (single-instance), auditoria.
- [ ] Worker async procesando cola autonomamente.
- [ ] Integracion GitHub bidireccional con check-runs.
- [ ] Deployment containerizado: Docker Compose + K8s manifests.
- [ ] Escala validada con carga realista (KPIs cumplidos vs baseline).
- [ ] Deuda estructural bajo control (ningun modulo > 400 LOC, quality gates en CI).
- [ ] Tests >= 294 (parity en ambos backends).
- [ ] Limitaciones documentadas con path a resolucion.
- [ ] Roadmap de siguiente etapa definido.

## Cierre de circulo (obligatorio)

Aplicar checklist de gobernanza final en `docs/CIERRE_CIRCULO_GOBERNANZA.md`.

Ninguna etapa se considera cerrada sin cumplir los 4 bloques:
1. Criterios numericos de exito (KPIs arriba, con baseline vs resultado).
2. Pilotos reales (GitHub App con repo de prueba).
3. Politica de compatibilidad/migraciones (rollback Postgres -> SQLite).
4. Revision de arquitectura recurrente (post-modularizacion).
