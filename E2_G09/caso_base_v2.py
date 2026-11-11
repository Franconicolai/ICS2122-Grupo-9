"""
Módulo: caso_base_v2.py
Descripción: CASO BASE v2, calculado bajo EXACTAMENTE las mismas reglas que el modelo final (red espacio-tiempo),
pero con una política de programación más razonable que la del caso base anterior (caso_base.py).

Por qué existe esta versión (críticas recibidas en la presentación):
  1. La versión sin frecuencias no era comparable (no cumplía las mismas reglas que el modelo) y la versión con
     las 25 frecuencias quedaba con un margen muy bajo (~0,5 MM USD), lo que favorecía demasiado al modelo.
     Aquí las 25 frecuencias mínimas son SIEMPRE obligatorias y, aun así, la política no desperdicia vuelos:
       - se programan ROTACIONES (ciclos base -> ... -> base de 2 a 4 tramos) y no solo viajes redondos
         base->x->base; así una frecuencia se cubre con tramos que además llevan carga (p. ej. el triángulo
         MIA->SJO->GUA->MIA cubre tres ODs con frecuencia) en vez de vuelos de posicionamiento vacíos;
       - la programación avanza en el tiempo (siempre decide el avión que queda libre antes) y estima la carga
         de cada tramo con la DEMANDA DIARIA del día en que sale (la regla del modelo es que la carga solo
         se embarca el día de su demanda), no con la demanda semanal agregada;
       - cubrir una frecuencia pendiente tiene un bono bajo en la puntuación (parámetro MU); las que
         aún queden pendientes se cubren como último recurso con recorridos de posicionamiento por camino más
         corto (como en el caso base anterior). Las 25 frecuencias deben quedar cubiertas.
  2. Interchange: los datos de interchange_airports implican que un avión puede cambiar de operador durante la
     semana (debe volver a su operador al cerrar el ciclo). El modelo final todavía NO lo incorpora, por lo que el
     caso base tampoco: cada avión mantiene su operador, igual que en el modelo. Queda para la siguiente entrega.
     (La asignación de carga sigue usando interchange_airports como soporte de transbordos de carga, igual que el
     modelo; ver nota en el informe.)

Lo que se mantiene (es lo que lo hace un caso base): decisiones secuenciales y miopes (primero se fijan los vuelos
con una regla de puntuación por hora, después se asigna la carga de forma óptima sobre esos vuelos), cada avión
se queda en su base (aeropuerto de su ventana de mantenimiento) y no se optimiza el conjunto de la red.

Carga: la misma asignación global del modelo final (src/asignacion_carga_multitramos.py): vuelo directo,
continuación en el mismo avión y transbordos (con TTR_H). Validación: src/validador.py con las 25 frecuencias.

Variables de entorno (todas opcionales):
    SOLVER          'gurobi' (por defecto aquí) o 'highs'
    THREADS         hilos del solver
    TTR_H           tiempo mínimo de transbordo en horas (2 por defecto, igual que el modelo)
    TLIM_CARGA      segundos para la asignación de carga (300 por defecto)
    CB_FRECUENCIAS  1 (por defecto) exige las 25 frecuencias; 0 las ignora (solo para sensibilidad)
    CB_MAX_TRAMOS   largo máximo de una rotación (6 por defecto)
    CB_MU           bono (USD) por frecuencia pendiente cubierta en la puntuación de una rotación (20.000 por defecto)
    CB_ESPERAS      esperas candidatas antes de salir, en horas, separadas por coma (0,6,12,18 por defecto)
    SALIDA          prefijo de los archivos de salida (por defecto results/caso_base_v2)

Uso (desde la raíz del repositorio):
    SOLVER=gurobi python src/caso_base_v2.py
"""
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

import pandas as pd
import yaml

os.environ.setdefault('SOLVER', 'gurobi')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asignacion_carga_multitramos as acm  # noqa: E402
from validador import validar  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = acm.RAW
H = 168.0
EPS_T = 1e-6
UMBRAL_LLENO = 0.95   # un vuelo está "lleno" con >= 95 % del payload (mismo supuesto que el modelo)
FRECUENCIAS = os.environ.get('CB_FRECUENCIAS', '1') == '1'
MAX_TRAMOS = int(os.environ.get('CB_MAX_TRAMOS', 6))
MU = float(os.environ.get('CB_MU', 20000.0))   # bono (USD) por frecuencia pendiente cubierta
ESPERAS = [float(x) for x in os.environ.get('CB_ESPERAS', '0,6,12,18').split(',')]
SALIDA = os.environ.get('SALIDA', os.path.join(RAIZ, 'results', 'caso_base_v2'))


# ------------------------------------------------------------------------------------------ datos
def cargar_datos(ruta_raw=RAW):
    """Lee los CSV y las reglas operativas y arma lo que necesita el caso base."""
    rd = lambda f: pd.read_csv(os.path.join(ruta_raw, f))
    ap, fl, lg = rd('airports.csv'), rd('fleet.csv'), rd('legs_catalog.csv')
    dw, dd, tr = rd('demand_weekly.csv'), rd('demand_daily.csv'), rd('traffic_rights.csv')
    cp, lf, mt = rd('cost_params.csv'), rd('landing_fees.csv'), rd('maintenance.csv')
    with open(os.path.join(ruta_raw, 'ops_rules.yaml'), encoding='utf-8') as f:
        reglas = yaml.safe_load(f)

    pais = dict(zip(ap.iata, ap.country))
    operador = dict(zip(fl.aircraft_id, fl.operator))
    payload = dict(zip(fl.aircraft_id, fl.payload_tons.astype(float)))
    permitido = {(o, c) for o, c, a in zip(tr.operator, tr.country, tr.allowed) if a == 1}
    tec = set(reglas['technical_stop_airports'])
    tat = {k: v / 60.0 for k, v in reglas['tat_minutes'].items()}
    max_bloque = float(reglas['max_continuous_block_hours'])
    min_ton = float(reglas['min_tons_per_extra_stop'])
    c = {(p, o): v for p, o, v in zip(cp.param, cp.operator, cp.value)}
    combustible_h = c[('fuel_burn_gal_per_hour', 'ALL')] * c[('fuel_price_usd_per_gal', 'ALL')]
    tasa = dict(zip(lf.airport, lf.fee_usd))
    tau = {(o, d): float(b) for o, d, b in zip(lg.origin, lg.dest, lg.block_hours)}
    tarifa = {(o, d): float(r) for o, d, r in zip(dw.origin, dw.dest, dw.tariff_usd_per_kg)}
    demanda = {(o, d): float(t) for o, d, t in zip(dw.origin, dw.dest, dw.tons_week)}
    freq = {(o, d): int(f) for o, d, f in zip(dw.origin, dw.dest, dw.min_weekly_freq) if f > 0}
    dem_dia = {(r.origin, r.dest, i): float(getattr(r, col))
               for r in dd.itertuples()
               for i, col in enumerate(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], start=1)
               if float(getattr(r, col)) > 1e-9}

    # tramos que puede volar cada avión: derechos de tráfico en origen y destino y límite de 9 h
    Ek = {k: {e for e in tau if tau[e] <= max_bloque
              and (operador[k], pais[e[0]]) in permitido and (operador[k], pais[e[1]]) in permitido}
          for k in fl.aircraft_id}
    costo = {(k, e): (combustible_h + c[('ex_fuel_usd_per_block_hour', operador[k])]) * tau[e] + tasa[e[1]]
             for k in fl.aircraft_id for e in Ek[k]}
    handling = {k: c[('handling_usd_per_ton', operador[k])] for k in fl.aircraft_id}

    # mantenimiento: (aeropuerto base, inicio, fin) en horas desde el inicio de la semana
    ini = datetime.fromisoformat(str(reglas['week_start']))
    mant = {}
    for r in mt.itertuples():
        h0 = (datetime.fromisoformat(r.start_datetime) - ini).total_seconds() / 3600.0
        h1 = (datetime.fromisoformat(r.end_datetime) - ini).total_seconds() / 3600.0
        mant[r.aircraft_id] = (r.airport, h0, h1)

    return dict(K=list(fl.aircraft_id), A=sorted(pais), operador=operador, payload=payload, tec=tec, tat=tat,
                min_ton=min_ton, tau=tau, tarifa=tarifa, demanda=demanda, dem_dia=dem_dia, freq=freq, Ek=Ek,
                costo=costo, handling=handling, mant=mant)


# ------------------------------------------------------------------------------------------ rotaciones
def _rotaciones(av, d, max_tramos):
    """Todos los ciclos base -> ... -> base de 2 a max_tramos tramos (sin repetir aeropuertos) con los tramos que el
    avión puede volar y al menos un tramo con demanda. Se calculan una vez por (operador, base)."""
    clave = (d['operador'][av['k']], av['base'], max_tramos)
    cache = d.setdefault('_rotaciones', {})
    if clave in cache:
        return cache[clave]
    b = av['base']
    sal = defaultdict(list)
    for (o, x) in d['Ek'][av['k']]:
        sal[o].append(x)
    for o in sal:
        sal[o].sort()
    out = []

    def dfs(actual, camino, visitados):
        for x in sal[actual]:
            e = (actual, x)
            if x == b:
                if camino:
                    tour = tuple(camino + [e])
                    if any(t in d['tarifa'] and t[0] not in d['tec'] and t[1] not in d['tec'] for t in tour):
                        out.append(tour)
                continue
            if x in visitados or len(camino) + 2 > max_tramos:
                continue
            camino.append(e)
            visitados.add(x)
            dfs(x, camino, visitados)
            camino.pop()
            visitados.discard(x)

    dfs(b, [], {b})
    cache[clave] = out
    return out


def _ruta(av, a, b, d):
    """Camino más corto (en horas, con tiempos en tierra) de a a b con los tramos que el avión puede volar.
    El catálogo es asimétrico (p. ej. no existe FRA->MIA). Devuelve la lista de tramos ([] si a == b) o None."""
    if a == b:
        return []
    clave = (d['operador'][av['k']], a, b)
    cache = d.setdefault('_rutas', {})
    if clave in cache:
        return cache[clave]
    tat = d['tat']
    dist, previo, pend_ = {a: 0.0}, {}, {a}
    hecho = set()
    while pend_:
        u = min(pend_, key=lambda x: (dist[x], x))
        pend_.discard(u)
        hecho.add(u)
        if u == b:
            break
        for e in sorted(d['Ek'][av['k']]):
            if e[0] != u or e[1] in hecho:
                continue
            w = dist[u] + d['tau'][e] + (tat['technical_stop'] if e[1] in d['tec'] else tat['default'])
            if w < dist.get(e[1], float('inf')) - 1e-12:
                dist[e[1]], previo[e[1]] = w, e
                pend_.add(e[1])
    if b not in previo:
        cache[clave] = None
        return None
    camino, x = [], b
    while x != a:
        camino.append(previo[x])
        x = previo[x][0]
    cache[clave] = camino[::-1]
    return cache[clave]


# ------------------------------------------------------------------------------------------ programación
def _carga_dia(av, e, t_dep, d, remd):
    """Carga que se espera llevar en el tramo e si sale en t_dep: demanda DIARIA que queda ese día (la carga solo
    se embarca el día UTC de su demanda). Cero si no hay demanda, si es escala técnica o si no llega al mínimo."""
    if e not in d['tarifa'] or e[0] in d['tec'] or e[1] in d['tec']:
        return 0.0
    dia = int(t_dep // 24) + 1
    c = min(av['Q'], remd.get((e[0], e[1], dia), 0.0))
    return c if c >= d['min_ton'] else 0.0


def _programar(av, tramos, cargas, d, t0):
    """Horas de salida y llegada de una secuencia de tramos que parte en t0, con el TAT que corresponde según las
    cargas estimadas (50 min escala técnica, 90 lleno-lleno, 70 resto). Devuelve None si no cierra la semana (con el
    TAT de cierre, que se toma conservadoramente de 90 min) o si pisaría la ventana de mantenimiento."""
    tat = d['tat']
    dep = t0
    prog = []
    for i, e in enumerate(tramos):
        arr = dep + d['tau'][e]
        prog.append((dep, arr))
        if i + 1 < len(tramos):
            if e[1] in d['tec']:
                espera = tat['technical_stop']
            elif cargas[i] >= UMBRAL_LLENO * av['Q'] and cargas[i + 1] >= UMBRAL_LLENO * av['Q']:
                espera = tat['full_in_full_out']
            else:
                espera = tat['default']
            dep = arr + espera + EPS_T
    fin_viaje = prog[-1][1]
    if fin_viaje + tat['full_in_full_out'] > H - EPS_T:
        return None
    if not av['mant_hecha'] and prog[0][0] < av['fin'] and fin_viaje > av['ini']:
        return None
    return prog


def _estimar(av, tramos, t0, d, remd):
    """Programa el recorrido y estima su carga por día de salida. Como el día depende de las horas y las horas del
    TAT (que depende de la carga), se itera dos veces. Devuelve (prog, cargas, utilidad) o None si no cabe."""
    tat = d['tat']
    dep = t0
    fechas = []
    for e in tramos:
        fechas.append(dep)
        dep += d['tau'][e] + (tat['technical_stop'] if e[1] in d['tec'] else tat['default']) + EPS_T
    cargas = [_carga_dia(av, e, td, d, remd) for e, td in zip(tramos, fechas)]
    prog = _programar(av, tramos, cargas, d, t0)
    if prog is None:
        return None
    cargas = [_carga_dia(av, e, p[0], d, remd) for e, p in zip(tramos, prog)]
    prog = _programar(av, tramos, cargas, d, t0)
    if prog is None:
        return None
    ing = 1000.0 * sum(d['tarifa'].get(e, 0.0) * c for e, c in zip(tramos, cargas))
    costo = sum(d['costo'][(av['k'], e)] for e in tramos) + d['handling'][av['k']] * sum(cargas)
    return prog, cargas, ing - costo


def _agregar(av, tramos, cargas, prog, remd, pend, d):
    """Fija el recorrido en el itinerario del avión, descuenta la demanda diaria estimada y las frecuencias."""
    for e, (td, ta) in zip(tramos, prog):
        av['legs'].append({'pos': len(av['legs']) + 1, 'tramo': e, 't_dep': td, 't_arr': ta})
    for e, c, (td, _) in zip(tramos, cargas, prog):
        if c > 0:
            q = (e[0], e[1], int(td // 24) + 1)
            remd[q] = remd.get(q, 0.0) - c
        if pend.get(e, 0) > 0:
            pend[e] -= 1
    av['t'] = prog[-1][1] + d['tat']['full_in_full_out'] + EPS_T     # colchón de 90 min en la base
    if av['t'] > av['fin']:
        av['mant_hecha'] = True


def _fase_principal(avs, remd, pend, d, mu, max_tramos, esperas):
    """Avanza en el tiempo: siempre decide el avión que queda libre antes. Para ese avión evalúa todas las rotaciones
    desde su base (y varias esperas antes de salir) y elige la de mayor (utilidad + MU * frecuencias pendientes
    cubiertas) por hora. Si ninguna tiene puntuación positiva, el avión termina su semana."""
    activos = set(avs)
    tat_cierre = d['tat']['full_in_full_out']
    while activos:
        k = min(activos, key=lambda k: (avs[k]['t'], -avs[k]['Q'], k))
        av = avs[k]
        mejor = None
        for tour in _rotaciones(av, d, max_tramos):
            cubre = len({e for e in tour if pend.get(e, 0) > 0})
            for w in esperas:
                t0 = av['t'] + w
                ev = _estimar(av, tour, t0, d, remd)
                if ev is None:
                    continue
                prog, cargas, util = ev
                puntaje = (util + mu * cubre) / (prog[-1][1] - av['t'] + tat_cierre)
                if puntaje > 0 and (mejor is None or puntaje > mejor[0] + 1e-9):
                    mejor = (puntaje, tour, cargas, prog)
        if mejor is None:
            # nada cabe antes de la ventana de mantenimiento: esperar a que termine y reintentar
            if not av['mant_hecha'] and av['t'] < av['fin']:
                av['t'], av['mant_hecha'] = av['fin'] + EPS_T, True
                continue
            activos.discard(k)
            continue
        _, tour, cargas, prog = mejor
        _agregar(av, tour, cargas, prog, remd, pend, d)


def _fase_rescate(avs, remd, pend, d):
    """Último recurso: si quedan frecuencias sin cubrir, programa recorridos de posicionamiento base ~> o, (o,d),
    d ~> base por el camino más corto (como en el caso base anterior), eligiendo el que cubre más por hora."""
    while any(v > 0 for v in pend.values()):
        mejor = None
        for av in sorted(avs.values(), key=lambda a: a['t']):
            Ek = d['Ek'][av['k']]
            for (o, dd_), f in pend.items():
                if f <= 0 or o in d['tec'] or dd_ in d['tec'] or (o, dd_) not in Ek:
                    continue
                ida, vuelta = _ruta(av, av['base'], o, d), _ruta(av, dd_, av['base'], d)
                if ida is None or vuelta is None:
                    continue
                tramos = ida + [(o, dd_)] + vuelta
                tmp, cubre = dict(pend), 0
                for e in tramos:
                    if tmp.get(e, 0) > 0:
                        tmp[e] -= 1
                        cubre += 1
                ev = _estimar(av, tramos, av['t'], d, remd)
                if ev is None or cubre == 0:
                    continue
                prog, cargas, _ = ev
                valor = cubre / (prog[-1][1] - av['t'] + d['tat']['full_in_full_out'])
                if mejor is None or valor > mejor[0] + 1e-12:
                    mejor = (valor, av, tramos, cargas, prog)
        if mejor is None:
            break
        _, av, tramos, cargas, prog = mejor
        _agregar(av, tramos, cargas, prog, remd, pend, d)
    return {e: f for e, f in pend.items() if f > 0}


def _nuevos_aviones(d):
    avs = {}
    for k in d['K']:
        base, ini, fin = d['mant'][k]
        avs[k] = dict(k=k, base=base, ini=ini, fin=fin, Q=d['payload'][k], legs=[], t=0.0, mant_hecha=False)
    return avs


def construir_caso_base(d, con_frecuencias=True, max_tramos=MAX_TRAMOS, mu=MU, esperas=None):
    """Devuelve ({avion: [ {pos, tramo, t_dep, t_arr}, ... ]}, frecuencias que no se pudieron cubrir).
    Una pasada con bono MU bajo (así las frecuencias casi no distorsionan la política de utilidad por hora) y, si
    quedan frecuencias pendientes, recorridos de posicionamiento de último recurso."""
    esperas = esperas or ESPERAS
    avs = _nuevos_aviones(d)
    remd = dict(d['dem_dia'])
    pend = dict(d['freq']) if con_frecuencias else {}
    _fase_principal(avs, remd, pend, d, mu if con_frecuencias else 0.0, max_tramos, esperas)
    sin_cubrir = {e: f for e, f in pend.items() if f > 0}
    if sin_cubrir:
        sin_cubrir = _fase_rescate(avs, remd, pend, d)
    itinerario = {k: av['legs'] for k, av in avs.items() if av['legs']}
    return itinerario, sin_cubrir


# ------------------------------------------------------------------------------------------ KPIs
def calcular_kpis(d, itinerario, v, res):
    demanda_total = sum(d['demanda'].values())
    ent = v['toneladas_entregadas']
    capacidad = sum(d['payload'][k] * len(ls) for k, ls in itinerario.items())
    a_bordo = sum(c['x'] for c in res['solucion']['cargas'])
    horas = sum(l['t_arr'] - l['t_dep'] for ls in itinerario.values() for l in ls)
    vuelos_od = {}
    for ls in itinerario.values():
        for l in ls:
            vuelos_od[l['tramo']] = vuelos_od.get(l['tramo'], 0) + 1
    pct = lambda a, b: round(100 * a / b, 1) if b else 0.0
    return {
        'validador_ok': v['ok'], 'violaciones': v['violaciones'][:30],
        'margen_usd': round(v['margen']), 'ingresos_usd': round(v['ingresos']),
        'costo_vuelos_usd': round(v['costo_vuelos']), 'handling_usd': round(v['handling']),
        'toneladas_entregadas': round(ent, 1), 'pct_demanda_servida': pct(ent, demanda_total),
        'vuelos': v['vuelos'], 'aviones_usados': len(itinerario), 'aviones_flota': len(d['K']),
        'horas_bloque': round(horas, 1),
        # factor de ocupación = toneladas a bordo (suma sobre vuelos) / capacidad ofrecida (payload x vuelos)
        'factor_ocupacion_pct': pct(a_bordo, capacidad),
        'ocupacion_entregada_pct': pct(ent, capacidad),
        'utilizacion_flota_pct': pct(horas, len(d['K']) * H),
        'utilizacion_usados_pct': pct(horas, len(itinerario) * H),
        'costo_por_tonelada_usd': round((v['costo_vuelos'] + v['handling']) / ent) if ent > 0 else None,
        'frecuencias_cumplidas': f"{sum(1 for e, f in d['freq'].items() if vuelos_od.get(e, 0) >= f)}/{len(d['freq'])}",
        'estado_solver_carga': res['estado'], 'gap_carga': res['gap'], 'ttr_h': acm.TTR_H,
        **res['estadisticas'],
    }


def detalle_vuelos(d, itinerario, res):
    """Carga a bordo, costo y número de vuelos por tramo (para el análisis del informe)."""
    carga = defaultdict(float)
    for c in res['solucion']['cargas']:
        carga[(c['k'], c['s'])] += c['x']
    filas = []
    for k, ls in itinerario.items():
        for l in ls:
            filas.append({'aeronave': k, 'operador': d['operador'][k], 'posicion': l['pos'],
                          'origen': l['tramo'][0], 'destino': l['tramo'][1],
                          'dia_salida': int(l['t_dep'] // 24) + 1,
                          'hora_salida': l['t_dep'], 'hora_llegada': l['t_arr'],
                          'carga_a_bordo_t': round(carga[(k, l['pos'])], 3), 'payload_t': d['payload'][k],
                          'costo_vuelo_usd': round(d['costo'][(k, l['tramo'])], 2)})
    return pd.DataFrame(filas)


def main():
    t0 = time.time()
    d = cargar_datos()
    itinerario, sin_cubrir = construir_caso_base(d, con_frecuencias=FRECUENCIAS)
    if sin_cubrir:
        print('ATENCIÓN: frecuencias que no se lograron programar:', sin_cubrir)
    print(f"Itinerario: {len(itinerario)} aviones, {sum(len(v) for v in itinerario.values())} vuelos, MU={MU:,.0f} "
          f"({time.time() - t0:.1f} s). Asignando carga...", flush=True)

    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    pd.DataFrame([{'aeronave': k, 'operador': d['operador'][k], 'posicion': l['pos'], 'origen': l['tramo'][0],
                   'destino': l['tramo'][1], 'hora_salida': l['t_dep'], 'hora_llegada': l['t_arr']}
                  for k, ls in itinerario.items() for l in ls]).to_csv(SALIDA + '_itinerario.csv', index=False)

    res = acm.asignar_carga_multitramos(itinerario)
    if not res['ok']:
        raise SystemExit(f"La asignación de carga no encontró solución: {res['estado']}")
    cfg = {'ciclico': True, 'tat_variable': True, 'min_ton_escala': True, 'mantenimiento': True,
           'frecuencias': FRECUENCIAS, 'demanda_diaria': True, 'dist_frecuencias': False,
           'transferencias': True, 'ttr_h': acm.TTR_H}
    v = validar(res['solucion'], cfg, ruta_raw=RAW, freq_exigibles=None if FRECUENCIAS else {})
    kpis = calcular_kpis(d, itinerario, v, res)
    kpis['frecuencias_sin_cubrir'] = {f'{o}-{dd}': f for (o, dd), f in sin_cubrir.items()}
    kpis['config'] = {'frecuencias_exigidas': FRECUENCIAS, 'max_tramos_rotacion': MAX_TRAMOS, 'mu_usd': MU,
                      'esperas_h': ESPERAS, 'solver': os.environ['SOLVER']}
    kpis['tiempo_total_s'] = round(time.time() - t0, 1)
    print(json.dumps(kpis, indent=2, ensure_ascii=False))
    with open(SALIDA + '_kpis.json', 'w', encoding='utf-8') as f:
        json.dump(kpis, f, indent=2, ensure_ascii=False)
    pd.DataFrame([kpis]).drop(columns=['violaciones', 'frecuencias_sin_cubrir', 'config']).to_csv(
        SALIDA + '_kpis.csv', index=False)
    detalle_vuelos(d, itinerario, res).to_csv(SALIDA + '_vuelos_detalle.csv', index=False)


if __name__ == '__main__':
    main()
