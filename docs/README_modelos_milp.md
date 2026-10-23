# Modelos MILP integrados: guía para el equipo

Esta guía resume el trabajo de inyectar la solución de la heurística en un MILP integrado. Los detalles de cada modelo están en sus propios documentos (sección 8).

## 1. Resumen

- El MILP original (`gurobi_model.py`, Hito 3) no servía para comparar con la heurística, por tres razones:
  - le faltaban reglas y tenía otras distintas (TAT de 2 h y costo de 5 USD/km);
  - su capa de datos (`estructuras_datos.py`) se había borrado en el Hito 7;
  - con 2,2 millones de restricciones no daba ninguna solución.
- **Se reescribió `gurobi_model.py` con las mismas reglas que revisa `src/validador.py`**, con tiempo continuo, y quedó con **unas 26 veces menos restricciones** (84 mil en vez de 2,2 millones).
- **La solución de la heurística se inyecta como punto de partida (MIP start).** Gurobi la acepta y, con su heurística NoRel, la mejora: **+66.581 USD (+1,1 %) en 8 minutos**, con el validador OK (`warm_start_milp.py`).
- **Mejor resultado (23 de octubre): 6.435.839 USD (+125.461 sobre la heurística original, +2,0 %)**, encadenando la heurística y el MILP: heurística con DT = 1 h partiendo de la corrida de 2 h (`START`, dos iteraciones) → `warm_start_milp.py` con NoRel partiendo de esa solución. La receta está en la sección 4.
- Se crearon dos modelos que incluyen los transbordos dentro de la optimización: uno en red espacio-tiempo y otro con tiempo continuo. Ambos son correctos, porque con los vuelos fijos reproducen exactamente la heurística, pero con todos los aviones libres no caben en un computador de 8 GB. Se pueden resolver liberando uno o dos aviones a la vez.

## 2. Qué modelo usar

| Archivo | Qué decide | Transbordos | Tiempo | ¿Corre con todos los aviones en 8 GB? | Mejor resultado |
|---|---|---|---|---|---|
| `warm_start_milp.py` (modelo en `gurobi_model.py`) | Los 19 aviones a la vez: vuelos, horarios y carga multitramo en el mismo avión | En un paso final, igual que la heurística | Continuo | **Sí** (262 mil variables) | **6.376.959 USD** (+66.581) |
| `milp_continuo_transbordos.py` | Los aviones de `--libres`; el resto queda fijo | **Dentro del modelo** | Continuo | No (8,8 M binarias de transbordo) | Sin mejora en 10 min con 2 aviones libres |
| `modelo_integrado_espacio_tiempo.py` | Los aviones de `--libres`; el resto queda fijo | **Dentro del modelo** | Bloques de `--dt` horas (2 h por defecto) | No (11,9 M variables con 2 h; 24,1 M con 1 h) | +5.685 USD con AC-10 libre y DT = 1 h (1 h de Gurobi) |
| `busqueda_por_avion.py` | Un avión a la vez, con el resto fijo en la mejor solución; recorre todos | **Dentro del modelo** | Bloques de 1 h por defecto | Sí: cada paso es el modelo anterior con un avión libre (1,9 M variables) | Pensado para correr de noche (sección 4) |

**Recomendación:** para buscar mejoras, `warm_start_milp.py` con NoRel. Los otros dos sirven para el informe (son la formulación integrada completa) y para resolver por vecindarios.

## 3. Resultados guardados en `data/results/`

| Carpeta | Qué es | Margen validado (USD) |
|---|---|---:|
| `Ejecucion_con_intervalos_de_2_horas` | Corrida de la heurística usada como punto de partida | 6.310.307 (6.310.378 recalculando la carga con gap 1e-6) |
| `MILP_completo_warm_start_2h_8min` | `warm_start_milp.py`, 8 min con 300 s de NoRel | **6.376.959** |
| `MILP_completo_warm_start_2h_interrumpida` | `warm_start_milp.py`, cortado con Ctrl+C | 6.359.560 |
| `Heuristica_dt1_desde_2h` | Heurística con DT = 1 h partiendo de la corrida de 2 h (`START`) | 6.328.722 |
| `Heuristica_dt1_iter2` | Segunda iteración de la heurística con DT = 1 h, partiendo de la anterior | 6.336.259 |
| `MILP_completo_desde_heuristica_dt1` | `warm_start_milp.py`, 30 min con 1.500 s de NoRel, partiendo de `Heuristica_dt1_iter2` | **6.435.839** |
| `MILP_completo_desde_heuristica_dt1_iter2` | Segunda pasada del MILP, partiendo de la anterior. Su objetivo sin transbordos subió (+9.674), pero con los transbordos el margen bajó, porque el MILP no ve el valor de las conexiones. **Basta con una pasada** | 6.413.268 |
| `Heuristica_dt1_iter3_AND` | Heurística: solo AND reoptimizado (`SOLO=AND`, 300 s) desde `Heuristica_dt1_iter2` | 6.345.128 |
| `MILP_completo_desde_heuristica_dt1_iter3` | `warm_start_milp.py`, 30 min con 1.500 s de NoRel, partiendo de `Heuristica_dt1_iter3_AND`. NoRel tiene azar: partir de algo mejor no garantiza terminar mejor | 6.422.235 |
| `Busqueda_por_avion_dt1` | `busqueda_por_avion.py` con AC-10 (modelo exacto, DT = 1 h) | 6.316.063 |
| `MILP_integrado_TSN_todos_limite_memoria` | Modelo espacio-tiempo con todos los aviones libres: se detuvo por memoria a los 11 s y exportó el punto de partida | 6.310.378 |

Todas tienen el mismo formato que las corridas de la heurística, así que se ven en el visualizador (`python main.py` → 2). Sus KPIs salen de `src/validador.py`, igual que los de la heurística.

## 4. Cómo ejecutarlos

Desde la raíz del repositorio, con el computador conectado al cargador. Las corridas largas se cortan si Windows suspende el equipo.

```bash
python warm_start_milp.py --solo-verificar
```

```bash
python warm_start_milp.py --tiempo 3600 --norel 2880 --salida data/results/MILP_completo_1h
```

```bash
python milp_continuo_transbordos.py --libres AC-06,AC-10 --tiempo 1800 --norel 1500
```

```bash
python modelo_integrado_espacio_tiempo.py --libres AC-06,AC-10 --tiempo 1800 --norel 1500
```

**Secuencia que dio el mejor resultado** (unos 75 min en total). Los márgenes de la sección 3 se recalcularon con la carga a gap 1e-6:

```bash
python correr_heuristica.py --salida data/results/Heuristica_dt1_desde_2h --dt 1 --start data/results/Ejecucion_con_intervalos_de_2_horas
```

```bash
python correr_heuristica.py --salida data/results/Heuristica_dt1_iter2 --dt 1 --start data/results/Heuristica_dt1_desde_2h
```

```bash
python warm_start_milp.py --corrida data/results/Heuristica_dt1_iter2 --tiempo 1800 --norel 1500 --salida data/results/MILP_completo_desde_heuristica_dt1
```

- **Por qué funciona.** El MILP con NoRel es una búsqueda local: llega más lejos si parte de una solución mejor. Partiendo de la corrida de 2 h llegó a 6.376.959; partiendo de la heurística con DT = 1 h, a 6.435.839.
- **De dónde sale la mejora.** Se cortan vuelos poco rentables (330 contra 344, con 9 vacíos menos): el costo de vuelos baja 562 mil y los ingresos, 452 mil.
- `warm_start_milp.py` también acepta como `--corrida` una salida del propio MILP. Como sus horarios vienen redondeados a 5 decimales, solo se fijan los vuelos y las binarias, y Gurobi recalcula los horarios.

**Corrida nocturna, avión por avión con DT = 1 h:** 20 min de NoRel por avión, unas 6,5 h por vuelta. Sigue mientras alguna vuelta mejore:

```bash
python busqueda_por_avion.py --vueltas 2
```

- Cada mejora se valida y se exporta al tiro en `data/results/Busqueda_por_avion_dt1/`. `progreso.csv` registra cada avión.
- Si se corta, se reanuda desde lo exportado: `python busqueda_por_avion.py --corrida data/results/Busqueda_por_avion_dt1 --desde AC-08`.
- El script le pide a Windows que no se suspenda mientras corre, sin cambiar la configuración del equipo. Igual conviene dejarlo enchufado y con la tapa abierta.

- **Tamaño sin armar el modelo:** `--estimar`.
- **Otros parámetros:** todas las opciones aparecen con `--help`.
- **Para cortar una corrida:** Ctrl+C **una sola vez**. Gurobi se detiene y el script valida y exporta la mejor solución que tenga.

## 5. Supuestos y decisiones importantes

1. **Mismas reglas que la heurística.** Tramos permitidos y de hasta 9 h, payload por avión, TAT de 70 min (50 en PTY/SID; 90 si el avión llega y sale lleno), rotación semanal cerrada, mantenimiento, 25 frecuencias mínimas, mínimo de 10 t por vuelo cargado, demanda por día de salida, costos reales y handling.
2. **Supuestos heredados de la heurística, por confirmar con el mandante.** "Lleno" significa al menos el 95 % del payload; `TTR_H` = 2 h es provisional; `interchange_airports.csv` se interpreta como soporte operacional; ningún vuelo cruza el corte domingo-lunes.
3. **`warm_start_milp.py` no decide los transbordos al elegir los vuelos.** Los agrega en el paso final, por lo que la cota de Gurobi vale solo para el problema sin transbordos.
4. **Máximo 32 vuelos por avión a la semana** en los modelos de tiempo continuo (`--smax`). La heurística usa como máximo 31.
5. **NoRel queda apagada por defecto** (valor predeterminado de Gurobi). Hay que pedirla con `--norel <segundos>`: en las pruebas, fue lo único que mejoró el punto de partida, mientras que el árbol de ramificación no avanzó más allá de la raíz.
6. **Sin límite de RAM por defecto.** Con los modelos que liberan todos los aviones, el computador se puede quedar sin memoria. `--memoria <GB>` hace que Gurobi se detenga ordenadamente.
7. **Comparación justa.** El margen final de cada corrida se recalcula con el submodelo de carga de la heurística (gap 1e-6) y se valida. Por eso la referencia de la heurística es 6.310.378, y no los 6.310.307 que reportó (calculados con gap 0,1 %).

## 6. Cómo se verificó que los modelos son correctos

- **El punto de partida cumple todas las restricciones** de cada modelo, y su objetivo coincide al dólar con el margen del validador.
- **Pruebas negativas:** si al punto de partida se le adelanta un vuelo, se le cambia el día, se le quita el mantenimiento o se le deja un vuelo con 5 t, el verificador lo detecta.
- **Soluciones propias del MILP:** 11 soluciones de `gurobi_model.py` en una instancia chica, distintas del punto de partida, pasaron el validador con 0 violaciones.
- **Con todos los vuelos fijos** (`--libres ninguno`), los dos modelos con transbordos dan exactamente 6.310.378 USD, el mismo óptimo del paso final de la heurística.

## 7. Pendientes

- Corridas largas de `warm_start_milp.py` con NoRel (varias horas), encadenando corridas con `--corrida <carpeta de un resultado anterior>` (no probado).
- Probar los modelos con transbordos en un computador con 32 GB o más.
- Confirmar con el mandante los supuestos del punto 2 de la sección 5.

## 8. Documentos detallados

- `docs/README_milp_completo.md`: formulación de `gurobi_model.py`, cambios respecto de la versión original, verificación y resultados de `warm_start_milp.py`.
- `docs/README_milp_continuo_transbordos.md`: `milp_continuo_transbordos.py`.
- `docs/README_modelo_integrado_espacio_tiempo.md`: `modelo_integrado_espacio_tiempo.py` (incluye `--sin-transbordos`, `--enlace-fuerte`, `--relajacion` y las pruebas con DT = 1 h).
- `docs/README_benders.md`: descomposiciones de Benders (`benders_binarias_maestro.py` y `benders_l_shaped.py`).
- `busqueda_por_avion.py`: el uso está en el encabezado del archivo y en `python busqueda_por_avion.py --help`.
