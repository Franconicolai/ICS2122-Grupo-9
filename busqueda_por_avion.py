"""
Búsqueda avión por avión sobre el modelo integrado en red espacio-tiempo (modelo_integrado_espacio_tiempo.py).

En cada paso libera un solo avión, con el resto fijo en la mejor solución conocida, y resuelve el modelo integrado
(vuelos, carga multitramo y transbordos exactos) con la heurística NoRel de Gurobi, partiendo de esa solución. Si el
margen validado mejora, la acepta, la exporta y el siguiente avión parte de ella. Pensado para correr de noche con
una grilla fina (DT = 1 h), donde el modelo completo no cabe en memoria pero el de un avión sí.

A diferencia de src/reoptimizacion_por_avion.py (paso de la heurística), aquí la carga y los transbordos de toda la
red se reoptimizan junto con las rotaciones del avión liberado.

Vecindarios (--vecindario): 'avion' libera un avión; 'par', dos aviones que comparten aeropuertos o transbordos
(los pares más afines primero); 'operador', la flota de un operador. Con varios niveles (--vecindario avion,par) es una
búsqueda por vecindarios variables: recorre el primer nivel hasta que una vuelta no mejora, pasa al siguiente y,
apenas una vuelta de un nivel mayor mejora, vuelve al primero. Se detiene cuando ningún nivel mejora o se cumple --horas.

Uso, desde la raíz del repositorio:
    python busqueda_por_avion.py                          # DT = 1 h, 20 min por avión, todos los aviones, 1 vuelta
    python busqueda_por_avion.py --vecindario avion,par --tiempo 120 --vueltas 10 --horas 4
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
import pickle
import sys
import time
from collections import defaultdict

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


def resolver_continuo(datos, ttr, itinerario, multi, grupo, args):
    """Vecindario en tiempo continuo: MILP de posiciones con transbordos exactos (milp_continuo_transbordos.py), con los
    aviones del grupo libres y el resto con sus vuelos y horarios fijos. Sin grilla: los tramos duran exactamente su
    tiempo de bloque + TAT. Devuelve (itinerario, info) como resolver_avion."""
    import milp_continuo_transbordos as mct
    avion = '+'.join(grupo)
    modelo = gm.construir_modelo_completo(datos, transbordos=True)
    pares, _ = mct.enumerar_pares(datos, itinerario, set(grupo), ttr)
    mct.agregar_transbordos(modelo, datos, pares, ttr)
    st = mct.armar_start(modelo, datos, itinerario, multi['solucion'], pares)
    mct.fijar_aviones(modelo, st, set(grupo))
    xs, violaciones, obj_start = ws.verificar_start(modelo, st, tol=ws.TOL_HORAS)   # horas redondeadas a 5 decimales
    if violaciones:
        raise RuntimeError(f'el start no cumple el modelo: { {f: len(l) for f, l in violaciones.items()} }')
    modelo.setAttr('Start', modelo.getVars(), xs.tolist())
    prm = modelo.Params
    prm.LogToConsole = 0
    prm.FeasibilityTol = 1e-5          # los horarios fijos vienen redondeados a 5 decimales
    prm.TimeLimit = args.tiempo
    prm.NoRelHeurTime = args.tiempo if args.norel is None else args.norel
    prm.MIPFocus = 1
    prm.MIPGap = 1e-4
    prm.NodefileStart = 0.5
    prm.LogFile = os.path.join(args.salida, 'logs', f'gurobi_{avion}.log')
    if args.hilos:
        prm.Threads = args.hilos
    if args.memoria:
        prm.SoftMemLimit = args.memoria
    modelo.optimize()
    info = {'estado': ws.ESTADOS.get(modelo.Status, str(modelo.Status)), 'interrumpido': modelo.Status == GRB.INTERRUPTED,
            'objetivo_start': obj_start, 'objetivo_modelo': modelo.ObjVal if modelo.SolCount else None,
            'tiempo_gurobi_s': modelo.Runtime, 'variables': modelo.NumVars}
    nuevo = ws.extraer_itinerario(modelo) if modelo.SolCount else None
    modelo.dispose()
    return nuevo, info


def ajustar_a_grilla(itinerario, dt, datos):
    """Lleva cada salida al inicio de su bloque de DT horas, atrasándola si el avión aún no cumple su TAT en la grilla.
    Sirve para partir de una solución con tiempos continuos (MILP continuo). Devuelve None si alguna rotación deja de
    cerrar dentro de la semana: pasa con los aviones que el MILP continuo usa al 100 %, porque la grilla alarga cada
    tramo hasta completar bloques enteros."""
    T = int(round(gm.H / dt))
    nuevo = {}
    for k, legs in itinerario.items():
        listo, salida = -10 ** 9, []
        for leg in sorted(legs, key=lambda l: l['pos']):
            e = tuple(leg['tramo'])
            t = max(int(np.floor(leg['t_dep'] / dt + 1e-9)), listo)
            listo = t + max(1, int(np.ceil((datos['tau_e'][e] + datos['TAT_e'][e]) / dt - 1e-9)))
            salida.append(dict(leg, t_dep=t * dt, t_arr=round(t * dt + datos['tau_e'][e], 5)))
        if salida and salida[0]['t_dep'] / dt + T < listo:
            return None
        nuevo[k] = salida
    return nuevo


def vuelos(itinerario):
    """Conjunto de vuelos (avión, tramo, salida) de un itinerario, para saber si cambió."""
    return {(k, tuple(l['tramo']), round(l['t_dep'], 4)) for k, legs in itinerario.items() for l in legs}


def afinidad(itinerario, solucion, datos, aviones):
    """Puntaje de cada par de aviones: toneladas transbordadas entre ellos + 5 por aeropuerto que ambos visitan.
    Un avión sin vuelos cuenta con los aeropuertos de sus tramos permitidos (a la mitad), para que pueda entrar."""
    visita = {}
    for k in aviones:
        aps = {a for leg in itinerario.get(k, []) for a in leg['tramo']}
        visita[k] = (aps, 1.0) if aps else ({a for e in datos['Ek'][k] for a in e}, 0.5)
    trans = defaultdict(float)
    for tr in solucion.get('transferencias', []):
        trans[frozenset((tr['from_k'], tr['to_k']))] += tr['tons']
    puntaje = {}
    for i, k1 in enumerate(aviones):
        for k2 in aviones[i + 1:]:
            comunes = len(visita[k1][0] & visita[k2][0]) * min(visita[k1][1], visita[k2][1])
            puntaje[k1, k2] = trans[frozenset((k1, k2))] + 5 * comunes
    return puntaje


def vecindarios(nivel, aviones, itinerario, multi, datos, args):
    """Grupos de aviones a liberar, en orden, para un nivel ('avion', 'par' u 'operador')."""
    if nivel == 'avion':
        if args.orden == 'uso':                                 # primero los aviones con menos vuelos
            return [(k,) for k in sorted(aviones, key=lambda k: len(itinerario.get(k, [])))]
        return [(k,) for k in aviones]
    if nivel == 'operador':
        return [tuple(k for k in aviones if datos['op'][k] == o) for o in sorted({datos['op'][k] for k in aviones})]
    if nivel == 'par':
        puntaje = afinidad(itinerario, multi['solucion'], datos, aviones)
        pares = sorted(puntaje, key=lambda p: -puntaje[p])
        return pares[:args.max_pares] if args.max_pares else pares
    if nivel == 'trio':                                         # cada par afín más el avión más afín a ambos
        puntaje = afinidad(itinerario, multi['solucion'], datos, aviones)
        af = lambda a, b: puntaje.get((a, b), puntaje.get((b, a), 0.0))
        trios = []
        for k1, k2 in sorted(puntaje, key=lambda p: -puntaje[p]):
            k3 = max((k for k in aviones if k not in (k1, k2)), key=lambda k: af(k, k1) + af(k, k2))
            trio = tuple(sorted((k1, k2, k3), key=aviones.index))
            if trio not in trios:
                trios.append(trio)
        return trios[:args.max_pares] if args.max_pares else trios
    raise ValueError(f'vecindario desconocido: {nivel} (use avion, par, trio u operador)')


def resolver_avion(datos, dt, ttr, itinerario, multi, grupo, args):
    """Modelo integrado con los aviones del grupo libres, partiendo de la solución actual. Devuelve (itinerario, info)."""
    avion = '+'.join(grupo)
    red = mi.construir(datos, dt, ttr, itinerario, set(grupo), enlace_fuerte=args.enlace_fuerte,
                       deposito_agregado=args.deposito_agregado)
    xs = mi.armar_start(red, itinerario, multi['solucion'])
    violaciones, obj_start = mi.verificar(red, xs)
    if violaciones:
        raise RuntimeError(f'el start no cumple el modelo: {violaciones}')
    env = gp.Env(empty=True)
    env.setParam('LogToConsole', 0)                # sin pantalla, pero con el log de Gurobi en logs/
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
    if args.metodo_raiz is not None:
        prm.Method = args.metodo_raiz
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
    red = f'modelo integrado espacio-tiempo (DT {dt:g} h)' if dt else 'MILP continuo con transbordos (sin grilla)'
    info = {'modelo': f'Búsqueda por vecindarios de aviones, {red}',
            'corrida_start': origen, 'estado': 'Búsqueda local', 'aviones_libres': 'por vecindarios',
            'tiempo_limite_s': None, 'dt_h': dt or None, 'ttr_h': ttr, 'pasos_aceptados': historial}
    kpis = ws.exportar(salida, itinerario, multi, val, datos, info, ttr)
    ruta_meta = os.path.join(salida, 'metadata.json')
    meta = json.load(open(ruta_meta, encoding='utf-8'))
    meta.update(descripcion=f'Búsqueda por vecindarios ({red}) desde {origen}', dt=f'{dt:g}' if dt else 'continuo')
    json.dump(meta, open(ruta_meta, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    return kpis


def main():
    ap = argparse.ArgumentParser(description='Búsqueda avión por avión con el modelo integrado espacio-tiempo.')
    ap.add_argument('--corrida', default=ws.CORRIDA_2H,
                    help='solución de partida (carpeta con itinerario_vuelos.csv); para reanudar, la carpeta de salida')
    ap.add_argument('--modelo', default='tsn', choices=('tsn', 'continuo'),
                    help='tsn: red espacio-tiempo con grilla de DT (por defecto); continuo: MILP de posiciones con '
                         'tiempo continuo y transbordos (milp_continuo_transbordos.py), sin pérdida por la grilla')
    ap.add_argument('--smax', type=int, default=gm.S_MAX, help=f'posiciones por avión del modelo continuo. Por defecto {gm.S_MAX}')
    ap.add_argument('--dt', type=float, default=1.0, help='horas por bloque de la red. Por defecto 1')
    ap.add_argument('--ttr', type=float, default=None, help='TTR_H (h); por defecto el de la corrida')
    ap.add_argument('--tiempo', type=float, default=1200, help='segundos de Gurobi por avión. Por defecto 1200')
    ap.add_argument('--norel', type=float, default=None,
                    help='segundos de NoRel por avión; por defecto todo --tiempo (no resuelve la relajación, que en '
                         'grillas finas no alcanza a terminar y pide mucha memoria)')
    ap.add_argument('--aviones', default='todos', help='aviones a recorrer, en orden: todos o lista (AC-01,AC-05)')
    ap.add_argument('--desde', default=None, help='empieza el recorrido en este avión (para reanudar)')
    ap.add_argument('--vueltas', type=int, default=1, help='vueltas completas sobre los vecindarios. Por defecto 1')
    ap.add_argument('--vecindario', default='avion',
                    help='niveles de vecindario, en orden: avion, par, trio, operador (p. ej. avion,par). Por defecto avion')
    ap.add_argument('--ajustar-grilla', action='store_true',
                    help='lleva las salidas de la solución de partida a la grilla de DT (para partir del MILP continuo, '
                         'cuyos tiempos no caen en la grilla); el margen validado de partida es el de la solución ajustada')
    ap.add_argument('--metodo-raiz', type=int, default=None,
                    help='Method de Gurobi para la relajación de la raíz (1 = simplex dual, que pide mucha menos '
                         'memoria que la barrera); solo importa si --norel es menor que --tiempo')
    ap.add_argument('--orden', default='lista', choices=('lista', 'uso'),
                    help='orden de los aviones en el nivel avion: lista (el de --aviones) o uso (primero los que '
                         'tienen menos vuelos, que en R10 fueron los que más mejoraron). Por defecto lista')
    ap.add_argument('--max-pares', type=int, default=0,
                    help='en el nivel par, solo los N pares más afines (0 = todos). Por defecto 0')
    ap.add_argument('--mejora-minima', type=float, default=0,
                    help='USD que debe ganar una vuelta para volver al vecindario más chico; si gana menos, pasa al '
                         'nivel siguiente (evita que una mejora marginal impida probar los pares). Por defecto 0')
    ap.add_argument('--horas', type=float, default=0, help='detiene la búsqueda tras estas horas (0 = sin límite)')
    ap.add_argument('--sin-enlace-fuerte', dest='enlace_fuerte', action='store_false',
                    help='omite las desigualdades de enlace fuerte (por defecto se agregan: con ellas NoRel encontró '
                         'la mejora de AC-10 en menos de 1.000 s)')
    ap.add_argument('--deposito-agregado', action='store_true',
                    help='inventario de transbordos agregado por aeropuerto (~30 %% menos variables y memoria; cada '
                         'candidato se revalida con el paso de carga exacto)')
    ap.add_argument('--hilos', type=int, default=0, help='Threads de Gurobi (0 = todos)')
    ap.add_argument('--memoria', type=float, default=6, help='GB máximos para Gurobi por avión. Por defecto 6')
    ap.add_argument('--salida', default=None, help='carpeta de salida (por defecto data/results/Busqueda_por_avion_dt<DT>)')
    args = ap.parse_args()

    t0 = time.time()
    continuo = args.modelo == 'continuo'
    dt = 0 if continuo else args.dt
    args.salida = args.salida or os.path.join('data', 'results', 'Busqueda_continua' if continuo
                                              else f'Busqueda_por_avion_dt{dt:g}')
    os.makedirs(os.path.join(args.salida, 'logs'), exist_ok=True)
    corrida = os.path.normpath(args.corrida)
    kpi = json.load(open(os.path.join(corrida, 'indicadores_kpi.json'), encoding='utf-8'))['kpis']
    ttr = args.ttr if args.ttr is not None else float(kpi.get('ttr_h', 2.0))
    datos = gm.cargar_datos(s_max=args.smax) if continuo else gm.cargar_datos()
    aviones = list(datos['K']) if args.aviones == 'todos' else [a.strip() for a in args.aviones.split(',')]
    faltan = [a for a in aviones if a not in datos['K']]
    if faltan:
        sys.exit(f'aviones desconocidos: {faltan}')
    if args.desde:
        aviones = aviones[aviones.index(args.desde):] + aviones[:aviones.index(args.desde)]

    itinerario = ws.leer_itinerario(corrida)
    ruta_pkl = os.path.join(corrida, 'modelo_resuelto.pkl')
    if continuo and os.path.exists(ruta_pkl):   # el CSV redondea las horas a 2 decimales y rompe los TAT ajustados
        with open(ruta_pkl, 'rb') as fh:
            itinerario = pickle.load(fh)['itinerario']
    if args.ajustar_grilla:
        itinerario = ajustar_a_grilla(itinerario, dt, datos)
        if itinerario is None:
            sys.exit(f'la solución de partida no se puede llevar a la grilla de {dt:g} h sin romper el cierre semanal')
    if not continuo:
        mi.construir(datos, dt, ttr, itinerario, set(), estimar=True)   # falla temprano si no calza con la grilla
    multi, val = ws.resolver_carga(itinerario, ttr)
    if not val['ok']:
        sys.exit(f'la solución de partida no pasa el validador: {val["violaciones"][:5]}')
    margen0 = mejor = val['margen']
    print(f'[inicio] {os.path.basename(corrida)} | {"tiempo continuo" if continuo else f"DT = {dt:g} h"} | TTR_H = {ttr:g} h | margen validado {mejor:,.0f} USD | '
          f'{len(aviones)} aviones x {args.vueltas} vuelta(s) x {args.tiempo:,.0f} s | salida {args.salida}', flush=True)

    ruta_prog = os.path.join(args.salida, 'progreso.csv')
    historial = []
    if os.path.normpath(args.salida) != corrida:
        exportar_mejor(args.salida, itinerario, multi, val, datos, dt, ttr, os.path.basename(corrida), historial)
    mantener_despierto(True)
    niveles = [n.strip() for n in args.vecindario.split(',') if n.strip()]
    try:
        nivel = 0
        for vuelta in range(1, args.vueltas + 1):
            mejoras_vuelta, inicio_vuelta = 0, mejor
            grupos = vecindarios(niveles[nivel], aviones, itinerario, multi, datos, args)
            print(f'[vuelta {vuelta}] nivel {niveles[nivel]}: {len(grupos)} vecindarios', flush=True)
            for grupo in grupos:
                if args.horas and time.time() - t0 > args.horas * 3600:
                    raise TimeoutError
                avion = '+'.join(grupo)
                t1 = time.time()
                try:
                    if continuo:
                        nuevo, info = resolver_continuo(datos, ttr, itinerario, multi, grupo, args)
                    else:
                        nuevo, info = resolver_avion(datos, dt, ttr, itinerario, multi, grupo, args)
                except (gp.GurobiError, MemoryError, RuntimeError) as e:
                    print(f'  [{avion}] error, se salta: {e}', flush=True)
                    continue
                acepta, margen_nuevo = False, None
                # con el depósito agregado el modelo puede subir sin cambiar vuelos (re-embarque propio): solo se
                # revalida si cambiaron los vuelos
                if (nuevo is not None and info['objetivo_modelo'] > info['objetivo_start'] + 1.0
                        and vuelos(nuevo) != vuelos(itinerario)):
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
                        'aceptado': acepta, 'mejor_margen': round(mejor, 2), 'segundos': round(time.time() - t1, 1),
                        'nivel': niveles[nivel], 'minutos_total': round((time.time() - t0) / 60, 1)}
                pd.DataFrame([fila]).to_csv(ruta_prog, mode='a', index=False, header=not os.path.exists(ruta_prog))
                print(f'  [vuelta {vuelta}] {avion}: {"MEJORA" if acepta else "sin mejora"} | modelo '
                      f'{info["objetivo_start"]:,.0f} -> {(info["objetivo_modelo"] or 0):,.0f} | mejor validado '
                      f'{mejor:,.0f} USD ({mejor - margen0:+,.0f}) | {time.time() - t1:.0f} s', flush=True)
                if info['interrumpido']:
                    raise KeyboardInterrupt
            if mejoras_vuelta and mejor - inicio_vuelta >= args.mejora_minima:
                nivel = 0                                       # tras mejorar, vuelve al vecindario más chico
            elif nivel + 1 < len(niveles):
                nivel += 1
                print(f'[vuelta {vuelta}] {"mejora menor que --mejora-minima" if mejoras_vuelta else "sin mejoras"}: '
                      f'pasa al nivel {niveles[nivel]}', flush=True)
            elif mejoras_vuelta:
                print(f'[vuelta {vuelta}] mejora menor que --mejora-minima en el último nivel: se detiene.')
                break
            else:
                print(f'[vuelta {vuelta}] ningún vecindario mejoró: se detiene.')
                break
    except TimeoutError:
        print(f'\n[!] Se cumplieron las {args.horas:g} h de --horas. La mejor solución ya está exportada.')
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
