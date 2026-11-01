"""
Reoptimización avión por avión con camino más largo sobre su red espacio-tiempo (idea del solver SDD del IPre de
diagramas de decisión: precalcular el valor de lo que viene después en cada arco y resolver con un camino más largo).

La red de cada avión es un diagrama de decisión exacto: nodos (aeropuerto, bloque), arcos de espera y de vuelo
(duración ceil((horas de bloque + TAT) / DT) bloques, como en modelo_integrado_espacio_tiempo.py), y cada camino de
(a0, 0) a (a0, T) es una rotación semanal cerrada. Para reoptimizar el avión k con los demás fijos:
  1. Valor de cada arco de vuelo: la ganancia de llevar la demanda pendiente de ese día en ese tramo (lo que los
     demás aviones no entregan), con el mínimo de 10 t y hasta el 95 % del payload, menos el costo del vuelo. Con
     --duales se suma el valor marginal de la capacidad del vuelo en el paso de carga (transbordos y carga en
     varios tramos), leído de la relajación lineal del submodelo de carga con las binarias fijas.
  2. Camino más largo en la red del avión, con su mantenimiento (en tierra en su aeropuerto durante la ventana) y
     las frecuencias mínimas que dejan de cumplir los demás (estado adicional: vuelos hechos en esos tramos).
  3. La rotación nueva se evalúa con el paso de carga exacto (con transbordos) y el validador; se acepta solo si
     sube el margen validado.
Cada camino más largo toma segundos y casi no usa memoria, así que sirve con grillas finas donde el modelo exacto
por avión (busqueda_por_avion.py) es lento o no cabe.

Uso, desde la raíz del repositorio:
    python reoptimizacion_camino_mas_largo.py --corrida data/results/Heuristica_dt1_iter3_AND --dt 1
    RAW=data/instancias/R4 python reoptimizacion_camino_mas_largo.py --corrida data/results/instancias/R4/heuristica_dt2 --dt 2
"""
import argparse
import ctypes
import json
import math
import os
import time
from collections import defaultdict

import pandas as pd

import warm_start_milp as ws   # deja la raíz como directorio de trabajo y agrega src/ (paso de carga y validador)
import gurobi_model as gm


def red_avion(datos, k, dt):
    """Arcos de vuelo del avión k en la grilla: {(a, t): [(e, t_llegada)]}, la ventana de mantenimiento y T."""
    T = int(round(gm.H / dt))
    salidas = defaultdict(list)
    for e in datos['Ek'][k]:
        dur = max(1, math.ceil((datos['tau_e'][e] + datos['TAT_e'][e]) / dt - 1e-9))
        for t in range(T - dur + 1):
            salidas[e[0], t].append((e, t + dur))
    ap_m, h0, h1 = datos['mant'][k]
    ventana = set(range(int(math.floor(h0 / dt)), int(math.ceil(h1 / dt))))
    return salidas, ap_m, ventana, T


def residual(datos, multi, k=None):
    """Demanda (o, d, día) sin entregar en la solución de carga actual (sin contar lo que entrega k, si se da k)."""
    entregado = defaultdict(float)
    for c in multi['solucion']['cargas']:
        if c['k'] != k:
            entregado[tuple(c['q'])] += c['a']
    return {q: max(0.0, d - entregado[q]) for q, d in datos['D_q'].items()}


def contribucion_vuelos(datos, itinerario, multi, k, dt):
    """Contribución actual de cada vuelo del avión k en la solución de carga (incluye carga en varios tramos y
    transbordos): ingreso de cada demanda repartido entre sus tramos por toneladas-hora, como en la exportación,
    menos el handling de lo que embarca. {(e, t): USD}, sin descontar el costo del vuelo."""
    leg = {(k2, l['pos']): l for k2, legs in itinerario.items() for l in legs}
    por_q, aporte = defaultdict(list), defaultdict(float)
    for c in multi['solucion']['cargas']:
        por_q[tuple(c['q'])].append(c)
        if c['k'] == k:
            aporte[c['s']] -= (c['b'] + c.get('transfer_in', 0.0)) * datos['h_k'][k]
    for q, regs in por_q.items():
        total = 1000 * datos['r_q'][q] * sum(c['a'] for c in regs)
        pesos = [c['x'] * (leg[c['k'], c['s']]['t_arr'] - leg[c['k'], c['s']]['t_dep']) for c in regs]
        if total > 0 and sum(pesos) > 0:
            for c, w in zip(regs, pesos):
                if c['k'] == k:
                    aporte[c['s']] += total * w / sum(pesos)
    return {(tuple(l['tramo']), int(round(l['t_dep'] / dt))): aporte[l['pos']] for l in itinerario.get(k, [])}


def valores_arcos(datos, k, salidas, dt, resid, actual=None):
    """Valor de cada arco de vuelo (e, t): ganancia de la carga directa pendiente menos el costo. Con actual
    ({(e, t): contribución}), los vuelos que el avión ya hace valen su contribución real en la solución de carga."""
    P, h, minton, tec = datos['seguro_k'][k], datos['h_k'][k], datos['min_ton'], datos['tec']
    valor = {}
    for (a, t), arcos in salidas.items():
        dia = int(t * dt // 24) + 1
        for e, _ in arcos:
            g = 0.0
            q = (e[0], e[1], dia)
            if q in datos['D_q'] and e[0] not in tec and e[1] not in tec:
                carga = min(P, resid[q])
                if carga >= minton - 1e-9:
                    g = max(0.0, (1000 * datos['r_q'][q] - h) * carga)
            if actual and (e, t) in actual:
                g = max(g, actual[e, t])
            valor[e, t] = g - datos['c_ke'][k, e]
    return valor


def camino_mas_largo(datos, k, dt, valor, faltan):
    """Mejor rotación cerrada del avión k. faltan: {tramo: vuelos mínimos que debe hacer este avión}.
    Devuelve (valor, [(e, t)]) o (None, None) si no hay rotación factible."""
    salidas, ap_m, ventana, T = red_avion(datos, k, dt)
    tramos_f = sorted(faltan)
    tope = tuple(faltan[e] for e in tramos_f)
    idx_f = {e: i for i, e in enumerate(tramos_f)}
    aeropuertos = sorted({a for e in datos['Ek'][k] for a in e} | {ap_m})
    mejor = (None, None)
    for a0 in aeropuertos:
        if 0 in ventana and a0 != ap_m:
            continue
        cero = tuple(0 for _ in tramos_f)
        etiquetas = defaultdict(dict)                 # (a, t) -> {estado: (valor, previo)}
        etiquetas[a0, 0][cero] = (0.0, None)
        for t in range(T):
            for a in aeropuertos:
                nodo = etiquetas.get((a, t))
                if not nodo:
                    continue
                for est, (v, _) in nodo.items():
                    if t not in ventana or a == ap_m:   # espera
                        dest = etiquetas[a, t + 1]
                        if est not in dest or dest[est][0] < v:
                            dest[est] = (v, (a, t, est, None))
                    for e, t2 in salidas.get((a, t), []):
                        if any(tt in ventana for tt in range(t, t2)):
                            continue
                        if e in idx_f:
                            i = idx_f[e]
                            est2 = est[:i] + (min(est[i] + 1, tope[i]),) + est[i + 1:]
                        else:
                            est2 = est
                        v2 = v + valor[e, t]
                        dest = etiquetas[e[1], t2]
                        if est2 not in dest or dest[est2][0] < v2:
                            dest[est2] = (v2, (a, t, est, (e, t)))
        fin = etiquetas.get((a0, T), {}).get(tope)
        if fin and (mejor[0] is None or fin[0] > mejor[0]):
            vuelos, paso = [], fin[1]
            nodo_t, est = (a0, T), tope
            while paso is not None:
                a, t, est_prev, arco = paso
                if arco is not None:
                    vuelos.append(arco)
                paso = etiquetas[a, t][est_prev][1]
            mejor = (fin[0], sorted(vuelos, key=lambda x: x[1]))
    if not faltan and (mejor[0] is None or mejor[0] < 0):
        return 0.0, []                                  # no usar el avión
    return mejor


def flujo_relajacion(datos, itinerario, k, dt, ttr):
    """Flujo del avión k en la relajación lineal del modelo exacto con k libre y los demás fijos (carga y
    transbordos de toda la red dentro del modelo). {(e, t): z*}; vacío si la relajación no se resuelve."""
    import numpy as np
    import gurobipy as gp
    from gurobipy import GRB
    import modelo_integrado_espacio_tiempo as mi
    red = mi.construir(datos, dt, ttr, itinerario, {k}, enlace_fuerte=True)
    vt = red['vt']
    lb = np.where(vt == GRB.SEMICONT, 0.0, red['lb'])
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag', 0)
        env.start()
        with gp.Model(env=env) as m:
            x = m.addMVar(red['n'], lb=lb, ub=red['ub'], obj=red['obj'])
            m.addMConstr(red['A'], x, red['sentido'], red['rhs'])
            m.ModelSense = GRB.MAXIMIZE
            m.Params.Method = 2
            m.Params.Crossover = 0
            m.optimize()
            if m.Status != GRB.OPTIMAL:
                return {}
            xv = x.X
    libres = np.nonzero(red['a_libre'])[0]
    return {(red['E'][red['a_e'][i]], int(red['a_t'][i])): float(xv[red['z'][i]]) for i in libres
            if xv[red['z'][i]] > 1e-6}


def rotacion_actual(itinerario, k, dt):
    return [(tuple(l['tramo']), int(round(l['t_dep'] / dt))) for l in sorted(itinerario.get(k, []), key=lambda l: l['pos'])]


def a_itinerario(datos, vuelos, dt):
    return [{'pos': p, 'tramo': e, 't_dep': t * dt, 't_arr': round(t * dt + datos['tau_e'][e], 5)}
            for p, (e, t) in enumerate(vuelos, start=1)]


def frecuencias_faltantes(datos, itinerario, k):
    cuenta = defaultdict(int)
    for k2, legs in itinerario.items():
        if k2 != k:
            for l in legs:
                cuenta[tuple(l['tramo'])] += 1
    faltan = {e: f - cuenta[e] for e, f in datos['f_od'].items() if f - cuenta[e] > 0}
    if any(e not in datos['Ek'][k] for e in faltan):
        return None                                     # este avión solo no puede cubrir esas frecuencias
    return faltan


def exportar(salida, itinerario, multi, val, datos, dt, ttr, origen, historial):
    info = {'modelo': f'Reoptimización avión por avión con camino más largo (DT {dt:g} h)', 'corrida_start': origen,
            'estado': 'Búsqueda local', 'aviones_libres': 'uno a la vez', 'tiempo_limite_s': None,
            'pasos_aceptados': historial}
    kpis = ws.exportar(salida, itinerario, multi, val, datos, info, ttr)
    ruta = os.path.join(salida, 'metadata.json')
    meta = json.load(open(ruta, encoding='utf-8'))
    meta.update(descripcion=f'Camino más largo avión por avión (DT {dt:g} h) desde {origen}', dt=f'{dt:g}')
    json.dump(meta, open(ruta, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    return kpis


def main():
    ap = argparse.ArgumentParser(description='Reoptimización avión por avión con camino más largo.')
    ap.add_argument('--corrida', required=True, help='solución de partida (itinerario en la grilla de --dt)')
    ap.add_argument('--dt', type=float, default=1.0, help='horas por bloque. Por defecto 1')
    ap.add_argument('--ttr', type=float, default=None, help='TTR_H (h); por defecto el de la corrida')
    ap.add_argument('--vueltas', type=int, default=5, help='vueltas máximas sobre los aviones. Por defecto 5')
    ap.add_argument('--guia-lp', action='store_true',
                    help='agrega un candidato que sigue el flujo de la relajación lineal del modelo exacto con el '
                         'avión libre (ve transbordos y conexiones; resuelve un LP por avión)')
    ap.add_argument('--salida', default=None, help='carpeta de salida (por defecto data/results/Camino_mas_largo_dt<DT>)')
    args = ap.parse_args()

    if os.name == 'nt':
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    t0 = time.time()
    dt = args.dt
    salida = args.salida or os.path.join('data', 'results', f'Camino_mas_largo_dt{dt:g}')
    os.makedirs(salida, exist_ok=True)
    corrida = os.path.normpath(args.corrida)
    kpi = json.load(open(os.path.join(corrida, 'indicadores_kpi.json'), encoding='utf-8'))['kpis']
    ttr = args.ttr if args.ttr is not None else float(kpi.get('ttr_h', 2.0))
    datos = gm.cargar_datos(gm.RAW)
    itinerario = ws.leer_itinerario(corrida)
    for k in datos['K']:
        itinerario.setdefault(k, [])
    multi, val = ws.resolver_carga(itinerario, ttr)
    margen0 = mejor = val['margen']
    print(f'[inicio] {os.path.basename(corrida)} | DT = {dt:g} h | margen validado {mejor:,.0f} USD', flush=True)
    historial, filas = [], []
    for vuelta in range(1, args.vueltas + 1):
        mejoras = 0
        for k in datos['K']:
            t1 = time.time()
            faltan = frecuencias_faltantes(datos, itinerario, k)
            if faltan is None:
                print(f'  [{vuelta}] {k}: no puede cubrir solo las frecuencias que dejan los demás; se salta')
                continue
            salidas = red_avion(datos, k, dt)[0]
            actual = rotacion_actual(itinerario, k, dt)
            # dos candidatos: (a) los vuelos actuales valen su contribución real y los nuevos solo la demanda que
            # nadie atiende; (b) todo se valora por la carga directa que queda libre sin este avión
            variantes = {'contribucion': valores_arcos(datos, k, salidas, dt, residual(datos, multi),
                                                       contribucion_vuelos(datos, itinerario, multi, k, dt)),
                         'directa': valores_arcos(datos, k, salidas, dt, residual(datos, multi, k))}
            if args.guia_lp:   # (c) rotación entera que sigue el flujo fraccionario de la relajación lineal
                z = flujo_relajacion(datos, itinerario, k, dt, ttr)
                if z:
                    variantes['relajacion'] = {a: z.get(a, 0.0) - 0.5 for a in variantes['directa']}   # redondeo: solo convienen los arcos con flujo > 0,5
            candidatos = {}
            for nombre, valor in variantes.items():
                v_nuevo, vuelos = camino_mas_largo(datos, k, dt, valor, faltan)
                v_actual = sum(valor.get(x, -1e18) for x in actual)
                umbral = 1e-3 if nombre == 'relajacion' else 1.0
                if v_nuevo is not None and vuelos != actual and v_nuevo > v_actual + umbral:
                    candidatos.setdefault(tuple(vuelos), []).append(nombre)
            t_dp = time.time() - t1
            acepta, evaluados = False, []
            for vuelos, nombres in candidatos.items():
                candidato = dict(itinerario)
                candidato[k] = a_itinerario(datos, list(vuelos), dt)
                try:
                    multi_n, val_n = ws.resolver_carga(candidato, ttr)
                except RuntimeError as e:
                    print(f'  [{vuelta}] {k}: el paso de carga falló ({e})')
                    continue
                evaluados.append(f"{'+'.join(nombres)} {val_n['margen']:,.0f}")
                if val_n['ok'] and val_n['margen'] > mejor + 1.0:
                    acepta, mejor = True, val_n['margen']
                    itinerario, multi, val = candidato, multi_n, val_n
            if acepta:
                historial.append({'vuelta': vuelta, 'avion': k, 'margen': mejor})
                mejoras += 1
            filas.append({'vuelta': vuelta, 'avion': k, 'candidatos': '; '.join(evaluados), 'aceptado': acepta,
                          'mejor_margen': round(mejor, 2), 'segundos_camino': round(t_dp, 2),
                          'segundos_total': round(time.time() - t1, 2)})
            print(f'  [{vuelta}] {k}: {"MEJORA" if acepta else "sin mejora"} | candidatos: '
                  f'{"; ".join(evaluados) or "ninguno"} | caminos {t_dp:.1f} s | mejor {mejor:,.0f} '
                  f'({mejor - margen0:+,.0f}) | {time.time() - t1:.0f} s', flush=True)
        if not mejoras:
            break
    pd.DataFrame(filas).to_csv(os.path.join(salida, 'progreso.csv'), index=False)
    exportar(salida, itinerario, multi, val, datos, dt, ttr, os.path.basename(corrida), historial)
    if os.name == 'nt':
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    print(f'\nMargen validado: {margen0:,.0f} -> {mejor:,.0f} USD ({mejor - margen0:+,.0f}) | {len(historial)} mejoras | '
          f'{(time.time() - t0) / 60:.1f} min | resultados en {salida}')


if __name__ == '__main__':
    main()
