"""
Búsqueda avión por avión sobre el modelo integrado en red espacio-tiempo (modelo_integrado_espacio_tiempo.py).

En cada paso libera un solo avión, con el resto fijo en la mejor solución conocida, y resuelve el modelo integrado
(vuelos, carga multitramo y transbordos exactos) con la heurística NoRel de Gurobi, partiendo de esa solución. Si el
margen validado mejora, la acepta, la exporta y el siguiente avión parte de ella. Pensado para correr de noche con
una grilla fina (DT = 1 h), donde el modelo completo no cabe en memoria pero el de un avión sí.

A diferencia de src/reoptimizacion_por_avion.py (paso de la heurística), aquí la carga y los transbordos de toda la
red se reoptimizan junto con las rotaciones del avión liberado.

Uso, desde la raíz del repositorio:
    python busqueda_por_avion.py                          # DT = 1 h, 20 min por avión, todos los aviones, 1 vuelta
    python busqueda_por_avion.py --vueltas 2 --tiempo 900
    python busqueda_por_avion.py --corrida data/results/Busqueda_por_avion_dt1     # reanuda desde lo ya exportado
    python busqueda_por_avion.py --aviones AC-03,AC-10 --desde AC-10

Ctrl+C corta el avión en curso, evalúa lo que encontró y detiene el recorrido. La mejor solución siempre queda
exportada en la carpeta de salida, y progreso.csv registra cada paso.
"""
import argparse
import ctypes
import json
import os
import sys
import time

import numpy as np
import pandas as pd

import warm_start_milp as ws   # deja la raíz como directorio de trabajo y agrega src/ (submodelo de carga y validador)
import gurobi_model as gm
import modelo_integrado_espacio_tiempo as mi
import gurobipy as gp
from gurobipy import GRB


def mantener_despierto(activo):
    """Pide a Windows no suspender por inactividad mientras corre (no cambia la configuración del equipo)."""
    if os.name == 'nt':
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if activo else 0))


def resolver_avion(datos, dt, ttr, itinerario, multi, avion, args):
    """Modelo integrado con un solo avión libre, partiendo de la solución actual. Devuelve (itinerario, info)."""
    red = mi.construir(datos, dt, ttr, itinerario, {avion}, enlace_fuerte=args.enlace_fuerte)
    xs = mi.armar_start(red, itinerario, multi['solucion'])
    violaciones, obj_start = mi.verificar(red, xs)
    if violaciones:
        raise RuntimeError(f'el start no cumple el modelo: {violaciones}')
    env = gp.Env(empty=True)
    env.setParam('OutputFlag', 0)
    env.start()
    m = gp.Model(f'busqueda_{avion}', env=env)
    xv = m.addMVar(red['n'], lb=red['lb'], ub=red['ub'], obj=red['obj'], vtype=red['vt'])
    m.addMConstr(red['A'], xv, red['sentido'], red['rhs'])
    m.ModelSense = GRB.MAXIMIZE
    m.ObjCon = red['constante']
    red['A'] = None
    xv.Start = xs
    prm = m.Params
    prm.TimeLimit = args.tiempo
    prm.NoRelHeurTime = args.tiempo if args.norel is None else args.norel   # por defecto, todo el tiempo en NoRel
    prm.MIPFocus = 1
    prm.MIPGap = 1e-4
    prm.NodefileStart = 0.5
    prm.LogFile = os.path.join(args.salida, 'logs', f'gurobi_{avion}.log')
    if args.hilos:
        prm.Threads = args.hilos
    if args.memoria:
        prm.SoftMemLimit = args.memoria
    m.optimize()
    info = {'estado': ws.ESTADOS.get(m.Status, str(m.Status)), 'interrumpido': m.Status == GRB.INTERRUPTED,
            'objetivo_start': obj_start,
            'objetivo_modelo': m.ObjVal if m.SolCount else None, 'tiempo_gurobi_s': m.Runtime,
            'variables': red['n']}
    nuevo = mi.extraer_itinerario(red, xv.X) if m.SolCount else None
    m.dispose()
    env.dispose()
    return nuevo, info


def exportar_mejor(salida, itinerario, multi, val, datos, dt, ttr, origen, historial):
    info = {'modelo': f'Búsqueda avión por avión, modelo integrado espacio-tiempo (DT {dt:g} h)',
            'corrida_start': origen, 'estado': 'Búsqueda local', 'aviones_libres': 'uno a la vez',
            'tiempo_limite_s': None, 'dt_h': dt, 'ttr_h': ttr, 'pasos_aceptados': historial}
    kpis = ws.exportar(salida, itinerario, multi, val, datos, info, ttr)
    ruta_meta = os.path.join(salida, 'metadata.json')
    meta = json.load(open(ruta_meta, encoding='utf-8'))
    meta.update(descripcion=f'Búsqueda avión por avión (DT {dt:g} h) desde {origen}', dt=f'{dt:g}')
    json.dump(meta, open(ruta_meta, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    return kpis


def main():
    ap = argparse.ArgumentParser(description='Búsqueda avión por avión con el modelo integrado espacio-tiempo.')
    ap.add_argument('--corrida', default=ws.CORRIDA_2H,
                    help='solución de partida (carpeta con itinerario_vuelos.csv); para reanudar, la carpeta de salida')
    ap.add_argument('--dt', type=float, default=1.0, help='horas por bloque de la red. Por defecto 1')
    ap.add_argument('--ttr', type=float, default=None, help='TTR_H (h); por defecto el de la corrida')
    ap.add_argument('--tiempo', type=float, default=1200, help='segundos de Gurobi por avión. Por defecto 1200')
    ap.add_argument('--norel', type=float, default=None,
                    help='segundos de NoRel por avión; por defecto todo --tiempo (no resuelve la relajación, que en '
                         'grillas finas no alcanza a terminar y pide mucha memoria)')
    ap.add_argument('--aviones', default='todos', help='aviones a recorrer, en orden: todos o lista (AC-01,AC-05)')
    ap.add_argument('--desde', default=None, help='empieza el recorrido en este avión (para reanudar)')
    ap.add_argument('--vueltas', type=int, default=1, help='vueltas completas sobre los aviones. Por defecto 1')
    ap.add_argument('--sin-enlace-fuerte', dest='enlace_fuerte', action='store_false',
                    help='omite las desigualdades de enlace fuerte (por defecto se agregan: con ellas NoRel encontró '
                         'la mejora de AC-10 en menos de 1.000 s)')
    ap.add_argument('--hilos', type=int, default=0, help='Threads de Gurobi (0 = todos)')
    ap.add_argument('--memoria', type=float, default=6, help='GB máximos para Gurobi por avión. Por defecto 6')
    ap.add_argument('--salida', default=None, help='carpeta de salida (por defecto data/results/Busqueda_por_avion_dt<DT>)')
    args = ap.parse_args()

    t0 = time.time()
    dt = args.dt
    args.salida = args.salida or os.path.join('data', 'results', f'Busqueda_por_avion_dt{dt:g}')
    os.makedirs(os.path.join(args.salida, 'logs'), exist_ok=True)
    corrida = os.path.normpath(args.corrida)
    kpi = json.load(open(os.path.join(corrida, 'indicadores_kpi.json'), encoding='utf-8'))['kpis']
    ttr = args.ttr if args.ttr is not None else float(kpi.get('ttr_h', 2.0))
    datos = gm.cargar_datos()
    aviones = list(datos['K']) if args.aviones == 'todos' else [a.strip() for a in args.aviones.split(',')]
    faltan = [a for a in aviones if a not in datos['K']]
    if faltan:
        sys.exit(f'aviones desconocidos: {faltan}')
    if args.desde:
        aviones = aviones[aviones.index(args.desde):] + aviones[:aviones.index(args.desde)]

    itinerario = ws.leer_itinerario(corrida)
    mi.construir(datos, dt, ttr, itinerario, set(), estimar=True)   # falla temprano si la corrida no calza con la grilla
    multi, val = ws.resolver_carga(itinerario, ttr)
    if not val['ok']:
        sys.exit(f'la solución de partida no pasa el validador: {val["violaciones"][:5]}')
    margen0 = mejor = val['margen']
    print(f'[inicio] {os.path.basename(corrida)} | DT = {dt:g} h | TTR_H = {ttr:g} h | margen validado {mejor:,.0f} USD | '
          f'{len(aviones)} aviones x {args.vueltas} vuelta(s) x {args.tiempo:,.0f} s | salida {args.salida}', flush=True)

    ruta_prog = os.path.join(args.salida, 'progreso.csv')
    historial = []
    if os.path.normpath(args.salida) != corrida:
        exportar_mejor(args.salida, itinerario, multi, val, datos, dt, ttr, os.path.basename(corrida), historial)
    mantener_despierto(True)
    try:
        for vuelta in range(1, args.vueltas + 1):
            mejoras_vuelta = 0
            for avion in aviones:
                t1 = time.time()
                try:
                    nuevo, info = resolver_avion(datos, dt, ttr, itinerario, multi, avion, args)
                except (gp.GurobiError, MemoryError, RuntimeError) as e:
                    print(f'  [{avion}] error, se salta: {e}', flush=True)
                    continue
                acepta, margen_nuevo = False, None
                if nuevo is not None and info['objetivo_modelo'] > info['objetivo_start'] + 1.0:
                    multi_n, val_n = ws.resolver_carga(nuevo, ttr)
                    margen_nuevo = val_n['margen']
                    if val_n['ok'] and margen_nuevo > mejor + 1.0:
                        acepta = True
                        itinerario, multi, val = nuevo, multi_n, val_n
                        historial.append({'vuelta': vuelta, 'avion': avion, 'margen': margen_nuevo,
                                          'mejora': margen_nuevo - mejor})
                        mejor = margen_nuevo
                        mejoras_vuelta += 1
                        exportar_mejor(args.salida, itinerario, multi, val, datos, dt, ttr,
                                       os.path.basename(corrida), historial)
                fila = {'fecha': time.strftime('%Y-%m-%d %H:%M:%S'), 'vuelta': vuelta, 'avion': avion,
                        'estado': info['estado'], 'objetivo_start': round(info['objetivo_start'], 2),
                        'objetivo_modelo': None if info['objetivo_modelo'] is None else round(info['objetivo_modelo'], 2),
                        'margen_validado': None if margen_nuevo is None else round(margen_nuevo, 2),
                        'aceptado': acepta, 'mejor_margen': round(mejor, 2), 'segundos': round(time.time() - t1, 1)}
                pd.DataFrame([fila]).to_csv(ruta_prog, mode='a', index=False, header=not os.path.exists(ruta_prog))
                print(f'  [vuelta {vuelta}] {avion}: {"MEJORA" if acepta else "sin mejora"} | modelo '
                      f'{info["objetivo_start"]:,.0f} -> {(info["objetivo_modelo"] or 0):,.0f} | mejor validado '
                      f'{mejor:,.0f} USD ({mejor - margen0:+,.0f}) | {time.time() - t1:.0f} s', flush=True)
                if info['interrumpido']:
                    raise KeyboardInterrupt
            if not mejoras_vuelta:
                print(f'[vuelta {vuelta}] ningún avión mejoró: se detiene.')
                break
    except KeyboardInterrupt:
        print('\n[!] Recorrido detenido (Ctrl+C). La mejor solución ya está exportada.')
    finally:
        mantener_despierto(False)

    print('\n' + '=' * 78)
    print(f'Margen validado: {margen0:,.0f} -> {mejor:,.0f} USD ({mejor - margen0:+,.0f}) | '
          f'{len(historial)} mejoras aceptadas | {(time.time() - t0) / 3600:.1f} h')
    print(f'Resultados en {args.salida} (progreso.csv tiene cada paso)')


if __name__ == '__main__':
    main()
