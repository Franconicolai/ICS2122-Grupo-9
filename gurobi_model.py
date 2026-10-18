"""
MILP integrado de Fleet Assignment y Cargo Routing (posición-indexado, tiempo continuo).

Representa el mismo problema que resuelve la heurística de red espacio-tiempo (src/), con las reglas que
revisa src/validador.py. Lo que cambia es la formulación: aquí el tiempo es continuo (t_dep, t_arr) y
cada avión recorre posiciones s = 1..S_max_k, eligiendo un tramo por posición (y[k,s,e]).

Reglas incluidas, iguales a las de la heurística:
  - tramos permitidos por derechos de tráfico y de hasta 9 h (Ek); payload propio de cada avión;
  - TAT de 70 min, 50 min si el vuelo llega a una escala técnica y 90 min si el avión llega y sale lleno;
  - rotación semanal cerrada en espacio y en tiempo; todos los vuelos dentro de la semana;
  - ventana de mantenimiento en tierra, en el aeropuerto asignado;
  - frecuencias mínimas semanales;
  - mínimo de 10 t por vuelo cargado (los vuelos ferry quedan exentos);
  - demanda diaria: la carga se embarca en su origen el día de la demanda y se entrega en su destino;
  - carga multitramo en el mismo avión (through);
  - costo real de vuelo (combustible + costo por hora del operador + tasa de aterrizaje) y handling.

Transbordos entre aviones: no están dentro del MILP. Con tiempo continuo requieren una binaria por cada par de
posiciones de aviones distintos (~350 mil) más su flujo por destino. Igual que en la heurística, se agregan
después con src/asignacion_carga_multitramos.py sobre los vuelos del MILP (ver warm_start_milp.py).

La formulación y su correspondencia con la heurística están en docs/README_milp_completo.md.
"""
import os
from collections import defaultdict
from datetime import datetime
from typing import Dict, Any

import gurobipy as gp
from gurobipy import GRB
import pandas as pd
import yaml

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'raw data')
S_MAX = 32            # posiciones por avión; la heurística usa como máximo 31 vuelos por avión en la semana
H = 168.0             # horas de la semana (lunes 00:00 a domingo 24:00, UTC)
DIAS = range(1, 8)    # día de salida del vuelo: 1 = lunes ... 7 = domingo
EPS_DIA = 1e-3        # una salida a las 24:00 exactas ya es del día siguiente, igual que int(t // 24) + 1
UMBRAL_LLENO = 0.95   # mismo supuesto que src/validador.py: un vuelo va "lleno" si lleva >= 95 % del payload


def cargar_datos(ruta_raw: str = RAW, s_max: int = S_MAX) -> Dict[str, Any]:
    """Conjuntos y parámetros del MILP, leídos de los mismos archivos que usa la heurística."""
    rd = lambda f: pd.read_csv(os.path.join(ruta_raw, f))
    ap, fl, lg = rd('airports.csv'), rd('fleet.csv'), rd('legs_catalog.csv')
    dw, dd, tr = rd('demand_weekly.csv'), rd('demand_daily.csv'), rd('traffic_rights.csv')
    cp, lf, mt = rd('cost_params.csv'), rd('landing_fees.csv'), rd('maintenance.csv')
    with open(os.path.join(ruta_raw, 'ops_rules.yaml'), encoding='utf-8') as f:
        reglas = yaml.safe_load(f)

    pais = dict(zip(ap.iata, ap.country))
    permitido = {(o, c) for o, c, v in zip(tr.operator, tr.country, tr.allowed) if v == 1}
    tec = set(reglas['technical_stop_airports'])
    tat = {k: v / 60.0 for k, v in reglas['tat_minutes'].items()}
    c = {(p, o): float(v) for p, o, v in zip(cp.param, cp.operator, cp.value)}
    fuel_h = c[('fuel_burn_gal_per_hour', 'ALL')] * c[('fuel_price_usd_per_gal', 'ALL')]
    fee = dict(zip(lf.airport, lf.fee_usd.astype(float)))

    # Flota y tramos factibles por avión (derechos de tráfico en ambos países y tramo de hasta 9 h)
    K = list(fl.aircraft_id)
    op = dict(zip(fl.aircraft_id, fl.operator))
    P_k = dict(zip(fl.aircraft_id, fl.payload_tons.astype(float)))
    tau_e = {(o, d): float(b) for o, d, b in zip(lg.origin, lg.dest, lg.block_hours)}
    E = list(tau_e)
    Ek = {k: [e for e in E if tau_e[e] <= float(reglas['max_continuous_block_hours'])
              and (op[k], pais[e[0]]) in permitido and (op[k], pais[e[1]]) in permitido] for k in K}

    # Costos: vuelo = (combustible + costo por hora del operador) * horas de bloque + tasa de aterrizaje
    c_ke = {(k, e): (fuel_h + c[('ex_fuel_usd_per_block_hour', op[k])]) * tau_e[e] + fee[e[1]]
            for k in K for e in Ek[k]}
    h_k = {k: c[('handling_usd_per_ton', op[k])] for k in K}

    # Commodities: (origen, destino, día) con demanda diaria positiva y tarifa por kg
    tarifa = {(o, d): float(r) for o, d, r in zip(dw.origin, dw.dest, dw.tariff_usd_per_kg)}
    columnas = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    D_q = {(r.origin, r.dest, dia): float(getattr(r, col))
           for r in dd.itertuples() for dia, col in zip(DIAS, columnas)
           if float(getattr(r, col)) > 1e-7 and (r.origin, r.dest) in tarifa}
    Q = list(D_q)
    r_q = {q: tarifa[q[0], q[1]] for q in Q}
    f_od = {(o, d): int(f) for o, d, f in zip(dw.origin, dw.dest, dw.min_weekly_freq) if f > 0}

    # Mantenimiento: (aeropuerto, inicio, fin) en horas desde el inicio de la semana
    inicio = datetime.fromisoformat(str(reglas['week_start']))
    mant = {}
    for r in mt.itertuples():
        h0 = (datetime.fromisoformat(r.start_datetime) - inicio).total_seconds() / 3600
        h1 = (datetime.fromisoformat(r.end_datetime) - inicio).total_seconds() / 3600
        if not 0 <= h0 <= h1 <= H:
            raise ValueError(f'{r.aircraft_id}: la ventana de mantenimiento debe quedar dentro de la semana')
        mant[r.aircraft_id] = (r.airport, h0, h1)

    return {
        'K': K, 'E': E, 'Q': Q, 'Ek': Ek, 'A': sorted(pais), 'op': op, 'P_k': P_k,
        'S_max_k': {k: s_max for k in K}, 'H': H, 'tau_e': tau_e, 'c_ke': c_ke, 'h_k': h_k,
        'D_q': D_q, 'r_q': r_q, 'f_od': f_od, 'mant': mant, 'tec': tec,
        'min_ton': float(reglas['min_tons_per_extra_stop']),
        # TAT por tramo según dónde llega el vuelo, y minutos extra si el avión llega y sale lleno
        'TAT_e': {e: tat['technical_stop'] if e[1] in tec else tat['default'] for e in E},
        'delta_lleno': max(0.0, tat['full_in_full_out'] - tat['default']),
        # carga máxima de un vuelo que no cuenta como lleno (mismo margen que asignacion_carga_multitramos.py)
        'seguro_k': {k: max(0.0, UMBRAL_LLENO * P_k[k] - 0.01) for k in K},
    }


def construir_modelo_completo(datos: Dict[str, Any], env: gp.Env = None, transbordos: bool = False) -> gp.Model:
    """Arma el MILP. Las variables quedan en modelo._v (diccionario de tupledicts) y los datos en modelo._datos.
    Con transbordos=True cada vuelo puede recibir (tin) y entregar (tout) carga a otros aviones; los pares de
    vuelos y sus reglas se agregan en milp_continuo_transbordos.py."""
    if env is None:
        env = gp.Env(empty=True)
        env.setParam("OutputFlag", 1)
        env.start()
    modelo = gp.Model("ICS2122_GRUPO_9_FLEET_ASSIGNMENT_Y_CARGO_ROUTING", env=env)

    # SETS Y PARÁMETROS
    K, Q, Ek, S_max_k = datos['K'], datos['Q'], datos['Ek'], datos['S_max_k']
    tau_e, TAT_e, delta = datos['tau_e'], datos['TAT_e'], datos['delta_lleno']
    D_q, r_q, c_ke, h_k = datos['D_q'], datos['r_q'], datos['c_ke'], datos['h_k']
    P_k, seguro_k, tec, min_ton = datos['P_k'], datos['seguro_k'], datos['tec'], datos['min_ton']
    M_TAT = H + max(TAT_e.values()) + delta   # basta para desactivar R11 cuando la posición siguiente no se usa
    M_CIERRE = max(TAT_e.values()) + delta    # basta para desactivar R12 en las posiciones que no son la última

    KS = [(k, s) for k in K for s in range(1, S_max_k[k] + 1)]
    KS_menos = [(k, s) for k in K for s in range(1, S_max_k[k])]
    KSE = [(k, s, e) for k in K for s in range(1, S_max_k[k] + 1) for e in Ek[k]]

    # Aeropuertos que toca cada avión y tramos que salen de / llegan a cada uno
    A_k = {k: sorted({a for e in Ek[k] for a in e}) for k in K}
    sale, llega = defaultdict(list), defaultdict(list)
    for k in K:
        for e in Ek[k]:
            sale[k, e[0]].append(e)
            llega[k, e[1]].append(e)

    # Sin transbordos, un avión solo puede llevar commodities que embarca en su origen y entrega en su destino;
    # con transbordos, embarca lo que sale de sus aeropuertos y puede llevar carga hacia cualquier destino
    if transbordos:
        Q_k = {k: [q for q in Q if sale[k, q[0]]] for k in K}
        D_k = {k: sorted({q[1] for q in Q}) for k in K}
    else:
        Q_k = {k: [q for q in Q if sale[k, q[0]] and llega[k, q[1]]] for k in K}
        D_k = {k: sorted({q[1] for q in Q_k[k]}) for k in K}
    O_k = {k: sorted({q[0] for q in Q_k[k]}) for k in K}
    Q_kd, Q_ko, Q_kdia = defaultdict(list), defaultdict(list), defaultdict(list)
    for k in K:
        for q in Q_k[k]:
            Q_kd[k, q[1]].append(q)
            Q_ko[k, q[0]].append(q)
            Q_kdia[k, q[2]].append(q)
    QKS = [(q, k, s) for k in K for s in range(1, S_max_k[k] + 1) for q in Q_k[k]]
    DKS = [(d, k, s) for k in K for s in range(1, S_max_k[k] + 1) for d in D_k[k]]
    DKS_menos = [(d, k, s) for d, k, s in DKS if s < S_max_k[k]]
    KS_q = defaultdict(list)
    for q, k, s in QKS:
        KS_q[q].append((k, s))

    # VARIABLES DE DECISIÓN
    # Aviones
    y = modelo.addVars(KSE, vtype=GRB.BINARY, name="y")                  # el avión k vuela el tramo e en la posición s
    u = modelo.addVars(KS, vtype=GRB.BINARY, name="u")                   # la posición s está en uso
    l = modelo.addVars(KS, vtype=GRB.BINARY, name="l")                   # s es la última posición usada
    t_dep = modelo.addVars(KS, lb=0, ub=H, name="t_dep")                 # hora de salida (h desde el lunes 00:00)
    t_arr = modelo.addVars(KS, lb=0, ub=H, name="t_arr")                 # hora de llegada
    dia = modelo.addVars([(k, s, d) for k, s in KS for d in DIAS], vtype=GRB.BINARY, name="dia")  # día de salida
    lc = modelo.addVars(KS, vtype=GRB.BINARY, name="lc")                 # el vuelo lleva carga (>= 10 t)
    ll = modelo.addVars(KS, vtype=GRB.BINARY, name="ll")                 # el vuelo puede ir lleno (> 95 % del payload)
    g = modelo.addVars(KS_menos, lb=0, ub=1, name="g")                   # TAT lleno-lleno entre s y s+1
    gc = modelo.addVars(KS, lb=0, ub=1, name="gc")                       # TAT lleno-lleno en el cierre (s -> 1)
    m0 = modelo.addVars(K, vtype=GRB.BINARY, name="m0")                  # mantenimiento antes del primer vuelo
    m = modelo.addVars(KS, vtype=GRB.BINARY, name="m")                   # mantenimiento después del vuelo s
    # Carga: el embarque es por commodity (origen, destino, día); lo que va a bordo se agrega por destino
    b = modelo.addVars(QKS, lb=0, ub=[min(D_q[q], P_k[k]) for q, k, s in QKS], name="b")  # embarque inicial
    x = modelo.addVars(DKS, lb=0, ub=[P_k[k] for d, k, s in DKS], name="x")  # a bordo con destino d
    a = modelo.addVars(DKS, lb=0, ub=[P_k[k] for d, k, s in DKS], name="a")  # entregado en d
    w = modelo.addVars(DKS_menos, lb=0, ub=[P_k[k] for d, k, s in DKS_menos], name="w")  # sigue en s+1 (through)
    s_q = modelo.addVars(Q, lb=0, ub=[D_q[q] for q in Q], name="s_q")      # toneladas servidas (R26c: s_q <= D_q)
    if transbordos:
        tin = modelo.addVars(DKS, lb=0, ub=[P_k[k] for d, k, s in DKS], name="tin")    # recibido de otro avión
        tout = modelo.addVars(DKS, lb=0, ub=[P_k[k] for d, k, s in DKS], name="tout")  # entregado a otro avión

    # Expresiones auxiliares por posición
    def llega_a(k, s, aerop):
        return gp.quicksum(y[k, s, e] for e in llega[k, aerop])

    def sale_de(k, s, aerop):
        return gp.quicksum(y[k, s, e] for e in sale[k, aerop])

    llega_tec = {(k, s): gp.quicksum(y[k, s, e] for e in Ek[k] if e[1] in tec) for k, s in KS}
    tat_ks = {(k, s): gp.quicksum(TAT_e[e] * y[k, s, e] for e in Ek[k]) for k, s in KS}

    # RESTRICCIONES DE AVIONES
    # R1: un tramo por posición utilizada
    modelo.addConstrs((gp.quicksum(y[k, s, e] for e in Ek[k]) == u[k, s] for k, s in KS), name="R1")

    # R2: las posiciones se ocupan sin huecos
    modelo.addConstrs((u[k, s+1] <= u[k, s] for k, s in KS_menos), name="R2")

    # R3a / R3b: identificación de la última posición usada
    modelo.addConstrs((l[k, s] == u[k, s] - u[k, s+1] for k, s in KS_menos), name="R3a")
    modelo.addConstrs((l[k, S_max_k[k]] == u[k, S_max_k[k]] for k in K), name="R3b")

    # R4: continuidad espacial: el vuelo s+1 sale del aeropuerto al que llegó el vuelo s
    modelo.addConstrs((llega_a(k, s, ap) >= sale_de(k, s+1, ap) for k, s in KS_menos for ap in A_k[k]),
                      name="R4_continuidad")

    # R4c: rotación cerrada en espacio: en la semana, cada aeropuerto recibe tantos vuelos del avión como despacha
    # (con R4, el último vuelo termina donde empezó el primero)
    modelo.addConstrs((gp.quicksum(y[k, s, e] for s in range(1, S_max_k[k] + 1) for e in llega[k, ap])
                       == gp.quicksum(y[k, s, e] for s in range(1, S_max_k[k] + 1) for e in sale[k, ap])
                       for k in K for ap in A_k[k]), name="R4c_ciclo")

    # R5: hora de llegada = hora de salida + duración del tramo
    modelo.addConstrs((t_arr[k, s] == t_dep[k, s] + gp.quicksum(tau_e[e] * y[k, s, e] for e in Ek[k])
                       for k, s in KS), name="R5")

    # R6: día de salida del vuelo (define qué demanda diaria puede embarcar); una posición sin uso sale en t = 0
    modelo.addConstrs((dia.sum(k, s, '*') == u[k, s] for k, s in KS), name="R6_dia")
    modelo.addConstrs((t_dep[k, s] >= gp.quicksum(24 * (d - 1) * dia[k, s, d] for d in DIAS) for k, s in KS),
                      name="R6_dia_ini")
    modelo.addConstrs((t_dep[k, s] <= gp.quicksum((24 * d - EPS_DIA) * dia[k, s, d] for d in DIAS) for k, s in KS),
                      name="R6_dia_fin")

    # R6b: la llegada ocurre dentro de la semana y solo si la posición está en uso
    modelo.addConstrs((t_arr[k, s] <= H * u[k, s] for k, s in KS), name="R6b")

    # R11: TAT entre vuelos consecutivos: 70 min, 50 si llega a escala técnica y 90 si llega y sale lleno
    modelo.addConstrs((g[k, s] >= ll[k, s] + ll[k, s+1] - 1 - llega_tec[k, s] for k, s in KS_menos), name="R11_lleno")
    modelo.addConstrs((t_dep[k, s+1] >= t_arr[k, s] + tat_ks[k, s] + delta * g[k, s] - M_TAT * (1 - u[k, s+1])
                       for k, s in KS_menos), name="R11_TAT")

    # R12: cierre temporal: el primer vuelo de la semana siguiente respeta el TAT después del último vuelo
    modelo.addConstrs((gc[k, s] >= ll[k, s] + ll[k, 1] - 1 - llega_tec[k, s] - (1 - l[k, s]) for k, s in KS),
                      name="R12_lleno")
    modelo.addConstrs((t_dep[k, 1] + H >= t_arr[k, s] + tat_ks[k, s] + delta * gc[k, s] - M_CIERRE * (1 - l[k, s])
                       for k, s in KS), name="R12_cierre")

    # R13 (desigualdad válida): los tramos y sus TAT mínimos caben en las 168 h de la rotación
    modelo.addConstrs((gp.quicksum((tau_e[e] + TAT_e[e]) * y[k, s, e]
                                   for s in range(1, S_max_k[k] + 1) for e in Ek[k]) <= H for k in K),
                      name="R13_tiempo")

    # R14: mantenimiento: el avión queda en tierra, en su aeropuerto, durante toda la ventana [ini, fin]
    #   m0[k] = 1: la ventana ocurre antes del primer vuelo (parte la semana en el aeropuerto de mantenimiento)
    #   m[k,s] = 1: la ventana ocurre después del vuelo s, que llega al aeropuerto de mantenimiento
    for k in K:
        ap_m, ini, fin = datos['mant'][k]
        modelo.addConstr(m0[k] + m.sum(k, '*') >= u[k, 1], name=f"R14_mant[{k}]")
        modelo.addConstr(m0[k] <= sale_de(k, 1, ap_m), name=f"R14_mant0_aerop[{k}]")
        modelo.addConstr(t_dep[k, 1] >= fin * m0[k], name=f"R14_mant0_hora[{k}]")
        for s in range(1, S_max_k[k] + 1):
            modelo.addConstr(m[k, s] <= llega_a(k, s, ap_m), name=f"R14_mant_aerop[{k},{s}]")
            modelo.addConstr(t_arr[k, s] <= ini + (H - ini) * (1 - m[k, s]), name=f"R14_mant_llegada[{k},{s}]")
            if s < S_max_k[k]:   # si s es el último vuelo, el avión queda en tierra hasta el fin de la semana
                modelo.addConstr(t_dep[k, s+1] >= fin * (m[k, s] - l[k, s]), name=f"R14_mant_salida[{k},{s}]")

    # R15: frecuencias mínimas semanales por par origen-destino
    for od, f in datos['f_od'].items():
        modelo.addConstr(gp.quicksum(y[k, s, od] for k in K if od in sale[k, od[0]]
                                     for s in range(1, S_max_k[k] + 1)) >= f, name=f"R15_frecuencia[{od[0]},{od[1]}]")

    # RESTRICCIONES DE CARGA
    # R7: mínimo de 10 t por vuelo cargado (un vuelo ferry, sin carga, queda exento)
    modelo.addConstrs((x.sum('*', k, s) >= min_ton * lc[k, s] for k, s in KS), name="R7_min_ton")

    # R8: capacidad; si el vuelo no se marca como lleno (ll = 0) lleva a lo más el 95 % del payload
    modelo.addConstrs((x.sum('*', k, s) <= seguro_k[k] * lc[k, s] + (P_k[k] - seguro_k[k]) * ll[k, s]
                       for k, s in KS), name="R8_capacidad")
    modelo.addConstrs((ll[k, s] <= lc[k, s] for k, s in KS), name="R8_lleno")
    modelo.addConstrs((lc[k, s] <= u[k, s] for k, s in KS), name="R8_uso")

    # R20: balance al entrar: lo que va a bordo hacia d = embarque inicial con destino d + lo que sigue del vuelo s-1
    #      (+ lo recibido de otro avión, si hay transbordos)
    modelo.addConstrs((x[d, k, s] == gp.quicksum(b[q, k, s] for q in Q_kd[k, d]) + (w[d, k, s-1] if s > 1 else 0)
                       + (tin[d, k, s] if transbordos else 0) for d, k, s in DKS), name="R20_entrada")

    # R21: balance al salir: se entrega en el destino o sigue en el vuelo s+1 del mismo avión
    #      (o se entrega a otro avión, si hay transbordos)
    modelo.addConstrs((x[d, k, s] == a[d, k, s] + (w[d, k, s] if s < S_max_k[k] else 0)
                       + (tout[d, k, s] if transbordos else 0) for d, k, s in DKS), name="R21_salida")

    # R23: solo se embarca carga en un vuelo que sale del origen del commodity
    modelo.addConstrs((gp.quicksum(b[q, k, s] for q in Q_ko[k, o]) <= P_k[k] * sale_de(k, s, o)
                       for k, s in KS for o in O_k[k]), name="R23_origen")

    # R24: solo se embarca la demanda del día en que sale el vuelo
    modelo.addConstrs((gp.quicksum(b[q, k, s] for q in Q_kdia[k, d]) <= P_k[k] * dia[k, s, d]
                       for k, s in KS for d in DIAS if Q_kdia[k, d]), name="R24_dia")

    # R25: solo se entrega carga en un vuelo que llega al destino
    modelo.addConstrs((a[d, k, s] <= P_k[k] * llega_a(k, s, d) for d, k, s in DKS), name="R25_destino")

    # R26a: cantidad servida = total embarcado
    modelo.addConstrs((s_q[q] == gp.quicksum(b[q, k, s] for k, s in KS_q[q]) for q in Q), name="R26a")

    # R26b: lo entregado en cada destino = lo servido con ese destino
    destinos = sorted({q[1] for q in Q})
    modelo.addConstrs((gp.quicksum(s_q[q] for q in Q if q[1] == d) == a.sum(d, '*', '*') for d in destinos),
                      name="R26b")

    # FUNCIÓN OBJETIVO: margen neto semanal
    ingresos = gp.quicksum(1000 * r_q[q] * s_q[q] for q in Q)
    costo_vuelos = gp.quicksum(c_ke[k, e] * y[k, s, e] for k, s, e in KSE)
    handling = gp.quicksum(h_k[k] * b[q, k, s] for q, k, s in QKS)

    modelo.setObjective(ingresos - costo_vuelos - handling, GRB.MAXIMIZE)
    modelo.update()

    modelo._v = {'y': y, 'u': u, 'l': l, 't_dep': t_dep, 't_arr': t_arr, 'dia': dia, 'lc': lc, 'll': ll,
                 'g': g, 'gc': gc, 'm0': m0, 'm': m, 'b': b, 'x': x, 'a': a, 'w': w, 's_q': s_q}
    if transbordos:
        modelo._v.update(tin=tin, tout=tout)
    modelo._datos = datos
    return modelo
