# ICS2122 Taller de Investigación Operativa (Capstone), grupo 9

**FLEET ASSIGNMENT Y CARGO ROUTING INTEGRADOS SOBRE UNA RED HUB-AND-SPOKE SEMANAL**

### 1. Archivos creados o modificados

| Archivo | Estado | Qué hace / qué cambió |
|---|---|---|
| `src/estructuras_datos.py` | modificado | Lee además `cost_params`, `landing_fees`, `maintenance` y `ops_rules.yaml`. Calcula costos reales `c_ke`, handling `h_k`, TAT en horas, aeropuertos de escala técnica, ventanas de mantenimiento en horas desde el lunes 00:00 UTC, frecuencias mínimas, conjunto disperso `Qk` y cota `S_max_k` configurable (antes 111 por avión, fija). |
| `src/gurobi_model.py` | modificado | Modelo reescrito con conjuntos dispersos, tandas activables por `config` y `modelo._vars` para extraer la solución sin depender de nombres de variables. |
| `src/instancias.py` | nuevo | `reducir_instancia` (subconjunto de aviones, aeropuertos, días, commodities) y `tamano` (cuenta variables sin construir el modelo). |
| `src/solver_bridge.py` | nuevo | Exporta el modelo de Gurobi a MPS y lo resuelve con HiGHS (útil con licencia restringida); `extraer_solucion` convierte los valores en itinerario y cargas. |
| `src/validador.py` | nuevo | Verificador independiente: lee los CSV/YAML originales, comprueba cada regla y recalcula el margen neto desde cero. |
| `src/heuristica.py` | nuevo | Caso base (shuttle miope) y `evaluar_itinerario` (fija vuelos y horarios y optimiza la carga con el mismo modelo). |
| `src/exportador_resultados.py` | modificado | Ya no parsea nombres de variables; KPIs recalculados con el validador; escribe `kpis.json`, `itinerario_vuelos.csv`, `demanda_servida.csv`, `carga_por_vuelo.csv`. |
| `src/main.py` | modificado | Configurable por `.env` (solver, límite, `S_max`, subconjunto de aviones/aeropuertos, tandas). |
| `src/prueba_instancia_reducida.py` | modificado | Reescrito con la API nueva (la versión del Hito 3 dependía de estructuras que cambiaron). |
| `src/experimentos_escala.py`, `src/experimento_caso_base.py` | nuevos | Experimentos reproducibles (tablas de la sección 6). |
| `tests/test_modelo.py` | nuevo | 20 pruebas automáticas (sección 5). |
| `results/*` | nuevos | `escala.csv/.log`, `caso_base.json`, `caso_base_kpis.csv`, `caso_base_itinerario.csv`. |

### 2. Formulación implementada

Se mantiene la formulación posición-indexada del Hito 3. Notación:

- **Conjuntos**: `K` aviones; `E` tramos del catálogo `(o,d)`; `Ek[k]` tramos que puede volar el avión `k` (derechos de tráfico del operador en origen y destino, y duración ≤ 9 h); `S_k = 1..S_max_k` posiciones (vuelos) del avión `k`; `Q` commodities `q=(origen, destino, día)` con demanda diaria `D_q`; `Qk[k]` commodities que `k` puede atender (hay un tramo que sale del origen y otro que llega al destino).
- **Parámetros**: `τ_e` horas de bloque; `Q_k` payload; `c_ke = (930·4,57 + ex_fuel_op(k))·τ_e + tasa_aterrizaje(destino)` (USD); `h_k` handling por tonelada del operador; `r_q` tarifa USD/kg (ingreso = `1000·r_q·t`); `H = 168` h; `TAT` = 70 min (base), 50 min (escala técnica), 90 min (lleno–lleno); `θ = 0,95` umbral de "lleno"; `(a_k, ini_k, fin_k)` ventana de mantenimiento; `f_od` frecuencias mínimas.
- **Variables**: `y[k,s,e]` binaria (vuelo `e` en la posición `s`); `u[k,s]` (posición usada); `l[k,s]` (última posición usada); `t_dep[k,s]`, `t_arr[k,s]` en `[0,168]`; carga por commodity: `x` (a bordo), `b` (embarcada), `a` (descargada), `w` (continúa en el mismo avión a la posición siguiente); `s_q` (demanda servida). Auxiliares por tanda: `zeta`, `lleno`, `ffo`, `cargado`, `mant`, `dia`, `vv`.
- **Objetivo**: `max  Σ_q 1000·r_q·s_q − Σ c_ke·y[k,s,e] − Σ h_k·b[q,k,s]`.

| ID | Restricción | Tanda / Etapa |
|---|---|---|
| R1 | `Σ_e y[k,s,e] = u[k,s]` | núcleo / 1 |
| R2 | `u[k,s+1] ≤ u[k,s]` (sin huecos) | núcleo / 1 |
| R3a/b | `l[k,s] = u[k,s] − u[k,s+1]`; en la última posición posible `l = u` | núcleo / 1 |
| R4 | `y[k,s+1,e2] ≤ Σ_{e llega a origen(e2)} y[k,s,e]` (continuidad espacial) | núcleo / 1 |
| R5 | `t_arr = t_dep + Σ τ_e·y` | núcleo / 1 |
| R6a/b | `t_dep, t_arr ≤ H·u` | núcleo / 1 |
| R8 | `Σ_q x[q,k,s] ≤ Q_k·u[k,s]` | núcleo / 1 |
| R20a/b, R21a/b, W_pos_terminal | balance de carga: `x[s] = b[s] + w[s−1]`, `x[s] = a[s] + w[s]` | núcleo / 1 |
| R23, R25 | solo se embarca en el origen y se descarga en el destino del commodity | núcleo / 1 |
| R26a/b/c | `s_q = Σ b = Σ a ≤ D_q` | núcleo / 1 |
| R11 | TAT entre vuelos consecutivos (constante 70 min sin la tanda B) | núcleo / 2 |
| CUT_horas | horas de vuelo + `TAT_min·(n−1)` + exceso de mantenimiento ≤ 168 (desigualdad válida; no cambia el óptimo) | refuerzo |
| C_base, C_cierre_esp, C_cierre_tmp | rotación cerrada: el último vuelo llega al aeropuerto base (origen del primero) y hay TAT hasta la primera salida de la semana siguiente | **A** / 1 |
| TAT_lleno_a/b, TAT_ffo, R11_TAT | `TAT_s = 70 − 20·tecnica_s + 20·ffo_s`; `lleno = 1` ⇔ carga ≥ 0,95·payload; `ffo ≥ lleno_s + lleno_{s+1} − 1 − tecnica_s` | **B** / 2 |
| MIN_ton, MIN_ton_ub | cada vuelo lleva 0 t (ferry) o ≥ 10 t | **C** / 2 |
| MANT_* | una ventana por avión: el avión está en `a_k` desde antes de `ini_k` hasta después de `fin_k`, o está ocioso | **D** / 2 |
| FREQ_o_d | `Σ_{k,s} y[k,s,(o,d)] ≥ f_od` | **E** / 2 |
| F_un_dia, F_dia_lb/ub, F_carga_dia | día UTC de salida de cada vuelo; `b[q,k,s] ≤ min(D_q,Q_k)·dia[k,s,día(q)]`: la carga solo se embarca el día de su demanda | **F** / 3 |
| G_dist_* | a lo más `⌈f_od/7⌉` salidas por día UTC de cada OD comprometido (distribución equilibrada) | **G** / 3 |

Derechos de tráfico y límite de 9 h por tramo entran a través de `Ek[k]`. La activación es por bandera: `ciclico` (A), `tat_variable` (B), `min_ton_escala` (C), `mantenimiento` (D), `frecuencias` (E), `demanda_diaria` (F), `dist_frecuencias` (G).

Cambio de forma respecto del Hito 3: R4 pasó de `Σ y[s,e llega] ≥ y[s+1,e2] + u[k,s] − 1` a la forma agregada `y[s+1,e2] ≤ Σ y[s,e llega]`, equivalente sobre enteros (R2 ya garantiza que `s` esté usada si `s+1` lo está) y más ajustada en la relajación lineal.

### 3. Supuestos y decisiones de modelación (por confirmar con el profesor o la ayudante)

1. **Cota de posiciones `S_max`** (por defecto 30 en `estructuras_datos`, 8 en `main.py`): reduce el tamaño, pero puede excluir soluciones con más vuelos por avión. La cota teórica es ~111.
2. **Tramos de más de 9 h** (MIA–MVD 9,08 h, SID–EZE 9,50 h, SID–MVD 9,25 h): no se vuelan directo (`max_continuous_block_hours: 9` en `ops_rules.yaml`); la carga que los necesitaría viaja con escalas en otros aeropuertos (confirmado por el grupo). **No se pierde demanda:** ninguno de esos tres pares tiene demanda propia en `demand_weekly.csv`, y existen los caminos MIA–VCP–MVD, SID–VCP–EZE y SID–VCP–MVD con tramos ≤ 9 h. La carga con escalas usa la variable `w` (continúa en el mismo avión). Corrección: una versión anterior de este README decía que no había camino alternativo; era incorrecto.
3. **"Lleno" ≥ 95 % del payload** (regla de TAT de 90 min). Con 100 % exacto el modelo evadía la regla cargando 10 kg menos. El umbral es un parámetro (`UMBRAL_LLENO`) y conviene hacerle sensibilidad.
4. **Mínimo de 10 t por escala adicional**: interpretado como "cada vuelo lleva 0 t (ferry) o al menos 10 t".
5. **Frecuencia mínima**: al menos `f_od` vuelos operados en el tramo directo `o→d`, independientemente de la carga que lleven.
6. **Distribución equilibrada** (Etapa 3): a lo más `⌈f/7⌉` salidas por día de cada OD comprometido. Es una interpretación propia del criterio del enunciado.
7. **Demanda diaria estricta (confirmada por el grupo)**: la carga solo se embarca el día UTC de su demanda (según el vuelo que sale ese día). La tanda F está **activa por defecto** y en todos los experimentos de resultados. El profesor comentó en el informe 1 que esto "se podría relajar con supuestos razonables"; queda como sensibilidad.
8. **Todos los vuelos dentro de `[0,168]`**: se omiten los vuelos que cruzan el límite domingo–lunes.
9. **Costos**: combustible y costo horario por hora de bloque, tasa de aterrizaje en cada aterrizaje (también en escalas técnicas) y handling por tonelada embarcada.
10. **Interchange** (`interchange_airports`): no se usa; el operador de cada avión es fijo según el enunciado.
11. **Carga en tránsito**: solo se encadena dentro del mismo avión (sin transbordo entre aviones), como indican el enunciado y el diccionario de datos. El informe 1 habla de "transbordo obligatorio en el hub", lo que contradice esto; hay que corregir el informe.
12. **Avión ocioso**: puede quedarse donde esté; se le exige mantenimiento solo si vuela.

### 4. Hallazgos y errores corregidos

- **H1. Tamaño del modelo del Hito 3**: con `S_max_k ≈ 111` resultaban ~303 mil binarias `y` y ~1,9 millones de variables continuas de carga. Con `S_max = 30` y `Qk` disperso bajan a ~82 mil y ~505 mil; aun así el MILP directo no resuelve (sección 6).
- **H2. Provisorios reemplazados**: `c_ke = distancia × 5` y `T_TAT = 2,0 h` pasaron a costos y TAT reales.
- **H3. Faltaban núcleos**: cierre cíclico, escalas técnicas, mantenimiento, frecuencias, mínimo por escala y demanda diaria ligada a la hora de salida. Ahora están (tandas A–G).
- **H4. Forado en la regla de TAT**: con "lleno = payload exacto − 10 kg" el solver evadía los 90 min. Detectado por el validador (no por el modelo); corregido con el umbral del 95 %.
- **H5. El MILP directo no escala** (sección 6).
- **H6. La rutina de HiGHS no respeta el límite de tiempo** en la relajación raíz de la instancia completa (`S=8`: 159 mil variables): tras >20 min no terminaba con `time_limit=240`. Por eso esa instancia solo se reporta en tamaño.
- **H7. Datos**: `data.sqlite` es idéntico a los CSV (verificado tabla por tabla). La "guía de modelo" (R1…R26) son los comentarios de `gurobi_model.py`; se conserva la numeración de las restricciones existentes (R1–R6, R8, R11, R20/21, R23, R25, R26). El repositorio `main` no contiene `docs/`.
- **H8. Inconsistencia de documentos**: el informe 1 menciona transbordo en el hub; el enunciado no lo permite (ver supuesto 11).

### 5. Validación y pruebas

**Validador independiente** (`src/validador.py`): no usa `estructuras_datos.py` ni `gurobi_model.py`. Comprueba tramo en catálogo, ≤ 9 h, derechos de tráfico, continuidad, cierre de la rotación, tiempos (llegada = salida + duración, dentro de la semana), TAT según escala técnica o lleno–lleno, capacidad, carga en el origen y el destino correctos, sin carga en escalas técnicas, balances de carga, demanda máxima, mínimo por vuelo, mantenimiento, frecuencias, día de embarque y distribución. Recalcula ingresos, costos y margen, y **el margen recalculado coincide con el objetivo del solver en todas las pruebas**.

**20 pruebas** (`tests/test_modelo.py`, ~4 min):

- Datos: costos reales, tramos > 9 h no volables directo (sin pérdida de demanda), derechos de tráfico.
- Tandas acumuladas (Etapa 1, A, B, C, D): cada una resuelta con HiGHS, validada y con margen coincidente.
- Frecuencia mínima (factible) y frecuencia imposible (infactible).
- Etapa 3: demanda diaria (validada; el día de embarque coincide con el de la demanda), el óptimo con demanda diaria nunca supera al de la Etapa 2, y distribución equilibrada.
- El validador no es una prueba vacía: se prueba que detecta discontinuidad, TAT insuficiente, sobrecarga y vuelo durante el mantenimiento.
- Prueba diferencial: sin la tanda D el óptimo **sí** viola la ventana de mantenimiento (AC-04 y AC-08), con la tanda D no.
- Caso base: factible en flota completa (con demanda diaria estricta), y no supera la cota del MILP en la instancia reducida.

### 6. Resultados

**Escala del MILP** (`results/escala.csv`; tandas A–D y F activas, HiGHS con 240 s por instancia; el validador aprobó todas las soluciones incumbentes):

| Instancia | Vars binarias `y` | Vars continuas de carga | Variables (modelo) | Restricciones | Estado (HiGHS, 240 s) | Margen incumbente (USD) | Gap | Tiempo (s) |
|---|---|---|---|---|---|---|---|---|
| XS: 2 aviones, 5 aerop., S=6 | 132 | 2.448 | 2.821 | 3.819 | Optimal | 655.845 | 0.1 % | 73.6 |
| S: 4 aviones, 5 aerop., S=8 | 448 | 7.040 | 8.047 | 10.721 | Time limit reached | 943.673 | 143.2 % | 240.0 |
| M: 6 aviones, 8 aerop., S=8 | 1.200 | 16.704 | 18.759 | 24.969 | Time limit reached | 0 | ∞ | 240.1 |
| L: 10 aviones, 12 aerop., S=8 | 3.248 | 47.936 | 52.654 | 69.760 | Time limit reached | 0 | ∞ | 240.1 |
| COMPLETA: 19 aviones, 44 aerop., S=8 | 21.856 | 134.752 | 159.919 | 218.304 | no resuelto (solo construcción) | — | — | — |
| COMPLETA: 19 aviones, 44 aerop., S=30 | 81.960 | 505.320 | — | — | solo tamaño | — | — | — |


Lectura: con 2 aviones el MILP llega al óptimo en ~10 s; con 4 aviones y 8 posiciones no cierra el gap en 4 min; con 6 y 10 aviones el incumbente es mucho peor que lo que el caso base logra con los mismos aviones (con 10 aviones, HiGHS ni siquiera mejoró la solución vacía). La causa es la debilidad de la relajación lineal (las `u` fraccionarias relajan el TAT y la continuidad). La instancia completa tiene 159 mil variables con `S=8` y ~587 mil con `S=30`.

**Caso base** (`results/caso_base*.{json,csv}`): cada avión repite viajes redondos desde su base hacia el spoke de mayor utilidad por hora sobre la demanda que queda, y luego la carga se asigna de forma óptima sobre esos vuelos (enfoque secuencial del informe 1). Flota completa, tandas A–D y F (demanda diaria estricta), sin frecuencias:

| KPI | Valor |
|---|---|
| Margen neto semanal (USD) | 2.514.066 |
| Ingresos (USD) | 12.908.131 |
| Costo de vuelos (USD) | 10.017.165 |
| Costo de handling (USD) | 376.899 |
| Toneladas entregadas | 8,776.8 |
| % de la demanda semanal servida | 65.6 % |
| Vuelos | 308 |
| Aviones usados / flota | 11 / 19 |
| Horas de bloque | 1,162.0 |
| Factor de ocupación | 58.4 % |
| ODs con frecuencia mínima cumplida | 9 de 25 |
| Aprobado por el validador independiente | sí |

Comparación en la instancia XS (2 aviones, 6 posiciones), donde el MILP llega al óptimo: caso base 304.204 USD frente a MILP 655.845 USD (+115.594 %). Con demanda diaria estricta el caso base pierde mucho, porque sus vuelos no se coordinan con los días en que aparece la carga, y el MILP sí. Sin demanda diaria (corrida previa, `results/*_sin_demanda_diaria.*`) el caso base de flota completa daba 4.657.324 USD (77,1 % de la demanda) y la ventaja del MILP en XS era de solo 0,046 %: la diferencia viene entera de la restricción diaria. Lo que aún no se puede medir es esa ventaja con la flota completa, que el MILP directo no resuelve.

### 7. Estado del avance del modelo

Criterio: un componente cuenta como hecho solo si está implementado, tiene prueba automática y pasa el validador. Los pesos son una decisión propia; conviene discutirlos.

| Bloque (peso) | Componentes | Avance |
|---|---|---|
| **Formulación implementada y validada (50 %)** | Estructura por posiciones, continuidad, capacidad, balance de carga, demanda, objetivo con costos reales, rotación cerrada, TAT 50/70/90, derechos de tráfico, mantenimiento, mínimo 10 t, demanda diaria, distribución equilibrada (14 de 15 completos). Frecuencias mínimas (probada con datos sintéticos; con las 25 frecuencias reales aún sin resolver en flota completa) cuenta a medias. El límite de 9 h con escalas está resuelto (verificado que no hay demanda perdida) y la demanda diaria estricta ya es la configuración por defecto. | ~92 % |
| **Solución a escala real (35 %)** | Instancia chica resuelta al óptimo y validada ✔; caso base de flota completa ✔; MILP resolviendo instancias medianas ✘; método integrado para la flota completa (matheurística o descomposición) ✘; mejora demostrada sobre el caso base ✘. | ~40 % |
| **Explotación (15 %)** | Escenarios de combustible y demanda, cuellos de botella, valor de una tonelada adicional, demanda que no conviene servir. | 0 % |

**Total ponderado ≈ 50 %.** Por etapas del enunciado (40 / 40 / 20 %): Etapa 1 completa en formulación, Etapa 2 casi completa en formulación, Etapa 3 con formulación pero sin explotación; el valor de las tres depende de resolver la flota completa. Lo que **no** está resuelto es lo más difícil: el modelo integrado aún no entrega un itinerario de 19 aviones (aunque en instancias chicas ya supera claramente al caso base).

### 8. Limitaciones y próximos pasos

1. **Matheurística o descomposición para la flota completa** (prioridad): LNS por subconjuntos de aviones usando este modelo como subproblema y el caso base como solución inicial; o formulación por rotaciones con generación de columnas (informe 1).
2. Probar las 25 frecuencias mínimas reales en instancias medianas.
3. Refuerzos de la relajación lineal (eliminar simetría entre aviones idénticos, cotas por operador).
4. Sensibilidad del umbral de "lleno" (95 %), de `S_max` y de la demanda diaria estricta.
5. Escenarios de la Etapa 3: precio del combustible, nivel de demanda, cuellos de botella, valor de la tonelada adicional (duales o corridas comparadas).
6. Confirmar con el profesor o la ayudante los supuestos 4, 5, 6 y 11 (los 2 y 7 los confirmó el grupo).
7. Generar `data/resources/resultados.json` para el visualizador.
