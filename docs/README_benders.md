# Descomposición de Benders del modelo integrado

## 1. Para qué sirve

La heurística resuelve el problema en tres pasos en secuencia (red agregada, avión por avión, carga con transbordos), y la información solo fluye hacia adelante: si el paso final descubre que un vuelo valía mucho como conexión, los pasos anteriores nunca se enteran. Por eso no hay garantía de optimalidad ni una cota global.

Los dos códigos de esta carpeta resuelven el **problema completo de forma exacta** con una descomposición de Benders. El problema completo es el modelo integrado en red espacio-tiempo de `modelo_integrado_espacio_tiempo.py`: vuelos de cada avión, carga multitramo y transbordos exactos, con las mismas reglas que `src/validador.py`.

- Un **maestro** decide los vuelos.
- Un **subproblema** rutea la carga con esos vuelos.
- Los **cortes de optimalidad** le devuelven al maestro cuánto vale cada vuelo, incluido su valor como conexión.

Con tiempo suficiente convergen al óptimo de la red con la grilla elegida. Aunque se corten antes, entregan una **cota global válida** y un gap real.

## 2. Los dos códigos

| | `benders_binarias_maestro.py` (A) | `benders_l_shaped.py` (B) |
|---|---|---|
| Maestro | Vuelos (`z`), esperas (`g`), avión en uso (`u`) y, por vuelo, `lc` (lleva carga, ≥ 10 t) y `f` (puede ir lleno, regla de 90 min) | Vuelos (`z`), esperas (`g`) y avión en uso (`u`) |
| Subproblema | **LP**: flujo de carga continuo | **MIP**: el paso final de la heurística completo, con sus binarias de 10 t y 90 min |
| Cortes | Benders clásico (duales del LP), exactos | Subgradiente (relajación LP del subproblema) + **L-shaped enteros** (Laporte y Louveaux, 1993) |
| Ventaja | Cortes fuertes y exactos | Maestro más chico; el subproblema es exactamente el paso 3 |
| Costo | Maestro con 2 binarias más por vuelo | Los cortes L-shaped son débiles: solo excluyen la solución evaluada |

Detalles de cada código:

- **A, mínimo de 10 t:** se escribe `L + s ≥ 10·lc`, con una holgura `s` penalizada en 100.000 USD/t. Así el subproblema siempre es factible. Como esa penalización supera cualquier valor dual, una solución óptima no usa holgura.
- **B, corte L-shaped** en una solución entera ŷ, con `Q_sup` la cota del subproblema entero y `U` una cota de la ganancia por carga para cualquier solución (todo el ingreso posible de la demanda):

  `θ ≤ Q_sup + (U − Q_sup)·[ Σ_{ŷ_i = 1} (1 − y_i) + Σ_{ŷ_i = 0} y_i ]`

## 3. Algoritmo (igual en ambos)

1. **Solución inicial.** Si la corrida de la heurística cae en la grilla elegida, se inyecta como punto de partida, con sus transbordos, y se agrega su corte.
2. **Fase 1: relajación lineal con cortes de subgradiente.** Se resuelve el maestro relajado y, en su solución fraccionaria, el subproblema LP: en A el subproblema mismo, en B su relajación. Se agrega el corte y se repite hasta que el corte ya no corta, o hasta el tiempo o las iteraciones máximas. Estos cortes son válidos para el problema entero y cierran gran parte de la brecha antes de ramificar. Su cota es una cota global válida.
3. **Fase 2: un solo árbol de ramificación** (*branch-and-Benders-cut*). El maestro entero se resuelve con todos los cortes de la fase 1. En cada solución entera:
   - se evalúa el subproblema;
   - si θ sobreestima la ganancia, se agrega el corte como **restricción perezosa** (subgradiente en A; subgradiente + L-shaped en B);
   - la mejor solución real se le devuelve a Gurobi para que la use como incumbente.
4. **Resultado.** Los vuelos de la mejor solución pasan por el paso final de la heurística y por el validador, y se exportan con el mismo formato que las demás corridas, junto con la cota y el gap globales.

## 4. Elegir la grilla de tiempo (`--dt`)

`--dt` es el tiempo entre nodos de la red, en horas: `0.25` = 15 min, `0.5` = 30 min, `0.75` = 45 min, `1`, `2`, etc. Tiene que dividir las 168 h de la semana.

- **Calidad del modelo:** más fino da itinerarios mejores, porque se pierde menos tiempo en tierra por el redondeo.
- **Tamaño del modelo:** crece casi en proporción. Los arcos de vuelo posibles con todos los aviones libres son unos 224 mil con 2 h, 446 mil con 1 h, 891 mil con 30 min y 1,8 M con 15 min. Con `--estimar` se ve el tamaño sin armarlo.
- **Solución inicial:** solo se usa si sus horarios caen en la grilla. La corrida de 2 h sirve para DT = 2, 1, 0,5 y 0,25; para 0,75 no. Las corridas de 1 h, 30 min y 15 min sirven para su propio DT y los divisores de este. Si no calza y todos los aviones son libres, el algoritmo parte sin solución inicial. Con aviones fijos (`--libres`), la corrida debe calzar.

## 5. Cómo ejecutarlo

Desde la raíz del repositorio, con el computador conectado al cargador:

```bash
python benders_binarias_maestro.py --dt 0.5 --estimar
```

```bash
python benders_binarias_maestro.py --dt 0.5 --tiempo 14400
```

```bash
python benders_l_shaped.py --dt 2 --libres AC-06,AC-10 --tiempo 1800
```

| Opción | Por defecto | Uso |
|---|---|---|
| `--dt` | el de la corrida | Horas entre nodos de la red |
| `--libres` | `todos` | `todos` es el problema completo; aviones u operadores para resolver un vecindario con el resto fijo |
| `--corrida` | corrida de 2 h | Solución inicial (y vuelos de los aviones fijos) |
| `--tiempo` | 3600 | Segundos de la fase 2 |
| `--fase1-tiempo` / `--fase1-iter` | 1800 / 500 | Límites de la fase 1 |
| `--sin-fase1` | | Omite la fase 1 |
| `--tiempo-sub` (solo B) | 600 | Segundos máximos de cada subproblema entero |
| `--gap` | 1e-4 | Gap de término |
| `--memoria`, `--hilos` | sin límite, todos | Parámetros de Gurobi |
| `--salida` | `data/results/Benders_<variante>_<libres>_dt<DT>` | Carpeta de salida |
| `--estimar` | | Solo el tamaño |

Ctrl+C (una vez) detiene la fase 2 y exporta la mejor solución.

## 6. Límites conocidos

- **Tamaño.** Con todos los aviones libres, el subproblema incluye la carga en todos los arcos posibles. En cada evaluación es un LP (o MIP) de millones de variables: unos 10 millones con 2 h. El maestro, sin la carga, sí es manejable. En un computador de 8 GB conviene probar primero con `--libres`.
- **Convergencia.** Benders puede necesitar muchas iteraciones. No se implementaron estabilización ni cortes de Pareto (Magnanti y Wong, 1981), que aceleran la convergencia. Con un avión libre ya es demasiado lento en un notebook (sección 7).
- **Cortes L-shaped (B).** Son débiles. Los cortes de subgradiente hacen la mayor parte del trabajo.
- **Óptimo de la red discretizada.** El óptimo es el de la red con bloques de DT horas, no el de tiempo continuo. Con DT más fino se acerca a este último.

## 7. Resultados de las pruebas

Pruebas con DT = 2 h, partiendo de la corrida de 2 h (margen validado 6.310.378 USD), en un notebook i7-8550U de 8 GB.

| Caso | Fase 1 | Fase 2 | Mejor | Cota global | Gap |
|---|---|---|---|---|---|
| A, `--libres ninguno` (control) | 201 cortes, 177 s | óptimo, 52 s | 6.310.378 | 6.310.458 | 0,001 % |
| A, `--libres AC-10` | 13 cortes, 1.044 s; la cota no bajó de 9.798.930 | no alcanzó a terminar | 6.310.378 | 9.798.930 | 55 % |
| B, `--libres AC-10` | 10 cortes, 1.021 s | 600 s: 8 cortes de subgradiente y 9 L-shaped | 6.310.378 | 9.384.460 | 48,7 % |

Lectura:

- **Los códigos son correctos.** Con los vuelos fijos recuperan exactamente el margen validado de la corrida y prueban su optimalidad. Los cortes se verificaron numéricamente en puntos cercanos a la solución inicial: ninguno corta una solución factible.
- **La formulación no es el problema.** La relajación lineal del modelo integrado completo con AC-10 libre vale 6.588.618 USD, solo 4,4 % sobre la solución. Es a esa cota a la que la fase 1 debería llegar.
- **El problema es la velocidad de convergencia.** Cada evaluación del subproblema LP toma 60–80 s cuando el maestro propone vuelos con carga, y los cortes de Kelley sin estabilizar mueven la cota muy poco por iteración. En el control, donde cada LP toma segundos, la fase 1 necesitó unos 110 cortes para converger. Con 1 avión libre eso significaría horas solo en la fase 1, más que resolver de una vez la relajación del modelo completo (967 s con barrera).
- **Corrección aplicada durante las pruebas.** El LP del subproblema usaba la base de la evaluación anterior, y con eso Gurobi se salta el presolve. En un punto lejano, que en la iteración 1 era trivial (sin carga), tardaba más de 300 s en lugar de 1 s. Ahora usa `LPWarmStart = 2`: aplica el presolve y después la base.

**Mejoras que faltan para que sea práctico**, en orden de impacto esperado:

1. **Estabilización *in-out*** (Ben-Ameur y Neto, 2007): evaluar el subproblema en un punto entre la solución del maestro y la solución inicial, no en la del maestro. Es la que suele reducir más las iteraciones.
2. **Cortes de Pareto** (Magnanti y Wong, 1981).
3. **Cortes iniciales de varias soluciones heurísticas** (otras corridas o vecindarios) antes de la fase 1.
