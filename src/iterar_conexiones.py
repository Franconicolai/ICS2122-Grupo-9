import os, sys, pickle, json, subprocess
from collections import defaultdict
import pandas as pd

RAW = os.environ.get('RAW', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'raw data'))
NIT = int(os.environ.get('NIT', 2))
FACTOR = float(os.environ.get('FACTOR', 0.5))    # qué fracción del valor de conexión se premia

REF = float(os.environ.get('REF_MARGEN', 6260460))   # margen validado de Vicente

def calcular_bono(itinerario, s, tarifa, DT, op_de, factor):
    """Reparte el ingreso de cada commodity entre los vuelos que lo transportan (según tonelada-hora)
    y premia solo lo que NO es carga directa del tramo (eso ya lo valora el paso 2)."""
    leg = {(k, l['pos']): l for k, legs in itinerario.items() for l in legs}
    por_q = defaultdict(list)
    for r in s['cargas']:
        por_q[tuple(r['q'])].append(r)
    bono = defaultdict(float)
    for q, regs in por_q.items():
        entregado = sum(r['a'] for r in regs)
        if entregado <= 1e-6: continue
        ingreso = 1000 * tarifa[(q[0], q[1])] * entregado
        pesos = [r['x'] * (leg[(r['k'], r['s'])]['t_arr'] - leg[(r['k'], r['s'])]['t_dep']) for r in regs]
        W = sum(pesos)
        if W <= 0: continue
        for r, w in zip(regs, pesos):
            l = leg[(r['k'], r['s'])]
            if tuple(l['tramo']) == (q[0], q[1]): continue
            bono[(op_de[r['k']], tuple(l['tramo']), int(round(l['t_dep'] / DT)))] += factor * ingreso * w / W
    return dict(bono)

def correr(pkl, sufijo, bono_pkl=None, start=None):
    env = dict(os.environ, SUFIJO=sufijo)
    if bono_pkl: env['BONO_PKL'] = bono_pkl
    if start: env['START_PKL'] = start
    subprocess.run([sys.executable, 'src/reoptimizacion_por_avion.py', pkl], env=env, check=True)
    base = pkl[:-4] + '_poravion' + sufijo
    kp = json.load(open(base + '_kpis.json'))
    return base, (kp['kpis']['margen_usd'] if kp['validador_ok'] else float('-inf'))

if __name__ == '__main__':
    pkl = sys.argv[1]
    P = pickle.load(open(pkl, 'rb'))
    DT, tarifa = P['DT'], P['info']['tarifa']
    fl = pd.read_csv(os.path.join(RAW, 'fleet.csv'))
    op_de = dict(zip(fl.aircraft_id, fl.operator))

    mejor_base, mejor = correr(pkl, '_it0', start=os.environ.get('START_PKL'))
    print(f'it0: {mejor:,.0f} (referencia Vicente {REF:,.0f})', flush=True)
    for it in range(1, NIT + 1):
        R = pickle.load(open(mejor_base + '.pkl', 'rb'))
        bono = calcular_bono(R['itinerario'], R['solucion_carga'], tarifa, DT, op_de, FACTOR)
        bono_pkl = pkl[:-4] + f'_bono{it}.pkl'
        pickle.dump(bono, open(bono_pkl, 'wb'))
        base, m = correr(pkl, f'_it{it}', bono_pkl, start=mejor_base + '.pkl')
        print(f'it{it}: {m:,.0f} (mejor hasta ahora {mejor:,.0f})', flush=True)
        if m > mejor: mejor_base, mejor = base, m
    print('MEJOR:', mejor_base, f'{mejor:,.0f}', '| supera a Vicente' if mejor > REF else '| NO supera a Vicente')