# Modelo Matemático

El modelo decide **simultáneamente**:

1. **Qué vuelos** opera cada avión (rotaciones cerradas semanales)
2. **Cómo se asigna la carga** sobre los vuelos programados

Se formula como un **Programa Lineal Entero Mixto (MILP)** de dos capas acopladas sobre una red tiempo-espacio con arquitectura **hub & spoke centrada en Miami (MIA)**.

La carga puede viajar en un vuelo directo al hub, o hacer **transbordo en MIA** transfiriéndose entre aviones distintos el mismo día para redistribuirse hacia su destino final.

## 1. Conjuntos e Índices

| Símbolo                              | Descripción                                                                                      |
| ------------------------------------- | ------------------------------------------------------------------------------------------------- |
| $\mathcal{K}$                       | Conjunto de aviones de la flota,$k \in \mathcal{K}$                                             |
| $\mathcal{A}$                       | Conjunto de aeropuertos de la red,$a \in \mathcal{A}$                                           |
| $\mathcal{L}$                       | Catálogo de tramos permitidos,$(a,b) \in \mathcal{L} \subseteq \mathcal{A} \times \mathcal{A}$ |
| $\mathcal{D}$                       | Días de la semana,$d \in \mathcal{D} = \{1,2,...,7\}$ (cíclico: $d=8 \equiv d=1$)           |
| $\mathcal{OD}$                      | Pares origen-destino con demanda,$(i,j) \in \mathcal{OD}$                                       |
| $\mathcal{P}$                       | Conjunto de operadores (filiales),$p \in \mathcal{P}$, $|\mathcal{P}|=4$                      |
| $\mathcal{H} \subseteq \mathcal{A}$ | Aeropuertos hub de la red (principalmente MIA)                                                    |

## 2. Parámetros

### Flota, Operadores y Aeropuertos

Cada avión $k$ pertenece de forma **permanente** a un operador $\text{op}_k \in \mathcal{P}$. Esta asignación es un **dato del problema**, no una variable de decisión. Los operadores difieren en estructura de costos y en derechos de tráfico.

| Símbolo                                        | Descripción                                                                         |
| ----------------------------------------------- | ------------------------------------------------------------------------------------ |
| $\text{cap}_k$                                | Capacidad máxima de carga del avión$k$ (toneladas)                               |
| $\text{op}_k \in \mathcal{P}$                 | Operador permanente del avión$k$                                                  |
| $\text{TAT}$                                  | Tiempo mínimo en tierra entre vuelos consecutivos (turnaround)                      |
| $[m_k^{\text{ini}}, m_k^{\text{fin}}, a_k^m]$ | Ventana de mantenimiento del avión$k$: días de inicio/fin y aeropuerto requerido |

### Tramos (Legs)

| Símbolo        | Descripción                                           |
| --------------- | ------------------------------------------------------ |
| $\tau_{ab}$   | Duración de vuelo del tramo$(a \to b)$ (horas)      |
| $\delta_{ab}$ | Distancia del tramo$(a \to b)$ (km)                  |
| $\text{lf}_b$ | Tasa de aterrizaje en el aeropuerto destino$b$ (USD) |

### Demanda

| Símbolo                          | Descripción                                                                                       |
| --------------------------------- | -------------------------------------------------------------------------------------------------- |
| $\overline{\text{dem}}_{ij}^d$  | Toneladas**máximas** disponibles del par $(i,j)$ el día $d$ (techo de mercado)         |
| $\underline{\text{dem}}_{ij}^d$ | Toneladas**mínimas** comprometidas del par $(i,j)$ el día $d$ (compromiso contractual) |
| $r_{ij}$                        | Tarifa por tonelada del par$(i,j)$ (USD/ton)                                                     |
| $f_{ij}^{\min}$                 | Frecuencia mínima comprometida para el par$(i,j)$ (vuelos/semana)                               |

### Costos Operacionales por Operador

Los costos de combustible y hora de vuelo son **específicos del operador** $p = \text{op}_k$, ya que cada filial opera con estructuras contractuales y bases distintas:

| Símbolo              | Descripción                                                                                    |
| --------------------- | ----------------------------------------------------------------------------------------------- |
| $c_k^{\text{fuel}}$ | Costo de combustible del avión$k$ por hora (USD/hr) — varía por operador $\text{op}_k$   |
| $c_k^{\text{hr}}$   | Costo horario del avión$k$ — tripulación + overhead — varía por operador $\text{op}_k$ |
| $c^{\text{man}}$    | Costo de manipulación de carga por tonelada (USD/ton) — uniforme en el hub                    |

Costo fijo de operar el tramo $(a \to b)$ con el avión $k$:

$$
c_{k,ab} = \underbrace{(c_k^{\text{fuel}} + c_k^{\text{hr}})}_{\text{varía por operador}} \cdot \tau_{ab} + \text{lf}_b
$$

Tarifa neta de manipulación por par OD:

$$
\tilde{r}_{ij} = r_{ij} - c^{\text{man}}
$$

### Derechos de Tráfico

Los derechos de tráfico son **específicos de cada operador** $p$ y determinan en qué países puede operar cada avión. No son intercambiables entre filiales.

| Símbolo                       | Descripción                                                          |
| ------------------------------ | --------------------------------------------------------------------- |
| $\text{TR}_{p,\text{país}}$ | $= 1$ si el operador $p$ tiene derechos en ese país, $0$ si no |

## 3. Variables de Decisión

### Capa 1: Flujo de Aviones (Variables Binarias)

$$
y_{k,a,b,d} \in \{0, 1\}
$$

> **Interpretación**: $y_{k,a,b,d} = 1$ si y solo si el avión $k$ opera el tramo $(a \to b)$ saliendo el día $d$.

$$
w_{k,a,d} \in \{0, 1\}
$$

> **Interpretación**: $w_{k,a,d} = 1$ si el avión $k$ permanece en tierra en el aeropuerto $a$ durante el día $d$.

### Capa 2: Flujo de Carga (Variables Continuas)

$$
x_{ij,k,a,b,d} \geq 0
$$

> **Interpretación**: Toneladas de carga del par $(i \to j)$ transportadas **en el avión $k$** a través del tramo $(a \to b)$ el día $d$.

> **Con transbordo**: El índice $k$ identifica el avión que lleva esa carga en **ese tramo específico**. Tras un transbordo en MIA, la misma carga puede continuar en un avión $k' \neq k$.

## 4. Función Objetivo

Maximizar el **margen neto semanal** de la red:

$$
\max \quad Z = \underbrace{\sum_{(i,j)} \sum_{k} \sum_{(a,b)} \sum_{d} \tilde{r}_{ij} \cdot x_{ij,k,a,b,d}}_{\text{Ingresos netos por carga (descontando manipulación)}} - \underbrace{\sum_{k} \sum_{(a,b)} \sum_{d} c_{k,ab} \cdot y_{k,a,b,d}}_{\text{Costos fijos de vuelos (varían por operador)}}
$$

> El costo $c_{k,ab}$ **difiere entre aviones** según el operador $\text{op}_k$ al que pertenecen, reflejando las distintas estructuras de costos de cada filial.

## 5. Restricciones

### [R1] Balance de Flujo de Aviones — Rotación Cerrada

Para cada avión $k$, aeropuerto $a$ y día $d$:

$$
\sum_{b \,:\, (a,b) \in \mathcal{L}} y_{k,a,b,d} + w_{k,a,d} = \sum_{b \,:\, (b,a) \in \mathcal{L}} y_{k,b,a,d-1} + w_{k,a,d-1} \quad \forall\, k,\, a,\, d
$$

> **Explicación**: Las "salidas" del avión $k$ desde $(a, d)$ igualan sus "llegadas" a $(a, d)$ provenientes del día anterior. Los índices son módulo 7, haciendo el ciclo semanal cerrado.

### [R2] Un Avión Solo Puede Hacer Una Cosa por Día

$$
\sum_{b \,:\, (a,b) \in \mathcal{L}} y_{k,a,b,d} + w_{k,a,d} \leq 1 \quad \forall\, k,\, a,\, d
$$

> **¿Por qué es necesaria si ya existe R1?** R1 solo garantiza *conservación de flujo* (lo que entra = lo que sale), pero no impide que un avión "se clone". Sin R2, una solución matemáticamente válida para R1 podría tener al avión $k$ volando simultáneamente de $a$ hacia $b$ **y** hacia $c$ en el mismo día $d$ — dos arcos de salida, ambos de valor 1. R2 prohíbe esto explícitamente: el avión hace **exactamente una cosa** por día (un vuelo, o espera en tierra en un aeropuerto).

### [R3] Capacidad de Carga — Restricción de Acoplamiento

$$
\sum_{(i,j) \in \mathcal{OD}} x_{ij,k,a,b,d} \leq \text{cap}_k \cdot y_{k,a,b,d} \quad \forall\, k,\, (a,b) \in \mathcal{L},\, d
$$

> **Rol central**: Si $y_{k,a,b,d} = 0$ (el vuelo no existe), fuerza $x_{ij,k,a,b,d} = 0$ para toda carga. Es la restricción que **enlaza ambas capas** del modelo.

### [R4'] Arquitectura Hub & Spoke — Transbordo Obligatorio por MIA

La red opera bajo una arquitectura **hub & spoke centrada en Miami**. Esto implica que toda la carga entre orígenes y destinos fuera del hub **debe transitar por MIA**. La restricción de conservación se aplica **exclusivamente en MIA** como nodo de consolidación y redistribución:

Para cada par $(i,j)$ con $i \neq \text{MIA}$ y $j \neq \text{MIA}$, y para cada día $d$:

$$
\underbrace{\sum_{k \in \mathcal{K}} \sum_{a \,:\, (a,\,\text{MIA}) \in \mathcal{L}} x_{ij,k,a,\text{MIA},d}}_{\text{carga }(i{\to}j)\text{ que LLEGA a MIA el día }d} \;=\; \underbrace{\sum_{k \in \mathcal{K}} \sum_{b \,:\, (\text{MIA},b) \in \mathcal{L}} x_{ij,k,\text{MIA},b,d}}_{\text{carga }(i{\to}j)\text{ que SALE de MIA el día }d}
$$

> **¿Qué dice?** Toda la carga del par $(i,j)$ que llega a Miami en cualquier avión el día $d$ debe salir de Miami en cualquier avión ese **mismo día $d$** hacia el destino final.

> **¿Por qué hub obligatorio?** El enunciado establece que ~70% del volumen viaja hacia el hub donde la carga se consolida antes de redistribuirse. La arquitectura hub & spoke centraliza en MIA la función de consolidación. No existen rutas directas entre spokes; toda carga pasa por el hub.

> **Transbordo el mismo día**: El transbordo físico (descarga + consolidación + recarga) ocurre en tierra durante la escala, dentro del mismo día operacional.

### [R5] Satisfacción de Demanda — Cotas Diarias

Las toneladas cargadas en el origen $i$ para el par $(i,j)$ el día $d$ deben respetar tanto el **techo de mercado** como el **compromiso mínimo contractual**:

$$
\underline{\text{dem}}_{ij}^d \;\leq\; \sum_{k \in \mathcal{K}} \sum_{b \,:\, (i,b) \in \mathcal{L}} x_{ij,k,i,b,d} \;\leq\; \overline{\text{dem}}_{ij}^d \quad \forall\, (i,j) \in \mathcal{OD},\, d \in \mathcal{D}
$$

> **Cota superior** $\overline{\text{dem}}_{ij}^d$: No se puede embarcar más carga de la que hay disponible ese día en el mercado.

> **Cota inferior** $\underline{\text{dem}}_{ij}^d$: Si la aerolínea tiene **compromisos contractuales** con ciertos embarcadores, debe garantizar al menos ese volumen mínimo diario. Para pares sin compromiso mínimo, $\underline{\text{dem}}_{ij}^d = 0$.

### [R6] Cierre de Flujo en el Destino

Para garantizar que la carga del par $(i,j)$ **no continúe más allá de su destino** $j$ (es decir, que $j$ actúe como nodo sumidero del commodity), se prohíbe cualquier flujo de carga $(i,j)$ saliendo desde el destino $j$:

$$
x_{ij,k,j,b,d} = 0 \quad \forall\, (i,j) \in \mathcal{OD},\, k \in \mathcal{K},\, b \in \mathcal{A},\, d \in \mathcal{D}
$$

> **¿Por qué es necesaria?** Sin esta restricción, el modelo podría enviar carga $(i,j)$ más allá de $j$ para "vaciar" su cupo en el avión y cargar otra demanda más rentable, generando flujos artificiales. Al fijar en cero todos los arcos de salida desde $j$ para el commodity $(i,j)$, se garantiza que $j$ es el punto final de la cadena logística de esa carga.

> **Analogía**: En una red de flujo, el nodo destino $j$ es un **sumidero** (sink) para el commodity $(i,j)$. Esta restricción impone esa condición formalmente.

### [R7] Frecuencia Mínima Comprometida

$$
\sum_{k \in \mathcal{K}} \sum_{d \in \mathcal{D}} y_{k,i,j,d} \geq f_{ij}^{\min} \quad \forall\, (i,j) \in \mathcal{OD} \;:\; f_{ij}^{\min} > 0
$$

### [R8] Derechos de Tráfico por Operador

Los derechos de tráfico son específicos del **operador** $\text{op}_k$ de cada avión. Un avión no puede operar un tramo si su operador no tiene derechos en el país de origen **o** en el país de destino:

$$
y_{k,a,b,d} = 0 \quad \text{si } \text{TR}_{\text{op}_k,\,\text{país}(a)} = 0 \;\text{ o }\; \text{TR}_{\text{op}_k,\,\text{país}(b)} = 0 \quad \forall\, k,\, (a,b),\, d
$$

> **Implicación**: Dos aviones de distintos operadores pueden tener acceso a tramos completamente distintos, incluso cuando el tramo físico es el mismo. Los aviones **no son intercambiables** entre operadores.

> **Implementación**: En la práctica se pre-filtra el conjunto de variables $y_{k,a,b,d}$ admisibles, eliminando las inviables antes de entregar el modelo al solver.

### [R9] Ventana de Mantenimiento

$$
y_{k,a,b,d} = 0 \quad \forall\, d \in [m_k^{\text{ini}}, m_k^{\text{fin}}],\; (a,b) \in \mathcal{L}
$$

$$
w_{k,\, a_k^m,\, d} = 1 \quad \forall\, d \in [m_k^{\text{ini}}, m_k^{\text{fin}}]
$$

> El avión $k$ debe permanecer inmovilizado en su aeropuerto de mantenimiento $a_k^m$ durante toda la ventana.

### [R10] Tiempo Mínimo en Tierra (Turnaround)

Si el avión $k$ llega al aeropuerto $a$ tras el tramo $(b \to a)$ el día $d$, el siguiente vuelo desde $a$ debe respetar el tiempo mínimo TAT:

$$
y_{k,b,a,d} + y_{k,a,c,d'} \leq 1 \quad \text{si la diferencia horaria entre llegada y salida} < \text{TAT}
$$

## 6. Dominio de Variables

$$
y_{k,a,b,d} \in \{0, 1\} \quad \forall\, k \in \mathcal{K},\; (a,b) \in \mathcal{L},\; d \in \mathcal{D}
$$

$$
w_{k,a,d} \in \{0, 1\} \quad \forall\, k \in \mathcal{K},\; a \in \mathcal{A},\; d \in \mathcal{D}
$$

$$
x_{ij,k,a,b,d} \geq 0 \quad \forall\, (i,j) \in \mathcal{OD},\; k \in \mathcal{K},\; (a,b) \in \mathcal{L},\; d \in \mathcal{D}
$$
