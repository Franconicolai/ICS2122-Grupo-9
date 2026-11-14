# Resultados del análisis de sensibilidad

Fecha de consolidación: 14 de noviembre de 2026.

## Alcance y método

- Todos los escenarios ejecutados mantienen fijos los 387 vuelos de la Rama B y reoptimizan la carga y la filial operadora.
- Cada resultado fue recalculado con el validador actual de `milp-completo-warm-start` y tiene `validador_ok: true`.
- Estos resultados miden robustez y adaptación operativa de corto plazo; no prueban optimalidad global.
- El control reproduce el margen congelado de USD 7.520.006.
- El caso comparable de USD 5.997.653 fue proporcionado por el equipo, pero no aparece como artefacto reproducible en este checkout.

## Tabla consolidada

| Parámetro | Valor | Margen (USD) | Cambio vs. base | Demanda atendida | Interchanges |
|---|---:|---:|---:|---:|---:|
| Control | Base | 7.520.006 | +0.0 % | 95.76 % | 53 |
| Costo PAC | 3.800 USD/h | 6.831.461 | -9.2 % | 95.76 % | 53 |
| Costo PAC | 4.250 USD/h | 6.684.548 | -11.1 % | 95.76 % | 57 |
| Costo PAC | 4.450 USD/h | 6.679.676 | -11.2 % | 95.76 % | 62 |
| Combustible | -20 % | 8.784.257 | +16.8 % | 95.76 % | 53 |
| Combustible | +20 % | 6.255.754 | -16.8 % | 95.76 % | 53 |
| Combustible | +40 % | 4.991.502 | -33.6 % | 95.76 % | 53 |
| Demanda | -20 % | 4.081.737 | -45.7 % | 98.11 % | 45 |
| Demanda | -10 % | 5.962.837 | -20.7 % | 97.73 % | 49 |
| Demanda | +10 % | 8.111.344 | +7.9 % | 89.27 % | 48 |
| Demanda | +20 % | 8.630.331 | +14.8 % | 83.58 % | 54 |
| Borde semanal | Estricto | 7.414.550 | -1.4 % | 95.76 % | 50 |
| Cambio de operador | Desactivado | 6.388.786 | -15.0 % | 95.75 % | 0 |
| Tarifas | -10 % | 5.540.629 | -26.3 % | 95.76 % | 53 |
| Tarifas | +10 % | 9.499.396 | +26.3 % | 95.76 % | 53 |
| TTR | 1 h | 7.520.006 | +0.0 % | 95.76 % | 53 |
| TTR | 3 h | 7.516.981 | -0.0 % | 95.74 % | 53 |

## Conclusiones observadas

- El peor margen de la batería rápida es demanda -20 %: USD 4.081.737 (-45.7 %). El margen sigue siendo positivo.
- En los rangos probados, la demanda produce el mayor intervalo de margen; tarifas y combustible también son determinantes.
- Con PAC a 4.450 USD/h, el margen es USD 6.679.676 (-11.2 % vs. Rama B) y aún supera el caso comparable informado en USD 682.023.
- Combustible +40 % reduce el margen a USD 4.991.502; la red conserva la misma cobertura e interchanges porque los vuelos están fijos.
- El borde estricto cuesta USD 105.456 (-1,4 %), por lo que el supuesto de borde flexible aporta valor, pero no explica la mayor parte de la mejora.
- Desactivar el cambio de operador reduce el margen en USD 1.131.220 (-15.0 %), manteniendo fijos los vuelos.
- TTR de 3 h reduce el margen solo en USD 3.025 (-0,04 %); TTR de 1 h no cambia el margen.
- Con demanda +20 %, el margen aumenta, pero la cobertura cae a 83,58 %: la flota y el itinerario fijo están saturados.
- El control rápido puede escoger una solución de carga alternativa con el mismo margen; por eso algunos KPI secundarios (por ejemplo vuelos vacíos) no coinciden exactamente con el archivo congelado.

## Reproducción

Con Gurobi y las dependencias instaladas, desde la raíz del repositorio:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-sensibilidad.txt
python ejecutar_sensibilidad.py --grupo todos
python analizar_sensibilidad.py
```

El ejecutor crea únicamente los escenarios derivados que falten, conserva intacta la Rama B congelada y omite resultados que ya tengan `validador_ok: true`. Use `--recrear-escenarios` o `--repetir` solo cuando se quiera regenerarlos.

## Pendientes

- Reoptimizar uno o dos escenarios adversos para medir cuánto valor recupera una replanificación de vuelos.
- Incorporar al repositorio el artefacto o comando que respalda el caso comparable de USD 5.997.653.
- No ejecutar el MILP completo hasta optimalidad: excede los recursos disponibles y no es necesario para esta sensibilidad.
