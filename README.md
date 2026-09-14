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

