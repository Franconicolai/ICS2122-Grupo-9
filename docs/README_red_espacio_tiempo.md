# Red espacio-tiempo: método de solución para la flota completa (Sprint 5)

## 1. Por qué se cambió de método

El MILP por posiciones (Hito 4) está bien formulado, pero no escala: resuelve al óptimo con 2 aviones y con la flota completa (19 aviones, 44 aeropuertos) no encuentra solución. Esto se comprobó con HiGHS (pruebas de escala de José) y con Gurobi (corrida de Franco del 27/09).

La causa es la formulación de las rotaciones: casillas por avión, horarios continuos y restricciones de tiempo con "M grande". Con ellas, la relajación lineal queda muy lejos del valor real (la del modelo con S = 8 da 8,89 M USD, frente a los 2,51 M del caso base). Ese es el cuello de botella que pedía identificar el profesor.

La respuesta fue reformular el MILP integrado como una **red de flujo espacio-tiempo**. El informe 1 ya la mencionaba (secciones 2.3, 2.4 y 3.1) y es la representación estándar en la literatura de asignación de flotas (Hane et al., 1995; Bélanger et al., 2006).

## 2. Cómo funciona (dos pasos)

**Paso 1. Red agregada por operador** (`src/red_espacio_tiempo.py`)

- La semana se divide en bloques de `DT` horas (se usó 2 h). Los nodos son pares (aeropuerto, bloque) y la red es cíclica: el último bloque se conecta con el primero.
- Hay flechas de vuelo y flechas de espera en tierra. Cada flecha de vuelo dura `ceil((horas de bloque + TAT) / DT)` bloques. El redondeo es hacia arriba, así que el tiempo en tierra siempre se cumple (supuesto conservador).
- Las variables son enteras y cuentan cuántos aviones de cada operador vuelan cada tramo en cada bloque. La carga directa es continua.
- Restricciones incluidas:
  - conservación de flujo;
  - tamaño de flota por operador;
  - derechos de tráfico y límite de 9 h, a través de los tramos permitidos;
  - escalas técnicas, sin carga y con TAT de 50 min;
  - mínimo de 10 t por vuelo cargado;
  - demanda diaria según el día de salida;
  - frecuencias mínimas;
  - mantenimiento como conteo de aviones en tierra.
- Resultado: una buena solución y una cota. Pero, como los aviones están agrupados, puede armar rotaciones que solo cierran cada dos semanas.

**Paso 2. Reoptimización por avión** (`src/reoptimizacion_por_avion.py`)

- Se resuelve un operador a la vez, ahora con **aviones individuales**. Cada avión tiene su propia red espacio-tiempo, que:
  - cierra exactamente una vez por semana;
  - pasa en tierra su ventana de mantenimiento completa, en su aeropuerto;
  - usa su propio payload.
- La demanda y las frecuencias se descuentan según lo que ya cubren los demás operadores (esquema tipo Gauss-Seidel).
- Para acotar el tamaño, cada operador solo considera los tramos entre aeropuertos que visitó en el paso 1, más los de sus aeropuertos de mantenimiento.
- Al final se corrige la regla de 90 min (lleno a lleno), si hace falta, y se valida todo con el **validador independiente de José** (`src/validador.py`).

`src/asignacion_aviones.py` es el primer intento del paso 2: repartir la solución agregada entre aviones sin reoptimizarla. **Resultó infactible**, porque las rotaciones de varias semanas no se pueden separar. Se conserva como evidencia para el informe; el archivo también tiene funciones auxiliares que usa el paso 2.

## 3. Resultados (flota completa, bloques de 2 h, HiGHS, un núcleo)

Mismas reglas activas que el caso base: tandas A a D y F (demanda diaria estricta). Además, esta solución exige las 25 frecuencias mínimas, que el caso base no exige.

| KPI | Caso base (José) | Red espacio-tiempo | Diferencia |
|---|---|---|---|
| Margen neto semanal (USD) | 2.514.066 | **5.647.601** | +124,6 % |
| Ingresos (USD) | 12.908.131 | 17.556.013 | +36,0 % |
| Costo de vuelos (USD) | 10.017.165 | 11.430.203 | +14,1 % |
| Costo de handling (USD) | 376.899 | 478.209 | +26,9 % |
| Toneladas entregadas | 8.776,8 | 11.388,1 | +29,8 % |
| % de la demanda servida | 65,6 % | 85,1 % | +19,5 pp |
| Vuelos | 308 | 338 | +30 |
| Aviones usados | 11 / 19 | 18 / 19 | +7 |
| Horas de bloque | 1.162,0 | 1.332,1 | +14,6 % |
| Factor de ocupación | 58,4 % | 64,7 % | +6,3 pp |
| Frecuencias mínimas cumplidas | 9 / 25 | **25 / 25** | |
| Validador independiente | OK | **OK (0 violaciones)** | |

**Actualización con Gurobi** (licencia académica, 16 hilos, mismos límites de tiempo): paso 1 con 5.453.071 USD, cota 5.652.872 y gap 3,7 %. Paso 2 con **5.779.750 USD de margen, +129,9 % sobre el caso base**, validador OK con 0 violaciones, 84,4 % de la demanda, 331 vuelos, 16 de 19 aviones y 25 de 25 frecuencias. Gaps por operador: BRA 0,06 %, PAC 0,7 %, SUR 1,2 % y AND 14,4 %.

El margen que recalcula el validador (5.647.601 USD) coincide al dólar con la suma de los objetivos del paso 2.

**Desempeño computacional**

| Etapa | Variables enteras | Estado | Gap | Tiempo |
|---|---|---|---|---|
| Paso 1 (agregado, 4 operadores) | 53.971 | Límite de tiempo | 6,6 % | 1.500 s (primera solución a los 232 s) |
| Paso 2, BRA (2 aviones) | 22.592 | Óptimo | 0,04 % | 12 s |
| Paso 2, PAC (2 aviones) | 18.978 | Óptimo | 0,5 % | 8 s |
| Paso 2, SUR (7 aviones) | 74.417 | Límite de tiempo | 6,1 % | 420 s |
| Paso 2, AND (8 aviones) | 79.520 | Límite de tiempo | 3,5 % | 420 s |

Con bloques de 1 h, el paso 1 da 5,40 M (cota 5,71 M) en 40 min. Esa corrida permitía vuelos que cruzan el corte domingo-lunes y no se llevó al paso 2.

## 4. Supuestos y limitaciones (para el informe)

1. **Bloques de 2 h con redondeo hacia arriba.** Todas las reglas de tiempo se cumplen, pero se pierde en promedio cerca de 1 hora (0,94 h) en tierra por vuelo. Con bloques de 1 h la pérdida baja a 0,47 h y el modelo crece al doble.
2. **Solo carga en vuelo directo.** Cubre el 97,7 % de las toneladas demandadas. El 2,3 % restante necesita encadenar vuelos del mismo avión y no se captura.
3. **Método heurístico.** Los dos pasos y la resolución operador por operador no garantizan el óptimo global. Los gaps que se reportan son por operador, y todavía no hay una cota global formal.
4. **Regla de 90 min lleno a lleno.** Se corrige después de resolver (recortando carga). En esta corrida no fue necesario recortar nada.
5. **Distribución equilibrada de frecuencias (tanda G).** No está exigida, igual que en la configuración por defecto de José.
6. **Vuelos dentro de la semana.** Ninguno cruza el corte domingo-lunes (mismo supuesto 8 del README de José).
7. **Solver.** Los resultados de este README son con HiGHS en un solo núcleo. Con Gurobi y más núcleos se esperan gaps menores, o poder usar bloques de 1 h en el mismo tiempo. Falta confirmarlo con la prueba de la sección 5.

## 5. Cómo correrlo

Requisitos: `pip install highspy scipy numpy pandas pyyaml` y, para usar Gurobi, `gurobipy` con la licencia académica. Los cuatro archivos (`red_espacio_tiempo.py`, `reoptimizacion_por_avion.py`, `asignacion_aviones.py` y `solver_util.py`) van en `src/`, junto a `validador.py` de José. Los datos se leen de `data/raw data/`.

El solver se elige con la variable `SOLVER`: `highs` (por defecto) o `gurobi`. `THREADS` fija los hilos (por defecto, todos). Ambos solvers se comprobaron sobre el mismo modelo de prueba y dan el mismo óptimo. La instancia completa con Gurobi no se pudo correr aquí, porque la licencia gratuita de gurobipy limita el tamaño del modelo.

En Linux o Mac:

```bash
# Paso 1: red agregada (DT en horas, TLIM en segundos)
SOLVER=gurobi DT=2 TLIM=1500 OUT=results/sol_dt2.pkl python src/red_espacio_tiempo.py
# Paso 2: reoptimización por avión + validador (TLIM_OP = segundos por operador)
SOLVER=gurobi TLIM_OP=420 python src/reoptimizacion_por_avion.py results/sol_dt2.pkl
```

En Windows (PowerShell), las variables se definen antes:

```powershell
$env:SOLVER="gurobi"; $env:DT="2"; $env:TLIM="1500"; $env:OUT="results/sol_dt2.pkl"
python src/red_espacio_tiempo.py
$env:TLIM_OP="420"
python src/reoptimizacion_por_avion.py results/sol_dt2.pkl
```

**Prueba sugerida con Gurobi** (anotar margen, gap y tiempo de cada corrida para el informe):

1. Paso 1 con `DT=2` y `TLIM=1500`. Referencia con HiGHS: 5,31 M USD, gap 6,6 %, primera solución a los 232 s.
2. Paso 2 sobre ese resultado con `TLIM_OP=420`. Referencia con HiGHS: 5,65 M USD, validador OK.
3. Si Gurobi cierra rápido, repetir con `DT=1` (bloques de 1 hora) y `OUT=results/sol_dt1.pkl`.

Conviene actualizar Gurobi a la versión 13, porque la corrida de Franco del 27/09 usó la 10.

**Ojo con el tiempo por operador:** con HiGHS, SUR y AND necesitan al menos unos 400 s cada uno. En una prueba con `TLIM_OP=60` el validador igual aprobó (la solución es factible), pero el margen fue negativo (-5,75 M USD), porque el solver no alcanzó a mejorar su primera solución. Un paso pendiente es entregarle al solver la solución agregada como punto de partida (*warm start*).

El paso 2 escribe `results/sol_dt2_poravion_kpis.json` (KPIs, violaciones y detalle por operador) y `results/sol_dt2_poravion_itinerario.csv` (un vuelo por fila, con avión, posición, origen, destino, horas, día y carga).

## 6. Próximos pasos sugeridos

1. Correr con Gurobi (licencia académica) y bloques de 1 h, y comparar margen y tiempos.
2. Una o dos vueltas más del paso 2 (reoptimizar cada operador con los demás ya fijos por avión) para bajar los gaps.
3. Construir una cota global válida: por ejemplo, la relajación lineal con el payload máximo de cada operador.
4. Análisis de sensibilidad de la Etapa 3 (precio del combustible, nivel de demanda) y lectura económica de las restricciones: qué rutas son cuellos de botella y cuánto vale una tonelada adicional.
5. Escribir la formulación matemática de ambos pasos para la sección de Metodología del informe 2.
