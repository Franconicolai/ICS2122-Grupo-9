# Registro de la corrida: Paso 2.5 sobre el baseline de 18 aviones

Esta carpeta conserva la corrida del Paso 2.5 que parte del **baseline de 18 aviones** de la referencia de 2 h. No se volvió a resolver el Paso 2, por lo que se conserva la flota y la red base de `Ejecucion_con_intervalos_de_2_horas`.

## Resultado principal

| Métrica | Valor |
|---|---:|
| Margen baseline | 6.310.307,05 USD |
| Margen validado | **7.140.267,60 USD** |
| Mejora | **+829.960,55 USD (+13,15 %)** |
| Vuelos | 344 |
| Aviones usados | **18 de 19** |
| Demanda servida | 87,89 % |
| Frecuencias | 25/25 |
| Swaps | 47 |
| Violaciones del validador | 0 |

## Configuración

```text
SOLVER=gurobi
DT=2
TLIM=1500
TLIM_OP=420
TLIM_CARGA=120
SWAPS=1
SWAP_PASSES=2
SWAP_TLIM=120
SWAP_MODO=auto
SWAP_DUAL_WEIGHT=0.5
SWAP_MAX_OD=15
SWAP_TECNICOS=1
SWAP_HUBS=1
```

La diferencia con `data/results/Ejecucion_con_swaps_2h` es importante: esa otra carpeta es una **prueba de integración** que volvió a resolver el Paso 2 con time limits y terminó con 17 aviones y margen 7.194.562,78 USD. Aquí, en cambio, se usa el baseline ya validado de 18 aviones y solo se aplica el Paso 2.5. Los márgenes no son directamente comparables.

## Archivos

| Archivo | Contenido |
|---|---|
| `indicadores_kpi.json` | KPIs del validador, margen, violaciones, resumen por operador y metadata de la corrida. |
| `modelo_resuelto.pkl` | Itinerario final con operador por vuelo, cargas, transferencias y solución de carga. |
| `modelo_base_sin_swaps.pkl` | Baseline de 18 aviones usado como entrada. |
| `itinerario_vuelos.csv` | Un registro por vuelo, con operador, horas, carga y costos. |
| `carga_transportada.csv` | Carga por avión, posición y commodity. |
| `transferencias_carga.csv` | Transbordos de carga utilizados. |
| `resultados.json` | Consolidado para la visualización web. |
| `metadata.json` | Configuración y procedencia de la corrida. |
| `sol_dt2.pkl` | Salida del Paso 1 usada por el baseline. |
| `evidencia_AC-14.json` | Evidencia del avión ocioso y de la prueba en modo amplio. |

## Verificación rápida

```powershell
python -c "import json; d=json.load(open('data/results/Ejecucion_con_swaps_2h_18aviones/indicadores_kpi.json', encoding='utf-8')); print(round(d['kpis']['margen_usd'], 2), d['kpis']['aviones_usados'], d['validador_ok'], len(d['violaciones']))"
```

Resultado esperado:

```text
7140267.6 18 True 0
```

## Nota sobre la flota

En esta corrida se usan **18 de 19** aviones. El avión ocioso es **AC-14**; AC-19 sí se usa. La justificación completa está en `../../README_AVION_OCIOSO.md`.
