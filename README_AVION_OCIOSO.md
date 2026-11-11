# Por qué la corrida de referencia usa 18 de 19 aviones

## Respuesta corta

No es un fallo de factibilidad ni un error del solver. El avión que queda en tierra en la corrida de referencia es **AC-14**, no AC-19. **AC-19 sí se usa** (14 vuelos). El modelo maximiza margen y la flota es opcional: no existe una restricción que obligue a usar todos los aviones.

## Qué dice el modelo

En `src/reoptimizacion_por_avion.py`, la variable de uso del avión se declara así:

```python
var(('u', k), 0, 1, 0.0, 1)
```

Es una variable binaria con **costo cero** en el objetivo. Aparece en el corte semanal y en la ventana de mantenimiento, pero no existe una restricción `u_k = 1`. Por lo tanto, el avión se activa solo si sus vuelos aportan margen positivo después de costos de vuelo, handling, demanda y restricciones operativas. Si no, queda en tierra.

Las reglas de `data/raw data/ops_rules.yaml` tampoco exigen usar toda la flota. Exigen rotación cíclica, mantenimiento, TAT y reglas de carga, pero no un mínimo de aviones activos.

## Evidencia en los datos

- `data/raw data/fleet.csv`: AC-14 es de **AND**, con **50 t** de payload.
- `data/raw data/maintenance.csv`: AC-14 tiene mantenimiento en **MIA** el **2026-03-05 de 06:30 a 12:30**.
- En `data/results/Ejecucion_con_intervalos_de_2_horas/modelo_resuelto.pkl`:
  - se usan **18 de 19** aviones;
  - **AC-14 tiene 0 vuelos**;
  - **AC-19 tiene 14 vuelos**.
- En `data/results/Ejecucion_con_swaps_2h_18aviones/evidencia_AC-14.json` está el resumen persistente de la prueba (también se conserva una copia en la carpeta de integración de 17 aviones).

## Prueba de que usarlo no es gratis

En la prueba de modo amplio, el MILP le propuso a AC-14 una rotación de **28 vuelos y 8 swaps**. El objetivo local del avión era positivo:

- objetivo MILP: **+891.353 USD**;
- pero el margen global validado caía:
  - pasada 1: **−200.662 USD**;
  - pasada 2: **−167.692 USD**.

Por eso el candidato fue rechazado y AC-14 quedó en tierra.

La explicación es la misma que motivó el Paso 2.5: el MILP del avión ve carga directa, pero no ve toda la red de conexiones, los transbordos, los vuelos vacíos ni la demanda limitada. Al activar AC-14, el modelo global termina moviendo capacidad que no genera suficiente ingreso adicional y el margen baja.

## Conclusión

**Usar 18 de 19 aviones no es un fallo.** Es el resultado de maximizar margen con flota opcional y demanda limitada. AC-14 es el avión marginal y su mejor rotación propuesta no compensa económicamente.

Si el requisito del curso es que se usen los 19 aviones, entonces falta una restricción de flota completa. Se puede agregar:

```text
u_k = 1    para todo avión k
```

o una penalización/costo fijo por avión ocioso. Eso produciría una solución con 19 aviones, pero probablemente con **menor margen** y posiblemente más vuelos vacíos. Es una decisión de alcance del modelo, no un error del solver.

## Nota sobre las corridas guardadas

La corrida principal sobre el **baseline de 18 aviones** está en `data/results/Ejecucion_con_swaps_2h_18aviones/`: usa **18 de 19** aviones y conserva la red base de `Ejecucion_con_intervalos_de_2_horas`. El avión ocioso sigue siendo AC-14.

La corrida de `data/results/Ejecucion_con_swaps_2h` es una **prueba de integración** distinta: usa **17 aviones** (AC-14 y AC-03 ociosos) porque volvió a resolver el Paso 2 con límites de tiempo y cambió el baseline. El mismo criterio aplica: la flota es opcional y el modelo no fuerza `u_k = 1`.
