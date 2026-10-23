# Modelo integrado en red espacio-tiempo

## 1. Qué es

`modelo_integrado_espacio_tiempo.py` formula en **un solo MILP** las tres etapas de la heurística:

- los vuelos de cada avión (pasos 1 y 2);
- la carga multitramo en el mismo avión (*through*);
- los transbordos entre aviones (paso final, `src/asignacion_carga_multitramos.py`).

Usa la misma modelación que la heurística: la semana se divide en bloques de `DT` horas y cada avión tiene su propia red cíclica. Cada arco de vuelo dura `ceil((horas de bloque + TAT) / DT)` bloques, y ningún vuelo cruza el corte domingo-lunes.

El parámetro `--libres` dice qué aviones decide el modelo. Los demás quedan fijos con los vuelos de la corrida de partida, pero su carga y sus transbordos se vuelven a optimizar:

| `--libres` | Qué resuelve |
|---|---|
| `todos` | El modelo integrado completo |
| `AC-06,AC-10` | Solo esos aviones |
| `BRA` (o `AND`, `PAC`, `SUR`) | Todos los aviones de un operador; se pueden combinar con comas |
| `ninguno` | Vuelos fijos: solo carga y transbordos. Es un control: debe dar lo mismo que el paso final de la heurística |

La solución de la corrida (por defecto, la de 2 h) se inyecta completa como MIP start, **con sus transbordos**.

## 2. Formulación

**Arcos de vuelo:**

- Avión libre: todo tramo de `Ek[k]` en todo bloque `t` tal que `t + dur_e ≤ T`.
- Avión fijo: sus vuelos de la corrida.
- Para cada arco se conocen la hora real de aterrizaje (`t·DT + horas de bloque`), el día de salida y el bloque desde el que la carga bajada se puede transbordar: `ceil((aterrizaje + TTR_H) / DT)`.

**Variables:**

- Aviones:
  - `z` (binaria): el avión libre vuela el arco.
  - `g`: espera en tierra.
  - `u` (binaria): avión en uso.
- Carga de cada vuelo:
  - `L` (semicontinua): 0 o entre 10 t y el payload.
  - `f` (binaria): el vuelo puede ir lleno, es decir, sobre el 95 % del payload.
- Carga por **destino**:
  - `x`: en vuelo.
  - `entrega`: se entrega al aterrizar.
  - `bajada`: se baja al depósito del aeropuerto para transbordo.
  - `h`: sigue a bordo mientras el avión espera.
  - `subida`: se sube desde el depósito.
- `b`: embarque inicial por commodity (origen, destino, día).
- Depósito, llevado **por avión que baja la carga**:
  - inventario de lo que bajó cada avión;
  - lo que sale de ese inventario hacia otro avión;
  - total transbordado en cada bloque.

**Objetivo:** ingresos (tarifa × toneladas embarcadas) − costo de los vuelos − handling del embarque − handling de cada transbordo, cobrado al operador que recibe. Es lo mismo que calcula el validador.

**Restricciones:**

| Familia | Qué impone |
|---|---|
| Red de cada avión libre | Conservación de flujo cíclica. Exactamente un cruce del corte semanal si el avión se usa, lo que da una rotación cerrada de una semana |
| Mantenimiento | El avión está en tierra en su aeropuerto en todos los bloques de la ventana (igual que el paso 2) |
| Balance de carga | En cada (avión, aeropuerto, bloque) y destino: llega − entrega − bajada + a bordo anterior + subida = sale − embarque + a bordo siguiente |
| Descarga | Entrega + bajada ≤ lo que trae ese vuelo para ese destino |
| Carga a bordo en tierra | Solo donde el avión está |
| Carga del vuelo | `L = Σ x`. `L` es 0 o al menos 10 t, a lo más el 95 % del payload salvo que `f = 1`, y solo en vuelos que existen. Embarques ≤ `L` |
| Regla de 90 min | Un vuelo que aterriza fuera de escala técnica y el siguiente vuelo del mismo avión, si sale antes de 90 min, no pueden ir ambos llenos (también en el cierre de la semana) |
| Depósito | Inventario por avión que baja: anterior + bajadas disponibles − salidas = siguiente. Lo que sale de los inventarios = total transbordado = lo que se sube |
| Sin re-embarque propio | Para cada avión, lo que sale de su inventario más lo que sube ≤ total transbordado |
| Frecuencias mínimas y demanda diaria | Igual que en la heurística |

Detalles de la formulación:

- **Carga por destino.** Es exacta. La red es acíclica en el tiempo, así que todo flujo por destino se descompone en caminos que van de un embarque a una entrega del mismo commodity.
- **Sin re-embarque propio.** Garantiza que, en cada aeropuerto, destino y bloque, el depósito se pueda repartir entre aviones distintos del que bajó cada tonelada (es la condición de Hall de un problema de transporte sin diagonal). Así se impone de forma exacta que un transbordo cambia de avión, sin una variable por cada par de aviones.
- **Descarga.** Impide "bajar" carga recién subida del depósito sin haberla volado. Sin ella, la carga podía cambiar de avión o de día sin un vuelo real.

## 3. Verificación

| Prueba | Resultado |
|---|---|
| `--libres ninguno`, start de la corrida de 2 h | Factible; objetivo **6.310.378 USD**, igual al margen validado |
| `--libres ninguno`, resuelto al óptimo | **6.310.378 USD** (cota 6.310.381), lo mismo que el paso final de la heurística con esos vuelos |
| `--libres ninguno` sin transbordos | 6.138.952 USD, lo mismo que el paso final sin transbordos |

Estas pruebas muestran que la parte de carga y transbordos es equivalente a la heurística. En el camino se corrigieron dos diferencias que permitía una primera versión y que inflaban el objetivo en unos 66 mil USD: un avión que subía carga que él mismo había bajado, y carga subida desde el depósito que se volvía a bajar sin volar.

## 4. Resultados en este notebook (i7-8550U, 7,8 GB de RAM, Gurobi 13.0.2, DT = 2 h)

| `--libres` | Tamaño | Resultado |
|---|---|---|
| `ninguno` | 348 mil variables, 281 mil restricciones | Óptimo en 5 s; 6.310.378 USD |
| `AC-06,AC-10` (BRA), 10 min | 1,6 M variables (49 mil binarias), 1,1 M restricciones | NoRel mejoró a **6.310.808 USD (+429)**: validador OK, 342 vuelos, 25 de 25 frecuencias. El LP de la raíz se detuvo por memoria (más de 5 GB) |
| `todos`, 3 intentos de 10–15 min | **11,9 M variables** (448 mil binarias, 224 mil semicontinuas), **7,2 M restricciones**, 39 M no ceros | Se arma en 8 s y el start es factible (6.310.378). **Gurobi se detiene por memoria a los 11 s**, con 5 GB, con 6,5 GB y 1 hilo, y con 6,5 GB, 1 hilo y sin presolve. No alcanza a buscar |

Una de esas corridas quedó guardada en `data/results/MILP_integrado_TSN_todos_limite_memoria/`, que contiene la solución de partida exportada.

El modelo con todos los aviones libres está bien formulado y acepta el start, pero en este notebook no hay memoria para que Gurobi lo explore. Para intentarlo se necesita un computador con bastante más RAM (por ejemplo, 32 GB o más); en ese caso:

```bash
python modelo_integrado_espacio_tiempo.py --libres todos --tiempo 14400 --memoria 28
```

En este notebook, lo práctico es resolverlo por partes, liberando uno o dos aviones a la vez.

## 5. Cómo ejecutarlo

Desde la raíz del repositorio. Requiere `gurobipy` con licencia, `pandas`, `numpy`, `scipy` y `pyyaml`.

```bash
python modelo_integrado_espacio_tiempo.py --libres AC-06,AC-10 --tiempo 1800
```

| Opción | Por defecto | Uso |
|---|---|---|
| `--libres` | `todos` | Aviones que decide el modelo (sección 1) |
| `--estimar` | | Solo muestra el tamaño del modelo, sin armarlo |
| `--solo-verificar` | | Arma el modelo y revisa el start, sin optimizar |
| `--sin-transbordos` | | Modelo sin transbordos entre aviones (sección 7) |
| `--relajacion` | | Solo resuelve la relajación lineal con barrera y guarda su cota en `relajacion.json` |
| `--corrida` | corrida de 2 h | Corrida de la heurística: start y vuelos de los aviones fijos (debe estar en la grilla de `DT`) |
| `--dt` / `--ttr` | los de la corrida | Horas por bloque y `TTR_H` |
| `--tiempo` | 3600 | Segundos de Gurobi |
| `--norel` | predeterminado de Gurobi | Segundos de la heurística NoRel antes de la raíz |
| `--memoria` | 0 (sin límite) | GB máximos para Gurobi; si los supera, se detiene y exporta la mejor solución |
| `--hilos`, `--presolve`, `--mipfocus`, `--gap` | todos, -1, 1, 0,01 | Parámetros de Gurobi |
| `--salida` | `data/results/MILP_integrado_TSN_<libres>` | Carpeta de salida |

La salida tiene los mismos archivos y columnas que la heurística, así que se puede ver en el visualizador. Al final, el script vuelve a calcular la carga de los vuelos encontrados con el paso final de la heurística y la valida con `src/validador.py`. Así el margen reportado es el validado y se compara directamente con el de la corrida.

**Corridas largas:** el notebook debe estar conectado al cargador y con la suspensión desactivada.

## 6. Comparación con `gurobi_model.py`

| | `gurobi_model.py` (tiempo continuo) | Este modelo (red espacio-tiempo) |
|---|---|---|
| Tiempo | Continuo: aprovecha horarios fuera de la grilla | Bloques de `DT` |
| Transbordos | Fuera del modelo (paso final) | Dentro del modelo, exactos |
| Tamaño con todos los aviones libres | 262 mil variables, 84 mil restricciones | 11,9 M variables, 7,2 M restricciones |
| Mejor resultado en este notebook | 6.376.959 USD (+66.581) en 8 min | 6.310.808 USD (+429) con 2 aviones libres en 10 min |

## 7. Modelo sin transbordos (`--sin-transbordos`)

Arma la misma red, pero la carga solo baja en su destino: no hay depósito de transbordos y la carga se queda a bordo del avión que la embarcó. Los transbordos se agregan después, con el paso final de la heurística sobre los vuelos encontrados, y el margen que se reporta es el validado.

- **Ventaja:** con todos los aviones libres y DT = 2 h, el modelo baja de 12,0 M a 6,4 M variables (−46 %).
- **Costo:** no optimiza el valor de los vuelos como conexiones, y su cota no es una cota del problema con transbordos. En la corrida de 12,5 h del MILP continuo, que tampoco tiene transbordos, el 38 % de la mejora sin transbordos (+79 mil) se perdió al agregarlos (+49 mil).

Pruebas (DT = 2 h, start de la corrida de 2 h):

| Caso | Resultado |
|---|---|
| `--libres ninguno --sin-transbordos` (control) | Óptimo 6.138.952 USD = carga sin transbordos de la heurística; con el paso final, 6.310.378 (igual a la corrida) |
| `--libres todos --sin-transbordos --relajacion` con barrera | Se queda sin memoria: 16,9 GB a la iteración 16; Windows pagina y cada iteración pasa de 3 s a más de 200 s |
| `--libres todos --sin-transbordos --relajacion --metodo-lp 6` (PDHG) | 5,4 GB estables. A las 3 h: entre **7.283.556 y 7.286.250 USD** (diferencia relativa 3,6·10⁻⁴) |

Para comparar, la relajación de la raíz del MILP continuo sin transbordos (`warm_start_milp.py`) es 8.257.861, un 34,5 % sobre el start. La de la red es ~7,285 M, un 18,7 % sobre el start. La red es bastante más ajustada, aunque con todos los aviones libres no tanto como con un solo avión: con AC-10 libre y transbordos, la relajación quedó 4,4 % sobre la solución.

PDHG (`--metodo-lp 6`) es un método de primer orden: usa mucha menos memoria que la barrera, pero entrega un valor aproximado. Con AC-10 libre llegó a 6.588.625–6.589.899, contra 6.588.618 exacto con barrera.

## 8. Desigualdades de enlace fuerte (`--enlace-fuerte`)

Con solo `L ≤ P·z`, la relajación lineal paga una fracción de avión exactamente proporcional a la carga, como si todos los vuelos fueran perfectamente llenos. Pero el 74 % de las demandas diarias (origen, destino, día) son menores que un avión: la mediana es 20,6 t y el payload, 50–54 t. `--enlace-fuerte` agrega, para cada vuelo libre `a` y cada demanda `q` que puede embarcar en él:

  `B(a,q) ≤ min(D_q, P)·z(a)`

No corta ninguna solución entera: la solución de la heurística las cumple todas. Lo que hace es eliminar soluciones fraccionarias del tipo "llevo una demanda entera de 20 t con 0,4 avión".

| Caso (DT = 2 h) | Relajación sin enlace fuerte | Con enlace fuerte | Brecha a la solución conocida |
|---|---|---|---|
| AC-10 libre, con transbordos (barrera) | 6.588.618 | **6.461.469** | 4,4 % → **2,4 %** (se cierra el 46 %) |
| Todos libres, sin transbordos (PDHG) | ~7.285.000 | **~7.000.000** (6.997.779–7.012.515) | 18,7 % → **~14 %** sobre el start sin transbordos (se cierra ~25 %) |

Con todos los aviones libres son 567 mil filas más: 6,4 M de variables y 3,1 M de restricciones.

MILP con AC-10 libre, transbordos y enlace fuerte (`--tiempo 1800 --gap 1e-4 --mipfocus 2`): la relajación de la raíz por sí sola tomó 1.772 s, con 177 mil iteraciones. La corrida se cortó al terminarla, sin cortes ni ramificación: solución 6.310.378 (la de la heurística), cota 6.460.448, gap 2,38 %. En la raíz quedan solo 856 binarias fraccionarias. Para saber si esa brecha es real hace falta una corrida de al menos 1–2 h.

## 9. Grilla de 1 hora (`--dt 1`)

Con todos los aviones libres, DT = 1 h son 12,9 M de variables sin transbordos y 24,1 M con transbordos. No cabe en un notebook de 8 GB.

Prueba con **AC-10 libre**, transbordos, enlace fuerte, start de la corrida de 2 h, `--tiempo 3600 --norel 1800`. Son 1,94 M de variables y 1,48 M de restricciones:

- NoRel encontró dos mejoras a los ~13 y ~15 min: 6.311.534 y 6.316.041.
- **Margen validado: 6.316.063 USD (+5.685)**. Validador OK, 344 vuelos, 25 de 25 frecuencias, 37 transbordos. Con DT = 2 h, dos aviones libres habían dado solo +429.
- La relajación de la raíz no alcanzó a terminar en los 30 min restantes: la factorización pide 3,3 GB. La cota informada, 8,35 M, es la de NoRel y no sirve.
