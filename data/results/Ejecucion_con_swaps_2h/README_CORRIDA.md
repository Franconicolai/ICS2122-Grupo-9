# Registro de la corrida: integración Paso 2 + Paso 2.5

Esta carpeta conserva la corrida de integración que reporta el margen validado de **7.194.562,78 USD** en `README_MEJORAS_SWAPS.md` y en el Hito 11 de `README.md`. La corrida principal sobre el baseline de 18 aviones está en `../Ejecucion_con_swaps_2h_18aviones/`; esta carpeta es la prueba de integración de 17 aviones.

## Resultado principal

| Métrica | Valor |
|---|---:|
| Margen validado | **7.194.562,78 USD** |
| Baseline de esta corrida | 6.280.514 USD (aproximado, según la corrida de integración) |
| Mejora reportada | **+914.049 USD** |
| Vuelos | 346 |
| Aviones usados | 17 |
| Demanda servida | 88,50 % |
| Frecuencias | 25/25 |
| Swaps | 48 |
| Violaciones del validador | 0 |

La diferencia entre el baseline de esta carpeta y el de `data/results/Ejecucion_con_intervalos_de_2_horas` se explica porque aquí se **volvió a resolver el Paso 2** con límites de tiempo antes de aplicar el Paso 2.5. No es la misma corrida base.

## Configuración

```text
SOLVER=gurobi
DT=2
TLIM=1500
TLIM_OP=420
TLIM_CARGA=120
SWAPS=1
SWAP_PASSES=1
SWAP_TLIM=120
SWAP_MODO=auto
SWAP_DUAL_WEIGHT=0.5
SWAP_MAX_OD=15
SWAP_TECNICOS=1
SWAP_HUBS=1
```

La corrida no volvió a ejecutar el Paso 1: partió del archivo `sol_dt2.pkl` de la corrida de referencia de 2 h.

## Archivos

| Archivo | Contenido |
|---|---|
| `indicadores_kpi.json` | KPIs del validador, margen, violaciones, resumen por operador y metadata de la corrida. |
| `modelo_resuelto.pkl` | Itinerario final con operador por vuelo, cargas, transferencias y solución de carga. |
| `itinerario_vuelos.csv` | Un registro por vuelo, con operador, horas, carga y costos. |
| `carga_transportada.csv` | Carga por avión, posición y commodity. |
| `transferencias_carga.csv` | Transbordos de carga utilizados. |
| `resultados.json` | Consolidado para la visualización web. |
| `metadata.json` | Configuración y procedencia de la corrida. |
| `sol_dt2.pkl` | Salida del Paso 1 usada como entrada. |
| `evidencia_AC-14.json` | Evidencia del avión ocioso y de la prueba en modo amplio. |

## Verificación rápida

```powershell
python -c "import json; d=json.load(open('data/results/Ejecucion_con_swaps_2h/indicadores_kpi.json', encoding='utf-8')); print(round(d['kpis']['margen_usd'], 2), d['validador_ok'], len(d['violaciones']))"
```

Resultado esperado:

```text
7194562.78 True 0
```

## Nota sobre la flota

Esta corrida usa 17 aviones. La justificación de por qué el modelo puede dejar aviones en tierra está en `../../README_AVION_OCIOSO.md`. En la corrida de referencia de 2 h el avión ocioso es **AC-14**; en esta integración también queda ocioso AC-14 y, además, AC-03, porque el Paso 2 se re-resolvió con time limits y cambió el baseline. No es un error del validador ni una violación de factibilidad.
