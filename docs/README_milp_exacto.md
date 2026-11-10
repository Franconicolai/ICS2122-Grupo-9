# MILP exacto

Este documento describe la versión del MILP en tiempo continuo que representa **exactamente** el problema que revisa
el validador (`src/validador.py`), incluidos los interchanges y los transbordos. Si el modelo es exacto:

- **toda solución del MILP es factible**: se puede validar tal cual, sin volver a resolver la carga, y su margen
  validado coincide con el objetivo del modelo;
- **la cota de Gurobi es una cota real** del problema (de toda la instancia si todos los aviones están libres, o del
  vecindario si hay aviones o vuelos fijos).

## Cómo se usa

```
# modelo completo exacto (todos los aviones libres)
python milp_continuo_transbordos.py --exacto --smax cota --corrida <carpeta> --tiempo 3600
# solo el tamaño, sin armarlo
python milp_continuo_transbordos.py --exacto --smax cota --corrida <carpeta> --estimar
# búsqueda por vecindarios con el MILP exacto en cada vecindario
python busqueda_por_avion.py --modelo continuo --exacto --corrida <carpeta> ...
```

`--exacto` implica `--interchange`. El borde de la semana es el flexible por defecto (`--borde base` para el estricto).

## Qué agrega el modo exacto

Con interchanges, la versión anterior usaba una relajación: el costo de la filial más barata con derechos en cada
tramo, sin revisar dónde se cambia de filial. El modo exacto agrega la filial de cada vuelo como variable
(`gurobi_model.py`, restricciones R17):

| Regla del validador | Formulación |
|---|---|
| Una filial por vuelo | `f[k,s,o]` binaria, `Σ_o f[k,s,o] = u[k,s]` |
| Derechos de tráfico de la filial en los dos países del tramo | `Σ_{e sin derechos para o} y[k,s,e] ≤ 1 − f[k,s,o]` |
| Costo por hora de la filial que opera | horas `hb[k,s,o] ≤ τ_max·f[k,s,o]`, `Σ_o hb[k,s,o] = Σ_e τ_e·y[k,s,e]`; costo `Σ ex_fuel_o·hb` |
| Cambio de filial entre dos vuelos seguidos solo donde ambas están habilitadas | `f[k,s,o] + f[k,s+1,o'] ≤ 1 + Σ_{e llega a un aeropuerto que habilita o y o'} y[k,s,e]` |
| Borde flexible: vuelve a la filial base en tierra, en el aeropuerto del borde | el primer vuelo sale, y el último llega, de un aeropuerto que habilita la filial base y la del vuelo |
| Borde base (opción) | el primer y el último vuelo los opera la filial base |
| Handling de la filial del vuelo que embarca (carga inicial y transbordos recibidos) | `Bo[k,s,o] ≤ P_k·f[k,s,o]`, `Σ_o Bo[k,s,o] = Σ_q b[q,k,s] + Σ_d tin[d,k,s]`; costo `Σ h_o·Bo` |
| Transbordo: las filiales de ambos vuelos habilitadas en el aeropuerto | `c[p,a] ≤ 1 − Σ_{o no habilitada en a} f[k,s,o]`, para el vuelo que entrega y el que recibe |

Además:

- **Posiciones por avión** (`--smax cota`, `gurobi_model.cota_posiciones`). Con 32 posiciones, el modelo podía
  excluir rotaciones factibles: la mejor solución ya usa 30. La cota se demuestra así: con n vuelos, la rotación dura
  al menos n·min(duración + TAT), y la ventana de mantenimiento cae dentro de una estadía en tierra que dura al menos
  fin − ini en lugar de su TAT. En la instancia completa da 85 a 88 vuelos por avión.
- **Distribución equilibrada de frecuencias** (opción `DIST_FRECUENCIAS=1`, apagada por defecto, igual que en el
  validador): a lo más ⌈f/7⌉ salidas por día de cada par comprometido (R16).
- **Extracción directa** (`milp_continuo_transbordos.extraer_solucion`): arma la solución del MILP en el formato del
  validador. El MILP agrega la carga por destino; para cada destino, los vuelos se recorren en orden de salida y lo que
  entrega cada vuelo se toma de su inventario por commodity.

## Tabla de fidelidad

| Regla | Estado |
|---|---|
| Rotación cíclica, continuidad, TAT (50/70/90 min), semana de 168 h, mantenimiento, frecuencias mínimas, 10 t por vuelo cargado, capacidad, demanda diaria, carga multitramo | Exactas (ya lo eran) |
| Transbordos: cambio de avión, mismo aeropuerto, fuera de escala técnica, TTR, handling del que recibe | Exactos (pares de posiciones) |
| Interchanges: filial por vuelo, derechos, costos, handling, aeropuertos de cambio, borde | Exactos con `--exacto` |
| Posiciones por avión | Exactas con `--smax cota` |
| Día de salida | Tolerancia de 3,6 s: una salida a menos de 0,001 h de la medianoche cuenta para el día siguiente |
| Umbral de vuelo lleno (95 % del payload) | Tolerancia de 0,005 t entre el modelo y el validador |

**Supuestos que no vienen de los datos** (el modelo es exacto respecto de ellos; si cambian, cambia el modelo):

- **TTR = 2 h** entre el aterrizaje del vuelo que entrega y la salida del que recibe un transbordo.
- **El interchange no tiene costo ni tiempo extra**, y se usa el borde flexible.
- **Tramos de más de 9 h**: el modelo los excluye (MIA–MVD 9,08 h, SID–EZE 9,5 h y SID–MVD 9,25 h). El enunciado dice
  que la autonomía limita la duración *a plena capacidad*; con algo menos de carga, estos tramos serían posibles, pero
  los datos no dan el peso del combustible. Pendiente de confirmar con el profesor.
- **Distribución equilibrada de frecuencias**: apagada, como en el validador. Con la regla programada, la mejor
  solución tiene 47 violaciones (por ejemplo, 16 salidas BOG–MIA un mismo día). Falta definir la regla.
- La TAT de 50 min se aplica solo en las escalas técnicas (PTY y SID).

## Pruebas (10 de noviembre de 2026)

| Prueba | Resultado |
|---|---|
| R4, todos los aviones libres, S_max = cota (87–88): start desde la mejor solución con interchanges | El start cumple todas las restricciones y su objetivo es igual al margen validado: **913.547 = 913.547** |
| R4, AC-06 libre, S_max = cota, 4 min | La solución del MILP validada tal cual: **913.547,30 = 913.547,30** (30 transbordos) |
| R4 desde la heurística (791.354), AC-06 libre, 5 min | El MILP encontró 10 soluciones nuevas. La mejor, validada tal cual: **846.416,81 = 846.416,81** |
| Búsqueda por vecindarios con `--exacto` en R4 | AC-10: objetivo del modelo 798.958 = margen validado 798.958 |
| Instancia completa, AC-03 libre, desde 7.406.840 | **Óptimo demostrado en 26 s** (gap 0 %): con el resto fijo, AC-03 no puede mejorar. Objetivo 7.406.840 = margen validado (la versión relajada daba 7.451.678) |

En todas, la diferencia entre el objetivo del MILP y el margen validado fue de 0,00 USD.

## Tamaño: el modelo completo exacto no cabe en 32 GB

La parte que crece es la de los transbordos: una binaria por cada par de posiciones de aviones distintos y aeropuerto,
más el flujo por destino. `--estimar` en la instancia completa da:

| Posiciones por avión | Pares de transbordo | Binarias de transbordo | Flujos transbordados |
|---|---|---|---|
| 32 (no demostrado suficiente) | 350.208 | 14,7 millones | 6,0 millones |
| Cota demostrada (85–88) | 2.563.574 | 107,7 millones | 43,6 millones |

R4 con todos los aviones libres y S_max = cota ya tiene 1,75 millones de variables: tomó 4,5 min armarlo y el presolve
de Gurobi no terminó en 3 min. La instancia completa es unas 60 veces más grande, así que el modelo completo exacto
no cabe ni en una máquina de 32 GB.

**Dónde sí sirve:** en la búsqueda por vecindarios (`busqueda_por_avion.py --modelo continuo --exacto`). Cada
vecindario es exacto, así que:

- una mejora del modelo es una solución factible con ese margen (igual se revalida antes de aceptarla);
- si Gurobi cierra el gap, queda demostrado que ese vecindario no puede mejorar. Por ejemplo, el vecindario de AC-03
  con el resto fijo. Con S_max = 32 la demostración vale para rotaciones de hasta 32 vuelos.

Una cota global real necesita otro modelo: una relajación demostrada del problema completo. Por ejemplo, con los
transbordos agregados por aeropuerto sin exigir el orden temporal. Ese modelo sí cabría, pero su cota sería débil
(la relajación del modelo completo ya tiene gaps de 42 a 47 % en R4).
