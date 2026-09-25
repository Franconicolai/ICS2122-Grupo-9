"""
Módulo: estructuras_datos.py
Descripción: 
"""

import sqlite3
import pandas as pd
import itertools
from typing import Dict, Any

def obtener_conexion(ruta_bd: str) -> sqlite3.Connection:
    return sqlite3.connect(ruta_bd)

def leer_tabla(conexion: sqlite3.Connection, nombre_tabla: str) -> pd.DataFrame:
    return pd.read_sql_query(f"SELECT * FROM {nombre_tabla}", conexion)

def cargar_estructuras_funcionales(ruta_bd: str) -> Dict[str, Any]:
  
    with obtener_conexion(ruta_bd) as conexion:
        dfs = {
            'aeropuertos': leer_tabla(conexion, 'airports').to_dict('records'),
            'flota': leer_tabla(conexion, 'fleet').to_dict('records'),
            'tramos': leer_tabla(conexion, 'legs_catalog').to_dict('records'),
            'demanda': leer_tabla(conexion, 'demand_daily').to_dict('records'),
            'derechos': leer_tabla(conexion, 'traffic_rights').to_dict('records')
        }

    # Aeropuertos
    A = list(map(lambda fila: fila['iata'], dfs['aeropuertos']))

    # País de cada aeropuerto
    pais_de = dict(map(lambda fila: (fila['iata'], fila['country']), dfs['aeropuertos']))

    #pares operador-pais donde operador tiene derefcho de tráfico
    permitido = set(map(lambda fila: (fila['operator'], fila['country']), filter(lambda fila: fila['allowed'] == 1, dfs['derechos'])))

    # Flota
    K = list(map(lambda fila: fila['aircraft_id'], dfs['flota']))

    # Tramos
    E = list(map(lambda fila: (fila['origin'], fila['dest']), dfs['tramos']))

    # Demanda
    Q = list(map(lambda fila: (fila['origin'], fila['dest']), dfs['demanda']))

    # Tiempo de vuelo
    tau_e = dict(map(lambda fila: ((fila['origin'], fila['dest']), fila['block_hours']), dfs['tramos']))

    # Distancia entre tramos
    dist_e = dict(map(lambda fila: ((fila['origin'], fila['dest']), fila['distance_km']), dfs['tramos']))

    #tramos factibles por avión (considerando derechos trafico y autonomia max)
    Ek = dict(map(lambda avion: (avion['aircraft_id'],list(filter(lambda tramo: tau_e[tramo] <= 9.0
                                                                  and (avion['operator'], pais_de[tramo[0]]) in permitido
                                                                  and (avion['operator'], pais_de[tramo[1]]) in permitido, E))),dfs['flota']))
    
    #duración del tramo más corto 
    tau_e_min_k = dict(map(lambda avion: (avion['aircraft_id'], min(map(lambda tramo: tau_e[tramo], Ek[avion['aircraft_id']]))), dfs['flota']))

    #cuántos vuelos como máximo podría hacer en la semana
    H = 168
    T_min = 50/60
    S_max_k = dict(map(lambda avion: (avion['aircraft_id'], int(H/(tau_e_min_k[avion['aircraft_id']] + T_min))), dfs['flota']))

    # Conexiones validas C: Filtro sobre producto cartesiano de tramos (Destino == Origen)
    C_consecutivos = list(filter(lambda par: par[0][1] == par[1][0], itertools.product(E, E)))

    # Demanda diaria
    D_q = dict(map(
        lambda fila: (
            (fila['origin'], fila['dest']), 
            sum(map(lambda dia: fila[dia], ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']))
        ),
        dfs['demanda']
    ))
    
    # Ingreso constante para prueba
    r_q = dict(map(lambda q: (q, 1.5), Q)) # Ingreso constante para prueba

    # Capacidad Q_ke: Cruzar flota con tramos
    Q_ke = dict(map(
        lambda tupla: ((tupla[0]['aircraft_id'], tupla[1]), tupla[0]['payload_tons']),
        itertools.product(dfs['flota'], E)
    ))
    
    # Costo Operacional c_ke: Cruzar flota con tramos (Costo asociado a distancia)
    c_ke = dict(map(
        lambda tupla: ((tupla[0], tupla[1]), dist_e[tupla[1]] * 5.0),
        itertools.product(K, E)
    ))

    return {
        'A': A, 'K': K, 'E': E, 'Ek': Ek, 'Q': Q, 'C': C_consecutivos,
        'tau_e': tau_e, 'Q_ke': Q_ke, 'c_ke': c_ke,
        'D_q': D_q, 'r_q': r_q,
        'S_max_k': S_max_k, 'H': H, 'T_TAT': 2.0, 'T_TR': 4.0, 'BigM': 1000
    }

