"""
Paso 2: asignación de aviones individuales (tail assignment) sobre la solución de la red espacio-tiempo.

La red agregada dice cuántos aviones de cada operador vuelan cada tramo en cada bloque. Aquí se reparte
cada uno de esos vuelos a un avión concreto, exigiendo que:
  - cada avión describa UNA rotación cerrada de exactamente una semana (cruza el corte domingo-lunes una vez),
  - cada avión usado pase su ventana de mantenimiento completa en tierra en su aeropuerto,
  - cada vuelo de la solución agregada quede asignado a exactamente un avión.
Luego se asigna la carga a cada copia del vuelo, se corrige la regla de 90 min (lleno-lleno) y se valida
con el validador independiente de José (lee los CSV originales, no usa este código).
"""
import math, os, sys, pickle, json
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd
import yaml
import highspy
from scipy.sparse import csc_matrix

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # validador.py de José está en esta misma carpeta
from validador import validar

RAW = os.environ.get('RAW', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'raw data'))


def _mip(nc, rows, integ, obj=None, tlim=300):
    data, ri, ci = [], [], []
    lo, hi = np.empty(len(rows)), np.empty(len(rows))
    for i, (coef, l, u) in enumerate(rows):
        lo[i], hi[i] = l, u
        for j, v in coef.items():
            ri.append(i); ci.append(j); data.append(v)
    M = csc_matrix((data, (ri, ci)), shape=(len(rows), nc))
    h = highspy.Highs(); h.setOptionValue('output_flag', False); h.setOptionValue('time_limit', tlim)
    lp = highspy.HighsLp(); lp.num_col_, lp.num_row_ = nc, len(rows)
    lp.col_cost_ = np.array(obj if obj is not None else [0.0] * nc, float)
    lp.col_lower_ = np.zeros(nc); lp.col_upper_ = np.ones(nc)
    lp.row_lower_, lp.row_upper_ = lo, hi
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_, lp.a_matrix_.index_, lp.a_matrix_.value_ = M.indptr, M.indices, M.data
    lp.integrality_ = [highspy.HighsVarType.kInteger if z else highspy.HighsVarType.kContinuous for z in integ]
    h.passModel(lp); h.run()
    st = h.modelStatusToString(h.getModelStatus())
    return st, (h.getSolution().col_value if 'Optimal' in st or h.getInfo().primal_solution_status == 2 else None)


def asignar(pkl):
    P = pickle.load(open(pkl, 'rb'))
    info, sol, DT = P['info'], P['sol'], P['DT']
    T = info['T']; dur = info['dur']; tau = info['tau']
    fl = pd.read_csv(os.path.join(RAW, 'fleet.csv'))
    mt = pd.read_csv(os.path.join(RAW, 'maintenance.csv'))
    reglas = yaml.safe_load(open(os.path.join(RAW, 'ops_rules.yaml'), encoding='utf-8'))
    ini = datetime.fromisoformat(str(reglas['week_start']))
    mant = {}
    for r in mt.itertuples():
        h0 = (datetime.fromisoformat(r.start_datetime) - ini).total_seconds() / 3600
        h1 = (datetime.fromisoformat(r.end_datetime) - ini).total_seconds() / 3600
        mant[r.aircraft_id] = (r.airport, h0, h1,
                               [t % T for t in range(int(math.floor(h0 / DT)), int(math.ceil(h1 / DT)))])
    payload = dict(zip(fl.aircraft_id, fl.payload_tons.astype(float)))

    itinerario, cargas_copia = {}, {}
    for op in info['ops']:
        K = list(fl[fl.operator == op].aircraft_id)
        copias = []   # (e, t, carga)
        for (o2, e, t), j in info['n'].items():
            if o2 != op: continue
            m = int(round(sol[j]))
            if m == 0: continue
            nl = int(round(sol[info['nl'][(o2, e, t)]])) if (o2, e, t) in info['nl'] else 0
            X = sol[info['x'][(o2, e, t)]] if (o2, e, t) in info['x'] else 0.0
            for i in range(m):
                copias.append((e, t, (X / nl) if (i < nl and X > 1e-6) else 0.0))
        if not copias:
            continue
        A = sorted({c[0][0] for c in copias} | {c[0][1] for c in copias} | {mant[k][0] for k in K})
        # variables: z[k,f] binarias, g[k,a,t] continuas en [0,1], u[k] binaria
        idx, integ = {}, []
        def v(key, ent):
            idx[key] = len(integ); integ.append(ent); return idx[key]
        for k in K:
            v(('u', k), 1)
            for f in range(len(copias)): v(('z', k, f), 1)
            for a in A:
                for t in range(T): v(('g', k, a, t), 0)
        rows = []
        for f in range(len(copias)):
            rows.append(({idx[('z', k, f)]: 1 for k in K}, 1, 1))
        sale = defaultdict(list); llega = defaultdict(list)
        for f, (e, t, _) in enumerate(copias):
            sale[(e[0], t)].append(f); llega[(e[1], (t + dur[e]) % T)].append(f)
        for k in K:
            for a in A:
                for t in range(T):
                    c = defaultdict(float)
                    for f in llega[(a, t)]: c[idx[('z', k, f)]] += 1
                    c[idx[('g', k, a, (t - 1) % T)]] += 1
                    for f in sale[(a, t)]: c[idx[('z', k, f)]] -= 1
                    c[idx[('g', k, a, t)]] -= 1
                    rows.append((c, 0, 0))
            c = {idx[('g', k, a, T - 1)]: 1 for a in A}
            for f, (e, t, _) in enumerate(copias):
                if t + dur[e] >= T:
                    c[idx[('z', k, f)]] = 1                          # vuelo que aterriza justo en el corte
            c[idx[('u', k)]] = -1
            rows.append((c, 0, 0))                                   # una sola vuelta semanal
            am, _, _, W = mant[k]
            for t in (W if os.environ.get('SIN_MANT') != '1' else []):
                rows.append(({idx[('g', k, am, t)]: 1, idx[('u', k)]: -1}, 0, np.inf))   # en tierra en su ventana
            for f in range(len(copias)):
                rows.append(({idx[('z', k, f)]: 1, idx[('u', k)]: -1}, -np.inf, 0))
        st, x = _mip(len(integ), rows, integ)
        print(f'{op}: {len(K)} aviones, {len(copias)} vuelos -> {st}', flush=True)
        if x is None:
            if os.environ.get('SEGUIR') == '1':
                continue
            return None, op
        for k in K:
            fs = sorted([f for f in range(len(copias)) if x[idx[('z', k, f)]] > 0.5], key=lambda f: copias[f][1])
            legs = []
            for p, f in enumerate(fs, start=1):
                e, t, carga = copias[f]
                legs.append({'pos': p, 'tramo': e, 't_dep': t * DT, 't_arr': t * DT + tau[e]})
                cargas_copia[(k, p)] = carga
            itinerario[k] = legs
    return (itinerario, cargas_copia, payload), None


def corregir_90min(itinerario, cargas, payload, reglas):
    tec = set(reglas['technical_stop_airports'])
    lleno = lambda k, p: cargas.get((k, p), 0) >= 0.95 * payload[k] - 0.005
    perdidas = 0.0
    for k, legs in itinerario.items():
        for i in range(len(legs) - 1):
            a_, b_ = legs[i], legs[i + 1]
            if a_['tramo'][1] in tec: continue
            if lleno(k, a_['pos']) and lleno(k, b_['pos']) and b_['t_dep'] - a_['t_arr'] < 1.5 - 1e-6:
                nuevo = 0.95 * payload[k] - 0.02
                perdidas += cargas[(k, b_['pos'])] - nuevo
                cargas[(k, b_['pos'])] = nuevo
    return perdidas


def a_formato_validador(itinerario, cargas):
    lista = []
    for k, legs in itinerario.items():
        for l in legs:
            c = cargas.get((k, l['pos']), 0.0)
            if c > 1e-9:
                o, d = l['tramo']
                q = (o, d, int(l['t_dep'] // 24) + 1)
                lista.append({'k': k, 's': l['pos'], 'q': q, 'x': c, 'b': c, 'a': c, 'w': 0.0})
    return {'itinerario': itinerario, 'cargas': lista}


if __name__ == '__main__':
    pkl = sys.argv[1]
    res, falla = asignar(pkl)
    if res is None:
        print('Asignación infactible en operador', falla); sys.exit(1)
    itinerario, cargas, payload = res
    reglas = yaml.safe_load(open(os.path.join(RAW, 'ops_rules.yaml'), encoding='utf-8'))
    perd = corregir_90min(itinerario, cargas, payload, reglas)
    print(f'Toneladas recortadas por la regla de 90 min: {perd:.2f}')
    s = a_formato_validador(itinerario, cargas)
    cfg = {'ciclico': True, 'tat_variable': True, 'min_ton_escala': True, 'mantenimiento': True,
           'frecuencias': True, 'demanda_diaria': True, 'dist_frecuencias': False}
    v = validar(s, cfg, ruta_raw=RAW)
    print('Validador OK:', v['ok'], '| violaciones:', len(v['violaciones']))
    for x in v['violaciones'][:20]: print('  -', x)
    dem_tot = pd.read_csv(os.path.join(RAW, 'demand_weekly.csv')).tons_week.sum()
    horas = sum(l['t_arr'] - l['t_dep'] for legs in itinerario.values() for l in legs)
    cap = sum(payload[k] for k, legs in itinerario.items() for l in legs)
    kp = {'margen_usd': v['margen'], 'ingresos_usd': v['ingresos'], 'costo_vuelos_usd': v['costo_vuelos'],
          'costo_handling_usd': v['handling'], 'toneladas': v['toneladas_entregadas'],
          'pct_demanda': 100 * v['toneladas_entregadas'] / dem_tot, 'vuelos': v['vuelos'],
          'aviones_usados': sum(1 for l in itinerario.values() if l), 'horas_bloque': horas,
          'factor_ocupacion_pct': 100 * v['toneladas_entregadas'] / cap}
    for k2, x in kp.items(): print(f'  {k2}: {x}')
    base = pkl.replace('.pkl', '')
    json.dump({'kpis': kp, 'validador_ok': v['ok'], 'violaciones': v['violaciones']},
              open(base + '_kpis.json', 'w'), indent=2, ensure_ascii=False)
    filas = [{'avion': k, 'pos': l['pos'], 'origen': l['tramo'][0], 'destino': l['tramo'][1],
              't_dep_h': l['t_dep'], 't_arr_h': l['t_arr'], 'dia': int(l['t_dep'] // 24) + 1,
              'carga_t': round(cargas.get((k, l['pos']), 0.0), 3)}
             for k, legs in itinerario.items() for l in legs]
    pd.DataFrame(filas).to_csv(base + '_itinerario.csv', index=False)
    print('Exportado', base + '_kpis.json', base + '_itinerario.csv')
