# MILP completo con tiempo continuo y transbordos

## 1. Qué es

`milp_continuo_transbordos.py` toma el MILP de `gurobi_model.py` (posiciones por avión, tiempo continuo y todas las reglas del validador) y le agrega **los transbordos entre aviones dentro del modelo**. Es el problema completo: las mismas reglas de la heurística, con horarios continuos y con los transbordos decididos junto con los vuelos.

Las reglas de los transbordos son las mismas del paso final de la heurística (`src/asignacion_carga_multitramos.py`):

- la carga cambia de avión;
- ambos vuelos están en el mismo aeropuerto, que no es escala técnica;
- los dos operadores tienen soporte en ese aeropuerto (`interchange_airports.csv`);
- el vuelo que recibe sale al menos `TTR_H` después del aterrizaje del vuelo que entrega;
- el handling se cobra al operador que recibe.

## 2. Formulación

Sobre `gurobi_model.py` con `transbordos=True`:

- Cada vuelo puede **recibir** (`tin`) y **entregar** (`tout`) carga por destino. Los balances de entrada (R20) y salida (R21) los incluyen.
- Un avión puede llevar carga hacia cualquier destino, no solo hacia los que él mismo alcanza.

Cada transbordo posible es un **par (vuelo que entrega i → vuelo que recibe j)** de aviones distintos, con su flujo por destino `tr[p, d]`. Hay cuatro tipos de pares:

| Tipo | Qué se sabe | Variables y restricciones |
|---|---|---|
| fijo → fijo | Aeropuerto y horas conocidos; el par existe solo si cumple todas las reglas | Solo el flujo |
| libre → fijo | El aeropuerto y la hora de salida los pone el vuelo fijo | Binaria `c`; el vuelo libre debe llegar a ese aeropuerto y aterrizar a más tardar a la salida − `TTR_H` |
| fijo → libre | El aeropuerto y la hora de aterrizaje los pone el vuelo fijo | Binaria `c`; el vuelo libre debe salir de ese aeropuerto al menos `TTR_H` después |
| libre → libre | Nada | Una binaria por aeropuerto posible; ambos vuelos en ese aeropuerto y salida ≥ aterrizaje + `TTR_H` (M = 170 h) |

Además:

- **Capacidad:** Σ_d `tr[p, d]` ≤ min(payload de ambos) × (par activo).
- **Totales por vuelo:** `tout` y `tin` de cada vuelo son la suma de sus pares.
- **Objetivo:** el de `gurobi_model.py` menos el handling de cada tonelada transbordada.

Los aviones que no están en `--libres` quedan con los vuelos **y horarios** de la corrida. Su carga, sus transbordos y sus marcas de lleno sí se vuelven a optimizar.

## 3. Tamaño (S_max = 32, corrida de 2 h)

| `--libres` | Pares de transbordo | Binarias de transbordo | Flujos transbordados |
|---|---:|---:|---:|
| `ninguno` | 10.686 | 0 | 181.662 |
| `AC-06,AC-10` | 44.365 | 103.808 | 754.205 |
| `SUR` (7 aviones) | 110.907 | 1.528.352 | 1.885.419 |
| `todos` | 350.208 | **8.767.488** | **5.953.536** |

El modelo crece con el cuadrado de las posiciones de los aviones libres. Con `todos` no cabe en un computador normal; el script se detiene sobre 2 millones de binarias, salvo que se use `--forzar`.

## 4. Verificación y resultados (i7-8550U, 7,8 GB, Gurobi 13.0.2)

| Prueba | Resultado |
|---|---|
| `--libres ninguno` | 466 mil variables y 116 mil restricciones. Start factible con objetivo 6.310.378 USD; óptimo **6.310.378 USD con cota 6.310.378 (gap 0 %)**, igual al paso final de la heurística |
| `--libres AC-06,AC-10`, 10 min (480 s de NoRel) | 1,14 M variables (199 mil enteras), 359 mil restricciones; se arma en 36 s. Start factible (6.310.378 USD). NoRel no encontró mejoras y la raíz no terminó: cota 8,64 M, **gap 36,9 %**, sin cambios respecto de la corrida |
| `--libres todos` | No se ejecutó: 8,8 M binarias y 6 M flujos no caben en 7,8 GB de RAM |

## 5. Cómo ejecutarlo

Desde la raíz del repositorio:

```bash
python milp_continuo_transbordos.py --libres AC-06,AC-10 --tiempo 1800 --norel 1200
```

| Opción | Por defecto | Uso |
|---|---|---|
| `--libres` | `todos` | `todos`, `ninguno`, aviones (`AC-06,AC-10`) u operadores (`BRA`) |
| `--estimar` | | Solo muestra cuántos pares y binarias de transbordo tendría el modelo |
| `--solo-verificar` | | Arma el modelo y revisa el start, sin optimizar |
| `--forzar` | | Arma el modelo aunque supere 2 millones de binarias de transbordo |
| `--corrida` | corrida de 2 h | Start y vuelos de los aviones fijos |
| `--ttr` / `--smax` | el de la corrida / 32 | `TTR_H` y posiciones por avión |
| `--tiempo` | 3600 | Segundos de Gurobi |
| `--norel` | predeterminado de Gurobi (0: apagada) | Segundos de la heurística NoRel antes de la raíz. En las pruebas, NoRel fue lo que encontró mejoras |
| `--memoria`, `--hilos`, `--mipfocus`, `--gap` | sin límite, todos, 1, 0,01 | Parámetros de Gurobi |
| `--salida` | `data/results/MILP_continuo_transbordos_<libres>` | Carpeta de salida |

La salida tiene el mismo formato que la heurística, así que se puede ver en el visualizador. Al final, el script vuelve a calcular la carga de los vuelos encontrados con el paso final de la heurística y la valida con `src/validador.py`.

## 6. Conclusiones

1. **El modelo es correcto y es el problema completo.** Con los vuelos fijos reproduce exactamente el paso final de la heurística, y los transbordos siguen las mismas reglas.
2. **No se puede resolver con todos los aviones libres.** Hacen falta del orden de 9 millones de binarias de pares. En un computador con mucha memoria se puede intentar con `--libres todos --forzar --norel <segundos>`, pero lo esperable es que Gurobi no alcance a resolver la raíz.
3. **Por vecindarios corre, pero en 10 min no mejoró la corrida.** Con dos aviones libres hay unas 100 mil binarias de transbordo y una relajación débil (gap ~37 %). Vale la pena probar con más tiempo de NoRel, o con un solo avión libre a la vez.
4. **Comparación:** en 8 minutos, `warm_start_milp.py` (sin transbordos dentro del modelo y todos los aviones libres) llegó a +66.581 USD. Este modelo es más fiel al problema, pero mucho más pesado.
