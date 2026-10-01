"""
Paso 2 (versión que sí garantiza rotaciones individuales): reoptimización por operador con aviones individuales.

La red agregada (paso 1) puede producir recorridos que solo cierran cada dos o más semanas al repartirlos
en aviones. Aquí, operador por operador, se resuelve una red espacio-tiempo POR AVIÓN, con:
  - rotación cerrada de exactamente una semana por avión (cruza el corte domingo-lunes una sola vez),
  - ventana de mantenimiento exacta de cada avión (en tierra en su aeropuerto durante toda la ventana),
  - payload propio de cada avión y mínimo de 10 t por vuelo cargado,
  - demanda diaria y frecuencias mínimas descontando lo que ya cubren los demás operadores.
Para acotar el tamaño, cada operador solo considera los tramos que usó en la solución agregada, más los
tramos con frecuencia mínima y los que salen o llegan a sus aeropuertos de mantenimiento (tramos candidatos).
Al terminar un operador, su solución reemplaza a la agregada (esquema tipo Gauss-Seidel).
"""
import math, os, sys, pickle, json, time
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd
import yaml
import highspy
from scipy.sparse import csc_matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # validador.py de José está en esta misma carpeta
from validador import validar
from asignacion_carga_multitramos import asignar_carga_multitramos

RAW = os.environ.get('RAW', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'raw data'))
TLIM_OP = float(os.environ.get('TLIM_OP', 300))
AMPLIAR = os.environ.get('AMPLIAR', '0') == '1'   # 1: usar todos los tramos del operador (modelo completo por avión)
TTR_H = float(os.environ.get('TTR_H', 2.0))       # provisional; debe confirmarse con el mandante
TLIM_CARGA = float(os.environ.get('TLIM_CARGA', 300))
START_PKL = os.environ.get('START_PKL')    # .pkl "_poravion.pkl" de un paso 2 previo
SUFIJO = os.environ.get('SUFIJO', '')      # para no pisar resultados
BONO_PKL = os.environ.get('BONO_PKL') 


def resolver_mip(lb, ub, obj, integ, rows, tlim, gap=0.01, log=False, start=None):
    from solver_util import resolver_matriz, filas_a_matriz
    A, lo, hi = filas_a_matriz(rows, len(lb))
    r = resolver_matriz(lb, ub, obj, integ, A, lo, hi, tlim, gap, log=log, start=start)
    r.update(nvars=len(lb), nint=sum(integ), nrows=len(rows))
    return r

def armar_start(prev, K, idx, nvars, T, DT, dur, dem_res, minton, payload):
    """Vector inicial (u, z, zl, x, g) desde un itinerario por avión previo."""
    st = np.zeros(nvars)
    restante = defaultdict(float, dem_res)
    usados = descartados = 0
    for k in K:
        legs = sorted(prev['itinerario'].get(k, []), key=lambda l: l['t_dep'])
        if not legs: continue
        fl_k = [(int(round(l['t_dep'] / DT)), tuple(l['tramo']), l['pos']) for l in legs]
        if any(('z', k, e, t) not in idx for t, e, _ in fl_k):
            descartados += 1; continue
        st[idx[('u', k)]] = 1
        for t, e, p in fl_k:
            st[idx[('z', k, e, t)]] = 1
            if ('x', k, e, t) in idx:
                q = (e[0], e[1], int(t * DT // 24))
                v = min(prev['cargas'].get((k, p), 0.0), payload[k], max(restante[q], 0.0))
                if v >= minton - 1e-6:
                    st[idx[('x', k, e, t)]] = v
                    st[idx[('zl', k, e, t)]] = 1
                    restante[q] -= v
        for i, (t, e, _) in enumerate(fl_k):
            llegada = t + dur[e]
            salida_sig = fl_k[i + 1][0] if i + 1 < len(fl_k) else fl_k[0][0] + T
            for s in range(llegada, salida_sig):
                st[idx[('g', k, e[1], s % T)]] = 1
        usados += 1
    return st, usados, descartados


def main(pkl):
    P = pickle.load(open(pkl, 'rb'))
    info, sol, DT = P['info'], P['sol'], P['DT']
    T, dur, tau = info['T'], info['dur'], info['tau']
    c, fee, fuel_h, tarifa = info['c'], info['fee'], info['fuel_h'], info['tarifa']
    dem, fmin = info['dem'], info['fmin']

    prev = pickle.load(open(START_PKL, 'rb')) if START_PKL else None
    bono = pickle.load(open(BONO_PKL, 'rb')) if BONO_PKL else {}   # bono: solo nivel 2

    fl = pd.read_csv(os.path.join(RAW, 'fleet.csv'))
    mt = pd.read_csv(os.path.join(RAW, 'maintenance.csv'))
    reglas = yaml.safe_load(open(os.path.join(RAW, 'ops_rules.yaml'), encoding='utf-8'))
    tec = set(reglas['technical_stop_airports'])
    minton = float(reglas['min_tons_per_extra_stop'])
    ini = datetime.fromisoformat(str(reglas['week_start']))
    payload = dict(zip(fl.aircraft_id, fl.payload_tons.astype(float)))
    mant = {}
    for r in mt.itertuples():
        h0 = (datetime.fromisoformat(r.start_datetime) - ini).total_seconds() / 3600
        h1 = (datetime.fromisoformat(r.end_datetime) - ini).total_seconds() / 3600
        mant[r.aircraft_id] = (r.airport, [t % T for t in range(int(math.floor(h0 / DT)), int(math.ceil(h1 / DT)))])

    # estado actual por operador: vuelos {(e,t): n} y carga {(e,t): x}
    vuelos = {op: defaultdict(float) for op in info['ops']}
    carga = {op: defaultdict(float) for op in info['ops']}
    for (op, e, t), j in info['n'].items():
        if round(sol[j]) > 0: vuelos[op][(e, t)] += round(sol[j])
    for (op, e, t), j in info['x'].items():
        if sol[j] > 1e-6: carga[op][(e, t)] += sol[j]

    itinerario, cargas = {}, {}
    orden = sorted(info['ops'], key=lambda o: info['N_op'][o])
    if os.environ.get('SOLO'): orden = os.environ['SOLO'].split(',')     # operadores chicos primero
    resumen = []
    for op in orden:
        K = list(fl[fl.operator == op].aircraft_id)
        otros = [o for o in info['ops'] if o != op]
        # residuales de demanda y frecuencia
        dem_res = defaultdict(float)
        for q, d in dem.items(): dem_res[q] = d
        for o in otros:
            for (e, t), x in carga[o].items(): dem_res[(e[0], e[1], int(t * DT // 24))] -= x
        freq_res = dict(fmin)
        for o in otros:
            for (e, t), n in vuelos[o].items():
                if e in freq_res: freq_res[e] -= n
        # tramos candidatos
        todos = info['legs_op'][op]
        if AMPLIAR:
            cand = set(todos)
        else:
            usados = {e for (e, t) in vuelos[op]}
            m_ap = {mant[k][0] for k in K}
            cand = usados | {e for e in todos if e in fmin and freq_res.get(e, 0) > 0} \
                   | {e for e in todos if (e[0] in m_ap or e[1] in m_ap)}
            ap_usados = {a for e in cand for a in e} | m_ap
            cand |= {e for e in todos if e[0] in ap_usados and e[1] in ap_usados}   # todos los tramos entre aeropuertos ya visitados
            cand = {e for e in cand if e in set(todos)}
            # cierre: asegurar tramo de vuelta hacia el aeropuerto de mantenimiento desde cada destino usado
            for e in list(cand):
                for f in todos:
                    if f[0] == e[1] and f[1] in m_ap: cand.add(f)
        A = sorted({e[0] for e in cand} | {e[1] for e in cand} | {mant[k][0] for k in K})

        lb, ub, obj, integ, idx = [], [], [], [], {}
        def var(key, l, u, o, i):
            idx[key] = len(lb); lb.append(l); ub.append(u); obj.append(o); integ.append(i)
        for k in K:
            var(('u', k), 0, 1, 0.0, 1)
            for e in cand:
                costo = (fuel_h + c[('ex_fuel_usd_per_block_hour', op)]) * tau[e] + fee[e[1]]
                carga_ok = e in tarifa and e[0] not in tec and e[1] not in tec
                for t in range(T):
                    if t + dur[e] > T: continue
                    var(('z', k, e, t), 0, 1, -costo + bono.get((op, e, t), 0.0), 1)
                    if carga_ok and dem_res.get((e[0], e[1], int(t * DT // 24)), 0) > minton - 1e-6:
                        var(('zl', k, e, t), 0, 1, 0.0, 1)
                        var(('x', k, e, t), 0, payload[k], 1000 * tarifa[e] - c[('handling_usd_per_ton', op)], 0)
            for a in A:
                for t in range(T): var(('g', k, a, t), 0, 1, 0.0, 0)
        rows = []
        sale = defaultdict(list); llega = defaultdict(list); corte = defaultdict(list)
        for key, j in idx.items():
            if key[0] != 'z': continue
            _, k, e, t = key
            sale[(k, e[0], t)].append(j); llega[(k, e[1], (t + dur[e]) % T)].append(j)
            if t + dur[e] >= T: corte[k].append(j)
        for k in K:
            for a in A:
                for t in range(T):
                    co = defaultdict(float)
                    for j in llega[(k, a, t)]: co[j] += 1
                    co[idx[('g', k, a, (t - 1) % T)]] += 1
                    for j in sale[(k, a, t)]: co[j] -= 1
                    co[idx[('g', k, a, t)]] -= 1
                    rows.append((co, 0, 0))
            co = {idx[('g', k, a, T - 1)]: 1 for a in A}
            for j in corte[k]: co[j] = 1
            co[idx[('u', k)]] = -1
            rows.append((co, 0, 0))
            am, W = mant[k]
            for t in (W if os.environ.get('SIN_MANT') != '1' else []):
                rows.append(({idx[('g', k, am, t)]: 1, idx[('u', k)]: -1}, 0, np.inf))
        por_q = defaultdict(list); por_od = defaultdict(list)
        for key, j in idx.items():
            if key[0] == 'x':
                _, k, e, t = key
                rows.append(({j: 1, idx[('zl', k, e, t)]: -minton}, 0, np.inf))
                rows.append(({j: 1, idx[('zl', k, e, t)]: -payload[k]}, -np.inf, 0))
                rows.append(({idx[('zl', k, e, t)]: 1, idx[('z', k, e, t)]: -1}, -np.inf, 0))
                por_q[(e[0], e[1], int(t * DT // 24))].append(j)
            if key[0] == 'z':
                por_od[key[2]].append(j)
        for q, js in por_q.items():
            rows.append(({j: 1 for j in js}, -np.inf, max(dem_res[q], 0.0)))
        for od, f in freq_res.items():
            if f > 0 and os.environ.get('SIN_FREQ') != '1':
                rows.append(({j: 1 for j in por_od.get(od, [])}, f, np.inf))
        # romper simetría: aviones idénticos (mismo payload y aeropuerto de mantenimiento) se ordenan por uso
        st = None
        if prev is not None:
            st, nu, nd = armar_start(prev, K, idx, len(lb), T, DT, dur, dem_res, minton, payload)
            print(f'{op}: warm start con {nu} aviones ({nd} descartados)', flush=True)
        r = resolver_mip(lb, ub, obj, integ, rows, TLIM_OP, log=os.environ.get('LOG') == '1', start=st)
        print(f"{op}: {len(K)} aviones, {len(cand)} tramos candidatos, {r['nint']} enteras, {r['nrows']} restr. "
              f"-> {r['estado']} obj={r['obj']} cota={(r['cota'] or 0):.0f} gap={r['gap']} t={r['tiempo']:.0f}s", flush=True)
        resumen.append({'operador': op, 'aviones': len(K), 'tramos_candidatos': len(cand), 'enteras': r['nint'],
                        'estado': r['estado'], 'objetivo': r['obj'], 'cota': r['cota'], 'gap': r['gap'],
                        'tiempo_s': r['tiempo']})
        if r['sol'] is None:
            print('Sin solución factible para', op); return
        x = r['sol']
        vuelos[op] = defaultdict(float); carga[op] = defaultdict(float)
        for k in K:
            fs = sorted([(key[3], key[2]) for key, j in idx.items() if key[0] == 'z' and key[1] == k and x[j] > 0.5])
            legs = []
            for p, (t, e) in enumerate(fs, start=1):
                legs.append({'pos': p, 'tramo': e, 't_dep': t * DT, 't_arr': t * DT + tau[e]})
                xv = x[idx[('x', k, e, t)]] if ('x', k, e, t) in idx else 0.0
                xv = xv if xv > 1e-6 else 0.0
                cargas[(k, p)] = xv
                vuelos[op][(e, t)] += 1; carga[op][(e, t)] += xv
            itinerario[k] = legs

    # Asignación global: ve simultáneamente todos los vuelos individuales y permite
    # carga directa, through y transbordos entre aeronaves u operadores.
    multi = asignar_carga_multitramos(
        itinerario, ruta_raw=RAW, ttr_h=TTR_H, tlim=TLIM_CARGA,
        log=os.environ.get('LOG_CARGA') == '1')
    print(f"Carga multitramos: {multi['estado']} | tamaño={multi['tamano']}", flush=True)
    if not multi['ok']:
        print('No se pudo obtener una asignación global de carga'); return
    s = multi['solucion']
    cargas = defaultdict(float)
    for registro in s['cargas']:
        cargas[(registro['k'], registro['s'])] += registro['x']
    perd = 0.0  # la incompatibilidad lleno-lleno se impone dentro del submodelo de carga
    cfg = {'ciclico': True, 'tat_variable': True, 'min_ton_escala': True, 'mantenimiento': True,
           'frecuencias': True, 'demanda_diaria': True, 'dist_frecuencias': False,
           'transferencias': True, 'ttr_h': TTR_H}
    v = validar(s, cfg, ruta_raw=RAW)
    dem_tot = pd.read_csv(os.path.join(RAW, 'demand_weekly.csv')).tons_week.sum()
    horas = sum(l['t_arr'] - l['t_dep'] for legs in itinerario.values() for l in legs)
    cap = sum(payload[k] for k, legs in itinerario.items() for l in legs)
    freq_real = defaultdict(int)
    for legs in itinerario.values():
        for l in legs: freq_real[l['tramo']] += 1
    kp = {'margen_usd': v['margen'], 'ingresos_usd': v['ingresos'], 'costo_vuelos_usd': v['costo_vuelos'],
          'costo_handling_usd': v['handling'], 'toneladas': v['toneladas_entregadas'],
          'pct_demanda': 100 * v['toneladas_entregadas'] / dem_tot, 'vuelos': v['vuelos'],
          'aviones_usados': sum(1 for l in itinerario.values() if l), 'horas_bloque': horas,
          'factor_ocupacion_pct': 100 * v['toneladas_entregadas'] / cap if cap else 0,
          'frecuencias_cumplidas': f"{sum(freq_real[od] >= f for od, f in fmin.items())}/{len(fmin)}",
          'toneladas_recortadas_regla_90min': perd,
          'flujo_through_t': multi['estadisticas']['flujo_through_t'],
          'flujo_transferido_t': multi['estadisticas']['flujo_transferido_t'],
          'conexiones_transferencia_usadas': multi['estadisticas']['conexiones_transferencia_usadas'],
          'transferencias_entre_operadores': multi['estadisticas']['transferencias_entre_operadores'],
          'ttr_h': TTR_H}
    print('Validador OK:', v['ok'], '| violaciones:', len(v['violaciones']))
    for w in v['violaciones'][:25]: print('  -', w)
    for k2, x in kp.items(): print(f'  {k2}: {x}')
    base = pkl.replace('.pkl', '') + ('_poravion_amp' if AMPLIAR else '_poravion') + SUFIJO
    json.dump({'kpis': kp, 'validador_ok': v['ok'], 'violaciones': v['violaciones'], 'por_operador': resumen,
               'modelo_carga': {k: x for k, x in multi.items() if k != 'solucion'}},
              open(base + '_kpis.json', 'w'), indent=2, ensure_ascii=False, default=str)
    filas = [{'avion': k, 'operador': fl.set_index('aircraft_id').operator[k], 'pos': l['pos'],
              'origen': l['tramo'][0], 'destino': l['tramo'][1], 't_dep_h': l['t_dep'], 't_arr_h': l['t_arr'],
              'dia': int(l['t_dep'] // 24) + 1, 'carga_t': round(cargas.get((k, l['pos']), 0.0), 3)}
             for k, legs in itinerario.items() for l in legs]
    pd.DataFrame(filas).to_csv(base + '_itinerario.csv', index=False)
    filas_carga = []
    for registro in s['cargas']:
        q = tuple(registro['q'])
        filas_carga.append({
            'avion': registro['k'], 'pos': registro['s'], 'origen_q': q[0], 'destino_q': q[1], 'dia_q': q[2],
            'a_bordo_t': registro['x'], 'embarque_inicial_t': registro['b'], 'entrega_t': registro['a'],
            'through_t': registro['w'], 'transfer_in_t': registro.get('transfer_in', 0.0),
            'transfer_out_t': registro.get('transfer_out', 0.0)})
    pd.DataFrame(filas_carga).to_csv(base + '_carga_multitramos.csv', index=False)
    pd.DataFrame(s['transferencias']).to_csv(base + '_transferencias.csv', index=False)
    pickle.dump({'itinerario': itinerario, 'cargas': dict(cargas), 'solucion_carga': s,
                 'transferencias': s['transferencias'], 'ttr_h': TTR_H}, open(base + '.pkl', 'wb'))
    print('Exportado', base)


if __name__ == '__main__':
    main(sys.argv[1])
