# Interchanges: aviones operados momentáneamente por otra filial

## La regla

El diccionario de datos define `interchange_airports.csv` así: es una matriz aeropuerto × operador, con 1 si ese
operador está habilitado para tomar la operación de una aeronave en ese aeropuerto. Un interchange es el punto
donde un avión pasa a ser operado por otra filial a mitad de rotación. Solo es posible si ambas filiales están
habilitadas en ese aeropuerto.

Hasta el Hito 16 los modelos usaban ese archivo solo como soporte de los transbordos de carga, y cada avión volaba
toda la semana con su operador base. El profesor confirmó (9 de noviembre de 2026) que se permiten tanto los
transbordos como los interchanges. Que el operador "no cambie" significa que la rotación cíclica termina con el
operador base, no que el avión no pueda pasar momentáneamente a otra filial.

## Por qué importa

Cada vuelo paga el costo ex-fuel por hora y el handling de la filial que lo opera, y necesita los derechos de
tráfico de esa filial:

| Filial | Ex-fuel (USD/h) | Handling (USD/t) | Sin derechos en |
|---|---|---|---|
| PAC | 3.300 | 42 | Alemania, Bélgica, Cabo Verde, Guatemala, Guyana, México |
| BRA | 3.800 | 52 | — |
| SUR | 4.250 | 39 | — |
| AND | 4.450 | 44 | Guyana |

Pasar un vuelo de AND a PAC ahorra 1.150 USD por hora de bloque. La flota tiene 8 aviones AND, 7 SUR, 2 BRA y
2 PAC. En la mejor solución vuela 1.308 h por semana.

## Supuestos

1. El interchange no tiene costo ni tiempo en tierra adicional (el TAT no cambia).
2. Entre dos vuelos seguidos, el avión solo cambia de filial en un aeropuerto donde ambas están habilitadas.
3. Borde de la semana (`--borde flexible`, por defecto desde el 10-11-2026): basta que el avión vuelva a su
   operador base en tierra, en el aeropuerto donde parte y termina la semana, si allí están habilitadas ambas
   filiales. Con `--borde base` (o `BORDE=base`), el primer y el último vuelo de la rotación los opera el operador
   base. El grupo decidió usar el borde flexible; los aeropuertos de borde son MIA, BOG, MDE (las 4 filiales), LAX
   (sin BRA) y GRU (sin PAC).
4. Cada vuelo usa los derechos de tráfico, el ex-fuel y el handling de la filial que lo opera.
5. Un transbordo exige que las filiales que operan el vuelo que entrega y el que recibe estén habilitadas en el
   aeropuerto, y el handling lo paga la filial del vuelo que recibe.
6. El mantenimiento no depende de la filial.

Los supuestos 1 y 3 son los que más conviene confirmar con el profesor.

## Implementación

- **Formato.** Cada vuelo del itinerario puede llevar `op` con la filial que lo opera. Sin `op`, lo opera el
  operador base, así que las soluciones anteriores se validan igual que antes. En `itinerario_vuelos.csv`, la
  columna `operador` es la filial de cada vuelo, y `warm_start_milp.leer_itinerario` la lee de vuelta.
- **`src/validador.py`.** Usa la filial de cada vuelo en derechos, costos, handling y soporte de transbordos.
  Verifica los supuestos 2 y 3 (opciones `interchanges` y `borde_operador_base`), y reporta los KPI `interchanges`
  y `vuelos_otra_filial`.
- **`src/asignacion_carga_multitramos.py`.** El submodelo de carga usa la filial de cada vuelo para el handling y el
  soporte de los transbordos.
- **`src/asignar_operadores.py`.** Con los vuelos y la carga fijos, elige la filial de cada vuelo. Para cada avión
  es un camino más corto en un grafo de capas (un nodo por vuelo y filial), resuelto por programación dinámica:
  da el óptimo exacto en milisegundos.
- **`reasignar_operadores.py`.** Alterna la programación dinámica (filiales con la carga fija) y el submodelo de
  carga (carga con las filiales fijas) hasta que el margen deja de subir. Ningún paso empeora el anterior. Valida y
  exporta el resultado.

```
python reasignar_operadores.py --corrida data/results/Busqueda_ventanas_desplazar_d2 --salida data/results/Interchange_desde_mejor --borde base
```

## Resultados con los vuelos de la mejor solución

| | Sin interchanges | Borde base | Borde flexible |
|---|---|---|---|
| Margen validado (USD) | 6.475.055 | **7.273.495** | 7.366.681 |
| Costo de vuelos (USD) | 11.236.256 | 10.434.227 | 10.340.209 |
| Handling (USD) | 504.457 | 509.262 | 510.094 |
| Interchanges | 0 | 58 | 61 |
| Vuelos por filial (PAC/SUR/BRA/AND) | 46/164/44/84 | 270/28/27/13 | 297/14/24/3 |

- Las dos variantes llegan en dos vueltas de la alternancia (`data/results/Interchange_desde_mejor` y
  `data/results/Interchange_desde_mejor_flexible`). Releer el CSV exportado y validarlo da el mismo margen.
- Casi todo se vuela como PAC, la filial más barata, y como BRA donde PAC no tiene derechos. Los vuelos que quedan
  en SUR y AND son el primero y el último de cada rotación (supuesto 3).
- Los interchanges se hacen sobre todo en MIA (19), MAO (8), VCP (5) y BOG (5).
- La carga casi no cambia (+2 t). Hay 56 transbordos en vez de 54.
- El borde flexible vale 93 mil USD más, porque casi no deja vuelos con el operador base. Depende del supuesto 3.
  En el borde flexible, 14 aviones vuelan con PAC el primer y el último tramo; AC-12 parte en GRU, donde PAC no está
  habilitada, y usa BRA.

## Fase 2: elegir los vuelos sabiendo que la filial puede cambiar

Los vuelos de la tabla anterior se eligieron con los costos antiguos. Con vuelos más baratos convienen otros vuelos,
así que ahora los modelos eligen los vuelos con una relajación del problema con interchanges, y cada candidato se
evalúa de forma exacta.

**Relajación en los modelos** (`INTERCHANGE=1`, o `--interchange` en `busqueda_por_avion.py`):

- `gurobi_model.cargar_datos` da a cada avión todos los tramos en que alguna filial tiene derechos, con el costo
  por hora de la más barata de ellas. Lo usan el MILP continuo, el MILP con transbordos y la red espacio-tiempo.
- Los transbordos de un vuelo fijo usan la filial que ya lo opera. Un vuelo libre puede transbordar en cualquier
  aeropuerto no técnico con alguna filial habilitada (`milp_continuo_transbordos.py`). En la red espacio-tiempo,
  todos los vuelos usan esta relajación.
- El handling sigue siendo el del operador base. Es una diferencia de pocos USD por tonelada.

**Evaluación exacta de cada candidato** (`warm_start_milp.resolver_carga_interchange`):

1. Los vuelos que no cambian parten con la filial que ya tenían.
2. Alterna carga (submodelo de la heurística) y filiales (`src/asignar_operadores.py`), hasta tres resoluciones de
   carga (~36 s cada una) o hasta que el margen deja de subir.
3. El validador revisa la mejor solución con las reglas de los interchanges. Solo esa se acepta.

La relajación sobreestima el margen. Con borde base, la diferencia es de unos 145 mil USD (7.418.665 contra
7.273.495 validado), sobre todo porque el primer y el último vuelo de cada rotación pagan el operador base. Con borde
flexible baja a unos 45 mil (7.451.678 contra 7.406.840). Por eso la búsqueda solo usa el modelo para proponer vuelos
y siempre compara márgenes validados.

```
python -u busqueda_por_avion.py --modelo continuo --interchange --corrida data/results/Interchange_desde_mejor --vecindario ventana,avion,par --ventana-h 12 --ventana-paso 6 --desplazar 2 --desplazar-aviones 10 --desplazar-presupuesto 60 --tiempo 300 --norel 60 --saltar-sin-cambios --umbral-aceptar 100 --mejora-minima 2000 --orden uso --vueltas 20 --horas 10 --salida data/results/Interchange_continua
```

- **Prueba** (3 aviones, 120 s cada uno): partió de 7.273.495 y AC-17 mejoró a 7.274.367.
- **Búsqueda por ventanas con borde base** (`data/results/Interchange_continua`): en 2,4 h subió a 7.308.301
  (+34.806, 5 mejoras, en las ventanas 54–66 h, 126–138 h, 144–156 h, 162–168 h y 30–42 h). Se detuvo al pasar al
  borde flexible.
- **Borde flexible sobre esos vuelos** (`reasignar_operadores.py`, `data/results/Interchange_continua_flexible`):
  **7.406.840, el mejor margen validado** (62 interchanges, 297 vuelos operados por otra filial).
- Las corridas largas pasan a la máquina de 32 GB (`docs/proximos_pasos.pdf`, `docs/comandos_32gb.txt`).

**Pendiente:**

- La heurística (`correr_heuristica.py`, `src/reoptimizacion_por_avion.py`) no usa esta relajación. Para la rama B
  habría que agregársela, o partir de una solución ya reasignada.
- La búsqueda usa el borde flexible por defecto (`--borde base` para el estricto). La relajación de los modelos
  no impone ningún borde.
- El MILP integrado (`warm_start_milp.py`) no está probado con interchanges.
