# Instancias reducidas: comparación de métodos y algoritmo candidato

## 1. Para qué

La instancia completa (19 aviones, 44 aeropuertos) no cabe en un notebook de 8 GB con el modelo exacto: la red espacio-tiempo con transbordos tiene 12 M de variables con DT = 2 h. Para elegir qué algoritmo llevar a un computador con más RAM, se compararon los métodos en instancias reducidas que conservan la estructura del problema.

## 2. Cómo se arman (`generar_instancia.py`)

Una instancia es una carpeta con los mismos 11 archivos de `data/raw data`, filtrados. Todos los códigos la leen con la variable de entorno `RAW`.

```bash
python generar_instancia.py --operadores BRA,PAC --nombre R4 --aeropuertos MIA,BOG,UIO,MDE,SCL,VCP,GRU,MAO,LIM,GUA
```

```bash
RAW=data/instancias/R4 python correr_heuristica.py --salida data/results/instancias/R4/heuristica_dt2 --dt 2
```

- **Flota:** se dejan solo los aviones de los operadores elegidos, con su mantenimiento, sus derechos de tráfico y sus costos.
- **Red:** se conservan los aeropuertos permitidos o, con `--aeropuertos`, un núcleo de la red (más los aeropuertos de mantenimiento).
- **Demanda:** solo los mercados dentro de la red, **escalada** para que la relación demanda/capacidad sea la de la instancia completa. Si no, unos pocos aviones enfrentarían toda la demanda y el problema sería otro.
- **Frecuencias mínimas:** escaladas por la fracción de aviones, solo en tramos volables.

| Instancia | Operadores | Aviones | Aeropuertos | Mercados | Variables del modelo exacto (DT = 2 h) |
|---|---|---|---|---|---|
| XS | BRA, PAC | 4 | 44 | 57 | 2,4 M |
| S | AND, BRA | 10 | 44 | 57 | 6,4 M |
| M | BRA, PAC, SUR | 11 | 44 | 57 | 7,0 M |
| **R4** | BRA, PAC | 4 | 10 (núcleo en torno a MIA) | 32 | **0,40 M** |
| R10 | AND, BRA | 10 | 10 | 32 | 1,1 M |
| Completa | todos | 19 | 44 | 57 | 12,0 M |

El núcleo de 10 aeropuertos (MIA, BOG, UIO, MDE, SCL, VCP, GRU, MAO, LIM y GUA) conserva los mercados grandes, el desbalance hacia MIA, el triángulo MIA→VCP→BOG y el par GRU↔MAO.

## 3. Resultados en R4 (DT = 2 h)

Todos parten de la misma solución de la heurística, y el margen es el validado con transbordos (carga a gap 1e-6). Corridas en el notebook (i7-8550U, 8 GB).

| Método | Tiempo | Margen validado | vs. heurística | Cota |
|---|---|---|---|---|
| Heurística de 3 pasos (`correr_heuristica.py`) | 1 min | 770.625 | — | — |
| Modelo exacto completo, ramificación (`modelo_integrado_espacio_tiempo.py --libres todos`) | 40 min | 794.793 | +3,1 % | 1.132.379 (gap 42 %) |
| Modelo exacto completo, solo NoRel (`--norel 900`) | 15 min | 811.146 | +5,3 % | — |
| MILP continuo con NoRel; transbordos en el paso final (`warm_start_milp.py`) | 10 min | 806.975 | +4,7 % | — |
| **Búsqueda avión por avión, modelo exacto con transbordos (`busqueda_por_avion.py`)** | **16 min** (1 vuelta) | **836.779** | **+8,6 %** | — |
| Pares de aviones (AC-06 + AC-07 y AC-10 + AC-15), partiendo de la búsqueda anterior: NoRel 5 min cada uno | 2 × 5 min | 836.779 | sin mejora | — |
| Par AC-06 + AC-07 con ramificación | 15 min | 836.779 | sin mejora | 1.062.296 (gap 27 %) |

Qué muestran:

1. **La relajación lineal del modelo completo es muy débil cuando todos los aviones están libres.** La raíz de R4 vale 1.132.379, 47 % sobre la heurística, y tardó 25 min con solo 400 mil variables. Con un avión libre y el resto fijo, la brecha era de 2,4 % (AC-10 en la instancia completa). La relajación "reparte" cada avión entre muchas rotaciones y sirve mercados chicos con fracciones de avión. **Ramificar no cierra esa brecha** (40 min de árbol: la cota no se movió), y por lo tanto más RAM no basta para resolver la instancia completa hasta el óptimo con este modelo.
2. **Funciona mejor optimizar exactamente por partes.** Liberar un avión a la vez, con la carga y los transbordos de toda la red dentro del modelo, mejoró a los 4 aviones (+31, +19, +14 y +2 mil) y superó a todos los demás métodos en menos tiempo. Cada subproblema es chico y su relajación ajustada, así que Gurobi encuentra mejoras rápido.
3. **Decidir los vuelos sin ver los transbordos sigue costando.** El MILP continuo subió su objetivo sin transbordos en 82 mil, pero con los transbordos la mejora quedó en 36 mil. La búsqueda avión por avión sí los ve, y fue la que más ganó.
4. **NoRel sobre el modelo completo es un buen segundo**, porque busca soluciones sin resolver la relajación, que es justamente lo caro y lo débil.
5. **La búsqueda avión por avión se estanca en un óptimo local:** la segunda vuelta no mejoró ningún avión. Para salir de ahí hacen falta vecindarios más grandes (dos aviones o un operador completo), que es donde ayuda la RAM.
6. **La relajación se debilita a medida que se liberan más aviones.** Con 1 de 19 libres (AC-10 en la instancia completa), la brecha era de 2,4 %; con 2 de 4 libres en R4, de 27 %; con 4 de 4, de 47 %. Por eso los vecindarios de dos aviones no logran demostrar que la solución sea óptima en ellos, aunque tampoco encuentran algo mejor.

## 4. Algoritmo candidato: búsqueda exacta por vecindarios (LNS sobre la red espacio-tiempo)

> **Actualizado el 1 de noviembre:** el candidato vigente está en la sección 11. Combina esta búsqueda con el MILP continuo y la búsqueda en tiempo continuo.

1. **Solución inicial.** La heurística con la grilla fina, partiendo de la de 2 h (`correr_heuristica.py --start`), con una segunda iteración. En la instancia completa da 6.345.128 con DT = 1 h (`data/results/Heuristica_dt1_iter3_AND`).
2. **Mejora por vecindarios.** Se libera un grupo de aviones (al principio, uno), con el resto fijo en la mejor solución, y se resuelve la red espacio-tiempo **con la carga y los transbordos de toda la red** (`busqueda_por_avion.py`, con enlace fuerte), partiendo de la solución actual. Se acepta solo si sube el margen validado.
3. **Vecindarios crecientes.** Cuando una vuelta completa no mejora, se pasa a vecindarios más grandes: dos aviones que comparten aeropuertos o mercados, un operador completo, y grillas más finas (30 min). **Aquí entra la RAM:** cada escalón cabe solo en una máquina más grande (sección 5).
4. **Dentro de cada vecindario:** primero NoRel, que no necesita la relajación; luego ramificación con el tiempo que quede.
5. **Opcional al final:** una pasada del MILP continuo con NoRel, para ganar los minutos fuera de la grilla, conservándola solo si sube el margen validado. En la instancia completa aportó ~+100 mil; en R4 quedó por debajo de la búsqueda exacta.

**Por qué este candidato:**
- En R4 fue el mejor método por amplio margen: +8,6 %, contra +5,3 % del modelo completo con NoRel y +3,1 % con ramificación.
- Ve los transbordos al decidir los vuelos, cosa que el MILP continuo no hace.
- Sus subproblemas tienen relajaciones mucho más ajustadas que el modelo completo.
- Su memoria crece con el tamaño del vecindario, no con el de la instancia completa.

**Lo que se descarta para la máquina grande:**
- resolver hasta el óptimo el modelo completo con ramificación: la relajación es débil (gap de 42–47 % en R4) y más RAM no lo arregla;
- Benders: no converge en la práctica;
- repetir el MILP continuo: empeora el margen con transbordos.

## 5. Memoria necesaria en la instancia completa (`--estimar`)

| Vecindario | DT | Variables | RAM aproximada |
|---|---|---|---|
| 1 avión | 2 h | 1,0 M | 8 GB (probado) |
| 1 avión | 1 h | 1,9 M | 8 GB (probado; la factorización de la raíz pide 3,3 GB) |
| 1 avión | 30 min | 3,9 M | 16 GB |
| 2 aviones | 1 h | 3,2 M | 16 GB |
| Operadores BRA y PAC (4 aviones) | 1 h | 5,2 M | 32 GB |
| Operador AND (8 aviones) | 1 h | 10,6 M | 64 GB |

## 6. Próximos pasos

1. **Ahora, en el notebook:** una noche de búsqueda avión por avión con DT = 1 h sobre la instancia completa. Cada vuelta de 19 aviones × 20 min toma ~6,5 h.
   ```bash
   python busqueda_por_avion.py --corrida data/results/Heuristica_dt1_iter3_AND --dt 1 --tiempo 1200 --vueltas 2
   ```
2. **Antes de la máquina grande:** generalizar `busqueda_por_avion.py` para que acepte vecindarios de varios aviones (pares, operadores) y los elija según qué aviones comparten aeropuertos. Validarlo en R10 (10 aviones, 1,1 M de variables, cabe en el notebook).
3. **En la máquina grande (32–64 GB):** vecindarios de 2–8 aviones con DT = 1 h y vecindarios de un avión con DT = 30 min, partiendo de la mejor solución que deje el paso 1.
4. **Si el informe necesita una cota**, la relajación del modelo completo no sirve (gap de 40 % o más). Habría que fortalecer la formulación, por ejemplo con rotaciones generadas por columnas o desigualdades de capacidad por mercado, o calcular una cota por discretización dinámica. Es un trabajo aparte.

## 7. Cómo reproducir

Instancias en `data/instancias/` y resultados en `data/results/instancias/R4/`. Cada corrida se reproduce anteponiendo `RAW=data/instancias/R4` al comando de la tabla de la sección 3.

## 8. Técnicas del IPre de diagramas de decisión (31 de octubre)

**Idea probada:** la del solver SDD del IPre, que precalcula el valor de lo que viene después en cada nodo o arco y resuelve con un camino más largo sobre el diagrama. Aquí, la red espacio-tiempo de cada avión es un diagrama de decisión exacto: nodos (aeropuerto, bloque) y arcos de vuelo o de espera.

**Código:** `reoptimizacion_camino_mas_largo.py`. Para cada avión, con los demás fijos:

1. calcula el valor de cada arco de vuelo;
2. busca el camino más largo en su red, respetando mantenimiento, frecuencias mínimas (como estado adicional) y el cierre semanal (probando cada aeropuerto de inicio);
3. evalúa la rotación con el paso de carga exacto y la acepta solo si sube el margen validado.

Se probaron tres formas de valorar los arcos:

| Valor de cada arco | Candidatos en R4 (margen validado; la solución actual vale 770.625) |
|---|---|
| Carga directa que queda libre sin el avión | 571–711 mil: todos peores |
| Vuelos actuales con su contribución real y vuelos nuevos solo con la demanda que nadie atiende | Siempre propone la rotación actual: nunca encuentra un cambio que convenga |
| Flujo de la relajación lineal del modelo exacto con el avión libre (redondeo: flujo − 0,5) | 543–764 mil: todos peores |

**Velocidad:** cada camino más largo toma menos de 2 s y la relajación lineal, ~12 s. El modelo exacto por avión toma 60–240 s.

**Por qué no funciona:**
- En R4, la búsqueda exacta ganó +66 mil sobre todo porque puso a volar a AC-06, que estaba casi parado: lanzaderas GRU↔VCP y GRU↔MAO que **alimentan transbordos**. La carga transbordada pasó de 43 a 119 t y la de varios tramos, de 105 a 187 t.
- Esos vuelos valen por las conexiones que habilitan, y esas conexiones dependen de cómo se reacomoda la carga de toda la red.
- Un valor fijo por arco no captura eso. Tampoco la relajación lineal, que con un avión libre en R4 queda 25 % sobre la solución.
- El supuesto del SDD (el valor de lo que viene después depende solo de un estado resumido) se cumple en el SCDKP, porque el recurso depende solo del consumo acumulado. **Aquí no se cumple**: el valor de un vuelo depende de qué otros vuelos existen y de qué carga se puede transbordar entre ellos.

**Qué haría falta para que funcione:**
- **potenciales exactos de la carga por (aeropuerto, bloque, destino)**, es decir, el dual del MCNF sobre la red espacio-tiempo, como en la sección 3 del IPre;
- **actualizarlos después de cada cambio**, al estilo de la generación de columnas: el precio se recalcula con el problema de carga y la rotación se vuelve a generar.

Eso es esencialmente **generación de columnas con rotaciones**, con el problema de carga como maestro. Es un proyecto de varias semanas, con resultado incierto, porque la relajación es débil.

**Lo que sí acelera el candidato:** menos tiempo por avión y más vueltas. En R4, la búsqueda exacta con 60 s por avión llegó a **834.798 (+64.173) en 8 min** (2 vueltas). Con 240 s había llegado a 836.779 en 16 min. En la instancia completa conviene probar 5–10 min por avión en lugar de 20.

**Conclusión de esta parte:** el algoritmo candidato de la sección 4 no cambia. El camino más largo queda como herramienta (`reoptimizacion_camino_mas_largo.py`), útil si en el futuro se calculan potenciales de carga exactos.

## 9. Vecindarios variables y depósito agregado (1 de noviembre)

**Código nuevo en `busqueda_por_avion.py`:**

- `--vecindario avion,par,operador`: búsqueda por vecindarios variables.
  - Recorre el primer nivel (un avión libre a la vez) hasta que una vuelta completa no mejora.
  - Entonces pasa a pares de aviones y después a operadores.
  - Apenas una vuelta de un nivel mayor mejora, vuelve a un avión.
- Los pares se ordenan por afinidad: toneladas transbordadas entre los dos aviones + 5 por aeropuerto que ambos visitan. Un avión sin vuelos cuenta con los aeropuertos de sus tramos permitidos. `--max-pares N` deja solo los N más afines.
- `--horas` corta la búsqueda en un tiempo dado.
- El log de Gurobi de cada vecindario ahora queda en `logs/` (antes se perdía).

**Código nuevo en `modelo_integrado_espacio_tiempo.py`:** `--deposito-agregado` (también en la búsqueda).

- Lleva un solo inventario de transbordos por (aeropuerto, destino, bloque), en vez de uno por avión que baja la carga.
- Con un avión libre en la instancia completa (DT = 1 h) el modelo baja de 1,95 M a **1,37 M variables (−30 %)**.
- El precio: permite que un avión vuelva a subir carga que él mismo bajó, así que es una relajación. **No pone en riesgo el resultado**, porque la búsqueda revalida cada candidato con el paso de carga exacto y solo acepta si sube el margen validado.
- Verificado en R4: el punto de partida es factible y su objetivo coincide con el del modelo exacto (834.798).

### Resultados (60 s por vecindario, NoRel, enlace fuerte)

| Instancia | Partida | Solo vecindarios de 1 avión | Con pares (vecindarios variables) | Tiempo |
|---|---|---|---|---|
| R4 (DT = 2 h) | 834.798 (búsqueda de 1 avión ya convergida) | 834.798 | 834.798; tampoco mejoran los operadores ni **30 min de NoRel sobre el modelo completo** | 13 min + 30 min |
| **R10 (DT = 1 h)** | 2.519.684 (heurística DT = 2 h → DT = 1 h con START) | 2.536.643 (+16.959; se estanca en la vuelta 3) | **2.594.270 (+74.585, +3,0 %)** | 1,4 h |

Detalle de R10: 8 mejoras aceptadas.

| Vuelta | Vecindario | Margen validado |
|---|---|---|
| 1 | AC-03 | 2.524.076 |
| 1 | AC-18 | 2.526.963 |
| 2 | AC-18 | 2.536.643 |
| 4 (pares) | AC-06 + AC-18 | 2.539.530 |
| 4 (pares) | AC-10 + AC-19 | 2.545.862 |
| 4 (pares) | AC-03 + AC-19 | 2.571.788 |
| 5 | AC-06 | 2.579.376 |
| 5 | AC-18 | 2.594.270 |

**Qué muestran:**

1. **Los pares sacan a la búsqueda del óptimo local de un avión.** En R10 aportaron directamente +35 mil y, al cambiar el contexto, destrabaron otras dos mejoras de un avión (+22 mil). Sin pares, la búsqueda se habría detenido en +17 mil.
2. En la mejora AC-03 + AC-19, el modelo integrado estimaba 2.547.485 y la carga exacta sobre esos vuelos dio 2.571.788. El modelo integrado con NoRel no llega a optimizar bien la carga en 60 s, pero sí elige buenos vuelos. **Revalidar cada candidato con el paso de carga exacto es parte esencial del algoritmo.**
3. En R4 la solución de 834.798 resistió todos los vecindarios y 30 min de NoRel sobre el modelo completo. Es un óptimo local muy firme, probablemente cercano al óptimo de la grilla de 2 h. Con 4 aviones, hay poco espacio que los pares no cubran.
4. **Tamaño de los pares en la instancia completa (DT = 1 h):** 2,45 M variables con depósito agregado (3,2 M sin él). Ese es el escalón que necesita la máquina grande (16–32 GB).

### Holgura del depósito agregado

Con los vuelos de la mejor solución de R10 fijos, la carga óptima vale 2.594.270 con el depósito exacto y 2.603.297 con el agregado: **+9 mil (0,35 %) de holgura** por re-embarque propio. En la instancia completa la holgura es de +21 mil (6.366.035 contra 6.345.128).

Consecuencia: con el depósito agregado, el modelo "mejora" aunque no cambie ningún vuelo. Por eso la búsqueda ahora revalida solo cuando cambian los vuelos. La holgura es chica frente al valor de un cambio de rotación, así que el modelo sigue sirviendo de guía.

### Instancia completa en el notebook (DT = 1 h, un avión libre)

- **Con 5 min por avión no alcanza.** De esos 300 s, el presolve toma ~60 s, y NoRel solo encontró la holgura del depósito en AC-01 y AC-02, sin cambiar vuelos. Con 5 GB de tope y el depósito exacto, Gurobi cortaba por memoria (AC-01, AC-03, AC-04).
- **Prioridad a los aviones subutilizados (`--orden uso`).** En la heurística, AC-14 y AC-18 están parados y AC-03 y AC-05 hacen 2–4 vuelos; los cuatro son de AND. En R10, los que más mejoraron fueron justamente AC-03 y AC-18. La búsqueda empieza por los aviones con menos vuelos.
- **15 min de NoRel por avión tampoco bastan.** AC-14, que está parado, no cambió vuelos: NoRel solo encontró la holgura del depósito.
- **Ramificación con un avión libre (`--norel 60`):**
  - con la barrera, la raíz se quedó sin memoria con el tope de 5,5 GB;
  - con simplex dual (`--metodo-raiz 1`), la raíz no terminó en 1.100 s (340 mil iteraciones).
- **Conclusión:** con DT = 1 h, la búsqueda exacta en la instancia completa no avanza en un notebook de 8 GB. Con un avión libre (1,4–1,9 M variables), NoRel no encuentra cambios de rotación en 15 min, y la raíz necesita la barrera con más memoria. En una máquina de 16–32 GB, con más núcleos, cabrían la barrera de la raíz y la ramificación, donde la relajación con un avión libre es ajustada (gap 2,4 % en AC-10). Por eso este paso del candidato se deja para la máquina grande.

### ¿Vale la pena una grilla más fina? (R10, DT = 30 min)

Se partió de la mejor solución con DT = 1 h (2.594.270), con vecindarios variables y 90 s por vecindario. Cada vecindario de un avión tiene 0,69 M variables y cada par, 1,1 M.

- **Resultado: 2.597.710 (+3.440, +0,13 %) en 1 h.** Solo mejoró AC-10 en la primera vuelta; después ningún avión ni ninguno de los 15 pares mejoró.
- En comparación, los vecindarios con DT = 1 h ganaron +74.585 en 1,4 h.
- **Conclusión:** en la máquina grande, la RAM rinde más en vecindarios más grandes (pares, operadores) con DT = 1 h que en vecindarios de un avión con DT = 30 min.

## 10. Tiempo continuo: el MILP de posiciones supera a la red espacio-tiempo en R10 (1 de noviembre)

### El MILP continuo en R10

`warm_start_milp.py --norel 600 --tiempo 600`, con transbordos solo en el paso final:

| Partida | Margen validado tras 10 min |
|---|---|
| Heurística DT = 1 h (2.519.684) | **2.657.270 (+137.586, +5,5 %)** |
| Búsqueda por vecindarios + DT = 30 min (2.597.710) | 2.628.255 (+30.546) |

Partiendo directamente de la heurística, el MILP continuo **supera en 10 min a la búsqueda por vecindarios sobre la red espacio-tiempo, que tomó 1,4 h** (2.594.270). En R4 había sido al revés: búsqueda +8,6 % contra MILP +4,7 %.

### Por qué: la grilla alarga cada tramo

En la red espacio-tiempo, cada tramo dura `ceil((tiempo de bloque + TAT) / DT)` bloques, así que pierde en promedio DT/2 por tramo.

- En la solución del MILP continuo de R10, **cinco de nueve aviones usados (AC-06, AC-08, AC-10, AC-16 y AC-19) ocupan la semana completa**. Al llevar sus vuelos a una grilla, la rotación no cierra ni con bloques de 15 min. Un avión con 30 tramos pierde ~15 h por semana con DT = 1 h.
- R10 (AND y BRA) tiene rotaciones muy cargadas. R4 tiene más holgura, y por eso ahí la grilla costaba menos.
- Bajar a DT = 30 min ayudó poco (+3.440): sigue perdiendo ~15 min por tramo, y el modelo crece al doble.

`busqueda_por_avion.py --ajustar-grilla` lleva una solución continua a la grilla de DT, pero falla justamente con esos aviones.

### Búsqueda por vecindarios en tiempo continuo (`--modelo continuo`)

Cada vecindario se resuelve con el MILP de posiciones con transbordos exactos de `milp_continuo_transbordos.py`:

- los aviones del grupo quedan libres;
- el resto conserva sus vuelos y horarios;
- la carga y los transbordos de todos se reoptimizan.

No hay grilla: cada tramo dura exactamente su tiempo de bloque + TAT. Lee los tiempos de `modelo_resuelto.pkl`, porque el CSV redondea las horas a 2 decimales y rompe los TAT ajustados.

```bash
RAW=data/instancias/R10 python busqueda_por_avion.py --modelo continuo --corrida data/results/instancias/R10/milp_continuo_desde_heuristica --tiempo 60 --vecindario avion,par --orden uso --max-pares 15 --vueltas 20 --horas 1
```

**Resultado en R10: de 2.657.270 a 2.682.344 (+25.074) en 0,8 h.**
- Mejoraron AC-14 (+2.300), AC-10 (+1.178) y AC-19 (+21.596).
- Después, ni los vecindarios de un avión ni los 15 pares mejoraron.

### Resumen en R10

| Cadena | Margen validado | vs. heurística | Tiempo |
|---|---|---|---|
| Heurística DT = 1 h (con START desde DT = 2 h) | 2.519.684 | — | 25 min |
| + búsqueda por vecindarios en la red espacio-tiempo (DT = 1 h, avión y par) | 2.594.270 | +3,0 % | +1,4 h |
| + la misma búsqueda con DT = 30 min | 2.597.710 | +3,1 % | +1 h |
| + MILP continuo con NoRel | 2.628.255 | +4,3 % | +10 min |
| Heurística + MILP continuo con NoRel | 2.657.270 | +5,5 % | +10 min |
| **Heurística + MILP continuo + búsqueda continua por vecindarios** | **2.682.344** | **+6,5 %** | +10 min + 0,8 h |

### Búsqueda continua en R4 y cadena híbrida

| Partida | Búsqueda continua (60 s por vecindario, avión y par) | Tiempo |
|---|---|---|
| MILP continuo con NoRel (806.975) | 815.241 (+8.267) | 0,3 h |
| **Búsqueda en la red espacio-tiempo (836.779)** | **845.742 (+8.963): nuevo mejor en R4, +9,7 % sobre la heurística** | 0,6 h |

- En R4 la red espacio-tiempo encuentra mejores estructuras de rotación y transbordos: las lanzaderas de AC-06 que alimentan transbordos.
- Partiendo de esa solución, que está en la grilla y por lo tanto también es válida en tiempo continuo, la búsqueda continua ajusta los horarios y agrega vuelos. Mejoró 6 veces: AC-15, AC-06 + AC-15 y AC-07 (tres veces) y AC-10.
- **Lectura:** la red espacio-tiempo es buena para decidir *qué* volar (ve los transbordos con un modelo ajustado) y el tiempo continuo, para decidir *cuándo* (sin perder tiempo por redondear a la grilla). Encadenarlos combina ambas ventajas.
- **En R10 la cadena híbrida no gana.** Partiendo de la red espacio-tiempo (2.597.710), la búsqueda continua llegó a 2.628.734 (+31.024) en 1,2 h. La cadena MILP continuo global → búsqueda continua llegó a 2.682.344. Cuando los aviones van llenos, la pasada global del MILP continuo reorganiza la flota completa con horarios exactos, y eso no se alcanza moviendo uno o dos aviones a la vez.
- **Conclusión de la comparación:** ninguna cadena gana en ambas instancias. Con holgura de tiempo (R4) conviene la red espacio-tiempo → búsqueda continua; con aviones saturados (R10), MILP continuo global → búsqueda continua. En la máquina grande, lo robusto es **correr ambas ramas desde la heurística y quedarse con la mejor**: son independientes y se pueden correr en paralelo.

### Otros resultados en tiempo continuo (R10)

- **Una segunda pasada global del MILP continuo empeora:** partiendo de 2.682.344 dio 2.674.626 (−7.717) con transbordos. Optimiza sin ver los transbordos y deshace lo que ajustó la búsqueda continua. Lo mismo pasó en la instancia completa.
- **Tríos en tiempo continuo:** 2.682.344 resistió vecindarios de un avión, pares y tríos con 90 s cada uno (0,9 h, sin mejoras). Es un óptimo local firme.
- **Variabilidad de NoRel:**
  - repitiendo la cadena con 3 hilos en vez de 8, el MILP global dio 2.604.441 (+84.757, contra +137.586) y la búsqueda continua lo llevó a 2.619.840;
  - la diferencia entre corridas (~2 %) pesa más que la búsqueda misma: **conviene correr varias veces el MILP global, con más hilos o distintas semillas, y seguir la búsqueda desde la mejor**.

## 11. Algoritmo candidato actualizado (1 de noviembre)

Desde la solución de la heurística, dos ramas independientes, que pueden correr en paralelo en la máquina grande. Se queda la mejor por margen validado.

**Rama A (aviones saturados; ganó en R10 y es la que tiene hoy el mejor resultado en la instancia completa):**
1. MILP continuo global con NoRel (`warm_start_milp.py --norel ...`). Repetirlo varias veces (distintas semillas o número de hilos) y quedarse con el mejor.
2. Búsqueda continua por vecindarios, alternando ventanas de tiempo (secciones 12 y 13) y grupos de aviones:
   ```
   python busqueda_por_avion.py --modelo continuo --corrida <solución> --vecindario ventana,avion,par        --ventana-h 12 --ventana-paso 6 --desplazar 2 --desplazar-aviones 10 --desplazar-presupuesto 60        --tiempo 300 --norel 60 --saltar-sin-cambios --umbral-aceptar 100 --mejora-minima 2000 --orden uso
   ```
   En instancias donde los aviones van llenos (como R10), el desplazamiento no aporta y agranda el modelo: conviene `--desplazar 0` o `--desplazar-aviones 3`. Cada vecindario usa el MILP de posiciones con transbordos exactos; los demás aviones quedan con sus vuelos y horarios.

**Rama B (con holgura de tiempo; ganó en R4):**
1. Búsqueda en la red espacio-tiempo: `busqueda_por_avion.py --dt 1 --vecindario avion,par`.
2. Búsqueda continua por vecindarios partiendo de esa solución, como en A.2.

**Qué no repetir:**
- la segunda pasada global del MILP continuo después de la búsqueda continua (empeora con transbordos);
- grillas de 30 min (aportan ~0,1 %);
- ramificar sobre el modelo completo (relajación débil).

**Recursos:**
- La rama A ya corre en el notebook en la instancia completa: un avión libre usa ~2,3 GB, 5 min por vecindario.
- La rama B necesita la máquina grande: con DT = 1 h y un avión libre, la raíz pide más de 5,5 GB, y NoRel no cambia vuelos en 15 min.
- En la máquina grande, con más núcleos, se puede correr ambas ramas y varias semillas de A.1 al mismo tiempo.

**Mejores resultados:**

| Instancia | Heurística | Mejor | Cadena |
|---|---|---|---|
| R4 | 770.625 | **868.027 (+12,6 %)** | B + ventanas desplazables (secciones 12–13) |
| R10 | 2.519.684 | **2.692.881 (+6,9 %)** | A + ventanas (sección 12) |
| **Completa** (19 aviones) | 6.345.128 (DT = 1 h) | **6.475.055 (+2,0 %)**: MILP continuo (6.435.839) + búsqueda por avión (6.451.322) + ventanas de 12 h (6.466.083) + ventanas desplazables (6.475.055) | A + ventanas desplazables |

**Búsqueda continua en la instancia completa** (`data/results/Busqueda_continua_desde_MILP_dt1`, 300 s por avión):
- partió de 6.435.839, el mejor resultado anterior, y llegó a **6.451.322 (+15.483)** con 7 mejoras: AC-07, AC-12, AC-10, AC-11, AC-04, AC-19 y AC-03;
- tras la primera vuelta, las mejoras se hicieron escasas: una de +5.403 (AC-19) y otra de +161 (AC-03) en ~4,5 h;
- la mejora de +161 hizo volver a los vecindarios de un avión, así que nunca se probaron pares. Para eso se agregó `--mejora-minima`;
- validador OK, 25/25 frecuencias, 330 vuelos, 16 aviones.


## 12. Vecindarios por ventana de tiempo (1 de noviembre, noche)

### La idea

Los vecindarios por avión, par o trío liberan pocos aviones completos. Un transbordo, en cambio, une vuelos de aviones distintos que coinciden en un aeropuerto en unas pocas horas. Para mover juntos esos vuelos hay que liberar varios aviones a la vez, y ningún vecindario de uno a tres aviones lo hace.

El nivel `ventana` (`busqueda_por_avion.py --modelo continuo --vecindario ventana`) libera, en **todos** los aviones, los vuelos que salen dentro de una ventana de tiempo, por ejemplo de 12 h. El resto de la semana queda fijo, con sus vuelos y horarios. La carga y los transbordos de toda la red se reoptimizan con el MILP continuo de posiciones.

**Detalles de la formulación:**
- **Número de vuelos por avión.** Las posiciones se ocupan sin huecos (R2), así que cada avión mantiene su número de vuelos dentro de la ventana. La excepción es cuando la ventana llega al último vuelo del avión o al fin de la semana: ahí se liberan además `--ventana-extra` posiciones vacías (2 por defecto), para agregar o quitar vuelos al final de la rotación.
- **Fijado.** `milp_continuo_transbordos.fijar_posiciones` fija las variables de cada posición fuera de la ventana. La marca de última posición (`l`) y el mantenimiento (`m`) quedan libres si la posición siguiente es libre.
- **Pares de transbordo.** `milp_continuo_transbordos.enumerar_pares_posiciones` arma los pares por posición. Descarta los que no caben entre la llegada del vuelo fijo anterior y la salida del vuelo fijo siguiente.

**Opciones de línea de comandos:**
- `--ventana-h`: ancho de la ventana, en horas.
- `--ventana-paso`: horas entre el inicio de dos ventanas seguidas.
- `--ventana-rango 102-168`: recorre solo una parte de la semana.
- `ventanaNN` como nivel (por ejemplo `--vecindario ventana,ventana48,avion`): ventanas de NN h con paso de NN/2 h.

**Tiempo para la ramificación.** Los modelos por ventana son chicos después del presolve, así que conviene dejarle tiempo al branch & bound: `--norel` corto (10–90 s) y el resto de `--tiempo` para ramificar. Con todo el tiempo en NoRel, Gurobi nunca ramifica (0 nodos).

### Resultados

| Instancia | Partida (óptimo local de avión, par y trío) | Con ventanas | Detalle |
|---|---|---|---|
| R4 | 845.742 | **849.367 (+3.625)** | ventanas de 24 h, 60 s; 12 min. Después, ventanas de 48 y 84 h, avión y par no mejoran: óptimo local para todos los vecindarios. |
| R10 | 2.682.344 | **2.692.016 (+9.672)** | ventanas de 24 h: la primera mejora (+2.908) vino de NoRel y la segunda (+6.764) de la ramificación. Luego, avión (+719) y par (+146) llegan a **2.692.881**, óptimo local de los tres niveles (1,4 h). |
| **Completa** | 6.451.322 | **6.466.083 (+14.761)** | ventanas de 12 h (paso de 6 h, 240 s, NoRel 60 s): 28 ventanas en ~1,5 h. Las mejoras salieron de las ventanas de 126–138 h (+1.904) y 138–150 h (+12.857). |

**En la instancia completa:**
- La ganancia de las ventanas es casi la misma que la de las 11,5 h de búsqueda por avión que produjeron 6.451.322, y llegó en ~1,5 h.
- La mayoría de las ventanas de 12 h quedan con **óptimo probado** en ~80 s (gap 0,0006 %), así que la búsqueda es exacta dentro de cada ventana. Algunas en la mitad de la semana no cierran la raíz en 240 s (gap 0,4–0,9 %).
- Con ventanas de 24 h, la relajación de la raíz consume todo el tiempo (1 nodo, gap 2,5–3 %), así que conviene partir con ventanas de 12 h.
- **Qué cambió la ventana de +12.857:** reorganizó 9 aviones a la vez alrededor de MIA (AC-01, 02, 07, 09, 10, 11, 12, 13 y 15). Pasó de 330 a 334 vuelos, con 115 t más. Varias salidas nuevas quedan justo a las 144,0 h, el inicio del día 7, para tomar la demanda de ese día. Es un movimiento coordinado de muchos aviones, que ningún vecindario de uno a tres aviones puede encontrar.
- **Después:** una pasada de ventanas de 18 h (paso de 6 h, 300 s, NoRel 90 s) sobre 102–168 h no encontró más mejoras en 0,9 h. Casi todas terminaron por tiempo, sin óptimo probado.
- **Dónde rinden:** las mejoras aparecen en la segunda mitad de la semana. Ahí hay menos holgura por el cierre de la rotación, y la ventana libera posiciones vacías al final. En R4 pasó lo mismo: 120–144 h y 144–168 h.

### Recomendación

Agregar las ventanas a la búsqueda continua, antes de los vecindarios por avión:

```
python busqueda_por_avion.py --modelo continuo --corrida <solución> --vecindario ventana,avion,par     --ventana-h 12 --ventana-paso 6 --tiempo 240 --norel 60 --mejora-minima 2000
```

En la máquina grande, con más núcleos y memoria, se pueden usar ventanas de 24 h con más tiempo, o correr varias ventanas que no se traslapen en paralelo.


## 13. Ventanas con desplazamiento: agregar o quitar vuelos en medio de la semana (2 de noviembre)

### Motivación

En la instancia completa, la mayoría de los aviones **no** va lleno. En la mejor solución (6.466.083), muchos tienen entre 20 y 76 h libres en la semana y huecos de 13 a 44 h. La ventana de la sección 12 no puede usar esos huecos: como las posiciones se ocupan sin huecos (R2), cada avión mantiene su número de vuelos dentro de la ventana. Solo puede ganarlos o perderlos cuando la ventana toca el final de su rotación.

### Formulación (`--desplazar D`)

En los `--desplazar-aviones` aviones con más horas ociosas dentro de la ventana (3 por defecto), los vuelos posteriores a la ventana mantienen tramo y horario, pero **todos se corren δ posiciones**, con −D ≤ δ ≤ D. Así, la ventana de ese avión puede tener hasta D vuelos más o menos.

Las restricciones del MILP no cambian; solo se agregan (`milp_continuo_transbordos.agregar_desplazamientos`):
- una binaria `z[k, δ]` por avión, con Σ_δ z = 1;
- `y[k, p+δ, e_p] ≥ z[k, δ]` y `t_dep[k, p+δ] = t_p` si `z[k, δ] = 1` (big-M), para cada vuelo p posterior a la ventana;
- `u[k, n+δ+1] ≤ 1 − z[k, δ]`: la rotación termina en la posición n+δ;
- en las posiciones que solo pueden ser de vuelos posteriores, los tramos que no son alternativas quedan con cota 0.

Un avión sin vuelos en la ventana también puede desplazarse, para usar un hueco largo.

**Transbordos (`enumerar_pares_flexibles`).** Cada posición libre lleva sus alternativas (tramo, salida, llegada). Un par entre dos vuelos posteriores a la ventana, o entre uno de ellos y un vuelo fijo, se arma **sin binaria** (tipo `SS`) cuando toda combinación de alternativas que calza en el aeropuerto cumple el TTR. En ese caso basta acotar el flujo por los tramos: `tr ≤ cap · llega_a` y `tr ≤ cap · sale_de`. Con esto, una ventana de 12 h con D = 1 y 3 aviones desplazables tiene ~30 % más binarias que la ventana normal. Sin el tipo `SS` tenía 5 veces más.

### Resultados

- **R4:** partiendo de 849.367, óptimo local de todos los vecindarios anteriores (ventanas de 24, 48 y 84 h, avión y par), sube a **851.847 (+2.481)** con `--desplazar 2 --desplazar-aviones 4`.
  - El movimiento cambió el número de vuelos en la mitad de la semana: AC-10 pasó de 16 a 18 vuelos y AC-15 de 21 a 23 (VCP–GRU–VCP cerca de las 20–24 h), mientras AC-06 dejó un viaje de ida y vuelta GRU–VCP.
  - Ningún vecindario anterior puede hacer ese movimiento.
- **R4, segunda corrida:** con D = 1 y 4 aviones desplazables, sube a 855.700 y después a **861.545**: la ventana de 120–144 h aportó +5.845. Ni avión, ni par, ni las ventanas de 48 h lo mejoran después. Son **+11,8 % sobre la heurística** (770.625).
- **R10:** 2.692.881 resiste las ventanas desplazables, avión y par (1,4 h sin mejoras). Es lo esperable: en R10 los aviones van llenos y no hay huecos que aprovechar.
- **Instancia completa:**
  - con 3 aviones desplazables, la pasada de ventanas de 12 h encontró una mejora en 78–90 h: **6.466.083 → 6.469.241**. La mayoría de las ventanas cierra con óptimo probado en ~90–120 s;
  - con 10 aviones desplazables (todos los que tienen al menos 3 h ociosas en la ventana), la ventana de 36–48 h subió a **6.471.283**. Cada ventana tiene ~40 % más binarias y muchas terminan por tiempo (240 s).

### Más aviones y más vuelos desplazables

- **`--desplazar-aviones 10`** (todos los aviones con al menos 3 h ociosas): en la instancia completa encontró la mejora de 36–48 h, que con 3 aviones no aparecía (**6.469.241 → 6.471.303**).
- **`--desplazar 2`:**
  - en R4, con 120 s por ventana, subió de 861.545 a 867.501, y con la VNS llegó a **868.027**. Es un óptimo local para ventanas de 12, 24, 36 y 48 h, avión y par: **+12,6 % sobre la heurística**.
  - En la instancia completa, en cambio, el modelo de la ventana de 0–12 h explota: 650 mil columnas tras el presolve y una raíz que no termina. En las ventanas del comienzo de la semana, todo lo posterior es sufijo, con 5 alternativas por posición.
  - `--desplazar-presupuesto 60` limita el total de vuelos que se corren (se eligen aviones por horas ociosas hasta llenar el presupuesto). Con eso, D = 2 queda del tamaño de D = 1, y la instancia completa subió a **6.475.055** (ventana de 54–66 h). Después, ni las ventanas cercanas al cambio ni ~1 h de búsqueda por avión (12 aviones, 320 s cada uno) mejoraron. **Resultado final: 6.475.055**, con validador OK, 25/25 frecuencias, 338 vuelos (antes 334), 16 aviones y 11.679 t (antes 11.581).
- **En instancias sin huecos (R10), el desplazamiento solo agranda el modelo.** Desde la solución de la rama B (2.628.734), las ventanas con 10 aviones desplazables no cierran la raíz en 120 s (gap ~10 %), y casi todas terminan sin mejora.
- **`--umbral-aceptar`:** las mejoras de unos pocos USD (+11, +20) son reprogramaciones casi equivalentes, que obligan a reevaluar la región con `--saltar-sin-cambios`. Conviene exigir al menos 50–100 USD.

### Óptimos locales y punto de partida (R4)

| Cadena desde la heurística (770.625) | Óptimo local |
|---|---|
| MILP continuo (806.975) → ventanas desplazables, avión, par, trío y operador | 839.809 |
| red espacio-tiempo (rama B, 845.742) → ventanas → ventanas desplazables (D = 1 y 2) → avión, par | **868.027** |

Desde 839.809, ni liberar 3 de los 4 aviones ni un operador completo saca a la búsqueda de ese óptimo local. **El punto de partida importa:** conviene correr las dos ramas, o varias semillas, en la máquina grande.

### Ventanas primero o aviones primero (R10)

Desde el MILP continuo (2.657.270):

| Cadena | Tiempo | Margen |
|---|---|---|
| avión y par → trío → ventanas → avión y par | ~3,4 h | 2.682.344 (0,8 h) → igual → 2.692.016 → **2.692.881** |
| ventanas de 24 h primero | 1,5 h | **2.688.968** (la primera ventana sola dio +24.386 en 132 s) |
| ventanas primero → VNS con ventanas desplazables, avión y par | 1,5 h + 1,9 h | 2.688.968 → **2.692.881**, el mismo óptimo local que la cadena larga |

A igual tiempo, empezar por las ventanas avanza más rápido. En R4 pasó lo contrario: desde el MILP continuo (806.975), las ventanas solas se estancan en ~808.000, y la búsqueda por avión da el salto (AC-10: +28.286). **Los dos tipos de vecindario se complementan**, y conviene alternarlos en la VNS.

### `--saltar-sin-cambios`

Tras una mejora, la VNS vuelve a recorrer todos los vecindarios, aunque la solución solo cambió en una parte de la semana. Con `--saltar-sin-cambios`, un vecindario que no mejoró se salta mientras no cambien los vuelos de su región:
- en una ventana, los vuelos que salen en la ventana ±`--ventana-h`;
- en un grupo de aviones, los vuelos de esos aviones y de los aviones que les transbordan carga.

Es una regla heurística (*don't look bits*), porque la carga y los transbordos conectan toda la red, pero en la instancia completa ahorra horas de reevaluación.
