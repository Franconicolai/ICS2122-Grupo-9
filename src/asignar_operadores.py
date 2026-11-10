"""
Interchange: elige la filial que opera cada vuelo, con los vuelos y la carga fijos.

Un interchange es el punto donde un avión pasa a ser operado por otra filial a mitad de rotación; es posible
entre dos operadores solo si ambos están habilitados en el aeropuerto (interchange_airports.csv). Cada vuelo usa
los derechos de tráfico, el costo ex-fuel por hora y el handling de la filial que lo opera. Como la operación es
cíclica, la rotación parte y termina con el operador base del avión (borde_base=True); con borde_base=False basta
volver al operador base en el aeropuerto del borde de la semana, si allí están habilitadas ambas filiales.

Para cada avión es un camino más corto en un grafo de capas (un nodo por vuelo y filial, 4 filiales), así que la
programación dinámica entrega el óptimo exacto para esos vuelos y esa carga. Supuestos: el interchange no tiene
costo ni tiempo en tierra adicional, y un transbordo exige que las filiales que operan el vuelo que entrega y el
que recibe estén habilitadas en el aeropuerto (la misma regla de soporte del validador).
"""
import os
from collections import defaultdict

import pandas as pd

RAW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'raw data')
INF = float('inf')


def cargar_datos_operadores(ruta_raw=RAW):
    rd = lambda f: pd.read_csv(os.path.join(ruta_raw, f))
    cp = rd('cost_params.csv')
    inter = rd('interchange_airports.csv')
    ops = sorted(c for c in inter.columns if c != 'airport')
    return {
        'ops': ops,
        'base': dict(zip(rd('fleet.csv').aircraft_id, rd('fleet.csv').operator)),
        'exf': {r.operator: float(r.value) for r in cp.itertuples() if r.param == 'ex_fuel_usd_per_block_hour'},
        'hdl': {r.operator: float(r.value) for r in cp.itertuples() if r.param == 'handling_usd_per_ton'},
        'pais': dict(zip(rd('airports.csv').iata, rd('airports.csv').country)),
        'derechos': {(r.operator, r.country) for r in rd('traffic_rights.csv').itertuples() if r.allowed == 1},
        'soporte': {(r.airport, o) for r in inter.itertuples() for o in ops if getattr(r, o) == 1},
    }


def asignar_operadores(solucion, datos=None, borde_base=True):
    """Devuelve (itinerario con 'op' en cada vuelo, resumen). solucion: itinerario, cargas y transferencias."""
    D = datos or cargar_datos_operadores()
    ops, base, sop = D['ops'], D['base'], D['soporte']
    # toneladas que paga cada vuelo en handling (embarque inicial y transbordos recibidos)
    ton = defaultdict(float)
    for c in solucion['cargas']:
        ton[c['k'], c['s']] += c['b']
    # filiales admitidas en cada vuelo por los transbordos que entrega o recibe
    admitidas = {}
    for tr in solucion.get('transferencias', []):
        ton[tr['to_k'], int(tr['to_s'])] += float(tr['tons'])
        for clave in ((tr['from_k'], int(tr['from_s'])), (tr['to_k'], int(tr['to_s']))):
            admitidas[clave] = admitidas.get(clave, set(ops)) & {o for o in ops if (tr['airport'], o) in sop}

    def costo(k, l, o):
        oo, dd = l['tramo']
        if (o, D['pais'][oo]) not in D['derechos'] or (o, D['pais'][dd]) not in D['derechos']:
            return INF
        if o not in admitidas.get((k, l['pos']), ops):
            return INF
        return D['exf'][o] * (l['t_arr'] - l['t_dep']) + D['hdl'][o] * ton[k, l['pos']]

    def cambia(a, de, o):
        return de == o or ((a, de) in sop and (a, o) in sop)

    itinerario, resumen = {}, {'ahorro_usd': 0.0, 'interchanges': 0, 'vuelos_otra_filial': 0, 'por_avion': {}}
    for k, legs in solucion['itinerario'].items():
        legs = sorted(legs, key=lambda l: l['pos'])
        if not legs:
            itinerario[k] = []
            continue
        b, a0 = base[k], legs[0]['tramo'][0]
        borde_ok = (lambda o: o == b) if borde_base else (lambda o: cambia(a0, b, o))
        # f[o] = (costo, cambios) mínimo hasta el vuelo i operado por o; empates: menos interchanges
        f = {o: ((costo(k, legs[0], o), 0) if borde_ok(o) else (INF, 0)) for o in ops}
        pred = []
        for i in range(1, len(legs)):
            a = legs[i]['tramo'][0]
            nf, pr = {}, {}
            for o in ops:
                c_o = costo(k, legs[i], o)
                cand = [((f[p][0] + c_o, f[p][1] + (p != o)), p) for p in ops if f[p][0] < INF and cambia(a, p, o)]
                nf[o], pr[o] = min(cand) if cand else ((INF, 0), None)
            f = nf
            pred.append(pr)
        fin = {o: f[o] for o in ops if borde_ok(o) and f[o][0] < INF}
        if not fin:   # no debería ocurrir: el operador base siempre es factible si la solución de partida lo era
            raise ValueError(f'{k}: no hay asignación de filiales factible')
        o = min(fin, key=fin.get)
        seq = [o]
        for pr in reversed(pred):
            seq.append(pr[seq[-1]])
        seq.reverse()
        actual = sum(costo(k, l, l.get('op') or b) for l in legs)
        nuevo = fin[o][0]
        cambios = sum(seq[i] != seq[i - 1] for i in range(1, len(seq))) + (0 if borde_base else
                                                                         (seq[0] != b) + (seq[-1] != b))
        itinerario[k] = [{**{c: v for c, v in l.items() if c != 'op'}, **({'op': o_} if o_ != b else {})}
                         for l, o_ in zip(legs, seq)]
        resumen['ahorro_usd'] += actual - nuevo
        resumen['interchanges'] += cambios
        resumen['vuelos_otra_filial'] += sum(o_ != b for o_ in seq)
        resumen['por_avion'][k] = {'base': b, 'ahorro_usd': round(actual - nuevo, 2), 'interchanges': cambios,
                                   'filiales': ''.join(o_[0] for o_ in seq)}
    return itinerario, resumen
