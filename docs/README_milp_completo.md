# MILP completo con warm start desde la heurística

## 1. Qué es

`gurobi_model.py` es el MILP integrado (posición-indexado, tiempo continuo) y ahora representa el mismo problema que resuelve la heurística de red espacio-tiempo (`src/`): tiene las mismas reglas que revisa el validador independiente (`src/validador.py`). La única excepción son los transbordos entre aviones, que se agregan en un paso final (sección 4). `warm_start_milp.py` lo resuelve con Gurobi partiendo de la solución de una corrida de la heurística. Por defecto usa la corrida de bloques de 2 h, que es la de mayor margen (6.310.307 USD).

La diferencia con la heurística es la formulación, no las reglas:

- el tiempo es continuo (`t_dep`, `t_arr`) en lugar de bloques de `DT` horas;
- cada avión recorre posiciones `s = 1..S_max` y elige un tramo por posición (`y[k,s,e]`);
- todo se decide en un solo modelo, en vez de los pasos 1 y 2 más la asignación de carga.

## 2. Qué cambió respecto de la versión anterior de `gurobi_model.py`

| Regla | Versión anterior | Versión actual |
|---|---|---|
| Datos | `estructuras_datos.py` (borrado en el Hito 7) | `cargar_datos()` en el mismo archivo, desde `data/raw data/` (mismos archivos que la heurística) |
| TAT | 2 h fijo | 70 min, 50 min si el vuelo llega a PTY/SID y 90 min si el avión llega y sale lleno (R11) |
| Rotación semanal cerrada | No | En espacio (R4c) y en tiempo (R12) |
| Mantenimiento | No | En tierra, en su aeropuerto, durante toda la ventana (R14) |
| Frecuencias mínimas | No | 25 pares origen-destino (R15) |
| Mínimo 10 t por vuelo cargado | No | R7 |
| Demanda diaria | Solo exigía el aeropuerto de origen | Embarque en el origen y el día de la demanda (R6, R23, R24) |
| Costo de vuelo | 5 USD/km (valor de prueba) | (combustible + costo por hora del operador) × horas de bloque + tasa de aterrizaje |
| Handling | No | 39 a 52 USD/t según el operador, por tonelada embarcada |
| Transbordos entre aviones | No | No, se agregan en un paso final (sección 4) |
| Variables `p[k,c]` | 18.240 binarias que no aparecían en ninguna restricción | Eliminadas |
| Posiciones por avión | 111 | 32 por defecto (`--smax`); la heurística usa como máximo 31 |

También se reformularon, de manera equivalente, algunas partes para que el modelo quepa en memoria y su relajación sea más ajustada:

- **Carga por destino.** La carga a bordo (`x`), la entregada (`a`) y la que sigue en el mismo avión (`w`) se agregan por destino: 17 flujos por posición en vez de 225. El embarque `b` sigue por commodity (origen, destino, día), porque la demanda y la tarifa dependen de él. Es exacto: sin transbordos, cualquier flujo por destino se descompone en caminos que van de un embarque a una entrega de ese mismo commodity.
- **Continuidad por aeropuerto (R4).** La continuidad se escribe por aeropuerto y no por tramo: tiene menos filas y es más ajustada.
- **Cierre del ciclo como balance (R4c).** En la semana, cada aeropuerto recibe tantos vuelos del avión como despacha. Junto con R4, eso obliga a que el último vuelo termine donde salió el primero, sin binarias nuevas.
- **M grande ajustada.** Se usa 169,5 h en R11 y 1,5 h en R12, en vez de 1000.
- **Desigualdad válida R13.** Por avión, los tramos más sus TAT mínimos caben en 168 h.

| Tamaño (19 aviones) | Versión anterior | Versión actual |
|---|---:|---:|
| Variables enteras | 325.710 | 94.739 |
| Variables continuas | 1.902.543 | 167.505 |
| Restricciones | 2.220.404 | 83.787 |
| Relajación lineal de la raíz | no se alcanzó a resolver | 8,26 M USD en 73 s (~250 MB) |

## 3. Formulación

**Conjuntos:**

- `K`: aviones.
- `E_k`: tramos que puede volar el avión k (derechos de tráfico y hasta 9 h).
- `S_k = {1..S_max}`: posiciones.
- `Q`: commodities (origen, destino, día) con demanda.
- `D`: destinos.
- `DIAS = {1..7}`.

**Parámetros:**

- `tau_e`: horas de bloque.
- `TAT_e`: 70 min, o 50 si el tramo llega a escala técnica.
- `Δ`: 20 min extra si el avión llega y sale lleno.
- `c_ke`: costo del vuelo.
- `h_k`: handling.
- `P_k`: payload.
- `seguro_k = 0,95·P_k − 0,01`: carga máxima que no cuenta como "lleno".
- `D_q`, `r_q`: demanda y tarifa.
- `f_od`: frecuencia mínima.
- `(am_k, ini_k, fin_k)`: aeropuerto y ventana de mantenimiento.

**Variables:**

- Binarias:
  - `y[k,s,e]`: tramo e en la posición s.
  - `u[k,s]`: la posición se usa.
  - `l[k,s]`: última posición usada.
  - `dia[k,s,d]`: día de salida.
  - `lc[k,s]`: el vuelo lleva carga.
  - `ll[k,s]`: el vuelo puede ir lleno.
  - `m0[k]`, `m[k,s]`: dónde cae el mantenimiento.
- Continuas:
  - `t_dep`, `t_arr`: horas de salida y llegada.
  - `g`, `gc`: TAT lleno-lleno entre vuelos y en el cierre.
  - `b[q,k,s]`: embarque inicial.
  - `x[d,k,s]`, `a[d,k,s]`, `w[d,k,s]`: carga a bordo, entregada y que sigue, por destino.
  - `s_q`: toneladas servidas.

**Objetivo:** `max Σ_q 1000·r_q·s_q − Σ c_ke·y[k,s,e] − Σ h_k·b[q,k,s]`

**Restricciones de aviones:**

| Restricción | Qué impone |
|---|---|
| R1, R2, R3a/b | Un tramo por posición usada, posiciones sin huecos y marca de la última posición |
| R4 | `Σ_{e llega a a} y[k,s,e] ≥ Σ_{e sale de a} y[k,s+1,e]`: el vuelo s+1 sale de donde llegó el s |
| R4c | `Σ_s Σ_{e llega a a} y = Σ_s Σ_{e sale de a} y`: rotación cerrada en espacio |
| R5 | `t_arr = t_dep + Σ tau_e·y` |
| R6 | `Σ_d dia = u`, `24(d−1) ≤ t_dep ≤ 24d − ε` para el día elegido; R6b: `t_arr ≤ 168·u` |
| R11 | `t_dep[s+1] ≥ t_arr[s] + Σ TAT_e·y[s] + Δ·g[s] − M(1−u[s+1])`, con `g[s] ≥ ll[s] + ll[s+1] − 1 − (llega a escala técnica)` |
| R12 | `t_dep[1] + 168 ≥ t_arr[s] + Σ TAT_e·y[s] + Δ·gc[s] − M(1−l[s])`: cierre en el tiempo |
| R13 | `Σ_s Σ_e (tau_e + TAT_e)·y ≤ 168` (desigualdad válida) |
| R14 | `m0 + Σ_s m ≥ u[1]`. Si `m0 = 1`, sale del aeropuerto de mantenimiento después de `fin`. Si `m[s] = 1`, el vuelo s llega a ese aeropuerto antes de `ini` y el s+1 sale después de `fin` (o s es el último vuelo) |
| R15 | `Σ_{k,s} y[k,s,(o,d)] ≥ f_od` |

**Restricciones de carga:**

| Restricción | Qué impone |
|---|---|
| R7 | `Σ_d x ≥ 10·lc`: un vuelo cargado lleva al menos 10 t |
| R8 | `Σ_d x ≤ seguro_k·lc + (P_k − seguro_k)·ll`, con `ll ≤ lc ≤ u` |
| R20, R21 | `x[d,s] = Σ_{q con destino d} b[q,s] + w[d,s−1]` y `x[d,s] = a[d,s] + w[d,s]` |
| R23 | `Σ_{q con origen o} b ≤ P_k·Σ_{e sale de o} y`: se embarca solo en el origen |
| R24 | `Σ_{q del día d} b ≤ P_k·dia[d]`: se embarca solo el día de la demanda |
| R25 | `a[d,s] ≤ P_k·Σ_{e llega a d} y`: se entrega solo en el destino |
| R26 | `s_q = Σ b ≤ D_q`; por destino, lo entregado = lo servido |

## 4. Transbordos entre aviones

El MILP no incluye transbordos. En tiempo continuo, un transbordo necesita saber si dos vuelos de aviones distintos coinciden en el mismo aeropuerto y con al menos `TTR_H` de diferencia. Eso exige una binaria por cada par de posiciones de aviones distintos (unos 350 mil con 32 posiciones) y un flujo por destino en cada par (unos 6 millones de variables). El modelo resultante no se puede resolver.

Por eso se hace lo mismo que en la heurística, donde los transbordos también se deciden después de fijar los vuelos. Al terminar Gurobi, `warm_start_milp.py` toma los vuelos del MILP y resuelve la carga con `src/asignacion_carga_multitramos.py` (through y transbordos, con el `TTR_H` de la corrida). Luego valida el resultado con `src/validador.py`.

Para que la comparación sea exacta, el script aplica ese mismo paso final, con gap 1e-6, a los vuelos de la heurística y a los del MILP. Con el gap por defecto del submodelo (0,1 %), dos resoluciones de los mismos vuelos pueden diferir en unos miles de dólares.

| Itinerario de la corrida de 2 h | Margen validado (USD) |
|---|---:|
| Reportado por la heurística (con transbordos) | 6.310.307 |
| Mismos vuelos, carga sin transbordos: **start del MILP** | **6.138.952** |
| Mismos vuelos, paso final con transbordos (gap 1e-6): **referencia de comparación** | **6.310.378** |

## 5. Cómo ejecutarlo

Requisitos: los mismos de la heurística (`gurobipy` con licencia, `pandas`, `numpy`, `scipy`, `pyyaml`). Desde la raíz del repositorio:

```bash
python warm_start_milp.py
```

Con eso se toma la corrida `data/results/Ejecucion_con_intervalos_de_2_horas`, Gurobi corre 1 hora y los resultados quedan en `data/results/MILP_completo_Ejecucion_con_intervalos_de_2_horas/`.

Opciones:

| Opción | Por defecto | Uso |
|---|---|---|
| `--tiempo` | 3600 | Segundos de Gurobi (por ejemplo, `--tiempo 14400` para 4 horas) |
| `--solo-verificar` | | Arma el modelo y revisa el start, sin optimizar (≈1 minuto) |
| `--corrida` | corrida de 2 h | Otra carpeta de resultados como start; también sirve la salida de un MILP anterior, para encadenar corridas |
| `--smax` | 32 | Posiciones por avión |
| `--ttr` | el de la corrida (2 h) | `TTR_H` de los transbordos del paso final |
| `--memoria` | 5 | GB máximos para Gurobi; si los supera, se detiene y exporta la mejor solución |
| `--norel` | 80 % de `--tiempo` | Segundos de la heurística NoRel de Gurobi antes de la raíz, que mejora el start sin resolver el LP; `0` la desactiva |
| `--hilos` | todos | Hilos de Gurobi |
| `--mipfocus` | 1 | 1 = priorizar mejores soluciones |
| `--gap` | 0,01 | Gap de término |
| `--sin-start` | | Resolver sin warm start, para comparar |
| `--salida` | | Otra carpeta de salida |

La corrida se puede cortar con **Ctrl+C**: Gurobi se detiene y el script exporta la mejor solución encontrada hasta ese momento. Hay que presionarlo una sola vez.

Salida (mismos archivos y columnas que la heurística, así que se puede ver con el visualizador desde `main.py` → "Visualizar Datos"):

- `itinerario_vuelos.csv`, `carga_transportada.csv`, `transferencias_carga.csv`: vuelos, carga por commodity y transbordos después del paso final.
- `indicadores_kpi.json`: KPIs validados y un bloque `milp` con el estado, el objetivo, la cota, el gap y el tiempo de Gurobi, el margen del start y el de la heurística.
- `gurobi.log`, `modelo_resuelto.pkl`, `metadata.json`, `resultados.json`.

Al terminar, la consola muestra el margen validado de la heurística y el del MILP, ambos con el mismo paso final con transbordos, y la diferencia entre ellos.

## 6. Verificación

- **Start de la corrida de 2 h.** Cumple todas las restricciones, cotas e integralidad del modelo. Su objetivo en el MILP (6.138.952 USD) coincide al dólar con el margen que calcula el validador independiente, y Gurobi lo carga: `Loaded user MIP start with objective 6.13895e+06`.
- **Pruebas negativas.** Si a ese start se le adelanta un vuelo, se le cambia el día de salida, se le quita el mantenimiento o se deja un vuelo con 5 t, la revisión detecta R11, R6/R24, R14 y R7, respectivamente.
- **Soluciones propias del MILP.** Se armó una instancia chica con los 2 aviones de BRA, la demanda y las frecuencias que dejan los demás aviones de la corrida de 2 h, sin start. Gurobi encontró 11 soluciones distintas, con márgenes entre −830 mil y +262 mil USD y entre 22 y 24 vuelos. La carga por destino de cada una se descompuso por commodity y se pasó por el validador independiente, sin re-optimizarla. Las 11 pasaron con 0 violaciones, y en todas el margen del validador coincidió al dólar con el objetivo del MILP.

## 7. Resultados de la prueba en este notebook (i7-8550U, 7,8 GB de RAM, Gurobi 13.0.2)

| Prueba | Resultado |
|---|---|
| Armado del modelo completo | 8 s; el proceso usa unos 650 MB de memoria |
| Relajación lineal de la raíz | 73 s; 8.257.861 USD, un gap inicial de 34,5 % sobre el start |
| Con el start de 2 h, sin NoRel, 2 y 10 minutos de Gurobi | No apareció una solución mejor; Gurobi siguió en el nodo raíz (cortes y heurísticas). Con esos vuelos, el paso final da 6.310.378 USD, igual que la referencia (diferencia 0); validador OK y 25 de 25 frecuencias |
| Sin start, 10 minutos | Ninguna solución factible, que es lo mismo que pasaba con el MILP anterior |
| Start de 2 h con 17 aviones fijos y los 2 de BRA libres, 5 minutos | Sin cambios respecto del start (gap 19,4 %) |
| **Start de 2 h, 8 minutos, de ellos 300 s de NoRel** | **Mejora el start desde los primeros segundos. Objetivo del MILP: 6.138.952 → 6.236.508 USD. Con el paso final: 6.376.959 USD contra 6.310.378 de la heurística con el mismo paso final (+66.581, +1,1 %). Validador OK, 343 vuelos, 25 de 25 frecuencias** |

La solución de 8 minutos quedó guardada en `data/results/MILP_completo_warm_start_2h_8min/` y se puede ver en el visualizador. Respecto de la heurística, cambia la secuencia de tramos de 16 de los 18 aviones usados. Además, 289 de sus 343 salidas quedan fuera de la grilla de 2 h, y la espera mediana sobre el TAT mínimo baja de 1,16 h a 0,33 h.

Las mejoras vinieron de NoRel. En estas pruebas, el árbol de ramificación no avanzó más allá de la raíz. Por eso el script usa NoRel por defecto durante el 80 % del tiempo. Con más tiempo (`--tiempo 14400` o más) NoRel puede seguir mejorando: en la prueba seguía encontrando soluciones mejores al final de sus 300 s. **El notebook debe estar conectado al cargador y con la suspensión desactivada:** en una de las pruebas, Windows lo suspendió 34 minutos por batería crítica y Gurobi quedó detenido todo ese tiempo.

## 8. Limitaciones

1. **Posiciones.** Con `S_max = 32` el modelo no admite más de 32 vuelos por avión a la semana. La heurística usa como máximo 31; si el MILP llena las 32 posiciones de algún avión, conviene repetir con `--smax` mayor.
2. **Transbordos.** Quedan fuera del MILP (sección 4). Por eso, la cota de Gurobi es una cota del problema sin transbordos, no del problema completo. Además, como el MILP no valora los transbordos, una mejora sin transbordos no siempre se traslada completa al margen final. En las pruebas, +97.636 USD sin transbordos se convirtieron en +66.581 con transbordos (8 minutos), y +39.656 en +2.595 (90 s).
3. **Cota en corridas cortas.** Si `--tiempo` es tan corto que Gurobi no alcanza a resolver la raíz después de NoRel (~75 s), la cota que aparece es la de NoRel, que es mucho más débil (por ejemplo, 16,7 M USD). La cota de la raíz es 8,26 M USD.
4. **Supuestos heredados de la heurística.** "Lleno" significa 95 % del payload, `TTR_H` es provisional, `interchange_airports.csv` se interpreta como soporte operacional, la demanda es diaria según el día de salida y los vuelos no cruzan el corte domingo-lunes.
5. **Dificultad.** La relajación sigue siendo débil por los tiempos con M grande (gap de ~32 % contra la cota de la raíz), así que no se espera demostrar optimalidad. El aporte está en mejorar la solución de la heurística, y eso sí se logró.
