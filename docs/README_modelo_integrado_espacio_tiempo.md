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
