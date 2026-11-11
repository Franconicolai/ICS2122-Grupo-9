# Paso 2.5: cambios de operador intra-semana

Documento de diseño, implementación, validación y fundamento bibliográfico de la mejora incorporada al modelo de flota y carga del grupo 9.

---

## 1. Resumen ejecutivo

El pipeline original asignaba **un operador fijo por avión durante toda la semana**. Esta mejora agrega un **Paso 2.5** que permite que un mismo avión vuele parte de la semana con un operador y parte con otro, cambiando de operador en aeropuertos *interchange*, y que regrese al operador inicial antes del corte semanal. El avión conserva su aeropuerto inicial y final; el último vuelo no necesita ser del operador inicial si el avión alcanza a cambiar de operador en su aeropuerto de inicio antes del cierre de la semana.

La decisión no se toma con el objetivo local del avión. Cada rotación propuesta por el MILP del avión se evalúa con el **modelo global de carga multitramos** y con el **validador independiente**; solo se acepta si el margen validado mejora. De esta forma, el Paso 2.5 puede explorar swaps, pero no puede degradar la solución baseline.

Resultados principales sobre el itinerario de referencia de 2 h (`modelo_resuelto.pkl`):

| Corrida | Margen base | Margen final | Mejora | Vuelos | Aviones | Demanda | Swaps | Violaciones |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Paso 2.5, 2 pasadas | 6.310.307 | **7.140.268** | **+829.961 (+13,15 %)** | 344 | 18 | 87,89 % | 47 | 0 |
| Integración Paso 2 + 2.5, 1 pasada de prueba | 6.280.514 | **7.194.563** | **+914.049 (+14,55 %)** | 346 | 17 | 88,50 % | 48 | 0 |

La segunda fila no es directamente comparable con la primera: la prueba vuelve a resolver el Paso 2 con límites de tiempo y obtiene un baseline algo distinto (6.280.514 en lugar de 6.310.307). Sirve como prueba de integración con el Paso 2: el Paso 2.5 se ejecutó dentro de `reoptimizacion_por_avion.py`, exportó el itinerario con operador por vuelo y el validador independiente lo aceptó con 0 violaciones. El registro de la corrida de 18 aviones está en `data/results/Ejecucion_con_swaps_2h_18aviones/`; el de la prueba de integración de 17 aviones está en `data/results/Ejecucion_con_swaps_2h/`.

El detalle de la formulación, la matheurística, los supuestos y la literatura está en las secciones siguientes.

---

## 2. Qué cambió

### 2.1 Archivos modificados o nuevos

| Archivo | Cambio |
|---|---|
| `src/swap_operadores.py` | **Nuevo.** Red espacio-tiempo por avión con estados de operador, arcos de swap, MILP de rotación y matheurística de aceptación por margen global. |
| `src/reoptimizacion_por_avion.py` | Integración del Paso 2.5 después del Paso 2. Exporta el operador por vuelo y el resumen de swaps. |
| `src/asignacion_carga_multitramos.py` | Usa el operador de cada vuelo (no el operador fijo del avión). Agrega la opción de resolver la relajación LP y devolver duales de capacidad. |
| `src/validador.py` | Valida operador por vuelo, soporte de cada swap, cierre inicial/final de operador, mantenimiento y costos de handling con el operador efectivo. |
| `src/solver_util.py` | Devuelve duales de las filas cuando se resuelve una relajación LP (Gurobi e HiGHS). |
| `main.py` | Variables de entorno y metadata del Paso 2.5. |
| `README.md` | Hito 11 y enlace a este documento. |

### 2.2 Semántica de un swap

Un swap es un cambio de operador **sin cambio de avión**. El avión sigue siendo el mismo; cambia quién lo opera comercialmente. El modelo permite:

- varios swaps en la misma semana;
- swaps en aeropuertos *interchange* que soportan ambos operadores;
- que el avión empiece y termine en el mismo aeropuerto, pero **no** necesariamente en la base de mantenimiento;
- que el último vuelo sea de otro operador, siempre que el avión vuelva al operador inicial en su aeropuerto de inicio antes del corte;
- que un avión ocioso se active con una rotación nueva (modo amplio).

No se impone un tiempo mínimo de swap. El tiempo mínimo entre vuelos sigue siendo el TAT operacional; el swap no lo relaja. Esta es una decisión de modelado que debe contrastarse con la operación real (ver limitaciones).

---

## 3. Punto de partida y motivación

La corrida de referencia de 2 h obtiene:

- margen validado: **6.310.307 USD**;
- 344 vuelos, 18 de 19 aviones, 25/25 frecuencias;
- 88,26 % de la demanda;
- 65 vuelos vacíos;
- 0 violaciones.

La suma de los objetivos del Paso 2 por operador es **5.798.166 USD**, mientras que el margen final es **6.310.307 USD**. La diferencia de **512.141 USD (8,1 %)** proviene de conexiones `through` y transbordos que el Paso 2 no ve porque solo modela carga directa por operador. Esto tiene dos consecuencias:

1. **No se puede descomponer la carga por operador cuando hay swaps.** Un avión que cambia de operador puede alimentar conexiones que cruzan operadores. Optimizar cada operador por separado dejaría fuera esas conexiones.
2. **El objetivo local del avión no es confiable para aceptar un swap.** El MILP del avión solo ve carga directa y demanda residual; el modelo global de carga ve la red completa. Por eso el Paso 2.5 usa el MILP solo para **proponer** y el modelo global + validador para **decidir**.

Los costos de operador son reales y están en `cost_params.csv`:

- costo de vuelo: `(fuel_h + ex_fuel_usd_per_block_hour[o]) * tau[e] + fee[destino]`;
- handling: `handling_usd_per_ton[o]`.

Si un operador tiene menor costo de combustible/handling para un tramo, puede convenir que ese avión vuele parte de la semana bajo ese operador. El modelo no impone un tope de flota por operador; por lo tanto, puede concentrar vuelos en el operador más eficiente. Esto es una decisión de escenario, no una recomendación operativa: en la práctica existen límites de flota, tripulación, certificaciones y contratos.

---

## 4. Formulación del MILP por avión

### 4.1 Conjuntos e índices

- `k`: avión.
- `t = 0, ..., T-1`: bloques de tiempo de duración `DT` horas. La semana tiene `T` bloques.
- `a`: aeropuerto.
- `o`: operador.
- `e = (origen, destino)`: tramo.
- `q = (origen, destino, día)`: commodity diario de carga.
- `E_k`: tramos candidatos del avión `k`.
- `E_k^o`: tramos que el operador `o` puede volar por derechos de tráfico.
- `A_k`: aeropuertos candidatos (extremos de los tramos, hubs, mantenimiento).
- `W_k`: bloques de la ventana de mantenimiento del avión `k`.
- `a_k^m`: aeropuerto de mantenimiento del avión `k`.
- `op0(k)`: operador inicial del avión `k`.

### 4.2 Variables

| Variable | Dominio | Significado |
|---|---|---|
| `z[e,t,o]` | binaria | el avión vuela el tramo `e` saliendo en el bloque `t` con el operador `o`. |
| `g[a,t,o]` | continua ≥ 0 | el avión está en tierra en `a` durante el bloque `t` bajo el operador `o`. |
| `s[a,t,o,o']` | binaria | el avión cambia de operador `o` a `o'` en `a` en el bloque `t`. |
| `u` | binaria | el avión se usa en la semana. |
| `zl[e,t,o]` | binaria | el vuelo se marca como cargado (al menos 10 t). |
| `x[e,t,o]` | continua ≥ 0 | toneladas de carga directa en el vuelo. |

El modelo es una red espacio-tiempo con **estados de operador**. La posición del avión es el par `(aeropuerto, operador)`; un swap es un arco que cambia el segundo componente sin mover el avión.

### 4.3 Objetivo

Para el avión `k`:

```text
max  Σ_{e,t,o} (1000 * tarifa[e] - handling[o]) * x[e,t,o]
   - Σ_{e,t,o} costo[o,e] * z[e,t,o]
   + Σ_{e,t,o} bono_dual[e,t] * z[e,t,o]
   - Σ_{a,t,o,o'} penalty_swap * s[a,t,o,o']
```

con

```text
costo[o,e] = (fuel_h + ex_fuel[o]) * tau[e] + fee[destino(e)]
```

El término `bono_dual` es una guía, no una regla exacta: se calcula con los duales de capacidad de la relajación LP del modelo global de carga (sección 5.4). El término de penalty de swap es 0 por defecto; el modelo no cobra un costo artificial por cambiar de operador.

### 4.4 Restricciones principales

**Conservación de flujo en cada estado `(a,t,o)`.** El avión que está en `a` al final del bloque `t-1`, o que llega en un vuelo, o que cambia de operador en `a`, debe salir en un vuelo, quedarse en tierra o cambiar de operador:

```text
g[a,t-1,o]
+ Σ_{e: destino(e)=a, t-dur[e]=t} z[e,t-dur[e],o]
+ Σ_{o'} s[a,t,o',o]
=
g[a,t,o]
+ Σ_{e: origen(e)=a} z[e,t,o]
+ Σ_{o'} s[a,t,o,o']
```

para todo `a`, `t`, `o`, con las condiciones de borde del corte semanal.

**Un vuelo por bloque.** Un avión no puede despegar dos vuelos en el mismo bloque:

```text
Σ_{e,o} z[e,t,o] <= u        para todo t
```

**Corte semanal en el operador inicial.** Al final de la semana el avión debe estar en el operador inicial. El corte se escribe como:

```text
Σ_a g[a,T-1,op0] + Σ_{e,t: t+dur[e]=T} z[e,t,op0] = u
```

No se permiten swaps en el bloque 0. Un vuelo que llega exactamente en `T` solo se permite con `op0`; si llega con otro operador, el avión debe llegar antes y cambiar en tierra. Esto garantiza que el avión tenga tiempo de volver al operador inicial antes del corte.

**Cierre de operador.** Para todo `o != op0`:

```text
Σ_a g[a,T-1,o] = 0
```

No puede quedar flujo en otro operador al final de la semana. El avión puede terminar el último vuelo con otro operador, pero debe cambiar a `op0` en tierra antes del corte.

**Mantenimiento.** Durante toda la ventana `W_k`, el avión debe estar en su aeropuerto de mantenimiento:

```text
Σ_o g[a_k^m,t,o] >= u        para todo t en W_k
```

El modelo no fija el aeropuerto inicial en la base de mantenimiento. El avión puede empezar en cualquier aeropuerto candidato; la ventana de mantenimiento se cumple si la rotación pasa por el aeropuerto de mantenimiento en el momento correcto.

**Soporte de swaps.** `s[a,t,o,o']` solo existe si `interchange_airports.csv` declara soporte para ambos operadores en `a`. Además:

```text
Σ_{o,o'} s[a,t,o,o'] <= u        para todo a,t
```

Esto evita ciclos artificiales de swaps en el mismo aeropuerto y bloque. No hay tiempo mínimo de swap; el único tiempo relevante es el TAT entre vuelos.

**Derechos de tráfico.** `z[e,t,o]` solo existe si `o` tiene derechos en el país de origen y destino de `e`, según `traffic_rights.csv`.

**Carga directa y regla de 10 t.** Para cada vuelo candidato con demanda residual:

```text
x[e,t,o] <= Q_k * zl[e,t,o]
x[e,t,o] >= minton * zl[e,t,o]
x[e,t,o] <= dem_res[q(e,t)] * zl[e,t,o]
zl[e,t,o] <= z[e,t,o]
Σ_{(e,t) en q} x[e,t,o] <= dem_res[q]        para cada q
```

donde `Q_k` es el payload del avión y `minton` es el mínimo de 10 t. La regla se aplica a todos los vuelos: un vuelo o va vacío o lleva al menos 10 t. Es una regla operacional del caso, no una ley de la literatura; se mantiene como decisión de modelado y se valida de forma independiente.

**Frecuencias mínimas.** En el modo amplio se agregan las frecuencias residuales por OD:

```text
Σ_{e en OD} z[e,t,o] >= f_res[OD]
```

En el modo baseline los vuelos se conservan, por lo que la frecuencia ya está cubierta; el validador global la vuelve a verificar.

### 4.5 Dos modos de generación

**Modo baseline (conservador).** Se conservan exactamente los mismos vuelos del avión (mismo tramo y mismo bloque de salida) y solo se decide **con qué operador** se vuela cada uno. Se agrega:

```text
Σ_o z[e,t,o] = 1        para cada vuelo baseline (e,t)
```

Ventajas:

- el tamaño del MILP es pequeño (del orden de los vuelos del avión, no de toda la red);
- no se destruyen conexiones ni se inventan vuelos que el modelo global luego no usa;
- el único cambio es el operador, que es exactamente la nueva habilidad;
- el evaluador global decide si el cambio de operador mejora el margen.

**Modo amplio.** Se permite crear una rotación nueva con tramos candidatos: los del baseline, los de mayor demanda residual, los que tocan hubs y los de mantenimiento. Se controla con `SWAP_MAX_OD`, `SWAP_HUBS`, `SWAP_AMPLIAR` y `SWAP_TECNICOS`. Es más grande y más riesgoso; se usa para aviones ociosos o cuando se quiere explorar una rotación distinta.

**Modo `auto` (por defecto).** Usa baseline para aviones con rotación y amplio para aviones ociosos. En la corrida reportada, el avión ocioso (AC-14) propuso una rotación en modo amplio, pero el evaluador global la rechazó porque el margen empeoraba. Eso es correcto: el modelo no acepta una rotación solo porque el objetivo local sea positivo.

---

## 5. Matheurística de aceptación

### 5.1 Por qué no se descompone por operador

Con swaps, un avión puede volar para un operador y luego para otro. La carga puede pasar de un vuelo de un operador a un vuelo de otro. Si se optimizara cada operador por separado:

- se perderían las conexiones que cruzan operadores;
- el costo de handling dependería del operador efectivo de cada vuelo, no del operador fijo del avión;
- el cierre de operador del avión no sería verificable dentro de un solo operador.

Por eso el baseline sigue siendo el pipeline por operador del Paso 2, pero la mejora es **avión por avión** y la evaluación es **global**.

### 5.2 Algoritmo

```text
Entrada: itinerario baseline it0, info, DT
base = evaluar_global(it0)                  # carga MILP + validador
best = base

para pasada = 1..SWAP_PASSES:
    duales = duales_LP(best)                # precios de capacidad
    mejoró = False
    para cada avión k:
        cand_k = MILP_swap(k, best, duales) # propone rotación
        si cand_k no cambia vuelos ni operador:
            continuar
        cand = best con cand_k
        ev = evaluar_global(cand)           # carga MILP + validador
        si ev.ok y ev.margen > best.margen + SWAP_MIN_MEJORA:
            best = ev
            mejoró = True
    si no mejoró:
        romper
```

`evaluar_global` hace:

1. asigna carga multitramos con el modelo global (directa, `through` y transbordos);
2. valida el itinerario y la carga con el validador independiente;
3. devuelve el margen validado y las violaciones.

La aceptación usa el **margen del validador**, no el objetivo del MILP. Si el candidato es infactible, tiene violaciones o empeora el margen, se rechaza y se conserva el mejor itinerario. Esto convierte al Paso 2.5 en una **matheurística best-of**: nunca queda por debajo del baseline.

### 5.3 Generación de candidatos

Para cada avión:

1. se calcula la demanda residual y la frecuencia residual después de los demás aviones;
2. se construyen los aeropuertos y tramos candidatos;
3. se resuelve el MILP con estados de operador;
4. se extraen los vuelos con `z > 0.5` y se cuenta el número de swaps **a partir de la secuencia de operadores de los vuelos**, no de las variables `s` (las variables `s` pueden tener circulaciones de costo cero; contarlas daría un número inflado).

El MILP se resuelve con `SWAP_TLIM` segundos y `MIPGap=1 %`. En modo baseline el MILP es pequeño y suele resolverse en menos de un segundo; en modo amplio puede usar todo el límite.

### 5.4 Bucle de precios de carga

El modelo global de carga es un MILP (tiene variables enteras para el mínimo de 10 t y la regla lleno-lleno), por lo que no entrega duales. Para obtener una señal de valor de capacidad se resuelve su **relajación LP** y se leen los duales de las restricciones de capacidad de cada vuelo.

Si `π_f` es el dual de capacidad del vuelo baseline `f`, el bono que se agrega al MILP del avión es:

```text
bono_dual[f] = SWAP_DUAL_WEIGHT * max(0, π_f) * payload
```

El bono se aplica a cualquier operador que vuele el mismo `(tramo, bloque)`, porque lo que se quiere preservar es el vuelo, no el operador. En modo baseline todos los vuelos son obligatorios, así que el bono es constante y no altera la elección de operador; su efecto principal aparece en modo amplio y en la valoración de vuelos candidatos. El peso por defecto es `0.5`: el dual es un valor marginal local y usarlo con peso 1 podría duplicar el valor de la carga directa. El peso es un parámetro de sintonía; la aceptación global impide que un peso mal calibrado degrade el resultado.

Esta idea es una forma simplificada de **precios sombra / descomposición de Benders / relajación lagrangiana**: se usa el dual para valorar el recurso escaso (capacidad) y se resuelve un subproblema con ese precio. No es un Benders exacto: no se generan cortes de optimalidad ni se garantiza convergencia; es una guía para la matheurística.

### 5.5 Por qué la aceptación global es necesaria

El MILP del avión solo ve:

- carga directa;
- demanda residual;
- costo de vuelo y handling del avión;
- una aproximación del valor de capacidad vía duales.

No ve:

- conexiones `through` y transbordos que usan ese vuelo;
- la reacción del modelo global al cambiar la capacidad;
- la competencia entre aviones por la misma demanda;
- la regla de 10 t en la asignación global.

Por eso un candidato con objetivo local positivo puede empeorar el margen global. En la corrida reportada, AC-14 en modo amplio tenía objetivo local positivo pero el margen global caía; se rechazó. AC-03 en modo baseline conservó sus 4 vuelos y cambió de operador; el margen global subió 13.939 USD; se aceptó.

---

## 6. Validación independiente

El validador `src/validador.py` se ejecuta después de cada evaluación y verifica, entre otras reglas:

- catálogo de tramos y duración de bloque;
- derechos de tráfico del operador efectivo de cada vuelo;
- continuidad de la rotación y cierre en el aeropuerto inicial;
- TAT entre vuelos consecutivos, incluyendo la regla lleno-lleno y las escalas técnicas;
- ventana de mantenimiento en el aeropuerto de mantenimiento;
- payload y regla de 10 t por vuelo;
- frecuencias mínimas;
- conservación de carga, embarque inicial, entrega, `through` y transbordos;
- soporte de ambos operadores en cada transferencia y en cada swap;
- que exista tiempo para el swap inicial/final antes del primer vuelo y antes del corte semanal;
- costos de vuelo y handling con el operador efectivo;
- cierre semanal.

El validador es la fuente única de los KPI y del margen. En la corrida de referencia con swaps devuelve `ok=True`, **0 violaciones**, 25/25 frecuencias y el margen reportado. La prueba de integración con el Paso 2 también devuelve `ok=True` y 0 violaciones.

---

## 7. Resultados

### 7.1 Corrida sobre el baseline de referencia

Todas las pruebas reportadas usan la instancia completa (19 aviones, 344–346 vuelos), no un prototipo reducido de dos aviones. Se usó `data/results/Ejecucion_con_intervalos_de_2_horas/modelo_resuelto.pkl` como baseline y `sol_dt2.pkl` para la información del Paso 1. Configuración: `DT=2`, `SOLVER=gurobi`, `SWAP_PASSES=2`, `SWAP_TLIM=120`, `TLIM_CARGA=120`, `SWAP_MAX_OD=15`, `SWAP_DUAL_WEIGHT=0.5`, `SWAP_MODO=auto`.

| Métrica | Baseline | Final | Cambio |
|---|---:|---:|---:|
| Margen validado (USD) | 6.310.307 | **7.140.268** | **+829.961** |
| Ingresos (USD) | 18.326.562 | 18.251.717 | −74.845 |
| Costo de vuelos (USD) | 11.511.511 | 10.605.435 | −906.076 |
| Handling (USD) | 504.744 | 506.014 | +1.270 |
| Toneladas | 11.806,2 | 11.757,2 | −49,0 |
| Demanda servida | 88,26 % | 87,89 % | −0,37 pp |
| Vuelos | 344 | 344 | 0 |
| Aviones usados | 18 | 18 | 0 |
| Vuelos vacíos | 65 | 71 | +6 |
| Frecuencias | 25/25 | 25/25 | 0 |
| Violaciones | 0 | 0 | 0 |

La mejora es esencialmente **ahorro de costo de vuelo** (-906 k USD), compensado en parte por una caída de ingresos (-75 k USD) y un pequeño aumento de handling (+1,3 k USD). El modelo prefiere volar los mismos vuelos con operadores más baratos, aunque eso reduzca levemente la carga transportada. El objetivo es margen, no demanda; si se exigiera un piso de demanda, habría que agregarlo como restricción. El registro completo de esta corrida de 18 aviones está en `data/results/Ejecucion_con_swaps_2h_18aviones/` (ver `README_CORRIDA.md`, `indicadores_kpi.json` y `modelo_resuelto.pkl`).

**Uso de operadores por vuelo:**

| Operador | Baseline | Final |
|---|---:|---:|
| SUR | 172 | 14 |
| AND | 82 | 5 |
| BRA | 46 | 26 |
| PAC | 44 | 299 |

La concentración en PAC es consecuencia de que el modelo no tiene tope de flota por operador. Es un resultado de escenario, no una recomendación: en la operación real habría límites de flota, tripulación, certificaciones y contratos. La justificación detallada de por qué AC-14 queda en tierra en la corrida de referencia está en `README_AVION_OCIOSO.md`.

**Swaps:** 47 en total. Algunos aviones hacen varios:

- AC-01: 4 swaps; secuencia resumida (sin repetir operadores consecutivos) SUR → PAC → BRA → PAC → SUR; inicia y termina en BOG;
- AC-17: 8 swaps; secuencia resumida (sin repetir operadores consecutivos) SUR → PAC → BRA → PAC → BRA → PAC → SUR; inicia y termina en MIA;
- AC-18: 3 swaps; secuencia resumida (sin repetir operadores consecutivos) AND → BRA → PAC → AND; inicia y termina en MIA;
- AC-03: 2 swaps; secuencia resumida (sin repetir operadores consecutivos) AND → PAC → AND; inicia y termina en MIA.

Los aeropuertos de swap usados incluyen MIA, BOG, SJO, VCP, UIO, SDQ, MDE, MAO y BAQ, todos con soporte de ambos operadores. Los aviones inician en BOG, MIA, GRU o LAX; no se fuerza la base de mantenimiento.

### 7.2 Prueba de integración con el Paso 2

Se ejecutó la integración del Paso 2.5 dentro de `reoptimizacion_por_avion.py` sobre una copia de `sol_dt2.pkl` (es decir, se volvió a resolver el Paso 2 y luego se aplicó el Paso 2.5) con `TLIM_OP=420`, `SWAPS=1`, `SWAP_PASSES=1`, `SWAP_TLIM=120`, `TLIM_CARGA=120`, `SWAP_MAX_OD=15`, `SWAP_MODO=auto`.

| Métrica | Baseline | Final |
|---|---:|---:|
| Margen validado (USD) | 6.280.514 | **7.194.563** |
| Vuelos | 346 | 346 |
| Aviones usados | 17 | 17 |
| Demanda | — | 88,50 % |
| Frecuencias | — | 25/25 |
| Swaps | — | 48 |
| Violaciones | 0 | 0 |

El baseline de esta prueba es distinto del de la sección 7.1 porque el Paso 2 se volvió a resolver con límites de tiempo; no debe compararse directamente con 6.310.307. Lo importante es que el Paso 2.5 se integró sin errores, exportó el operador por vuelo y el validador aceptó la solución. El registro completo de esta prueba de integración de 17 aviones está en `data/results/Ejecucion_con_swaps_2h/` (ver `README_CORRIDA.md`, `indicadores_kpi.json` y `modelo_resuelto.pkl`).

### 7.3 Tiempo de cómputo

En esta máquina (Gurobi académico):

- Paso 2.5 sobre la referencia, 2 pasadas: **504 s** (8,4 min);
- LP de duales: 11–15 s por pasada;
- MILP baseline por avión: menos de 1 s en la mayoría de los casos;
- evaluación global de carga: 14–40 s por candidato;
- modo amplio para el avión ocioso: hasta 120 s (límite), rechazado.

La prueba de integración (Paso 2 + Paso 2.5) terminó dentro del límite de 30 min de la herramienta de prueba; el Paso 1 no se volvió a ejecutar en esa prueba porque se partió del `sol_dt2.pkl` de referencia. El límite de **12 h** no es un cuello de botella para esta instancia; los time limits (`TLIM`, `TLIM_OP`, `SWAP_TLIM`, `TLIM_CARGA`) son salvaguardas para hardware más lento o para instancias mayores. La configuración recomendada para la corrida final es `DT=2`, `TLIM=1500`, `TLIM_OP=420`, `SWAP_PASSES=2`, `SWAP_TLIM=120`, `TLIM_CARGA=300`.

---

## 8. Decisiones de modelado y fundamento bibliográfico

Esta sección explica por qué se tomó cada decisión y con qué corriente de la literatura se relaciona. No se afirma que el modelo sea una implementación literal de un paper; se indica qué ideas se usaron y qué se adaptó al caso.

### 8.1 Red espacio-tiempo con estados de operador

**Decisión.** Discretizar la semana en bloques de 2 h y construir una red espacio-tiempo por avión, con estados `(aeropuerto, operador)` y arcos de vuelo, tierra y swap.

**Fundamento.** La representación de un problema dinámico como una red de flujo en el tiempo es clásica: Ford y Fulkerson (1958) muestran cómo construir flujos dinámicos a partir de flujos estáticos, y Ahuja, Magnanti y Orlin (1993) sistematizan las redes de flujo y sus aplicaciones. La red espacio-tiempo es el lenguaje natural para modelar la posición de un avión en cada bloque y la continuidad de su rotación. Agregar el operador como segundo componente del estado es una extensión directa: un swap es un arco que cambia el operador sin mover el avión. Esta extensión es la que permite modelar cambios intra-semana sin duplicar el avión.

**Por qué 2 h.** La discretización de 2 h ya estaba en el modelo base y fue la mejor en las pruebas previas: 1 h daba 6,08 M, 30 min 6,21 M y 15 min 6,08 M, frente a 6,31 M con 2 h. Una discretización más fina aumenta el número de estados y arcos, y el MILP se vuelve más difícil sin mejorar el margen. La elección de 2 h es, por tanto, una decisión de compromiso entre fidelidad temporal y tractabilidad, consistente con la práctica de redes de tiempo expandido en programación de aerolíneas (Belobaba, Odoni y Barnhart, 2016).

### 8.2 Tail assignment / rotación por avión

**Decisión.** Resolver la rotación de cada avión individualmente, con su payload, su ventana de mantenimiento y su operador inicial, en lugar de asignar aviones a una rotación agregada.

**Fundamento.** El problema de asignar aviones específicos a vuelos se conoce como *tail assignment* o *aircraft routing*. Grönkvist (2005) formula el tail assignment con restricciones de mantenimiento y de flota; Gabteni y Grönkvist (2009) combinan generación de columnas y programación con restricciones; Haouari, Mansour y Sherali (2013) combinan generación de columnas con *large neighborhood search*. La literatura muestra que la asignación por avión es necesaria cuando los aviones no son intercambiables: distinto payload, distinta ventana de mantenimiento, distinto costo. En este caso, los 19 aviones tienen payload de 50, 52 o 54 t y ventanas de mantenimiento distintas; no se pueden tratar como una flota homogénea. El Paso 2 ya usaba esta idea; el Paso 2.5 la extiende con estados de operador.

### 8.3 Corte semanal y rotación cíclica

**Decisión.** Exigir que cada avión cierre la semana en su aeropuerto inicial y en su operador inicial, con un corte semanal; permitir que el último vuelo sea de otro operador si el avión vuelve a `op0` en tierra antes del corte.

**Fundamento.** Los horarios de aerolíneas son periódicos: la flota se programa para repetir una semana. La programación periódica y los horarios cíclicos se estudian, por ejemplo, en Serafini y Ukovich (1989). En flota, el cierre semanal es una forma de garantir que la rotación sea repetible y que el avión quede disponible para la semana siguiente. El corte en `op0` es una extensión del cierre de rotación: no basta con cerrar el aeropuerto; también debe cerrarse el estado de operador. La regla “un vuelo que llega exactamente en `T` solo con `op0`” es una forma de asegurar que el avión tenga tiempo de cambiar de operador antes del corte; si llega con otro operador, debe llegar antes y hacer el swap en tierra.

### 8.4 Aeropuerto inicial libre, no fijo en la base de mantenimiento

**Decisión.** No fijar el aeropuerto inicial en la base de mantenimiento. El avión puede empezar en cualquier aeropuerto candidato; la ventana de mantenimiento se cumple si la rotación pasa por el aeropuerto de mantenimiento en el momento correcto.

**Fundamento.** En tail assignment y maintenance routing, la base de mantenimiento es una restricción de mantenimiento, no necesariamente el origen de la rotación. Sarac, Batta y Rump (2006) y Papadakos (2009) modelan la programación de mantenimiento y la asignación de aviones; la literatura de *maintenance feasible aircraft routing* (Clarke, Hane, Johnson y Nemhauser, 1997) trata la ventana de mantenimiento como una restricción de la ruta. Forzar el inicio en la base de mantenimiento sería una restricción adicional que el caso no exige y que podría eliminar soluciones válidas. El modelo, en cambio, verifica que el avión esté en el aeropuerto de mantenimiento durante toda la ventana; el inicio puede ser otro aeropuerto si la rotación lo permite. En la corrida final, los aviones inician en BOG, MIA, GRU y LAX, no todos en la base de mantenimiento.

### 8.5 Swaps en aeropuertos interchange y derechos de tráfico

**Decisión.** Permitir un swap solo si el aeropuerto soporta ambos operadores según `interchange_airports.csv`, y permitir un vuelo solo si el operador tiene derechos de tráfico en los países de origen y destino.

**Fundamento.** La literatura de alianzas y *code-sharing* estudia por qué las aerolíneas coordinan operaciones en aeropuertos comunes: Brueckner (2001) analiza la economía de los acuerdos de código compartido; Adler (2001) modela la competencia en un mercado desregulado; Doganis (2019) describe la operación y la economía de las aerolíneas. El caso no modela una alianza completa, sino una posibilidad operativa: un avión puede ser operado por otro operador en un aeropuerto donde ambos tienen presencia. El archivo `interchange_airports.csv` es la fuente de soporte; `traffic_rights.csv` es la fuente de derechos. El modelo no inventa soporte: si el aeropuerto no soporta a ambos, el arco de swap no existe. Los aeropuertos técnicos se pueden habilitar para swaps con `SWAP_TECNICOS=1`, pero no para carga comercial; esta es una decisión conservadora para no mezclar una escala técnica con una operación comercial.

### 8.6 Sin tiempo mínimo de swap

**Decisión.** No agregar un tiempo mínimo de swap. El swap puede ocurrir en el mismo bloque; el único tiempo relevante es el TAT entre vuelos.

**Fundamento.** No hay una regla universal de tiempo mínimo de swap en la literatura; depende del contrato, de la tripulación, del handling y del aeropuerto. La literatura de *turnaround* y de operaciones aeroportuarias trata el tiempo de tierra como una variable operativa (Wu y Caves, 2000; Bazargan, 2010), no como una constante legal. En este caso, el TAT del modelo base ya impone un tiempo mínimo entre vuelos; agregar un tiempo adicional de swap sin datos sería una invención. Por eso se optó por no imponerlo y declararlo como supuesto. Es una relajación: en la operación real podría hacer falta tiempo para el cambio de tripulación, inspección o documentación. La sensibilidad a este supuesto queda como trabajo futuro.

### 8.7 Sin tope de flota por operador

**Decisión.** No imponer un número máximo de aviones por operador. Un operador puede operar todos los aviones si es eficiente.

**Fundamento.** En la literatura de *fleet assignment*, las restricciones de tamaño de flota son habituales (Barnhart, Belobaba y Odoni, 2003; Sherali, Bish y Zhu, 2006): cada flota tiene un número limitado de aviones. En este caso, el enunciado del problema no fija un tope por operador y se pidió explícitamente no imponerlo. Por eso el modelo lo relaja. La consecuencia es que el optimizador puede concentrar vuelos en el operador de menor costo (en la corrida, PAC con 299 vuelos). Esto es un resultado de escenario; no es una recomendación. Si se agregara un tope, el modelo seguiría siendo válido: bastaría con limitar el número de aviones activos por operador, por ejemplo `Σ_k u_{k,o} <= flota_o`, donde `u_{k,o}` indica si el avión `k` opera para `o`; alternativamente, un límite por avión. La literatura de flota justifica esta extensión; el caso la deja fuera por decisión del alcance.

### 8.8 Costos reales de operador

**Decisión.** Usar los costos de `cost_params.csv`: costo de combustible por hora de bloque y costo de handling por tonelada, específicos de cada operador. El costo de vuelo es `(fuel_h + ex_fuel[o]) * tau[e] + fee[destino]`.

**Fundamento.** La economía de aerolíneas descompone los costos en combustible, tripulación, mantenimiento, handling y costos de aeropuerto (Vasigh, Fleming y Tacker, 2018; Doganis, 2019). El modelo usa los componentes que el caso provee y que son relevantes para la decisión: el costo de combustible por hora y el handling por tonelada. No inventa un costo de tripulación ni de mantenimiento por operador porque no está en los datos. La literatura de flota y *cost indexing* justifica que el costo por hora de bloque y el handling sean los drivers de la elección de operador; el modelo los usa de forma transparente y el validador los recalcula con el operador efectivo de cada vuelo.

### 8.9 Regla de 10 t

**Decisión.** Mantener la regla de que un vuelo o va vacío o lleva al menos 10 t, para todos los vuelos.

**Fundamento.** La regla de 10 t es una regla operacional del caso (`ops_rules.yaml`), no un resultado de la literatura. Se puede relacionar con la literatura de consolidación de carga, que estudia umbrales económicos para consolidar envíos (Higginson y Bookbinder, 1995): hay un costo fijo por manipular un vuelo y, por debajo de cierto volumen, no compensa cargarlo. Pero el valor exacto de 10 t no se deriva de esa literatura; es un dato del caso. Se mantiene porque el modelo base ya lo usaba y porque el validador lo verifica. La decisión de aplicarlo a todos los vuelos es una simplificación: en la práctica podría aplicar solo a escalas adicionales o a ciertos tipos de vuelo. Se declara como supuesto y se deja abierta la sensibilidad.

### 8.10 Carga directa en el MILP del avión y carga global en la aceptación

**Decisión.** El MILP del avión solo modela carga directa; las conexiones `through` y los transbordos se evalúan en el modelo global de carga. La aceptación usa el margen global validado.

**Fundamento.** El diseño de redes de carga y la asignación de flujos multicommodity se estudian en Crainic (2000), Wieberneit (2008) y Derigs y Friederichs (2009). Estos trabajos muestran que la carga se mueve en una red con conexiones y transbordos, y que el valor de un vuelo no es solo su carga directa. Incluir toda la red de conexiones dentro del MILP de cada avión sería exacto pero muy costoso; separar la decisión de vuelo de la asignación de carga es una descomposición práctica. La literatura de matheurísticas (Archetti y Speranza, 2014; Maniezzo, Stützle y Voß, 2021) justifica usar un subproblema simplificado para generar candidatos y un evaluador más completo para aceptarlos. La diferencia de 512 k USD entre la suma de objetivos del Paso 2 y el margen global es la evidencia empírica de que el subproblema no ve todo el valor; por eso la aceptación no puede ser local.

### 8.11 Bucle de precios y duales

**Decisión.** Resolver la relajación LP del modelo global de carga y usar los duales de capacidad como bono en el MILP del avión.

**Fundamento.** Los precios sombra son la herramienta estándar para valorar un recurso escaso en programación lineal (Bertsimas y Tsitsiklis, 1997; Wolsey, 1998). La descomposición de Benders (Geoffrion, 1972) y la relajación lagrangiana (Fisher, 1981) usan precios/cortes para coordinar subproblemas. La generación de columnas (Desrosiers y Lübbecke, 2005) usa precios duales para decidir qué columnas entran. En este caso no se implementa un Benders exacto: se usa el dual como una señal heurística para que el MILP del avión no destruya vuelos con capacidad escasa. La aceptación global es la que garantiza que la señal no empeore el resultado. El peso 0,5 es una amortiguación; el dual es local y podría sobrevalorar la capacidad si se usa con peso 1.

### 8.12 Matheurística best-of

**Decisión.** Aceptar un candidato solo si el margen validado mejora; conservar siempre el mejor; hacer varias pasadas.

**Fundamento.** Las matheurísticas combinan métodos exactos y heurísticos para resolver problemas grandes (Archetti y Speranza, 2014; Maniezzo, Stützle y Voß, 2021). El *large neighborhood search* (Pisinger y Ropke, 2010) y las metaheurísticas en general (Blum y Roli, 2003) usan una vecindad y aceptan solo mejoras (o aceptan empeoramientos controlados). En este caso la vecindad es “cambiar la rotación de un avión” y la aceptación es determinista: solo mejoras. Esto es conservador y evita el riesgo de regresión; también hace que el resultado sea reproducible y fácil de auditar. La desventaja es que puede quedar atrapado en un óptimo local; se mitiga con varias pasadas y con el modo amplio para aviones ociosos.

### 8.13 Validación independiente

**Decisión.** Usar un validador separado del modelo para calcular el margen y verificar todas las reglas.

**Fundamento.** La validación independiente es una buena práctica en optimización aplicada: el modelo puede tener un error de formulación o una relajación no intencionada; un validador que reconstruye la solución desde los datos y las reglas detecta inconsistencias. La literatura de *decision support* y de auditoría de modelos recomienda separar la generación de la solución de su verificación. En este proyecto el validador ya existía y se usó como árbitro; el Paso 2.5 lo adopta como fuente única de KPI. Esto es especialmente importante con swaps, porque hay más reglas que verificar: soporte de ambos operadores, cierre de operador, handling del operador efectivo.

---

## 9. Supuestos y limitaciones

1. **Mantenimiento bajo cualquier operador.** Se asume que el avión puede cumplir su ventana de mantenimiento en su aeropuerto aunque cambie de operador. En la realidad, el mantenimiento puede estar contratado con un operador específico; habría que agregar una restricción de soporte de mantenimiento por operador.
2. **Sin tope de flota por operador.** El modelo puede concentrar vuelos en un operador. Es un escenario, no una recomendación. Agregar topes de flota, tripulación o certificaciones es trabajo futuro.
3. **Sin costo ni tiempo mínimo de swap.** El swap es instantáneo en el modelo; solo se respeta el TAT entre vuelos. En la práctica puede requerir tiempo de transición, documentación, inspección o cambio de tripulación.
4. **Sin restricciones de tripulación, slots, capacidad de aeropuerto ni certificaciones.** El modelo es táctico de flota/carga; no reemplaza un plan de tripulaciones ni un estudio regulatorio.
5. **Regla de 10 t aplicada a todos los vuelos.** Es una simplificación del caso; podría aplicar solo a ciertas escalas. Se mantiene porque el modelo base y el validador la usan.
6. **Discretización de 2 h.** Los vuelos y swaps se alinean a bloques de 2 h. Una discretización más fina no mejoró el margen en las pruebas previas y aumenta el tamaño.
7. **Carga como demanda máxima, no obligatoria.** El modelo maximiza margen; puede dejar demanda sin servir. En la corrida final la demanda baja 0,37 pp respecto del baseline. Si se exige un piso de demanda, hay que agregarlo como restricción.
8. **Sin cota global.** El resultado es una solución factible validada, no una prueba de optimalidad global. Los gaps de los subproblemas no se pueden sumar; la suma de objetivos del Paso 2 no es una cota del margen global.
9. **Matheurística secuencial.** Se optimiza un avión a la vez; el orden importa. Se mitiga con varias pasadas y con el criterio best-of, pero no se garantiza el óptimo global.
10. **Duales de la relajación LP.** El modelo de carga es un MILP; los duales vienen de su relajación LP. Son una señal, no un precio exacto del MILP. El peso 0,5 es una sintonía; no se hizo un estudio exhaustivo de sensibilidad.
11. **Aeropuertos técnicos.** Se permite swap en aeropuertos técnicos con `SWAP_TECNICOS=1`, pero no carga comercial. Es una decisión conservadora; en la corrida reportada los swaps ocurrieron en aeropuertos comerciales.
12. **Datos y supuestos provisionales.** `TTR_H=2` h y la interpretación de `interchange_airports.csv` como soporte operacional siguen siendo supuestos a confirmar con el mandante.

---

## 10. Reproducción

### 10.1 Pipeline completo

Desde la raíz del repositorio, en PowerShell:

```powershell
$env:DT='2'
$env:SOLVER='gurobi'
$env:TLIM='1500'
$env:TLIM_OP='420'
$env:SWAPS='1'
$env:SWAP_PASSES='2'
$env:SWAP_TLIM='120'
$env:TLIM_CARGA='300'
$env:SWAP_MODO='auto'
$env:SWAP_DUAL_WEIGHT='0.5'
$env:SWAP_MAX_OD='15'
python main.py
```

`main.py` es interactivo: pide un nombre de versión y una descripción. Los resultados quedan en `data/results/<version>/` con `indicadores_kpi.json`, `itinerario_vuelos.csv`, `modelo_resuelto.pkl`, `metadata.json`, etc. El `metadata.json` incluye `swaps`, `swap_tlim`, `swap_passes`, `swap_modo`, `swap_dual_weight`, `swap_max_od`, `swap_tecnicos` y `swap_hubs`.

### 10.2 Prueba del Paso 2.5 sobre un baseline existente

Para probar solo el Paso 2.5 sobre el baseline de referencia, sin volver a resolver el Paso 1/2:

```powershell
$env:SOLVER='gurobi'
$env:SWAP_PASSES='2'
$env:SWAP_TLIM='120'
$env:TLIM_CARGA='120'
$env:SWAP_MODO='auto'
$env:SWAP_DUAL_WEIGHT='0.5'
$env:SWAP_MAX_OD='15'
python src/swap_operadores.py `
  "data/results/Ejecucion_con_intervalos_de_2_horas/sol_dt2.pkl" `
  "data/results/Ejecucion_con_intervalos_de_2_horas/modelo_resuelto.pkl"
```

El script imprime el resumen por avión y el margen base/final. Para inspeccionar el itinerario final con operador por vuelo, usar la función `mejorar_con_swaps` y guardar `out["itinerario"]`; el `__main__` solo imprime el resumen.

### 10.3 Variables de entorno del Paso 2.5

| Variable | Default | Uso |
|---|---:|---|
| `SWAPS` | `1` | habilita/deshabilita el Paso 2.5. |
| `SWAP_TLIM` | `120` | límite de tiempo del MILP por avión (s). |
| `SWAP_PASSES` | `2` | número de pasadas de la matheurística. |
| `SWAP_MODO` | `auto` | `auto`, `baseline` o `amplio`. |
| `SWAP_DUAL_WEIGHT` | `0.5` | peso del bono de duales. |
| `SWAP_MAX_OD` | `15` | máximo de OD residuales en modo amplio. |
| `SWAP_TECNICOS` | `1` | permite swaps en aeropuertos técnicos. |
| `SWAP_HUBS` | `1` | `1` = lista de hubs por defecto; `0` = sin hubs; o lista separada por comas. |
| `SWAP_AMPLIAR` | `0` | `1` = usar todos los aeropuertos/tramos candidatos en modo amplio. |
| `SWAP_PENALTY` | `0` | penalidad artificial por swap (USD); por defecto no se cobra. |
| `SWAP_SOLO` | vacío | lista de aviones separados por coma, para pruebas. |
| `SWAP_MIN_MEJORA` | `1.0` | mejora mínima para aceptar (USD). |
| `SWAP_PENALTY` | `0` | penalidad artificial por swap. |

---

## 11. Referencias

1. Adler, N. (2001). Competition in a deregulated air transportation market. *European Journal of Operational Research*, 129(2), 337–345.
2. Ahuja, R. K., Magnanti, T. L., & Orlin, J. B. (1993). *Network Flows: Theory, Algorithms, and Applications*. Prentice Hall.
3. Archetti, C., & Speranza, M. G. (2014). A survey on matheuristics for routing problems. *EURO Journal on Computational Optimization*, 2(4), 223–246.
4. Barnhart, C., Belobaba, P., & Odoni, A. R. (2003). Applications of operations research in the air transport industry. *Transportation Science*, 37(4), 368–391.
5. Bartodziej, P., & Derigs, U. (2004). On an experimental algorithm for revenue management for cargo airlines. In *Experimental and Efficient Algorithms* (WEA 2004), LNCS 3059, 57–71. Springer.
6. Bazargan, M. (2010). *Airline Operations and Scheduling* (2nd ed.). Ashgate.
7. Belobaba, P., Odoni, A., & Barnhart, C. (Eds.). (2016). *The Global Airline Industry* (2nd ed.). Wiley.
8. Bertsimas, D., & Tsitsiklis, J. N. (1997). *Introduction to Linear Optimization*. Athena Scientific.
9. Blum, C., & Roli, A. (2003). Metaheuristics in combinatorial optimization: Overview and conceptual comparison. *ACM Computing Surveys*, 35(3), 268–308.
10. Brueckner, J. K. (2001). The economics of international codesharing: an analysis of airline alliances. *International Journal of Industrial Organization*, 19(10), 1475–1498.
11. Clarke, L. W., Hane, C. A., Johnson, E. L., & Nemhauser, G. L. (1997). Maintenance and crew considerations in fleet assignment. *Transportation Science*, 31(3), 229–240.
12. Crainic, T. G. (2000). Service network design in freight transportation. *European Journal of Operational Research*, 122(2), 272–288.
13. Derigs, U., & Friederichs, S. (2009). Air cargo scheduling: integrated models and solution procedures. *OR Spectrum*, 31(2), 325–353.
14. Desrosiers, J., & Lübbecke, M. E. (2005). A primer in column generation. In *Column Generation* (pp. 1–32). Springer.
15. Doganis, R. (2019). *Flying Off Course: Airline Economics and Marketing* (5th ed.). Routledge.
16. Fisher, M. L. (1981). The Lagrangian relaxation method for solving integer programming problems. *Management Science*, 27(1), 1–18.
17. Ford, L. R., & Fulkerson, D. R. (1958). Constructing maximal dynamic flows from static flows. *Operations Research*, 6(3), 419–433.
18. Gabteni, S., & Grönkvist, M. (2009). Combining column generation and constraint programming to solve the tail assignment problem. *Annals of Operations Research*, 171, 61–76.
19. Geoffrion, A. M. (1972). Generalized Benders decomposition. *Journal of Optimization Theory and Applications*, 10(4), 237–260.
20. Grönkvist, M. (2005). *The Tail Assignment Problem*. PhD thesis, Chalmers University of Technology.
21. Haouari, M., Mansour, F. Z., & Sherali, H. D. (2013). A hybrid column generation and large neighborhood search for the tail assignment problem. *Computers & Operations Research*, 40(1), 307–317.
22. Higginson, J. K., & Bookbinder, J. H. (1995). Markovian decision processes in shipment consolidation. *Transportation Science*, 29(3), 242–255.
23. Maniezzo, V., Stützle, T., & Voß, S. (Eds.). (2021). *Matheuristics: Algorithms and Implementations*. Springer.
24. Papadakos, N. (2009). Integrated airline scheduling. *Computers & Operations Research*, 36(1), 176–195.
25. Pisinger, D., & Ropke, S. (2010). Large neighborhood search. In *Handbook of Metaheuristics* (pp. 399–419). Springer.
26. Sarac, A., Batta, R., & Rump, C. M. (2006). A branch-and-price approach for operational aircraft maintenance routing. *European Journal of Operational Research*, 175(3), 1850–1869.
27. Serafini, P., & Ukovich, W. (1989). A mathematical model for periodic scheduling problems. *SIAM Journal on Discrete Mathematics*, 2(4), 550–581.
28. Sherali, H. D., Bish, E. K., & Zhu, X. (2006). Airline fleet assignment concepts, models, and algorithms. *European Journal of Operational Research*, 172(1), 1–30.
29. Vasigh, B., Fleming, K., & Tacker, T. (2018). *Introduction to Air Transport Economics: From Theory to Applications* (3rd ed.). Routledge.
30. Wieberneit, N. (2008). Service network design for freight transportation: a review. *OR Spectrum*, 30(1), 77–112.
31. Wolsey, L. A. (1998). *Integer Programming*. Wiley.
32. Wu, C.-L., & Caves, R. E. (2000). Aircraft operational factors and airline performance. *Journal of Air Transport Management*, 6(1), 29–36.
