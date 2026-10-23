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
# 1: enlace fuerte x <= min(payload, demanda residual)·zl. Cada x es la carga de una sola demanda (origen, destino,
# día), así que no puede superar esa demanda; con payload en vez de demanda, la relajación lineal paga una fracción de
# avión proporcional a la carga aunque la demanda sea mucho menor que un avión (la mediana es 20,6 t; el payload, 52 t)
ENLACE_FUERTE = os.environ.get('ENLACE_FUERTE', '1') == '1'


def resolver_mip(lb, ub, obj, integ, rows, tlim, gap=0.01, log=False, start=None):
    """Función resolver_mip: [Descripción pendiente]."""
    from solver_util import resolver_matriz, filas_a_matriz
    A, lo, hi = filas_a_matriz(rows, len(lb))
    r = resolver_matriz(lb, ub, obj, integ, A, lo, hi, tlim, gap, log=log, start=start)
    r.update(nvars=len(lb), nint=sum(integ), nrows=len(rows))
    return r


def main(pkl):
    """Función main: [Descripción pendiente]."""
    P = pickle.load(open(pkl, 'rb'))
    info, sol, DT = P['info'], P['sol'], P['DT']
    T, dur, tau = info['T'], info['dur'], info['tau']
    c, fee, fuel_h, tarifa = info['c'], info['fee'], info['fuel_h'], info['tarifa']
    dem, fmin = info['dem'], info['fmin']
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

    # punto de partida (START): vuelos de cada avión en una corrida anterior; ver red_espacio_tiempo.py
    from red_espacio_tiempo import leer_itinerario, bloques_itinerario, esperas_avion
    it_start = leer_itinerario(os.environ['START']) if os.environ.get('START') else None
    vb_start = bloques_itinerario(it_start, DT) if it_start else None
    if it_start and vb_start is None:
        print(f'[!] Punto de partida: sus horarios no caen en la grilla de {DT:g} h; el paso 2 parte de cero')

    # estado actual por operador: vuelos {(e,t): n} y carga {(e,t): x}
    vuelos = {op: defaultdict(float) for op in info['ops']}
    carga = {op: defaultdict(float) for op in info['ops']}
    for (op, e, t), j in info['n'].items():
        if round(sol[j]) > 0: vuelos[op][(e, t)] += round(sol[j])
    for (op, e, t), j in info['x'].items():
        if sol[j] > 1e-6: carga[op][(e, t)] += sol[j]
    # con punto de partida, el paso 2 mejora la corrida de partida operador por operador: el estado inicial (vuelos y
    # carga directa de los demás operadores) es el de esa corrida, así su solución siempre es factible y cada operador
    # solo puede mejorar. Los tramos que eligió el paso 1 se suman a los candidatos.
    usados_paso1 = {op: {e for (e, t) in vuelos[op]} for op in info['ops']}
    indirecta = defaultdict(float)   # carga de la corrida de partida que llega en varios tramos o con transbordo
    if vb_start:
        op_k = dict(zip(fl.aircraft_id, fl.operator))
        it_df = pd.read_csv(os.path.join(os.environ['START'], 'itinerario_vuelos.csv'))
        cg_df = pd.read_csv(os.path.join(os.environ['START'], 'carga_transportada.csv'))
        cg_df = cg_df.merge(it_df[['avion', 'pos', 'origen', 'destino', 't_dep_h']], on=['avion', 'pos'])
        directa = cg_df[(cg_df.origen_q == cg_df.origen) & (cg_df.destino_q == cg_df.destino)]
        directa = directa.assign(t=lambda d: (d.t_dep_h / DT).round().astype(int),
                                 x=lambda d: d[['embarque_inicial_t', 'entrega_t']].min(axis=1))
        vuelos = {op: defaultdict(float) for op in info['ops']}
        carga = {op: defaultdict(float) for op in info['ops']}
        for k, b in vb_start.items():
            for e, t in b:
                vuelos[op_k[k]][(e, t)] += 1
        for r in directa.itertuples():
            carga[op_k[r.avion]][((r.origen, r.destino), r.t)] += r.x
        # el paso 2 solo ve carga directa: la que la corrida entrega por otras rutas se descuenta de la demanda de
        # todos los operadores, para no volver a venderla (dia_q va de 1 a 7; la demanda del paso 1, de 0 a 6)
        entregada = cg_df.groupby(['origen_q', 'destino_q', 'dia_q']).entrega_t.sum()
        directa_q = directa.groupby(['origen_q', 'destino_q', 'dia_q']).x.sum()
        for (o, d, dia), ton in (entregada - directa_q.reindex(entregada.index).fillna(0)).items():
            if ton > 1e-6:
                indirecta[(o, d, int(dia) - 1)] += ton

    itinerario, cargas = {}, {}
    if vb_start:   # los operadores que no se reoptimizan (p. ej. con SOLO) mantienen los vuelos de la corrida de partida
        itinerario = {k: [{'pos': p, 'tramo': e, 't_dep': t * DT, 't_arr': t * DT + tau[e]}
                          for p, (e, t) in enumerate(b, start=1)] for k, b in vb_start.items()}
    orden = sorted(info['ops'], key=lambda o: info['N_op'][o])
    if os.environ.get('SOLO'): orden = os.environ['SOLO'].split(',')     # operadores chicos primero
    resumen = []
    for op in orden:
        K = list(fl[fl.operator == op].aircraft_id)
        otros = [o for o in info['ops'] if o != op]
        # residuales de demanda y frecuencia
        dem_res = defaultdict(float)
        for q, d in dem.items(): dem_res[q] = d - indirecta.get(q, 0.0)
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
            usados = {e for (e, t) in vuelos[op]} | usados_paso1[op]
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
        if vb_start:   # los tramos de la corrida de partida siempre son candidatos, para que su solución exista
            cand |= {e for k in K for e, t in vb_start.get(k, []) if e in set(todos)}
        A = sorted({e[0] for e in cand} | {e[1] for e in cand} | {mant[k][0] for k in K})

        lb, ub, obj, integ, idx = [], [], [], [], {}
        def var(key, l, u, o, i):
            """Función var: [Descripción pendiente]."""
            idx[key] = len(lb); lb.append(l); ub.append(u); obj.append(o); integ.append(i)
        for k in K:
            var(('u', k), 0, 1, 0.0, 1)
            for e in cand:
                costo = (fuel_h + c[('ex_fuel_usd_per_block_hour', op)]) * tau[e] + fee[e[1]]
                carga_ok = e in tarifa and e[0] not in tec and e[1] not in tec
                for t in range(T):
                    if t + dur[e] > T: continue
                    var(('z', k, e, t), 0, 1, -costo, 1)
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
                tope = min(payload[k], max(dem_res[(e[0], e[1], int(t * DT // 24))], 0.0)) if ENLACE_FUERTE else payload[k]
                rows.append(({j: 1, idx[('zl', k, e, t)]: -tope}, -np.inf, 0))
                rows.append(({idx[('zl', k, e, t)]: 1, idx[('z', k, e, t)]: -1}, -np.inf, 0))
                por_q[(e[0], e[1], int(t * DT // 24))].append(j)
            if key[0] == 'z':
                por_od[key[2]].append(j)
        for q, js in por_q.items():
            rows.append(({j: 1 for j in js}, -np.inf, max(dem_res[q], 0.0)))
        for od, f in freq_res.items():
            if f > 0 and os.environ.get('SIN_FREQ') != '1':
                rows.append(({j: 1 for j in por_od.get(od, [])}, f, np.inf))
        # punto de partida parcial: vuelos (z), esperas (g) y uso (u) de cada avión; Gurobi completa la carga
        start = None
        if vb_start:
            start = {j: 0 for key, j in idx.items() if key[0] in ('u', 'z', 'g')}
            for k in K:
                b = vb_start.get(k, [])
                if not b:
                    continue
                start[idx[('u', k)]] = 1
                faltan = [(e, t) for e, t in b if ('z', k, e, t) not in idx]
                if faltan:
                    print(f'[!] {op}: el vuelo {k} {faltan[0]} de la corrida de partida no existe en la red; sin punto de partida')
                    start = None
                    break
                for e, t in b:
                    start[idx[('z', k, e, t)]] = 1
                for a, t in esperas_avion(b, dur, T):
                    start[idx[('g', k, a, t)]] = 1
        # romper simetría: aviones idénticos (mismo payload y aeropuerto de mantenimiento) se ordenan por uso
        r = resolver_mip(lb, ub, obj, integ, rows, TLIM_OP, log=os.environ.get('LOG') == '1', start=start)
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
    # con punto de partida, se evalúa también la corrida de partida (mismo paso 3 y validador) y se conserva la mejor:
    # así refinar la grilla nunca da un resultado peor que la corrida de la que se partió
    partida = None
    if it_start:
        it0 = {k: [{'pos': p, 'tramo': l['tramo'], 't_dep': l['t_dep'], 't_arr': l['t_dep'] + tau[l['tramo']]}
                   for p, l in enumerate(sorted(legs, key=lambda l: l['t_dep']), start=1)]
               for k, legs in it_start.items()}
        multi0 = asignar_carga_multitramos(it0, ruta_raw=RAW, ttr_h=TTR_H, tlim=TLIM_CARGA)
        if multi0['ok']:
            v0 = validar(multi0['solucion'], cfg, ruta_raw=RAW)
            m0, m1 = v0['kpis']['margen_usd'], v['kpis']['margen_usd']
            conserva = bool(v0['ok'] and (m0 > m1 or not v['ok']))
            partida = {'corrida': os.environ['START'], 'margen_partida_usd': m0, 'margen_nuevo_usd': m1,
                       'se_conserva_partida': conserva}
            print(f"Punto de partida: margen {m0:,.0f} | solución nueva {m1:,.0f} -> se conserva la "
                  f"{'de partida' if conserva else 'nueva'}", flush=True)
            if conserva:
                itinerario, multi, v = it0, multi0, v0
                s = multi['solucion']
                cargas = defaultdict(float)
                for registro in s['cargas']:
                    cargas[(registro['k'], registro['s'])] += registro['x']
    # El validador es la fuente única de KPI. Esto evita que el ejecutor y el
    # informe usen fórmulas distintas (en particular para ocupación CTK/ACTK).
    kp = dict(v['kpis'])
    kp.update({
        'toneladas_recortadas_regla_90min': perd,
        'flujo_through_t': multi['estadisticas']['flujo_through_t'],
        'flujo_transferido_t': multi['estadisticas']['flujo_transferido_t'],
        'ttr_h': TTR_H,
    })
    calidad_optimizacion = {
        'metodo': 'secuencial_en_tres_pasos',
        'gap_global_disponible': False,
        'gap_global': None,
        'nota': ('No existe una cota común para la solución integrada: los gaps de los '
                 'subproblemas no se pueden sumar ni promediar para formar un gap global.'),
        'punto_de_partida': partida,
        'paso_1_red_agregada': P.get('res', {}),
        'paso_2_por_operador': [
            {clave: fila.get(clave) for clave in ('operador', 'estado', 'objetivo', 'cota', 'gap', 'tiempo_s')}
            for fila in resumen
        ],
        'paso_3_carga_condicionada_itinerarios': {
            clave: multi.get(clave) for clave in ('estado', 'objetivo_carga', 'cota', 'gap', 'tiempo_s')
        },
    }
    print('Validador OK:', v['ok'], '| violaciones:', len(v['violaciones']))
    for w in v['violaciones'][:25]: print('  -', w)
    for k2, x in kp.items(): print(f'  {k2}: {x}')
    base = pkl.replace('.pkl', '') + ('_poravion_amp' if AMPLIAR else '_poravion')
    json.dump({'kpis': kp, 'validador_ok': v['ok'], 'violaciones': v['violaciones'],
               'calidad_optimizacion': calidad_optimizacion, 'por_operador': resumen,
               'modelo_carga': {k: x for k, x in multi.items() if k != 'solucion'}},
              open(base + '_kpis.json', 'w', encoding='utf-8'), indent=2, ensure_ascii=False, default=str)

    
    op_de = dict(zip(fl.aircraft_id, fl.operator))
    leg = {(k, l['pos']): l for k, legs in itinerario.items() for l in legs}
    costo_v = {(k, p): (fuel_h + c[('ex_fuel_usd_per_block_hour', op_de[k])]) * tau[tuple(l['tramo'])]
                       + fee[tuple(l['tramo'])[1]] for (k, p), l in leg.items()}
    hand_v, ing_v, por_q = defaultdict(float), defaultdict(float), defaultdict(list)
    for reg in s['cargas']:
        hand_v[(reg['k'], reg['s'])] += (reg['b'] + reg.get('transfer_in', 0.0)) * c[('handling_usd_per_ton', op_de[reg['k']])]
        por_q[tuple(reg['q'])].append(reg)
    for q, regs in por_q.items():
        total = 1000 * tarifa[(q[0], q[1])] * sum(reg['a'] for reg in regs)
        pesos = [reg['x'] * (leg[(reg['k'], reg['s'])]['t_arr'] - leg[(reg['k'], reg['s'])]['t_dep']) for reg in regs]
        Wq = sum(pesos)
        if total <= 0 or Wq <= 0: continue
        for reg, w in zip(regs, pesos): ing_v[(reg['k'], reg['s'])] += total * w / Wq
    print(f"[costos por vuelo] vuelos {sum(costo_v.values()):,.0f} vs {v['costo_vuelos']:,.0f} | "
          f"handling {sum(hand_v.values()):,.0f} vs {v['handling']:,.0f} | "
          f"ingresos {sum(ing_v.values()):,.0f} vs {v['ingresos']:,.0f}")
    filas = [{'avion': k, 'operador': op_de[k], 'pos': l['pos'],
              'origen': l['tramo'][0], 'destino': l['tramo'][1], 't_dep_h': l['t_dep'], 't_arr_h': l['t_arr'],
              'dia': int(l['t_dep'] // 24) + 1, 'carga_t': round(cargas.get((k, l['pos']), 0.0), 3),
              'costo_usd': round(costo_v[(k, l['pos'])], 2),
              'handling_usd': round(hand_v[(k, l['pos'])], 2),
              'ingreso_usd': round(ing_v[(k, l['pos'])], 2)}
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
