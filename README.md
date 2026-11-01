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
- **Modelos MILP integrados** (alternativa a la heurística; guía para el equipo en `docs/README_modelos_milp.md`):
  - `gurobi_model.py`: MILP completo (posición-indexado, tiempo continuo) con las mismas reglas que la heurística.
  - `warm_start_milp.py`: resuelve ese MILP partiendo de una corrida de la heurística (warm start). Detalles en `docs/README_milp_completo.md`.
  - `milp_continuo_transbordos.py`: el mismo MILP con los transbordos dentro del modelo; `--libres` indica qué aviones decide. Detalles en `docs/README_milp_continuo_transbordos.md`.
  - `modelo_integrado_espacio_tiempo.py`: MILP integrado en red espacio-tiempo (vuelos, carga multitramo y transbordos en un solo modelo); `--libres` indica qué aviones decide. Detalles en `docs/README_modelo_integrado_espacio_tiempo.md`.
  - `busqueda_por_avion.py`: recorre los aviones uno por uno con el modelo anterior (un avión libre, DT = 1 h por defecto), aceptando cada mejora validada; pensado para correr de noche. Uso en `docs/README_modelos_milp.md`.
  - `generar_instancia.py`: arma instancias reducidas (subconjunto de operadores y de aeropuertos) para comparar métodos con un óptimo de referencia; todos los códigos las leen con la variable `RAW`. Resultados y algoritmo candidato en `docs/README_instancias_reducidas.md`.
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
python main.py
```

**Partir de una corrida anterior (refinar la grilla).** `main.py` pregunta por una carpeta de una corrida anterior; también se puede fijar con la variable `START`. Los vuelos de esa corrida se usan como punto de partida de los pasos 1 y 2 (solo con `SOLVER=gurobi`), sus tramos entran a los candidatos del paso 2 y, al final, se conserva la mejor de las dos soluciones. Como una corrida de 2 h es factible con DT = 1 h, 30 min o 15 min, la grilla fina ya no puede dar menos que la corrida de la que parte (antes, con el mismo `TLIM`, daba menos: 6,08 M con 1 h frente a 6,31 M con 2 h).

**2. Levantar la Visualización (Interfaz Gráfica)**
Para levantar el servidor web y visualizar los resultados recién exportados en tu navegador, ejecuta:

```bash
python visualization/main.py
```

**3. Modelos MILP integrados (opcional)**
Resuelven el problema con un MILP partiendo de la corrida de 2 h de la heurística, y dejan sus resultados en `data/results/` con el mismo formato que la heurística (se ven en el visualizador). Para mejorar la solución hay que pedir la heurística NoRel de Gurobi (`--norel`), porque viene apagada por defecto. El MILP de tiempo continuo, con todos los aviones a la vez, es el que mejoró la heurística en +66.581 USD en 8 minutos:

```bash
python warm_start_milp.py --tiempo 3600 --norel 2880 --salida data/results/MILP_completo_1h
```

El mejor resultado hasta ahora, **6.435.839 USD**, se obtuvo encadenando la heurística con DT = 1 h (partiendo de la corrida de 2 h) y este MILP partiendo de ese resultado. La secuencia de comandos está en la sección 4 de `docs/README_modelos_milp.md`.

Qué modelo usar, los resultados guardados, los supuestos y las opciones de cada script están en `docs/README_modelos_milp.md`.

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

**Hito 9**
- **Integrante**: Franco Nicolai
- **Fecha**: 2 de Octubre de 2026
- **Desarrollo**: Se ejecutó el modelo con distintos intervalos temporales de discretización (1 hora, 30 minutos y 15 minutos) para observar el impacto en los resultados. Además, se realizaron sustanciales mejoras de experiencia de usuario en la interfaz gráfica web:
  - Diseño *Glassmorphism* consolidado en todos los controles, paneles y ventanas modales (filtros, kpis, reproductor), unificando el estilo con curvaturas uniformes (14px).
  - Renovación del selector de datos en un menú desplegable fluido y transparente que escanea dinámicamente los sets de datos.
  - Configuración de colores semánticos en las trazas de los aviones para representar la ocupación de carga (Verde: Alta >70%, Amarillo: Media 40-70%, Rojo: Baja <40%), junto a una leyenda visual.
  - Implementación de etiquetas dinámicas integradas en el lienzo para cada aeropuerto (carga in/out) y aeronave que aparecen gradualmente de acuerdo al nivel de zoom sin invadir la vista global.

**Hito 10**
- **Integrante**: Fernando Mora
- **Fecha**: 2 de Octubre de 2026
- **Desarrollo**: Se hizo una prueba con distintos intervalos ttr_h usando de base el itinerario generado por Ejecucion_con_intervalos_de_2_horas, para esto es necesario correr variabilidad_ttrh.py. Los resultados fueron:

 TTR_H  | margen_usd | toneladas | transbordos | entre_operadores | violaciones
  0.00  |   6310378  |  11806.2  |         31  |              18  |          0
  0.25  |   6292713  |  11800.2  |         26  |              15  |          0
  0.50  |   6298840  |  11800.2  |         36  |              18  |          0
  1.00  |   6298194  |  11800.2  |         29  |              18  |          0
  2.00  |   6310307  |  11806.2  |         33  |              19  |          0


**Hito 11**
- **Integrante**: Vicente Olea (con apoyo de Claude Code)
- **Fecha**: 15 al 18 de Octubre de 2026
- **Desarrollo**: Se retomó el MILP integrado para inyectarle la solución de la heurística como punto de partida (MIP start). Rama `milp-completo-warm-start`; guía para el equipo en `docs/README_modelos_milp.md`.
  - `gurobi_model.py` se reescribió con las mismas reglas que `src/validador.py` (TAT 70/50/90 min, rotación cerrada, mantenimiento, frecuencias, mínimo 10 t, demanda por día, costos reales y handling) y su propia carga de datos, porque `estructuras_datos.py` se había borrado. Pasó de 2,2 millones a 84 mil restricciones.
  - `warm_start_milp.py` inyecta la corrida de 2 h, verifica que cumpla todas las restricciones, resuelve con Gurobi, agrega los transbordos con el paso final de la heurística, valida y exporta. Con 8 minutos y la heurística NoRel de Gurobi llegó a un margen validado de **6.376.959 USD (+66.581, +1,1 %)**; una corrida cortada con Ctrl+C llegó a 6.359.560 USD.
  - `modelo_integrado_espacio_tiempo.py` y `milp_continuo_transbordos.py` incluyen los transbordos dentro del modelo. Con los vuelos fijos reproducen exactamente la heurística (6.310.378 USD), pero con todos los aviones libres no caben en un computador de 8 GB (11,9 M variables y 8,8 M binarias de transbordo, respectivamente); se pueden resolver liberando pocos aviones (`--libres`).
  - Los resultados MILP calculan sus KPI con `src/validador.py`, igual que la heurística desde el commit "centraliza KPIs y calidad de solución", y quedan en `data/results/MILP_*`, visibles en el visualizador.

**Hito 12**
- **Integrante**: Vicente Olea (con apoyo de Claude Code)
- **Fecha**: 21 al 23 de Octubre de 2026
- **Desarrollo**: Mejoras de formulación y de la heurística para grillas finas. Rama `milp-completo-warm-start`.
  - **Diagnóstico.** La heurística empeoraba al refinar la grilla con el mismo `TLIM` (2 h: 6,31 M; 1 h: 6,08 M; 30 min: 6,21 M; 15 min: 6,08 M), aunque toda solución de 2 h es factible en las grillas finas.
  - **Punto de partida entre grillas (`START`).** `main.py` (o la variable `START`) recibe la carpeta de una corrida anterior. Sus vuelos son el punto de partida del paso 1. El paso 2 mejora esa corrida operador por operador: el estado inicial de los demás operadores es el de la corrida, con su carga directa, y la carga que llega en varios tramos o con transbordo se descuenta de la demanda. Al final se evalúa la corrida de partida con el mismo paso 3 y validador, y se conserva la mejor, así que refinar la grilla ya no puede empeorar el resultado. Solo funciona con `SOLVER=gurobi`.
  - **Resultado con DT = 1 h desde la corrida de 2 h** (`TLIM=1500`, `TLIM_OP=420`): margen validado **6.314.758 USD**, contra 6.310.378 de la corrida de partida (ambos con carga a gap 1e-6), con 334 vuelos, 16 aviones y 25 de 25 frecuencias. La corrida original con 1 h daba 6.081.562. La mejora sobre la de 2 h (+4.380) está dentro del ruido de unos 30.000 USD que se midió en el Hito 7. Lo que sí es nuevo es la garantía de no empeorar.
  - **Primeros intentos que no funcionaron, y por qué:**
    - Si el paso 2 usaba como estado de los demás operadores la solución del paso 1, el punto de partida violaba las frecuencias residuales y Gurobi lo descartaba.
    - Si no se descontaba la carga indirecta, cada operador "vendía" de nuevo demanda que ya estaba servida por conexiones: el paso 2 subió ~197 mil, pero el margen final bajó a 6.288.728.
  - Coincide con la conclusión del Hito 7: el límite está en la formulación del paso 2 (AND quedó con gap de 12,9 %) y en que los pasos 1 y 2 solo ven carga directa.
  - Modelos exactos: `--sin-transbordos`, `--enlace-fuerte` y `--relajacion` en `modelo_integrado_espacio_tiempo.py`, Benders (`docs/README_benders.md`) y `busqueda_por_avion.py`. Con AC-10 libre y DT = 1 h, el modelo exacto llegó a **6.316.063 USD**. Las desigualdades de enlace fuerte `B ≤ min(D, P)·z` bajan la relajación lineal de 6.588.618 a 6.461.469 con AC-10 libre, y de ~7,285 M a ~7,00 M con todos los aviones libres y sin transbordos. Detalles en `docs/README_modelo_integrado_espacio_tiempo.md`.
  - **Encadenamiento heurística → MILP (23 de octubre).**
    - Heurística con DT = 1 h partiendo de la corrida de 2 h, más una segunda iteración: 6.336.259.
    - `warm_start_milp.py` con 1.500 s de NoRel partiendo de esa solución: **6.435.839 USD**, el mejor margen validado hasta ahora (+125.461 sobre la heurística original y +58.880 sobre el mejor MILP anterior). Validador OK, 330 vuelos, 16 aviones y 25 de 25 frecuencias.
    - La mejora viene de cortar vuelos poco rentables: el costo de vuelos baja 562 mil y los ingresos, 452 mil.
    - Una segunda pasada del MILP empeora el margen con transbordos (6.413.268), así que basta con una.
    - La secuencia de comandos está en `docs/README_modelos_milp.md`, sección 4.
  - **Correcciones y mejoras de la heurística:**
    - `GAP_CARGA` del paso 3 bajó de 0,1 % a 1e-6. El objetivo de carga es de ~17,8 M, así que 0,1 % dejaba hasta ~18 mil USD sin asignar y metía ruido: la misma solución valía 6.319.306 con el gap anterior y 6.336.259 con el nuevo.
    - El JSON de indicadores del paso 2 se escribe en UTF-8. En Windows salía en cp1252 y el visualizador no lo podía leer.
    - `SOLO=<operador>` junto con `START` conserva los vuelos de los demás operadores.
    - Nuevo `correr_heuristica.py`: corre los tres pasos sin el menú interactivo, para encadenar corridas.
  - **`warm_start_milp.py`:**
    - acepta como `--corrida` una salida del propio MILP: con horarios redondeados, solo fija vuelos y binarias, y Gurobi recalcula los horarios;
    - el chequeo de mantenimiento tolera 1e-4 h.
  - **Lo que no sirvió:**
    - una tercera iteración de la heurística, que converge (los operadores mejoran menos de 2 mil);
    - el enlace fuerte en el paso 2: la cota de AND casi no cambia, probablemente porque Gurobi ya lo deduce;
    - darle a AND 40 min en vez de 7: la cota queda en ~692 mil contra ~590 mil de solución, un gap de 17 %. El límite es la relajación del paso 2, como en el Hito 7.
  - **Análisis de la flota de AND.** Varios aviones casi no vuelan, aunque queda demanda hacia MIA sin servir (UIO→MIA 691 t/semana). Esa demanda no es rentable sola, porque la vuelta va vacía. Lo que sí podría pagar son triángulos (MIA→VCP→BOG→MIA, ~+85 mil) o pares con carga en ambos sentidos (GRU↔MAO), pero la heurística no los encontró. Hace falta una formulación que valore las conexiones al decidir los vuelos.

**Hito 13**
- **Integrante**: Vicente Olea (con apoyo de Claude Code)
- **Fecha**: 30 de Octubre de 2026
- **Desarrollo**: Instancias reducidas para elegir el algoritmo que se llevará a un computador con más RAM. Detalles en `docs/README_instancias_reducidas.md`.
  - `generar_instancia.py` filtra operadores y aeropuertos, escala la demanda para conservar la relación demanda/capacidad y escala las frecuencias mínimas. `gurobi_model.py` y `main.py` ahora leen los datos desde `RAW`, como el resto de los códigos.
  - **En R4** (BRA y PAC, 4 aviones, 10 aeropuertos en torno a MIA; 400 mil variables en el modelo exacto con DT = 2 h), partiendo de la heurística (770.625):
    - búsqueda avión por avión con el modelo exacto y transbordos: **836.779 (+8,6 %)** en 16 min;
    - modelo completo con NoRel: +5,3 %;
    - MILP continuo con NoRel: +4,7 %;
    - modelo completo con ramificación (40 min): +3,1 %, con un gap de 42 %.
  - **Conclusión.** La relajación del modelo completo se debilita al liberar más aviones (2,4 % con 1 de 19 libres; 27 % con 2 de 4; 47 % con 4 de 4), así que más RAM no permite resolverlo hasta el óptimo. El candidato es una **búsqueda exacta por vecindarios** sobre la red espacio-tiempo con transbordos, con vecindarios que crecen según la RAM disponible (1 avión con DT = 1 h en 8 GB; un operador completo con DT = 1 h en 64 GB).
  - **Técnicas del IPre de diagramas de decisión (31 de octubre).** Se probó reoptimizar cada avión con un camino más largo sobre su red espacio-tiempo, al estilo del solver SDD (`reoptimizacion_camino_mas_largo.py`). Es 30–100 veces más rápido que el modelo exacto por avión, pero sus propuestas son peores: los vuelos que más valen son los que alimentan transbordos, y ese valor no se puede asignar a cada arco por separado. La búsqueda exacta con 60 s por avión logra casi lo mismo que con 240 s (834.798 contra 836.779 en R4), en la mitad del tiempo.

**Hito 14**
- **Integrante**: Vicente Olea (con apoyo de Claude Code)
- **Fecha**: 1 de Noviembre de 2026
- **Desarrollo**: Búsqueda por vecindarios variables y en tiempo continuo. Detalles en `docs/README_instancias_reducidas.md`, secciones 9 y 10.
  - **Opciones nuevas de `busqueda_por_avion.py`:**
    - `--vecindario avion,par,operador`: vecindarios variables; los pares se eligen por transbordos y aeropuertos compartidos;
    - `--orden uso`: recorre primero los aviones menos usados;
    - `--modelo continuo`: cada vecindario con el MILP de posiciones con transbordos, sin grilla;
    - `--deposito-agregado`: −30 % de variables en la red espacio-tiempo;
    - `--horas` y `--metodo-raiz`.
  - **R10 (AND y BRA, 10 aviones), partiendo de la heurística con DT = 1 h (2.519.684):**
    - red espacio-tiempo con aviones y pares: **+3,0 % en 1,4 h**; los pares sacaron a la búsqueda del óptimo local de un avión (sin ellos, +0,7 %);
    - MILP continuo con NoRel: **+5,5 % en 10 min**;
    - **MILP continuo + búsqueda continua por vecindarios: 2.682.344 (+6,5 %).**
  - **Por qué gana el tiempo continuo.** La red espacio-tiempo redondea la duración de cada tramo hacia arriba hasta completar bloques enteros. En R10, cinco aviones del MILP continuo ocupan la semana completa y no caben en ninguna grilla. Bajar a DT = 30 min aportó solo +0,13 %.
  - **Instancia completa en el notebook:** la búsqueda en la red espacio-tiempo no avanza (NoRel no cambia vuelos en 15 min y la raíz no cabe). La búsqueda continua desde la mejor solución (6.435.839) sí mejora: **6.451.322 (+15.483), nuevo mejor margen validado** (`data/results/Busqueda_continua_desde_MILP_dt1`), con 300 s por avión durante 6,9 h.
  - **Candidato actualizado** (sección 11 de `docs/README_instancias_reducidas.md`), dos ramas en paralelo desde la heurística:
    - (A) MILP continuo global con NoRel, repetido con distintas semillas, y luego búsqueda continua por vecindarios;
    - (B) búsqueda en la red espacio-tiempo y luego búsqueda continua.
  - En R4 ganó la rama B (845.742, +9,7 %); en R10, la A (+6,5 %). NoRel varía ~2 % entre corridas. Una segunda pasada global del MILP empeora con transbordos.
