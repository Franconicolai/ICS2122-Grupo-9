# ICS2122 Taller de Investigación Operativa (Capstone), grupo 9

**FLEET ASSIGNMENT Y CARGO ROUTING INTEGRADOS SOBRE UNA RED HUB-AND-SPOKE SEMANAL**

## Estructura del repositorio

El proyecto está modularizado en cuatro ejes principales (datos, documentación, código fuente y configuración) para separar el procesamiento de la lógica matemática:

- `data/`: Base de datos SQLite y datos de entrada originales.
  - `data.sqlite`: Base de datos principal de trabajo.
  - `raw_data/`: Resguardo de los datos brutos de entrada.
- `docs/`: Documentación del proyecto e informes en formato LaTeX (ej. `appendix1.tex`).
- `src/`: Código fuente del modelo de optimización.
  - `main.py`: Script para ejecutar exclusivamente el modelo Gurobi sin interfaz web.
  - `estructuras_datos.py`: Carga y estructuración funcional de datos desde SQLite.
  - `gurobi_model.py`: Construcción del modelo de optimización (variables, función objetivo y restricciones).
  - `exportador_resultados.py`: Generación de reportes y extracción de indicadores clave (KPIs).
- `visualization/`: Interfaz gráfica web interactiva que despliega los resultados del modelo geográficamente.
  - `main.py`: Script que levanta el servidor web y abre automáticamente la plataforma visual en el navegador.
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
