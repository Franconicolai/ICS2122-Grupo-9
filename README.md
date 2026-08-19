# ICS2122-Grupo-9

## Tabla de contenidos

- [Contexto](#contexto)
- [Objetivo](#objetivo)
- [Alcance del proyecto: etapas](#alcance-del-proyecto-etapas)
- [Datos](#datos)
- [Lenguaje y herramientas](#lenguaje-y-herramientas)
- [Estructura del repositorio](#estructura-del-repositorio)
- [Cómo ejecutar](#cómo-ejecutar)
- [Autores](#autores)

## Contexto

La red modelada opera bajo una arquitectura *hub & spoke* centrada en Miami (MIA), con 19 aviones cargueros repartidos entre cuatro operadores (filiales con base en países distintos), cerca de doce mil toneladas semanales de carga y una fuerte asimetría de flujo hacia el hub. La operación es **cíclica**: el itinerario se repite semana a semana, por lo que cada avión debe terminar su rotación donde puede comenzar la siguiente.

El operador de cada avión es fijo y no es una decisión del modelo, pero condiciona qué tramos puede volar (derechos de tráfico) y su estructura de costos.

## Objetivo

Diseñar e implementar un modelo de optimización que decida simultáneamente:

1. **Qué vuelos opera cada avión** — qué tramos, en qué orden y con qué frecuencia
   durante la semana, de modo que cada aeronave describa una rotación cerrada.
2. **Cómo se asigna la carga** a los vuelos programados, respetando capacidad,
   demanda por par origen-destino y conservación de la carga en tránsito (incluyendo
   conexiones sin transbordo).

## Alcance del proyecto por etapas

| Etapa                                                  | Foco                                                         | Restricciones incorporadas                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------ | ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1 Modelo base de la red**                     | Acoplamiento entre el flujo de aeronaves y el flujo de carga | Capacidad de transporte, balance de flota (rotaciones cerradas), satisfacción de demanda por OD, conservación de carga en tránsito                                                                                                                                                          |
| **2 Realismo operacional**                       | Que el itinerario sea ejecutable en la práctica             | Tiempos mínimos en tierra (TAT), derechos de tráfico, escalas técnicas, duración máxima de vuelo continuo (autonomía de combustible), ventanas de mantenimiento, frecuencias mínimas comprometidas                                                                                      |
| **3 Dimensión comercial-temporal y análisis** | Demanda día a día y valor de negocio                       | Distribución diaria de la demanda (no transferible entre días), distribución equilibrada de frecuencias, análisis de escenarios (precio de combustible, niveles de demanda), interpretación económica (cuellos de botella, valor de capacidad adicional, demanda que conviene no servir) |

## Datos

Instancia representativa de planificación semanal, ubicada en `data/`.

### Convenciones generales

- **Semana de planificación:** lunes **2026-03-02 00:00 UTC**, 168 horas. La
  operación es **cíclica**: cada avión debe terminar donde puede empezar la
  semana siguiente.
- **Horas** en UTC (`YYYY-MM-DDTHH:MM:SS`). **Toneladas** métricas. **Montos** en USD.
- **Aeropuertos** identificados por código IATA. El **hub** de la red es **MIA**.
- **OD** = par origen-destino.
- Los cuatro operadores son `SUR` (Austral Cargo), `AND` (Andina Cargo),
  `BRA` (Brasil Cargo) y `PAC` (Pacífico Cargo).

### Especificaciones generales de los datos

### `fleet.csv` — 19 aviones

| Columna          | Descripción                                                     |
| ---------------- | ---------------------------------------------------------------- |
| `aircraft_id`  | Identificador (`AC-01`…`AC-19`). No codifica el operador.   |
| `operator`     | Operador al que está pre-asignado. Fijo durante toda la semana. |
| `payload_tons` | Capacidad máxima de carga por vuelo (50–54 t).                 |

### `airports.csv` — 44 aeropuertos

| Columna     | Descripción                                   |
| ----------- | ---------------------------------------------- |
| `iata`    | Código IATA.                                  |
| `country` | País. Se cruza con`traffic_rights.csv`.     |
| `region`  | `NAM`, `SAM`, `BRA`, `CEAM` o `EUR`. |

### `legs_catalog.csv` — 151 tramos

Tramos que la flota puede volar. **Solo se pueden programar vuelos de este catálogo.**

| Columna              | Descripción                                    |
| -------------------- | ----------------------------------------------- |
| `origin`, `dest` | Aeropuertos del tramo (dirigido: A→B ≠ B→A). |
| `block_hours`      | Duración del vuelo, puerta a puerta.           |
| `distance_km`      | Distancia.                                      |

### `demand_weekly.csv` — 57 ODs

| Columna               | Descripción                                                         |
| --------------------- | -------------------------------------------------------------------- |
| `origin`, `dest`  | Par OD de la demanda.                                                |
| `region`            | Unidad comercial:`NB`, `NAM`, `EUR` o `DOM`.                 |
| `tons_week`         | Demanda potencial. Es un**techo**: se puede transportar menos. |
| `tariff_usd_per_kg` | Tarifa. Ingreso =`tarifa × 1000 × toneladas`.                    |
| `min_weekly_freq`   | Frecuencia semanal mínima comprometida (0 = sin compromiso).        |

La carga puede viajar en un vuelo directo o encadenar varios vuelos del mismo
avión sin transbordo. Hay ODs sin tramo directo, que solo se sirven vía
conexiones, y demanda que puede no convenir servir.

### `demand_daily.csv`

Reparte `tons_week` en columnas `mon`…`sun` (día UTC de salida). **La demanda
de un día no es transferible a otro**: es un tope diario por OD.

### `traffic_rights.csv` — 84 filas

| Columna                   | Descripción                                |
| ------------------------- | ------------------------------------------- |
| `operator`, `country` | Par al que aplica el derecho.               |
| `allowed`               | 1 si el operador puede operar en ese país. |

Un avión puede volar A→B solo si su operador tiene `allowed=1` en el país de
A **y** en el de B.

### `interchange_airports.csv` — 44 aeropuertos

Matriz `airport × operador`, con 1 si ese operador está habilitado para tomar
la operación de una aeronave en ese aeropuerto. Un **interchange** es el punto
donde un avión pasa a ser operado por otra filial a mitad de rotación: es
posible entre dos operadores solo si ambos están habilitados en el aeropuerto.

### `maintenance.csv` — 19 ventanas (una por avión)

| Columna                              | Descripción                                       |
| ------------------------------------ | -------------------------------------------------- |
| `aircraft_id`                      | Avión.                                            |
| `airport`                          | Dónde debe realizarse.                            |
| `start_datetime`, `end_datetime` | Ventana (6–12 h) en que el avión no puede volar. |

### `cost_params.csv` — 10 filas

Formato `param, operator, value, unit`, donde `operator = ALL` indica que
aplica a toda la flota.

| Parámetro                     | Alcance                                                        |
| ------------------------------ | -------------------------------------------------------------- |
| `fuel_burn_gal_per_hour`     | Toda la flota (mismo tipo de avión).                          |
| `fuel_price_usd_per_gal`     | Toda la flota.                                                 |
| `ex_fuel_usd_per_block_hour` | **Por operador**: tripulaciones, mantenimiento, seguros. |
| `handling_usd_per_ton`       | **Por operador**: manipulación de carga.                |

### `landing_fees.csv` — 44 aeropuertos

Tasa fija por aterrizaje (`fee_usd`), cobrada en el aeropuerto de destino.

### `ops_rules.yaml`

| Clave                          | Descripción                                                                                                                                                                       |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `week_start`, `hub`        | Lunes de la semana (UTC) y hub de la red.                                                                                                                                          |
| `operators`                  | Nombre de cada operador.                                                                                                                                                           |
| `tat_minutes`                | Tiempo mínimo en tierra entre dos vuelos del mismo avión: 50 min si solo se reabastece combustible, 90 min si llega lleno y sale lleno, 70 min en el resto.                      |
| `technical_stop_airports`    | PTY y SID: el avión solo se reabastece, no carga ni descarga. Por eso no generan demanda propia.                                                                                  |
| `fuel_capacity`              | Combustible y carga compiten por el peso máximo de despegue: a plena carga solo cabe`max_fuel_at_max_payload_gal`, que descontando `reserve_hours` alcanza para 9 h de vuelo. |
| `max_continuous_block_hours` | Duración máxima de**un tramo** (no un límite diario ni semanal). Los tramos que no caben requieren una escala técnica.                                                   |
| `min_tons_per_extra_stop`    | Toneladas mínimas que debe mover una escala adicional. Un vuelo*ferry* (posicionamiento vacío, 0 t) queda exento.                                                              |

## Stack de herramientas

## Estructura del repositorio

```
.
└── data/    # Datos de entrada
```

## Cómo ejecutar

_Pendiente de definir una vez que exista una implementación._
