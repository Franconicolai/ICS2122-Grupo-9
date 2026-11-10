"""
Búsqueda avión por avión sobre el modelo integrado en red espacio-tiempo (modelo_integrado_espacio_tiempo.py).

En cada paso libera un solo avión, con el resto fijo en la mejor solución conocida, y resuelve el modelo integrado
(vuelos, carga multitramo y transbordos exactos) con la heurística NoRel de Gurobi, partiendo de esa solución. Si el
margen validado mejora, la acepta, la exporta y el siguiente avión parte de ella. Pensado para correr de noche con
una grilla fina (DT = 1 h), donde el modelo completo no cabe en memoria pero el de un avión sí.

A diferencia de src/reoptimizacion_por_avion.py (paso de la heurística), aquí la carga y los transbordos de toda la
red se reoptimizan junto con las rotaciones del avión liberado.

Vecindarios (--vecindario): 'avion' libera un avión; 'par', dos aviones que comparten aeropuertos o transbordos
(los pares más afines primero); 'trio', cada par afín más el avión más afín a ambos; 'operador', la flota de un
operador; 'ventana' (solo --modelo continuo), todos los vuelos que salen en una ventana de tiempo de --ventana-h horas, en
todos los aviones, con el resto de la semana fijo ('ventana48': ventanas de 48 h). Las ventanas liberan juntos los
vuelos de un mismo transbordo, que van en aviones distintos y que los vecindarios por avión rara vez liberan a la vez. Con varios niveles (--vecindario avion,par) es una
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
    ventana = ventana_de(grupo)
    modelo = gm.construir_modelo_completo(datos, transbordos=True)
    sufijos = {}
    if ventana and args.desplazar:
        info_pos, sufijos = posiciones_ventana_flexible(itinerario, datos, *ventana, args.desplazar,
                                                       args.desplazar_aviones, args.ventana_extra,
                                                       args.desplazar_presupuesto)
        libres = set(info_pos)
        pares, _ = mct.enumerar_pares_flexibles(datos, itinerario, info_pos, ttr)
    elif ventana:
        libres, cotas = posiciones_ventana(itinerario, datos, *ventana, args.ventana_extra)
        pares, _ = mct.enumerar_pares_posiciones(datos, itinerario, libres, cotas, ttr)
    else:
        pares, _ = mct.enumerar_pares(datos, itinerario, set(grupo), ttr)
    mct.agregar_transbordos(modelo, datos, pares, ttr)
    st = mct.armar_start(modelo, datos, itinerario, multi['solucion'], pares)
    if sufijos:
        z = mct.agregar_desplazamientos(modelo, sufijos)
        for k in sufijos:
            st[z[k, 0]] = 1                     # la solución actual no desplaza nada
    if ventana:
        mct.fijar_posiciones(modelo, st, libres)
    else:
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
    if nuevo is not None and 'f' in modelo._v:   # filiales exactas: cada vuelo trae la filial que eligió el modelo
        fv = modelo.getAttr('X', modelo._v['f'])
        for k, legs in nuevo.items():
            for leg in legs:
                o = max(sorted(datos['exf']), key=lambda o: fv[k, leg['pos'], o])
                if o != datos['op'][k]:
                    leg['op'] = o
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


def ventana_de(grupo):
    """(t_ini, t_fin) si el vecindario es una ventana de tiempo ('ventana_48-72h'); None si es un grupo de aviones."""
    if len(grupo) == 1 and grupo[0].startswith('ventana_'):
        t_ini, t_fin = grupo[0][len('ventana_'):-1].split('-')
        return float(t_ini), float(t_fin)
    return None


def posiciones_ventana(itinerario, datos, t_ini, t_fin, extra):
    """Posiciones (k, s) que libera una ventana de tiempo: en todos los aviones, los vuelos que salen en [t_ini, t_fin).
    Como las posiciones se ocupan sin huecos, cada avión mantiene su número de vuelos en la ventana, salvo cuando la
    ventana llega a su último vuelo o al fin de la semana: ahí se liberan además hasta `extra` posiciones vacías, para
    agregar o quitar vuelos al final de la rotación. Devuelve (libres, cotas), con cotas[k] = (lo, hi): llegada del
    vuelo fijo anterior y salida del vuelo fijo siguiente (0 y H si no hay)."""
    libres, cotas = set(), {}
    for k in datos['K']:
        legs = sorted(itinerario.get(k, []), key=lambda l: l['pos'])
        n = len(legs)
        dentro = [l['pos'] for l in legs if t_ini <= l['t_dep'] < t_fin]
        if n and (dentro and dentro[-1] == n or t_fin >= gm.H - 1e-9):
            dentro += list(range(n + 1, min(datos['S_max_k'][k], n + extra) + 1))
        if not dentro:
            continue
        libres.update((k, s) for s in dentro)
        antes = [l for l in legs if l['pos'] < dentro[0]]
        despues = [l for l in legs if l['pos'] > dentro[-1]]
        cotas[k] = (antes[-1]['t_arr'] if antes else 0.0, despues[0]['t_dep'] if despues else gm.H)
    return libres, cotas


def ocio_en_ventana(legs, datos, k, t_ini, t_fin):
    """Horas en que el avión k está en tierra sin cumplir TAT ni mantenimiento dentro de [t_ini, t_fin)."""
    H, tat = gm.H, datos['TAT_e']
    libres = [(0.0, legs[0]['t_dep'])] if legs else [(0.0, H)]
    for l1, l2 in zip(legs, legs[1:]):
        libres.append((l1['t_arr'] + tat[tuple(l1['tramo'])], l2['t_dep']))
    if legs:
        libres.append((legs[-1]['t_arr'] + tat[tuple(legs[-1]['tramo'])], H))
    _, m_ini, m_fin = datos['mant'][k]
    horas = 0.0
    for a, b in libres:
        a, b = max(a, t_ini), min(b, t_fin)
        if b > a:
            horas += b - a - max(0.0, min(b, m_fin) - max(a, m_ini))
    return horas


def posiciones_ventana_flexible(itinerario, datos, t_ini, t_fin, desplazar, max_aviones, extra, presupuesto=0):
    """Ventana con desplazamiento. Libera las posiciones de la ventana como posiciones_ventana y, en los max_aviones
    aviones con más horas ociosas dentro de la ventana (sin pasar de `presupuesto` vuelos desplazables), permite ganar o perder hasta `desplazar` vuelos en cualquier
    parte de la semana: sus vuelos posteriores a la ventana mantienen tramo y horario y se corren todos delta
    posiciones (milp_continuo_transbordos.agregar_desplazamientos). Un avión sin vuelos en la ventana también puede
    desplazarse, para usar un hueco largo.
    Devuelve (info, sufijos): info[(k, s)] = (alternativas, salida_min, salida_max, llegada_min) de cada posición libre
    (alternativas: None si es un vuelo de la ventana, o la lista de vuelos (tramo, salida, llegada) que puede ocupar), y
    sufijos[k] los datos del desplazamiento de cada avión que se desplaza."""
    H, S = gm.H, datos['S_max_k']
    piernas = {k: sorted(itinerario.get(k, []), key=lambda l: l['pos']) for k in datos['K']}
    ocio = {k: ocio_en_ventana(piernas[k], datos, k, t_ini, t_fin) for k in datos['K'] if piernas[k]}
    desplazan, usados = set(), 0
    for k in sorted((k for k in ocio if ocio[k] >= 3.0), key=lambda k: -ocio[k])[:max_aviones]:
        posteriores = sum(1 for l in piernas[k] if l['t_dep'] >= t_ini)   # vuelos que se correrían de posición
        if presupuesto and usados + posteriores > presupuesto:
            continue
        desplazan.add(k)
        usados += posteriores
    info, sufijos = {}, {}
    for k, legs in piernas.items():
        n = len(legs)
        if not n:
            continue
        dentro = [l['pos'] for l in legs if t_ini <= l['t_dep'] < t_fin]
        if dentro:
            a, b = dentro[0], dentro[-1]
        elif k in desplazan:                     # sin vuelos en la ventana: la ventana es el hueco antes del vuelo a
            a = next((l['pos'] for l in legs if l['t_dep'] >= t_fin), n + 1)
            b = a - 1
        else:
            continue
        lo = legs[a - 2]['t_arr'] if a >= 2 else 0.0
        if b == n or t_fin >= H - 1e-9:          # la ventana llega al final de la rotación: posiciones vacías al final
            for s in range(a, min(S[k], n + extra) + 1):
                info[k, s] = (None, lo, H, lo)
            continue
        if k not in desplazan:                   # ventana normal: mantiene el número de vuelos
            for s in range(a, b + 1):
                info[k, s] = (None, lo, legs[b]['t_dep'], lo)
            continue
        dmin, dmax = -min(desplazar, b - a + 1), min(desplazar, S[k] - n)
        suf = legs[b:]                           # vuelos b+1..n, con tramo y horario fijos
        sufijos[k] = {'b': b, 'n': n, 'dmin': dmin, 'dmax': dmax,
                      'vuelos': [(l['pos'], tuple(l['tramo']), l['t_dep']) for l in suf]}
        tope = suf[0]['t_dep']                   # los vuelos de la ventana terminan antes del primer vuelo del sufijo
        for s in range(a, n + dmax + 1):
            cand = [(tuple(l['tramo']), l['t_dep'], l['t_arr']) for l in suf if dmin <= s - l['pos'] <= dmax]
            if s <= b + dmax:                    # puede ser un vuelo de la ventana
                info[k, s] = (None, min([lo] + [c[1] for c in cand]), max([tope] + [c[1] for c in cand]),
                              min([lo] + [c[2] for c in cand]))
            else:
                info[k, s] = (cand, min(c[1] for c in cand), max(c[1] for c in cand), min(c[2] for c in cand))
    return info, sufijos


def huella_region(grupo, itinerario, multi, args):
    """Vuelos de la región de un vecindario: los que salen en la ventana +- --ventana-h, o los de los aviones del
    grupo y de los aviones que les transbordan carga. Si no cambió desde que el vecindario no mejoró, se salta."""
    ventana = ventana_de(grupo)
    if ventana:
        t_ini, t_fin = ventana[0] - args.ventana_h, ventana[1] + args.ventana_h
        return frozenset(v for v in vuelos(itinerario) if t_ini <= v[2] < t_fin)
    socios = set(grupo)
    for tr in multi['solucion'].get('transferencias', []):
        if tr['from_k'] in grupo or tr['to_k'] in grupo:
            socios.update((tr['from_k'], tr['to_k']))
    return frozenset(v for v in vuelos(itinerario) if v[0] in socios)


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
    if nivel.startswith('ventana'):                             # ventanas de tiempo que se traslapan
        # 'ventana' usa --ventana-h y --ventana-paso; 'ventana48' usa ventanas de 48 h con paso de 24 h
        ancho = float(nivel[len('ventana'):]) if nivel != 'ventana' else args.ventana_h
        paso = ancho / 2 if nivel != 'ventana' else args.ventana_paso
        desde, hasta = (float(x) for x in args.ventana_rango.split('-'))
        inicios = np.arange(desde, hasta - ancho / 2 + 1e-9, paso)
        return [(f'ventana_{t:g}-{min(t + ancho, hasta):g}h',) for t in inicios]
    raise ValueError(f'vecindario desconocido: {nivel} (use avion, par, trio, operador o ventana)')


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
    info = {'modelo': f'Búsqueda por vecindarios (aviones o ventanas de tiempo), {red}',
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
                    help='niveles de vecindario, en orden: avion, par, trio, operador, ventana, ventanaNN (ventanas de NN h; solo '
                         'con --modelo continuo). P. ej. avion,par o ventana,ventana48,par. Por defecto avion')
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
    ap.add_argument('--ventana-h', type=float, default=24,
                    help='nivel ventana (solo --modelo continuo): horas de cada ventana de tiempo. Por defecto 24')
    ap.add_argument('--ventana-paso', type=float, default=12,
                    help='nivel ventana: horas entre el inicio de dos ventanas seguidas. Por defecto 12')
    ap.add_argument('--ventana-rango', default=f'0-{gm.H:g}',
                    help='nivel ventana: horas de la semana que recorren las ventanas (p. ej. 108-168). Por defecto toda')
    ap.add_argument('--desplazar', type=int, default=0,
                    help='nivel ventana: cada avión puede ganar o perder hasta N vuelos dentro de la ventana, en '
                         'cualquier parte de la semana (los vuelos posteriores se corren de posición sin cambiar tramo '
                         'ni horario). 0 = mantiene el número de vuelos salvo al final de la rotación. Por defecto 0')
    ap.add_argument('--desplazar-aviones', type=int, default=3,
                    help='con --desplazar: cuántos aviones por ventana pueden desplazarse (los de más horas ociosas '
                         'en la ventana). Por defecto 3')
    ap.add_argument('--desplazar-presupuesto', type=int, default=0,
                    help='con --desplazar: tope al total de vuelos que se corren de posición en una ventana (suma sobre '
                         'los aviones que se desplazan). En las ventanas del comienzo de la semana todo lo posterior se '
                         'corre y el modelo crece mucho; 0 = sin tope. Por defecto 0')
    ap.add_argument('--ventana-extra', type=int, default=2,
                    help='nivel ventana: posiciones vacías que se liberan al final de la rotación. Por defecto 2')
    ap.add_argument('--umbral-aceptar', type=float, default=1.0,
                    help='USD que debe ganar un vecindario para aceptar la solución (las mejoras de unos pocos USD '
                         'son reprogramaciones casi equivalentes que obligan a reevaluar la región). Por defecto 1')
    ap.add_argument('--saltar-sin-cambios', action='store_true',
                    help='no repite un vecindario que no mejoró mientras no cambien los vuelos de su región (la '
                         'ventana +- --ventana-h, o los aviones del grupo y los que les transbordan carga)')
    ap.add_argument('--horas', type=float, default=0, help='detiene la búsqueda tras estas horas (0 = sin límite)')
    ap.add_argument('--sin-enlace-fuerte', dest='enlace_fuerte', action='store_false',
                    help='omite las desigualdades de enlace fuerte (por defecto se agregan: con ellas NoRel encontró '
                         'la mejora de AC-10 en menos de 1.000 s)')
    ap.add_argument('--deposito-agregado', action='store_true',
                    help='inventario de transbordos agregado por aeropuerto (~30 %% menos variables y memoria; cada '
                         'candidato se revalida con el paso de carga exacto)')
    ap.add_argument('--interchange', action='store_true',
                    help='permite que otra filial opere cada vuelo (docs/README_interchanges.md): los modelos usan el '
                         'costo de la filial más barata con derechos y cada candidato se evalúa eligiendo las filiales')
    ap.add_argument('--exacto', action='store_true',
                    help='con --modelo continuo: filiales exactas en el MILP de cada vecindario (la filial de cada vuelo '
                         'es una variable con todas las reglas del validador; docs/README_milp_exacto.md). Implica '
                         '--interchange')
    ap.add_argument('--borde', choices=('base', 'flexible'), default=None,
                    help='borde de la semana con interchanges: flexible (por defecto) basta volver al operador base '
                         'en tierra; base exige que el primer y el último vuelo sean del operador base')
    ap.add_argument('--hilos', type=int, default=0, help='Threads de Gurobi (0 = todos)')
    ap.add_argument('--memoria', type=float, default=6, help='GB máximos para Gurobi por avión. Por defecto 6')
    ap.add_argument('--salida', default=None, help='carpeta de salida (por defecto data/results/Busqueda_por_avion_dt<DT>)')
    args = ap.parse_args()

    t0 = time.time()
    continuo = args.modelo == 'continuo'
    if 'ventana' in args.vecindario and not continuo:
        sys.exit('el nivel ventana necesita --modelo continuo')
    dt = 0 if continuo else args.dt
    args.salida = args.salida or os.path.join('data', 'results', 'Busqueda_continua' if continuo
                                              else f'Busqueda_por_avion_dt{dt:g}')
    os.makedirs(os.path.join(args.salida, 'logs'), exist_ok=True)
    corrida = os.path.normpath(args.corrida)
    kpi = json.load(open(os.path.join(corrida, 'indicadores_kpi.json'), encoding='utf-8'))['kpis']
    ttr = args.ttr if args.ttr is not None else float(kpi.get('ttr_h', 2.0))
    gm.INTERCHANGE = gm.INTERCHANGE or args.interchange or args.exacto
    if args.borde:
        ws.CFG_VALIDADOR['borde_operador_base'] = args.borde == 'base'
    datos = gm.cargar_datos(s_max=args.smax) if continuo else gm.cargar_datos()
    datos['borde_base'] = ws.CFG_VALIDADOR['borde_operador_base']
    datos['filiales_exactas'] = args.exacto and continuo
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
    if gm.INTERCHANGE and val['ok']:
        itinerario = multi['solucion']['itinerario']
    if not val['ok']:
        sys.exit(f'la solución de partida no pasa el validador: {val["violaciones"][:5]}')
    margen0 = mejor = val['margen']
    inter = (f' | interchanges{" (filiales exactas)" if datos["filiales_exactas"] else ""}, borde '
             f'{"base" if ws.CFG_VALIDADOR["borde_operador_base"] else "flexible"}' if gm.INTERCHANGE else '')
    print(f'[inicio] {os.path.basename(corrida)} | {"tiempo continuo" if continuo else f"DT = {dt:g} h"} | TTR_H = {ttr:g} h{inter} | margen validado {mejor:,.0f} USD | '
          f'{len(aviones)} aviones x {args.vueltas} vuelta(s) x {args.tiempo:,.0f} s | salida {args.salida}', flush=True)

    ruta_prog = os.path.join(args.salida, 'progreso.csv')
    historial = []
    if os.path.normpath(args.salida) != corrida:
        exportar_mejor(args.salida, itinerario, multi, val, datos, dt, ttr, os.path.basename(corrida), historial)
    mantener_despierto(True)
    niveles = [n.strip() for n in args.vecindario.split(',') if n.strip()]
    evaluados = {}                                      # vecindario -> huella de su región cuando no mejoró
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
                huella = huella_region(grupo, itinerario, multi, args) if args.saltar_sin_cambios else None
                if huella is not None and evaluados.get(grupo) == huella:
                    print(f'  [vuelta {vuelta}] {avion}: sin cambios en su región desde que no mejoró, se salta', flush=True)
                    continue
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
                    if gm.INTERCHANGE:   # los vuelos que no cambian parten con la filial que ya tienen
                        filial = {(k, tuple(l['tramo']), round(l['t_dep'], 4)): l['op']
                                  for k, legs in itinerario.items() for l in legs if l.get('op')}
                        nuevo = {k: [dict(l, op=filial[k, tuple(l['tramo']), round(l['t_dep'], 4)])
                                     if (k, tuple(l['tramo']), round(l['t_dep'], 4)) in filial else l for l in legs]
                                 for k, legs in nuevo.items()}
                    multi_n, val_n = ws.resolver_carga(nuevo, ttr)
                    margen_nuevo = val_n['margen']
                    if val_n['ok'] and margen_nuevo > mejor + args.umbral_aceptar:
                        acepta = True
                        # con INTERCHANGE, el itinerario del submodelo de carga trae la filial elegida de cada vuelo
                        itinerario, multi, val = multi_n['solucion']['itinerario'], multi_n, val_n
                        evaluados.pop(grupo, None)
                        historial.append({'vuelta': vuelta, 'avion': avion, 'margen': margen_nuevo,
                                          'mejora': margen_nuevo - mejor})
                        mejor = margen_nuevo
                        mejoras_vuelta += 1
                        exportar_mejor(args.salida, itinerario, multi, val, datos, dt, ttr,
                                       os.path.basename(corrida), historial)
                if huella is not None and not acepta:
                    evaluados[grupo] = huella
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
