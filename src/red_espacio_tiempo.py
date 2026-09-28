"""
Prototipo v0: red espacio-tiempo (time-space network) para el itinerario semanal de la flota.

Idea: la semana (168 h) se divide en bloques de DT horas. Los nodos son (aeropuerto, bloque).
Para cada operador (flota agregada) hay:
  - n[op, tramo, t]  (entera >= 0): aviones del operador que salen en el bloque t por el tramo.
  - g[op, aerop, t]  (continua >= 0, entera en la práctica): aviones esperando en tierra de t a t+1.
  - nl[op, tramo, t] (entera): de esos vuelos, cuántos van cargados (>= 10 t); el resto son ferry.
  - x[op, tramo, t]  (continua): toneladas de carga directa del par (origen, destino) = tramo.
El largo de cada flecha de vuelo es ceil((duración + TAT) / DT) bloques (redondeo conservador).
La red es cíclica: el bloque 167 se conecta con el 0.

Simplificaciones de esta v0 (documentadas para el informe):
  S1. Flota agregada por operador; capacidad = payload mínimo del operador (conservador).
  S2. Solo carga en vuelo directo (97,7 % de las toneladas tiene tramo directo).
  S3. TAT de 70 min (50 min si el destino es escala técnica); la regla de 90 min lleno-lleno se revisa ex post.
  S4. Mantenimiento como conteo: en cada bloque de la ventana, al menos tantos aviones del operador
      en tierra en el aeropuerto como aviones en mantenimiento (ventana redondeada hacia afuera).
  S5. La separación en rotaciones de aviones individuales se hace en un segundo paso (no incluido aquí).
"""
import math, time, sys, os
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd
import yaml
import highspy
from scipy.sparse import csc_matrix

RAW = os.environ.get('RAW', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'raw data'))
DT = float(os.environ.get('DT', 1.0))          # horas por bloque
TLIM = float(os.environ.get('TLIM', 600))       # segundos
GAP = float(os.environ.get('GAP', 0.01))
NOWRAP = os.environ.get('NOWRAP', '1') == '1'
H = 168.0


def cargar():
    rd = lambda f: pd.read_csv(os.path.join(RAW, f))
    ap, fl, lg = rd('airports.csv'), rd('fleet.csv'), rd('legs_catalog.csv')
    dw, dd, tr = rd('demand_weekly.csv'), rd('demand_daily.csv'), rd('traffic_rights.csv')
    cp, lf, mt = rd('cost_params.csv'), rd('landing_fees.csv'), rd('maintenance.csv')
    reglas = yaml.safe_load(open(os.path.join(RAW, 'ops_rules.yaml'), encoding='utf-8'))
    return ap, fl, lg, dw, dd, tr, cp, lf, mt, reglas


def construir(DT=DT):
    ap, fl, lg, dw, dd, tr, cp, lf, mt, reglas = cargar()
    T = int(round(H / DT))
    pais = dict(zip(ap.iata, ap.country))
    ops = sorted(fl.operator.unique())
    N_op = fl.operator.value_counts().to_dict()
    Q_op = fl.groupby('operator').payload_tons.min().to_dict()
    ok = {(o, c) for o, c, a in zip(tr.operator, tr.country, tr.allowed) if a == 1}
    tec = set(reglas['technical_stop_airports'])
    tat = {k: v / 60.0 for k, v in reglas['tat_minutes'].items()}
    maxb = float(reglas['max_continuous_block_hours'])
    minton = float(reglas['min_tons_per_extra_stop'])
    c = {(p, o): v for p, o, v in zip(cp.param, cp.operator, cp.value)}
    fuel_h = c[('fuel_burn_gal_per_hour', 'ALL')] * c[('fuel_price_usd_per_gal', 'ALL')]
    fee = dict(zip(lf.airport, lf.fee_usd))
    tarifa = {(o, d): r for o, d, r in zip(dw.origin, dw.dest, dw.tariff_usd_per_kg)}
    fmin = {(o, d): int(f) for o, d, f in zip(dw.origin, dw.dest, dw.min_weekly_freq) if f > 0}
    dias = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    dem = {(r.origin, r.dest, i): float(getattr(r, dcol)) for r in dd.itertuples() for i, dcol in enumerate(dias)}
    tau = {(o, d): b for o, d, b in zip(lg.origin, lg.dest, lg.block_hours)}

    legs_op = {op: [e for e in tau if tau[e] <= maxb and (op, pais[e[0]]) in ok and (op, pais[e[1]]) in ok]
               for op in ops}
    dur = {e: max(1, math.ceil((tau[e] + (tat['technical_stop'] if e[1] in tec else tat['default'])) / DT - 1e-9))
           for e in tau}

    # mantenimiento (conteo por operador, aeropuerto, bloque); ventana redondeada hacia afuera
    ini = datetime.fromisoformat(str(reglas['week_start']))
    opk = dict(zip(fl.aircraft_id, fl.operator))
    mcount = defaultdict(int)
    for r in mt.itertuples():
        h0 = (datetime.fromisoformat(r.start_datetime) - ini).total_seconds() / 3600
        h1 = (datetime.fromisoformat(r.end_datetime) - ini).total_seconds() / 3600
        for t in range(int(math.floor(h0 / DT)), int(math.ceil(h1 / DT))):
            mcount[(opk[r.aircraft_id], r.airport, t % T)] += 1

    # ---------------- variables ----------------
    cols_lb, cols_ub, cols_obj, cols_int, names = [], [], [], [], []
    def var(nombre, lb, ub, obj, entera):
        names.append(nombre); cols_lb.append(lb); cols_ub.append(ub); cols_obj.append(obj); cols_int.append(entera)
        return len(names) - 1

    A = sorted(pais)
    n, g, nl, x = {}, {}, {}, {}
    for op in ops:
        for e in legs_op[op]:
            costo = (fuel_h + c[('ex_fuel_usd_per_block_hour', op)]) * tau[e] + fee[e[1]]
            carga = (e in tarifa) and e[0] not in tec and e[1] not in tec
            for t in range(T):
                if NOWRAP and t + dur[e] > T:
                    continue   # supuesto 8 de José: ningún vuelo cruza el límite domingo-lunes
                n[op, e, t] = var(('n', op, e, t), 0, N_op[op], -costo, 1)
                if carga and dem.get((e[0], e[1], int(t * DT // 24)), 0) > 0:
                    nl[op, e, t] = var(('nl', op, e, t), 0, N_op[op], 0.0, 1)
                    x[op, e, t] = var(('x', op, e, t), 0, np.inf,
                                      1000 * tarifa[e] - c[('handling_usd_per_ton', op)], 0)
        for a in A:
            for t in range(T):
                g[op, a, t] = var(('g', op, a, t), 0, N_op[op], 0.0, 0)

    rows = []  # (dict col->coef, lo, hi, name)
    # conservación de flujo en cada nodo (op, a, t)
    llega = defaultdict(list); sale = defaultdict(list)
    for (op, e, t), j in n.items():
        sale[(op, e[0], t)].append(j)
        llega[(op, e[1], (t + dur[e]) % T)].append(j)
    for op in ops:
        for a in A:
            for t in range(T):
                coef = defaultdict(float)
                for j in llega[(op, a, t)]: coef[j] += 1
                coef[g[op, a, (t - 1) % T]] += 1
                for j in sale[(op, a, t)]: coef[j] -= 1
                coef[g[op, a, t]] -= 1
                rows.append((coef, 0, 0, ('flujo', op, a, t)))
    # tamaño de flota: todo lo que cruza el corte t=0 (espera 167->0 y vuelos que dan la vuelta)
    for op in ops:
        coef = defaultdict(float)
        for a in A: coef[g[op, a, T - 1]] += 1
        for (o2, e, t), j in n.items():
            if o2 == op and t + dur[e] > T - 1 + 1e-9 and (t + dur[e]) >= T:
                coef[j] += 1
        rows.append((coef, 0, N_op[op], ('flota', op)))
    # carga: 10*nl <= x <= Q*nl ; nl <= n
    for key, jx in x.items():
        op = key[0]
        rows.append(({jx: 1, nl[key]: -minton}, 0, np.inf, ('min10',) + key))
        rows.append(({jx: 1, nl[key]: -Q_op[op]}, -np.inf, 0, ('cap',) + key))
        rows.append(({nl[key]: 1, n[key]: -1}, -np.inf, 0, ('nl_n',) + key))
    # demanda diaria (día de salida del vuelo)
    por_q = defaultdict(list)
    for (op, e, t), jx in x.items():
        por_q[(e[0], e[1], int(t * DT // 24))].append(jx)
    for q, js in por_q.items():
        rows.append(({j: 1 for j in js}, -np.inf, dem[q], ('dem',) + q))
    # frecuencias mínimas
    por_od = defaultdict(list)
    for (op, e, t), j in n.items(): por_od[e].append(j)
    for od, f in fmin.items():
        rows.append(({j: 1 for j in por_od.get(od, [])}, f, np.inf, ('freq',) + od))
    # mantenimiento (conteo)
    for (op, a, t), m in mcount.items():
        rows.append(({g[op, a, t]: 1}, m, np.inf, ('mant', op, a, t)))

    info = dict(T=T, ops=ops, legs_op=legs_op, dur=dur, tau=tau, names=names, n=n, g=g, nl=nl, x=x,
                tarifa=tarifa, dem=dem, fmin=fmin, N_op=N_op, Q_op=Q_op, fee=fee, fuel_h=fuel_h, c=c)
    return (cols_lb, cols_ub, cols_obj, cols_int, rows), info


def resolver(modelo, info, tlim=TLIM, gap=GAP, relax=False):
    from solver_util import resolver_matriz, filas_a_matriz, SOLVER
    cols_lb, cols_ub, cols_obj, cols_int, rows = modelo
    A, lo, hi = filas_a_matriz(rows, len(cols_lb))
    print(f'Modelo: {len(cols_lb)} variables ({sum(cols_int)} enteras), {len(rows)} restricciones, '
          f'{A.nnz} no ceros. Solver: {SOLVER}', flush=True)
    return resolver_matriz(cols_lb, cols_ub, cols_obj, cols_int, A, lo, hi, tlim, gap, log=True, relax=relax)


def kpis(info, sol):
    n, x, nl = info['n'], info['x'], info['nl']
    vuelos = sum(round(sol[j]) for j in n.values())
    ton = sum(sol[j] for j in x.values())
    ing = sum(sol[j] * 1000 * info['tarifa'][k[1]] for k, j in x.items())
    cv = sum(round(sol[j]) * ((info['fuel_h'] + info['c'][('ex_fuel_usd_per_block_hour', k[0])]) * info['tau'][k[1]]
                              + info['fee'][k[1][1]]) for k, j in n.items())
    ch = sum(sol[j] * info['c'][('handling_usd_per_ton', k[0])] for k, j in x.items())
    horas = sum(round(sol[j]) * info['tau'][k[1]] for k, j in n.items())
    cap = sum(round(sol[j]) * info['Q_op'][k[0]] for k, j in n.items())
    dem_tot = sum(info['dem'].values())
    freq_ok = 0
    por_od = defaultdict(int)
    for k, j in n.items(): por_od[k[1]] += round(sol[j])
    for od, f in info['fmin'].items(): freq_ok += por_od[od] >= f
    return {'margen_usd': ing - cv - ch, 'ingresos_usd': ing, 'costo_vuelos_usd': cv, 'costo_handling_usd': ch,
            'toneladas': ton, 'pct_demanda': 100 * ton / dem_tot, 'vuelos': vuelos, 'horas_bloque': horas,
            'factor_ocupacion_pct': 100 * ton / cap if cap else 0,
            'frecuencias_cumplidas': f'{freq_ok}/{len(info["fmin"])}'}


if __name__ == '__main__':
    t0 = time.time()
    modelo, info = construir()
    print(f'Construcción: {time.time()-t0:.1f}s, DT={DT} h, T={info["T"]} bloques')
    relax = '--lp' in sys.argv
    r = resolver(modelo, info, relax=relax)
    print({k: v for k, v in r.items() if k != 'sol'})
    if r['sol'] is not None:
        for k, v in kpis(info, r['sol']).items(): print(f'  {k}: {v}')
        import pickle
        salida = os.environ.get('OUT', f'sol_dt{DT:g}.pkl')
        pickle.dump({'info': info, 'sol': list(r['sol']),
                     'res': {k: v for k, v in r.items() if k != 'sol'}, 'DT': DT}, open(salida, 'wb'))
        print('guardado', salida)
