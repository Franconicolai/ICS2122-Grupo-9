"""
Módulo: validador.py
Descripción: verificador INDEPENDIENTE de itinerarios. Lee directamente los CSV/YAML originales
(no usa estructuras_datos.py ni gurobi_model.py) y comprueba, regla por regla, que la solución sea
ejecutable, recalculando además el margen neto desde cero. Sirve para detectar errores de modelación:
si el modelo dice que una solución es óptima pero el validador encuentra violaciones (o un objetivo
distinto), hay un error en el modelo.
"""

import os
from datetime import datetime
from typing import Dict, Any, List, Optional

import pandas as pd
import yaml

TOL = 1e-4
UMBRAL_LLENO = 0.95  # supuesto: "lleno" = carga >= 95 % del payload (ver README); 0.005 t de holgura numérica


def _cargar_raw(ruta_raw: str) -> Dict[str, Any]:
    rd = lambda f: pd.read_csv(os.path.join(ruta_raw, f))
    with open(os.path.join(ruta_raw, 'ops_rules.yaml'), encoding='utf-8') as f:
        reglas = yaml.safe_load(f)
    cp = {(r.param, r.operator): float(r.value) for r in rd('cost_params.csv').itertuples()}
    fleet = rd('fleet.csv')
    ap = rd('airports.csv')
    inicio = datetime.fromisoformat(str(reglas['week_start']))
    mant = {}
    for r in rd('maintenance.csv').itertuples():
        mant[r.aircraft_id] = (r.airport,
                               (datetime.fromisoformat(r.start_datetime) - inicio).total_seconds() / 3600.0,
                               (datetime.fromisoformat(r.end_datetime) - inicio).total_seconds() / 3600.0)
    dw = rd('demand_weekly.csv')
    return {
        'reglas': reglas,
        'op': dict(zip(fleet.aircraft_id, fleet.operator)),
        'payload': dict(zip(fleet.aircraft_id, fleet.payload_tons.astype(float))),
        'pais': dict(zip(ap.iata, ap.country)),
        'block': {(r.origin, r.dest): float(r.block_hours) for r in rd('legs_catalog.csv').itertuples()},
        'permitido': {(r.operator, r.country) for r in rd('traffic_rights.csv').itertuples() if r.allowed == 1},
        'cp': cp,
        'fee': dict(zip(rd('landing_fees.csv').airport, rd('landing_fees.csv').fee_usd.astype(float))),
        'mant': mant,
        'tarifa': {(r.origin, r.dest): float(r.tariff_usd_per_kg) for r in dw.itertuples()},
        'freq': {(r.origin, r.dest): int(r.min_weekly_freq) for r in dw.itertuples() if r.min_weekly_freq > 0},
        'demanda_diaria': {(r.origin, r.dest, d): float(getattr(r, c))
                           for r in rd('demand_daily.csv').itertuples()
                           for d, c in enumerate(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], start=1)},
        'inicio': inicio,
    }


def validar(solucion: Dict[str, Any], config: Optional[Dict[str, bool]] = None,
            ruta_raw: Optional[str] = None, aviones: Optional[List[str]] = None,
            freq_exigibles: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Parámetros
    ----------
    solucion : salida de solver_bridge.extraer_solucion.
    config   : mismas banderas del modelo (para saber qué reglas exigir).
    aviones  : aviones considerados en la instancia (los no incluidos se ignoran).
    freq_exigibles : {(o,d): f} frecuencias exigidas en la instancia (por defecto todas las de la base).
    """
    cfg = {'ciclico': True, 'tat_variable': True, 'min_ton_escala': True, 'mantenimiento': True, 'frecuencias': True,
           'demanda_diaria': False, 'dist_frecuencias': False}
    cfg.update(config or {})
    ruta_raw = ruta_raw or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'raw data')
    R = _cargar_raw(ruta_raw)
    reg = R['reglas']
    H = 168.0
    tat = {k: v / 60.0 for k, v in reg['tat_minutes'].items()}
    tecnicos = set(reg['technical_stop_airports'])
    max_bloque = float(reg['max_continuous_block_hours'])
    min_ton = float(reg['min_tons_per_extra_stop'])
    quema, precio = R['cp'][('fuel_burn_gal_per_hour', 'ALL')], R['cp'][('fuel_price_usd_per_gal', 'ALL')]

    viol: List[str] = []
    it = solucion['itinerario']
    aviones = aviones if aviones is not None else list(R['op'])

    # carga por (k, s) y por commodity
    carga = {}
    for c in solucion['cargas']:
        carga[(c['k'], c['s'], c['q'])] = c
    carga_vuelo = {}
    for (k, s, q), c in carga.items():
        carga_vuelo[(k, s)] = carga_vuelo.get((k, s), 0.0) + c['x']

    costo_vuelos = 0.0
    freq_real = {}
    for k in aviones:
        legs = it.get(k, [])
        if not legs:
            # avión ocioso: solo se exige mantenimiento si la config lo pide (ocioso cumple trivialmente)
            continue
        op = R['op'][k]
        if [l['pos'] for l in legs] != list(range(1, len(legs) + 1)):
            viol.append(f"{k}: posiciones no consecutivas {[l['pos'] for l in legs]}")
        for l in legs:
            o, d = l['tramo']
            if (o, d) not in R['block']:
                viol.append(f"{k}: tramo {o}-{d} no está en el catálogo")
                continue
            if R['block'][(o, d)] > max_bloque + TOL:
                viol.append(f"{k}: tramo {o}-{d} dura {R['block'][(o, d)]} h > {max_bloque} h")
            if (op, R['pais'][o]) not in R['permitido'] or (op, R['pais'][d]) not in R['permitido']:
                viol.append(f"{k}: operador {op} sin derechos de tráfico en {o}-{d}")
            if abs(l['t_arr'] - l['t_dep'] - R['block'][(o, d)]) > TOL:
                viol.append(f"{k}: llegada != salida + duración en {o}-{d} (pos {l['pos']})")
            if l['t_dep'] < -TOL or l['t_arr'] > H + TOL:
                viol.append(f"{k}: vuelo fuera de la semana ({l['t_dep']:.2f}-{l['t_arr']:.2f})")
            freq_real[(o, d)] = freq_real.get((o, d), 0) + 1
            costo_vuelos += (quema * precio + R['cp'][('ex_fuel_usd_per_block_hour', op)]) * R['block'][(o, d)] + R['fee'][d]
            # capacidad y mínimo por vuelo
            load = carga_vuelo.get((k, l['pos']), 0.0)
            if load > R['payload'][k] + TOL:
                viol.append(f"{k}: carga {load:.2f} t > payload {R['payload'][k]} t en pos {l['pos']}")
            if cfg['min_ton_escala'] and TOL < load < min_ton - TOL:
                viol.append(f"{k}: vuelo pos {l['pos']} lleva {load:.2f} t (0 o >= {min_ton})")
        # continuidad, TAT y cierre
        for i in range(len(legs) - 1):
            a_, b_ = legs[i], legs[i + 1]
            if a_['tramo'][1] != b_['tramo'][0]:
                viol.append(f"{k}: discontinuidad {a_['tramo']} -> {b_['tramo']}")
            gap = b_['t_dep'] - a_['t_arr']
            if cfg['tat_variable']:
                lleno = lambda s_: carga_vuelo.get((k, s_), 0.0) >= UMBRAL_LLENO * R['payload'][k] - 0.005
                if a_['tramo'][1] in tecnicos:
                    req = tat['technical_stop']
                elif lleno(a_['pos']) and lleno(b_['pos']):
                    req = tat['full_in_full_out']
                else:
                    req = tat['default']
            else:
                req = tat['default']
            if gap < req - TOL:
                viol.append(f"{k}: TAT {gap:.3f} h < {req:.3f} h entre pos {a_['pos']} y {b_['pos']}")
        if cfg['ciclico']:
            if legs[-1]['tramo'][1] != legs[0]['tramo'][0]:
                viol.append(f"{k}: rotación no cierra ({legs[0]['tramo'][0]} ... {legs[-1]['tramo'][1]})")
            if legs[0]['t_dep'] + H - legs[-1]['t_arr'] < tat['default'] - TOL:
                viol.append(f"{k}: cierre temporal sin TAT suficiente")
        # mantenimiento
        if cfg['mantenimiento']:
            ap, ini, fin = R['mant'][k]
            ok = (legs[0]['tramo'][0] == ap and legs[0]['t_dep'] >= fin - TOL)
            for i, l in enumerate(legs):
                if l['tramo'][1] == ap and l['t_arr'] <= ini + TOL:
                    if i == len(legs) - 1 or legs[i + 1]['t_dep'] >= fin - TOL:
                        ok = True
            if not ok:
                viol.append(f"{k}: sin ventana de mantenimiento válida en {ap} [{ini:.1f}, {fin:.1f}]")

    # cargas: conservación y reglas por commodity
    por_avion_q = {}
    for (k, s, q), c in carga.items():
        por_avion_q.setdefault((k, q), {})[s] = c
    entregado = {}
    ingresos = 0.0
    handling = 0.0
    for (k, q), d in por_avion_q.items():
        legs = {l['pos']: l['tramo'] for l in it.get(k, [])}
        for s, c in sorted(d.items()):
            if s not in legs:
                viol.append(f"{k}: carga {q} en pos {s} sin vuelo")
                continue
            o, de = legs[s]
            prev = d.get(s - 1)
            entra = c['b'] + (prev['w'] if prev else 0.0)
            if abs(c['x'] - entra) > 1e-5:
                viol.append(f"{k}: balance de entrada roto {q} pos {s}")
            if abs(c['x'] - c['a'] - c['w']) > 1e-5:
                viol.append(f"{k}: balance de salida roto {q} pos {s}")
            if c['b'] > 1e-6 and o != q[0]:
                viol.append(f"{k}: embarca {q} en {o} (origen {q[0]})")
            if c['a'] > 1e-6 and de != q[1]:
                viol.append(f"{k}: descarga {q} en {de} (destino {q[1]})")
            if cfg['demanda_diaria'] and c['b'] > 1e-6:
                t0 = next(l['t_dep'] for l in it[k] if l['pos'] == s)
                dia = int(t0 // 24) + 1
                if dia != q[2]:
                    viol.append(f"{k}: embarca {q} el día {dia} (demanda del día {q[2]})")
            if c['b'] > 1e-6 and o in tecnicos:
                viol.append(f"{k}: carga en escala técnica {o}")
            if c['a'] > 1e-6 and de in tecnicos:
                viol.append(f"{k}: descarga en escala técnica {de}")
            entregado[q] = entregado.get(q, 0.0) + c['a']
            handling += R['cp'][('handling_usd_per_ton', R['op'][k])] * c['b']
    for q, ton in entregado.items():
        if ton > R['demanda_diaria'][q] + 1e-5:
            viol.append(f"commodity {q}: entregado {ton:.2f} > demanda {R['demanda_diaria'][q]}")
        ingresos += 1000.0 * R['tarifa'][(q[0], q[1])] * ton
    # embarcado == entregado por commodity
    embarcado = {}
    for (k, s, q), c in carga.items():
        embarcado[q] = embarcado.get(q, 0.0) + c['b']
    for q in set(embarcado) | set(entregado):
        if abs(embarcado.get(q, 0) - entregado.get(q, 0)) > 1e-5:
            viol.append(f"commodity {q}: embarcado {embarcado.get(q, 0):.3f} != entregado {entregado.get(q, 0):.3f}")

    # frecuencias mínimas
    if cfg['frecuencias']:
        exig = freq_exigibles if freq_exigibles is not None else R['freq']
        for od, f in exig.items():
            if freq_real.get(od, 0) < f:
                viol.append(f"frecuencia {od}: {freq_real.get(od, 0)} < {f}")

    # distribución equilibrada: a lo más ceil(f/7) salidas por día UTC de cada OD comprometido
    if cfg['dist_frecuencias']:
        exig = freq_exigibles if freq_exigibles is not None else R['freq']
        por_dia = {}
        for k in aviones:
            for l in it.get(k, []):
                por_dia[(l['tramo'], int(l['t_dep'] // 24) + 1)] = por_dia.get((l['tramo'], int(l['t_dep'] // 24) + 1), 0) + 1
        for (od, dia), n in por_dia.items():
            if od in exig and n > -(-exig[od] // 7):
                viol.append(f"distribución: {od} tiene {n} salidas el día {dia} (tope {-(-exig[od] // 7)})")

    return {
        'ok': not viol,
        'violaciones': viol,
        'ingresos': ingresos,
        'costo_vuelos': costo_vuelos,
        'handling': handling,
        'margen': ingresos - costo_vuelos - handling,
        'toneladas_entregadas': sum(entregado.values()),
        'vuelos': sum(len(v) for v in it.values()),
    }
