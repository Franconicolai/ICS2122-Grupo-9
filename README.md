# ICS2122 Taller de Investigación Operativa (Capstone), grupo 9

**FLEET ASSIGNMENT Y CARGO ROUTING INTEGRADOS SOBRE UNA RED HUB-AND-SPOKE SEMANAL**

## Estructura del repositorio

El proyecto está modularizado para separar los datos, el motor matemático de la red espacio-tiempo y la visualización:

- `data/`: Contiene todos los datos requeridos e informados por el modelo.
  - `data.sqlite`: Base de datos principal de trabajo.
  - `raw data/`: Resguardo de los datos brutos de entrada (`airports.csv`, `fleet.csv`, etc.).
  - `results/`: Directorio donde se guardan las salidas de cada corrida (versionadas por carpeta), incluyendo los `.pkl` de la solución, indicadores KPI y archivos para la visualización.
- `docs/`: Documentación del proyecto (ej. `README_red_espacio_tiempo.md`).
- `src/`: Código fuente principal del modelo de optimización.
  - `red_espacio_tiempo.py`: Primer paso (red agregada por operador).
  - `reoptimizacion_por_avion.py`: Segundo paso (separación avión por avión).
  - `asignacion_carga_multitramos.py`: Optimización final y rutero multicommodity de la carga.
  - `validador.py`: Auditor independiente de reglas logísticas.
  - `solver_util.py`: Herramientas de conectividad para solvers (HiGHS, Gurobi).
- `main.py`: **Orquestador central e interactivo (CLI)**. Ejecuta el modelo guiado paso a paso, versiona los resultados y permite visualizar los datos.
- `visualization/`: Interfaz gráfica web interactiva que despliega los resultados.
  - `main.py`: Script que levanta el servidor web.
- **Archivos de configuración**:
  - `.env`: Variables de entorno para configuración (conexión a base de datos, parámetros límite).
  - `.gitignore`: Exclusión de archivos temporales y entornos virtuales.



## Instrucciones de ejecución

Para operar el modelo basado en la estructura anterior, es indispensable contar con el entorno configurado y las dependencias instaladas.

**Prerrequisitos:**

- Python 3.8 o superior.
- Licencia y solver de Gurobi instalados (`gurobipy`).
- Librerías de dependencia: `pandas`, `python-dotenv`.

Instalación de dependencias:

```bash
pip install gurobipy pandas python-dotenv
```

**Ejecución Modular:**

La arquitectura ahora separa estrictamente la ejecución del modelo y la visualización.

**1. Ejecutar el Modelo Matemático (Gurobi)**
Desde la raíz del proyecto, ejecuta el script del modelo para resolver y exportar los resultados en consola:

```bash
python src/main.py
```

**2. Levantar la Visualización (Interfaz Gráfica)**
Para levantar el servidor web y visualizar los resultados recién exportados en tu navegador, ejecuta:

```bash
python visualization/main.py
```

## Bitácora de implementación

**Hito 0**

- **Integrantes**: Fernando Mora, Vicente Alvarado y Franco Nicolai
- **Fecha**: 15 de agosto al 13 de septiembre.
- **Desarrollo**: Inicialización del repositorio y configuración del entorno de trabajo. Se incorporaron los integrantes, se estructuraron los archivos de configuración (`.gitignore`, `README`) y se cargó el conjunto de datos original junto con un script inicial de análisis y visualización gráfica.

**Hito 1**

- **Integrante**: Franco Nicolai
- **Fecha**: 12 de Septiembre al 14 de Septiembre.
- **Desarrollo**: Se organizó la jerarquía de directorios, aislando los datos originales en la carpeta `raw_data` como respaldo. Los datos fueron migrados a una base de datos SQLite para optimizar la eficiencia de lectura y el manejo de consultas.Se estableció el directorio `src` para albergar la lógica del modelo. El flujo se centraliza en `main.py`, el cual invoca la rutina de estructuración de datos encargada de conectar con SQLite y formatear los parámetros de entrada para luego ejecutar la formulación matemática mediante `gurobi_model`.e incorporó un módulo para extraer y almacenar los resultados generados por Gurobi, facilitando su posterior procesamiento y análisis. 

Nota: quedaron todas las restricciones comentadas.

**Hito 2**

- **Integrante**: Franco Nicolai
- **Fecha**: 14 de Septiembre.
- **Desarrollo**: Con el apoyo de inteligencia artificial, se desarrolló un visualizador web (`index.html`) que permite observar los resultados geográficos y logísticos del modelo. La ejecución matemática quedó delegada de manera nativa y eficiente a la terminal mediante los scripts correspondientes.

**Hito 3**
- **Integrante**: Agustina Caneo
- **Fecha**: 22 al 25 de Septiembre.
- **Desarrollo**: Se conectó `estructuras_datos.py` con la guía de modelo nueva (posición-indexada, transbordo general): se agregó `Ek` (tramos factibles por avión, filtrado por derechos de tráfico y autonomía de 9h), `S_max_k` (cota de posiciones por avión, reemplazando el `S_max=5` fijo), y se reconstruyó `Q`/`D_q`/`r_q` como commodities diarios `(origen, destino, día)` con tarifa real de `demand_weekly` (antes `r_q=1.5` fijo para todos). Se conectó `gurobi_model.py` con estas estructuras usando índices dispersos (`KS`, `KS_menos`, `KSE`, `QKS`) en vez de `itertools.product` sobre listas globales, y se activaron las restricciones núcleo de programación de aeronaves y flujo básico de carga (R1, R2, R3a/b, R4 simplificado, R5, R6a/b, R11, R8, R20a/b, R21a/b, R23, R25, R26a/b/c). Se corrigió además un bug de `gurobipy` (`.sum()`/`.select()` no encuentra coincidencias cuando se le pasa una tupla como argumento exacto, en vez de comodín) que dejaba `s_q` siempre en 0. Se validó el modelo con una instancia reducida (1 avión, 1 commodity) con resultado óptimo correcto.

**Hito 5**
- **Integrante**: Sara Kita
- **Fecha**: 28 de Septiembre.
- **Desarrollo**: Se cambió la metodología de solución, dado que el MILP posición-indexado no escala a la flota completa (19 aviones, 44 aeropuertos): su relajación lineal es débil por las restricciones de tiempo con "M grande" y la simetría entre aviones. El MILP integrado se reformuló como una **red de flujo espacio-tiempo** (semana cíclica discretizada en bloques de `DT` horas; el tiempo mínimo en tierra queda incluido en el largo de cada arco de vuelo), resuelta en dos pasos. Se agregaron a `src/`:
  - `red_espacio_tiempo.py`: paso 1, red agregada por operador con flujo de aviones, carga directa, demanda diaria, mínimo de 10 t por vuelo, frecuencias mínimas y mantenimiento como conteo.
  - `reoptimizacion_por_avion.py`: paso 2, reoptimiza operador por operador con aviones individuales, exigiendo rotación semanal cerrada por avión, ventana de mantenimiento exacta y payload propio. Luego valida el itinerario completo.
  - `asignacion_aviones.py`: primer intento del paso 2 (repartir la solución agregada entre aviones sin reoptimizar), que resultó infactible. Se conserva como evidencia y porque contiene funciones auxiliares que usa el paso 2.
  - `solver_util.py`: permite elegir el solver con la variable `SOLVER` (`gurobi` o `highs`).
  - `validador.py`: validador independiente desarrollado por Jose en el Hito 4. Se subió porque el paso 2 lo necesita.

  Se creó la carpeta `results/` con los resultados de la corrida con Gurobi (`sol_dt2_poravion_kpis.json`, `sol_dt2_poravion_itinerario.csv` y las soluciones en `.pkl`), además de `results/highs_referencia/` con la misma corrida hecha con HiGHS, para comparar solvers. No se modificó ningún archivo existente. Con bloques de 2 h y Gurobi se obtuvo un itinerario completo de los 19 aviones, aprobado por el validador sin violaciones, con un margen semanal de 5.779.750 USD (+129,9 % sobre el caso base), 84,4 % de la demanda servida y las 25 frecuencias mínimas cumplidas.

  **Todos los detalle de la formulación, los resultados por operador, los supuestos, las limitaciones y las instrucciones de ejecución está en `docs/README_red_espacio_tiempo.md`.**

**Hito 6**
- **Integrante**: Vicente Alvarado
- **Fecha**: 29 de Septiembre.
- **Desarrollo**: Se extendió la segunda etapa de la red espacio-tiempo para incorporar una asignación global de carga multitramos sobre los itinerarios individuales. Se agregó `src/asignacion_carga_multitramos.py`, que observa simultáneamente los vuelos de todos los operadores y permite vuelo directo, continuación `through` en vuelos consecutivos del mismo avión, transbordos entre aeronaves del mismo operador y transbordos entre operadores. Se implementaron las siguientes reglas:
  - conservación de carga por commodity diario en cada vuelo;
  - capacidad individual por aeronave y mínimo de 10 t por vuelo cargado;
  - embarque inicial únicamente en el origen y día correspondiente, e ingreso solamente al entregar en el destino;
  - continuación `through` sin nuevo costo de handling ni tiempo de transbordo;
  - transbordos con coincidencia de aeropuerto, soporte de ambos operadores según `interchange_airports.csv` y tiempo mínimo configurable mediante `TTR_H`;
  - prohibición de carga, descarga y transbordo comercial en PTY y SID;
  - cobro de handling al operador receptor cuando la carga cambia de avión;
  - cumplimiento del TAT de 90 min cuando dos vuelos consecutivos se encuentran llenos.

  También se modificaron:
  - `reoptimizacion_por_avion.py`: ejecuta automáticamente la asignación global después de construir los itinerarios individuales y exporta el detalle de carga y transferencias;
  - `validador.py`: valida independientemente los balances de transferencia, ubicación, soporte operacional, `TTR_H`, costos de handling y cierre semanal lleno–lleno;
  - `tests/`: se agregaron 8 pruebas automáticas para vuelos directos, conexiones `through`, transbordos entre operadores y casos inválidos por tiempo, soporte o escala técnica.

  Se ejecutó nuevamente el pipeline completo con bloques de 2 h y Gurobi. El paso 1 finalizó por límite de tiempo con un margen agregado de 5.454.147 USD, 332 vuelos, 82,4 % de la demanda, las 25 frecuencias mínimas cumplidas y un gap de 3,16 %. En el paso 2, BRA y PAC terminaron dentro del criterio de optimalidad; SUR terminó con gap de 1,02 % y AND con gap de 11,64 %. La asignación multitramos final generó 340 vuelos utilizando 17 de 19 aviones, entregó 11.659,0 t (87,2 % de la demanda) y obtuvo un margen semanal validado de **6.260.460 USD**, con 25 de 25 frecuencias, 26 conexiones de transbordo utilizadas, 16 de ellas entre operadores, y **0 violaciones** en el validador independiente.

  Los resultados se guardaron en `results/verificacion_sol_dt2*`, incluyendo el itinerario, KPIs, carga por commodity, transferencias y soluciones `.pkl`, sin reemplazar los resultados anteriores. El valor `TTR_H=2` h y la interpretación de `interchange_airports.csv` como soporte operacional se mantienen como supuestos provisionales que deben confirmarse con el mandante. Los itinerarios de los pasos 1 y 2 todavía se construyen inicialmente con una aproximación de carga directa; la carga multitramos se optimiza globalmente sobre los vuelos resultantes.

**Hito 7**
- **Integrante**: Franco Nicolai
- **Fecha**: 30 de Septiembre de 2026.
- **Desarrollo**: Refactorización profunda de la base de código para establecer un flujo central unificado y limpio. Se eliminaron todos los archivos obsoletos o redundantes (como `gurobi_model.py`, `estructuras_datos.py`, `database.sqlite`, scripts de asignación sin uso, etc.) y se documentaron los archivos restantes en `src/` con docstrings completos. Se reescribió `main.py` transformándolo en un orquestador interactivo (CLI) que ejecuta secuencialmente los pasos de optimización y gestiona la exportación de resultados ordenados en versiones dentro de `data/results/`. Finalmente, se solucionó un problema crítico de vinculación entre los resultados generados y el entorno gráfico (`visualization/index.html`), inyectando de forma automatizada las coordenadas geográficas de los aeropuertos y corrigiendo los fallos en la animación de las rutas.



**Hito 7**
- **Integrante**: Fernando Mora 
- **Fecha**: 30 de Septiembre de 2026
- **Desarrollo**: Se exploraron dos de los "próximos pasos" que dejó Sara en `docs/README_red_espacio_tiempo.md`: la mejora del paso 2 mediante **vueltas adicionales que usen el valor de las conexiones** (punto 2) y la reducción de los gaps de los operadores SUR y AND, que quedaron sin cerrar (11,64 % en AND en el Hito 6). El objetivo era mejorar el margen sin rediseñar el modelo. Ninguna de las dos líneas dio un beneficio, y ambas quedan descartadas. Para no afectar el código actual ni agregar procesos que se demostraron ineficientes quedan los siguientes cambios guardados en la branch Intento-warm-start en caso de querer volver a probarlo o tener de evidencia a futuro.

  **Modificaciones y archivos nuevos:**
  - `src/solver_util.py`: se agregó el parámetro `start` a `resolver_matriz` para pasar una solución inicial (warm start) a Gurobi (`x.Start`) y a HiGHS (`setSolution`).
  - `src/reoptimizacion_por_avion.py`: se agregaron las variables de entorno `START_PKL`, `SUFIJO` y `BONO_PKL`; la función `armar_start`, que construye el vector inicial (variables `u`, `z`, `zl`, `x`, `g`) a partir de un itinerario por avión previo; el parámetro `start` en `resolver_mip`; y un término de bonificación en el coeficiente objetivo de las variables de vuelo `z`. El sufijo permite no sobrescribir resultados anteriores.
  - `src/iterar_conexiones.py` (nuevo): ejecuta el paso 2 de forma iterativa. En cada vuelta calcula, desde la asignación multitramos, una bonificación por vuelo que participa en conexiones (ingreso del commodity repartido por tonelada-hora, solo en vuelos que no son carga directa del tramo), la devuelve al objetivo del paso 2 y conserva siempre la mejor solución validada.

  **Resultados.** Todos los márgenes son los recalculados por el validador independiente (0 violaciones y 25 de 25 frecuencias en todas las corridas):

  | Corrida | Margen validado (USD) | Vuelos | Aviones |
  |---|---:|---:|---:|
  | Hito 6 (referencia, Vicente) | 6.260.460 | 340 | 17 |
  | Warm start, corrida 1 | 6.240.421 | 327 | 17 |
  | Warm start, corrida 2 (it0, bonificación 0,5) | 6.269.506 | 334 | 18 |
  | Warm start, corrida 3 (it0, bonificación 0,2) | 6.245.734 | 329 | 16 |
  | Bonificación 0,5 (it1) | 6.205.050 | 347 | 19 |
  | Bonificación 0,2 (it1) | 6.231.180 | 331 | 16 |

  **Por qué no hubo beneficio:**
  1. **El warm start no supera el ruido.** Tres corridas equivalentes (mismo modelo, mismo start, sin bonificación) dieron 6.240.421, 6.269.506 y 6.245.734 USD: una variación de casi 30.000 USD sin cambiar nada del modelo. La diferencia con el Hito 6 (entre −20.000 y +9.000 USD) cae dentro de ese rango, por lo que no puede atribuirse al warm start. El start fue aceptado por Gurobi (`Loaded user MIP start`, 0 aviones descartados), de modo que el resultado no se debe a un start infactible.
  2. **La bonificación por conexiones empeora el margen.** Con factor 0,5 el margen cayó 64.000 USD respecto de la corrida base: el modelo voló más (347 vuelos, 19 aviones), los ingresos subieron unos 284.000 USD, pero los costos de vuelo subieron unos 337.000 USD, porque el premio inflaba el objetivo sin que las conexiones se materializaran. Con factor 0,2 la caída fue de 14.600 USD, que ya es ruido; es decir, al reducir el premio el efecto negativo desaparece, pero tampoco aparece una mejora. La bonificación es exacta en `(tramo, bloque)`, por lo que cualquier cambio de horario rompe la conexión que se quería proteger. El aparente descenso del gap de AND en esa corrida (11,6 %) no es una mejora real: el premio infla el objetivo y la cota.
  3. **Los gaps restantes valen poco.** Con los valores de la corrida completa, cerrar por completo los gaps de AND (~92.000 USD) y SUR (~41.000 USD) daría como máximo unos 140.000 USD, cerca del 2 % del margen. En la práctica sería bastante menos, porque las cotas son optimistas.

  **Conclusión principal: el problema es la cota, no la solución encontrada.** En todas las corridas, la cota de AND se mantuvo prácticamente fija (≈ 680.000: 680.347, 679.867, 679.501 y 680.096) y su incumbente tampoco se movió (≈ 587.000–593.000), con un gap de 15–16 %. SUR se comportó igual, con un gap cercano a 1 %. Si el cuello de botella fuera la búsqueda de soluciones, un warm start habría movido la incumbente; como no lo hizo, el límite está en la relajación lineal del modelo por avión, que sigue siendo débil. Por lo tanto, cualquier mejora que actúe solo sobre la solución (warm start, premios en el objetivo, más tiempo de cómputo) no puede reducir el gap de forma significativa. Se descartó además romper la simetría entre aviones de AND: todos tienen distinta ventana de mantenimiento (de 6 a 12 h y en días distintos) y payload de 50, 52 o 54 t, por lo que no son intercambiables y ordenarlos por uso excluiría soluciones válidas.

  **Decisión.** El warm start y `iterar_conexiones.py` (bonificación) quedan **descartados para uso futuro** como vía de mejora del margen: no aportaron beneficio medible y, en el caso de la bonificación, lo empeoraron. Los cambios se conservan en el código como evidencia, pero la solución de referencia sigue siendo la del Hito 6 (6.260.460 USD). Cabe señalar que la diferencia de gaps entre corridas no se traduce en diferencias de margen, pues el gap mide solo la optimización de los vuelos con carga directa, mientras que el margen final proviene de la asignación multitramos posterior (que por sí sola aportó unos 465.000 USD sin cambiar vuelos).

  **Líneas que quedan abiertas** (cambios estructurales, no de ajuste): fortalecer la formulación del paso 2 para subir la cota (por ejemplo, acotar la variable de carga por la demanda residual, que no se llegó a probar de forma aislada), revisar si la regla de 10 t (`min_tons_per_extra_stop`) aplica a todo vuelo cargado o solo a ciertas escalas, e incorporar las conexiones dentro de la decisión de vuelos en lugar de tratarlas como posproceso.


**Hito 8**
- **Integrante**: Fernando Mora 
- **Fecha**: 1 de Octubre de 2026
- **Desarrollo**: Se hicieron un par de cambios en las funciones que producen los resultados json y el html de la animación para añadir los KPI en el tiempo y como evolucionan. Para correrlo se usa el main pero seleccionar NUEVA BASE para ver los KPI porque la vieja no tiene todos los datos necesarios. No está relacionado a los filtros eso si.

