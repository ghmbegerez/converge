# Cierre de Circulo - Gobernanza Final de Etapa

Este documento formaliza los 4 puntos requeridos para cerrar el ciclo entre
vision, diseno, operacion y aprendizaje.

## 1. Criterios de exito numericos por etapa

Definir y publicar objetivos cuantitativos antes de ejecutar cada etapa:

- `SLO_validacion_p95_ms`
- `SLO_queue_throughput_intents_hora`
- `SLO_api_error_rate`
- `SLO_api_latency_p95_ms` / `SLO_api_latency_p99_ms`
- `SLO_requeue_rate_max`
- `SLO_drift_entropia_max_semanal`

Regla de gobierno:
- No se declara etapa cerrada sin evidencia de cumplimiento de SLO/SLA.

## 2. Plan de adopcion real (equipos piloto)

Cada etapa de producto debe validar uso real con equipos concretos:

- Equipo piloto A (repositorio principal)
- Equipo piloto B (repositorio con dinamica distinta)
- Duracion minima de piloto: 2-4 semanas
- Retroalimentacion obligatoria: fricciones, valor percibido, falsos positivos, tiempos

Regla de gobierno:
- No se priorizan features nuevas sin aprendizajes del piloto anterior.

## 3. Politica de compatibilidad y migraciones

Definir politica explicita para:

- Versionado de esquema de base de datos
- Compatibilidad de tipos de evento (`event_type`) y payload
- Versionado de API externa e interna
- Estrategia de rollback por version

Regla de gobierno:
- Todo cambio incompatible debe incluir migracion, plan de rollback y prueba de paridad.

## 4. Cadencia de revision de arquitectura

Establecer review estructural periodico:

- Frecuencia: mensual
- Insumos minimos:
  - Complejidad por modulo
  - Acoplamiento entre capas
  - Drift de entropia
  - Incidentes post-merge
  - Deuda tecnica abierta/cerrada
- Salida:
  - Decisiones (ADRs cortos)
  - Plan de refactoring priorizado

Regla de gobierno:
- Si una revision detecta degradacion sostenida, se congela expansion funcional
  hasta restaurar indicadores estructurales.

## Definicion de cierre del circulo

Se considera cerrado cuando:

1. Hay metricas numericas activas y auditables.
2. Hay adopcion piloto real con evidencia de uso.
3. Hay politica formal de compatibilidad/migraciones aplicada.
4. Hay revision de arquitectura recurrente con decisiones registradas.
