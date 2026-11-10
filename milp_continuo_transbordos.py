"""
MILP completo con tiempo continuo y transbordos entre aviones dentro del modelo.

Parte del MILP de gurobi_model.py (posiciones por avión, tiempo continuo, todas las reglas del validador) y le
agrega los transbordos de forma exacta, con las mismas reglas del paso final de la heurística: cambio de avión, mismo
aeropuerto, fuera de escala técnica, ambos operadores con soporte (interchange_airports.csv), salida del vuelo que
recibe al menos TTR_H después del aterrizaje del que entrega, y handling cobrado al operador que recibe.

Cada transbordo posible es un par (vuelo que entrega -> vuelo que recibe) con su flujo por destino. Si ambos aviones
son libres, el par necesita una binaria por aeropuerto y una restricción de tiempo con M grande. Por eso el modelo
crece con el cuadrado de las posiciones de los aviones libres, y el parámetro --libres dice qué aviones decide:
    --libres AC-06,AC-10    solo esos aviones (el resto queda con los vuelos y horarios de la corrida)
    --libres BRA            todos los aviones de un operador; se pueden combinar con comas
    --libres ninguno        vuelos fijos: solo carga y transbordos (control: reproduce el paso final de la heurística)
    --libres todos          problema completo (~10 millones de binarias de pares: no cabe en un computador normal;
                            se arma solo con --forzar)

La solución de la corrida (por defecto, la de 2 h) se inyecta completa como MIP start, con sus transbordos.

Uso, desde la raíz del repositorio:
    python milp_continuo_transbordos.py --libres AC-06,AC-10 --tiempo 1800 --norel 1200
    python milp_continuo_transbordos.py --libres todos --estimar        # solo el tamaño
    python milp_continuo_transbordos.py --help
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict

import pandas as pd

import warm_start_milp as ws   # deja la raíz como directorio de trabajo y agrega src/ (submodelo de carga y validador)
import gurobi_model as gm
import gurobipy as gp
from gurobipy import GRB

from modelo_integrado_espacio_tiempo import interpretar_libres

LIMITE_BINARIAS = 2_000_000    # sobre esto el modelo no cabe en un computador de 8 GB; se exige --forzar


def preparar(datos, itinerario, libres, ttr):
    """Datos de los transbordos: soporte, aeropuertos por avión y vuelos de los aviones fijos."""
    inter = pd.read_csv(os.path.join(gm.RAW, 'interchange_airports.csv'))
    ops = sorted(set(datos['op'].values()))
    sop = {(r.airport, o) for r in inter.itertuples() for o in ops if getattr(r, o) == 1}
    op, tec = datos['op'], datos['tec']
    if datos.get('interchange'):
        # un vuelo fijo (s1/s2 = su posición) mantiene la filial que lo opera en la solución de partida; un vuelo
        # libre puede quedar operado por cualquier filial habilitada en el aeropuerto (relajación)
        con_soporte = {a for a, _ in sop}
        op_vuelo = {(k, l['pos']): l.get('op') or op[k] for k, legs in itinerario.items() for l in legs}
        lado = lambda a, k, s: (a, op_vuelo[k, s]) in sop if s is not None else a in con_soporte
        if datos.get('filiales_exactas'):   # modelo exacto: la filial de cada vuelo es variable, el soporte se exige
            lado = lambda a, k, s: a in con_soporte      # en agregar_transbordos con f[k,s,o]
        ok = lambda a, k1, k2, s1=None, s2=None: a not in tec and lado(a, k1, s1) and lado(a, k2, s2)
    else:
        ok = lambda a, k1, k2, s1=None, s2=None: a not in tec and (a, op[k1]) in sop and (a, op[k2]) in sop
    llegan = {k: {e[1] for e in datos['Ek'][k]} for k in datos['K']}
    salen = {k: {e[0] for e in datos['Ek'][k]} for k in datos['K']}
    fijos = [(k, l['pos'], l['tramo'][0], l['tramo'][1], l['t_dep'], l['t_arr'])
             for k, legs in itinerario.items() if k not in libres for l in legs]
    return ok, llegan, salen, fijos


def enumerar_pares(datos, itinerario, libres, ttr, solo_contar=False):
    """Pares (vuelo que entrega i -> vuelo que recibe j) de aviones distintos donde puede haber transbordo.
    Tipos: FF fijo->fijo (factibilidad conocida), LF libre->fijo, FL fijo->libre, LL libre->libre."""
    ok, llegan, salen, fijos = preparar(datos, itinerario, libres, ttr)
    S, H = datos['S_max_k'], gm.H
    pares, cuenta = [], defaultdict(int)
    salidas_fijas = defaultdict(list)
    for f in fijos:
        salidas_fijas[f[2]].append(f)

    def nuevo(tipo, i, j, aps, t_i=None, t_j=None):
        cuenta[tipo] += 1
        cuenta['binarias'] += 0 if tipo == 'FF' else len(aps)
        if not solo_contar:
            pares.append({'tipo': tipo, 'i': i, 'j': j, 'aps': aps, 't_i': t_i, 't_j': t_j})

    for k, s, o, d, td, ta in fijos:                                     # fijo -> fijo
        for k2, s2, o2, d2, td2, ta2 in salidas_fijas.get(d, []):
            if k2 != k and ok(d, k, k2, s, s2) and td2 >= ta + ttr - 1e-9:
                nuevo('FF', (k, s), (k2, s2), [d])
    for k in sorted(libres):
        for k2, s2, o2, d2, td2, ta2 in fijos:                            # libre -> fijo (el fijo sale de o2)
            if o2 in llegan[k] and ok(o2, k, k2, None, s2) and td2 - ttr > 0:
                for s in range(1, S[k] + 1):
                    nuevo('LF', (k, s), (k2, s2), [o2], t_j=td2)
        for k1, s1, o1, d1, td1, ta1 in fijos:                            # fijo -> libre (el fijo llega a d1)
            if d1 in salen[k] and ok(d1, k1, k, s1) and ta1 + ttr < H:
                for s in range(1, S[k] + 1):
                    nuevo('FL', (k1, s1), (k, s), [d1], t_i=ta1)
        for k2 in sorted(libres):                                         # libre -> libre
            if k2 == k:
                continue
            aps = sorted(a for a in llegan[k] & salen[k2] if ok(a, k, k2))
            if aps:
                for s in range(1, S[k] + 1):
                    for s2 in range(1, S[k2] + 1):
                        nuevo('LL', (k, s), (k2, s2), aps)
    return pares, dict(cuenta)


def enumerar_pares_posiciones(datos, itinerario, libres, cotas, ttr):
    """Como enumerar_pares, pero los libres son posiciones (k, s) y no aviones completos (vecindarios por ventana de
    tiempo). cotas[k] = (lo, hi): las posiciones libres de k salen después de lo (llegada del vuelo fijo anterior) y
    antes de hi (salida del vuelo fijo siguiente); los pares que no caben en ese intervalo se descartan."""
    ok, llegan, salen, _ = preparar(datos, itinerario, set(), ttr)
    fijos = [(k, l['pos'], l['tramo'][0], l['tramo'][1], l['t_dep'], l['t_arr'])
             for k, legs in itinerario.items() for l in legs if (k, l['pos']) not in libres]
    pares, cuenta = [], defaultdict(int)
    salidas_fijas = defaultdict(list)
    for f in fijos:
        salidas_fijas[f[2]].append(f)

    def nuevo(tipo, i, j, aps, t_i=None, t_j=None):
        cuenta[tipo] += 1
        cuenta['binarias'] += 0 if tipo == 'FF' else len(aps)
        pares.append({'tipo': tipo, 'i': i, 'j': j, 'aps': aps, 't_i': t_i, 't_j': t_j})

    for k, s, o, d, td, ta in fijos:                                     # fijo -> fijo
        for k2, s2, o2, d2, td2, ta2 in salidas_fijas.get(d, []):
            if k2 != k and ok(d, k, k2, s, s2) and td2 >= ta + ttr - 1e-9:
                nuevo('FF', (k, s), (k2, s2), [d])
    libres = sorted(libres)
    for k, s in libres:
        lo, hi = cotas[k]
        for k2, s2, o2, d2, td2, ta2 in fijos:                            # libre -> fijo
            if k2 != k and o2 in llegan[k] and ok(o2, k, k2, None, s2) and td2 - ttr > lo:
                nuevo('LF', (k, s), (k2, s2), [o2], t_j=td2)
        for k1, s1, o1, d1, td1, ta1 in fijos:                            # fijo -> libre
            if k1 != k and d1 in salen[k] and ok(d1, k1, k, s1) and ta1 + ttr < hi:
                nuevo('FL', (k1, s1), (k, s), [d1], t_i=ta1)
        for k2, s2 in libres:                                             # libre -> libre
            if k2 != k and cotas[k][0] + ttr < cotas[k2][1]:
                aps = sorted(a for a in llegan[k] & salen[k2] if ok(a, k, k2))
                if aps:
                    nuevo('LL', (k, s), (k2, s2), aps)
    return pares, dict(cuenta)


def enumerar_pares_flexibles(datos, itinerario, info, ttr):
    """Pares de transbordo para las ventanas con desplazamiento. info[(k, s)] = (alternativas, salida_min, salida_max,
    llegada_min) de cada posición libre: alternativas es None si la posición puede tomar cualquier tramo y horario
    (vuelo de la ventana), o la lista de vuelos (tramo, salida, llegada) que puede ocupar (vuelo posterior a la ventana,
    que se corre de posición sin cambiar tramo ni horario).
    Tipos: FF fijo->fijo; SS cuando ninguno de los dos vuelos es de la ventana y toda combinación de alternativas que
    calza en el aeropuerto cumple el TTR (sin binaria: basta que los tramos calcen); LF, FL y LL como en enumerar_pares,
    con binaria y TTR por horarios variables. Los pares que no caben en los aeropuertos u horarios se descartan."""
    ok, llegan, salen, _ = preparar(datos, itinerario, set(), ttr)
    alt = {}                                          # alternativas (tramo, salida, llegada) o None, por vuelo
    for k, legs in itinerario.items():
        for l in legs:
            if (k, l['pos']) not in info:
                alt[k, l['pos']] = [(tuple(l['tramo']), l['t_dep'], l['t_arr'])]
    for p, (cands, _, _, _) in info.items():
        alt[p] = cands
    libre = lambda p: p in info
    llega = {p: (llegan[p[0]] if c is None else {e[1] for e, _, _ in c}) for p, c in alt.items()}
    sale = {p: (salen[p[0]] if c is None else {e[0] for e, _, _ in c}) for p, c in alt.items()}
    t_llega_min = {p: (info[p][3] if c is None else min(ta for _, _, ta in c)) for p, c in alt.items()}
    t_sale_max = {p: (info[p][2] if c is None else max(td for _, td, _ in c)) for p, c in alt.items()}
    salen_de = defaultdict(list)
    for p in alt:
        for a in sale[p]:
            salen_de[a].append(p)
    pares, cuenta = [], defaultdict(int)

    def nuevo(tipo, i, j, aps, t_i=None, t_j=None, **extra):
        cuenta[tipo] += 1
        cuenta['binarias'] += 0 if tipo in ('FF', 'SS') else len(aps)
        pares.append(dict({'tipo': tipo, 'i': i, 'j': j, 'aps': aps, 't_i': t_i, 't_j': t_j}, **extra))

    for i in sorted(alt):
        k = i[0]
        por_j = defaultdict(list)                     # aeropuertos donde i puede entregar a j
        for a in sorted(llega[i]):
            for j in salen_de.get(a, []):
                if (j[0] != k and ok(a, k, j[0], None if libre(i) else i[1], None if libre(j) else j[1])
                        and t_llega_min[i] + ttr <= t_sale_max[j] + 1e-9):
                    por_j[j].append(a)
        for j, aps in por_j.items():
            ci, cj = alt[i], alt[j]
            if not libre(i) and not libre(j):
                nuevo('FF', i, j, aps)
                continue
            if ci is not None and cj is not None and all(   # horarios conocidos que cumplen el TTR
                    max(ta for e, _, ta in ci if e[1] == a) + ttr <= min(td for e, td, _ in cj if e[0] == a) + 1e-9
                    for a in aps):
                for a in aps:
                    nuevo('SS', i, j, [a], i_libre=libre(i), j_libre=libre(j))
            elif libre(i) and libre(j):
                nuevo('LL', i, j, aps)
            elif libre(i):
                nuevo('LF', i, j, aps, t_j=cj[0][1])
            else:
                nuevo('FL', i, j, aps, t_i=ci[0][2])
    return pares, dict(cuenta)


def agregar_desplazamientos(modelo, sufijos):
    """Ventanas con desplazamiento: los vuelos posteriores a la ventana de cada avión mantienen tramo y horario, pero
    todos pueden correrse delta posiciones (dmin <= delta <= dmax), para que la ventana gane o pierda vuelos.
    sufijos[k] = {'b': última posición de la ventana, 'n': vuelos del avión, 'dmin', 'dmax', 'vuelos': [(p, tramo,
    salida)]}. Devuelve z[k, delta] (binaria: el avión k usa el desplazamiento delta)."""
    v = modelo._v
    y, u, t_dep = v['y'], v['u'], v['t_dep']
    S, Ek = modelo._datos['S_max_k'], modelo._datos['Ek']
    z = {}
    for k, sf in sufijos.items():
        deltas = range(sf['dmin'], sf['dmax'] + 1)
        for dl in deltas:
            z[k, dl] = modelo.addVar(vtype=GRB.BINARY, name=f'z_desp[{k},{dl}]')
        modelo.addConstr(gp.quicksum(z[k, dl] for dl in deltas) == 1, name=f'desp_uno[{k}]')
        posibles = defaultdict(set)                  # tramos que puede tomar cada posición que solo es de sufijo
        for p, e, td in sf['vuelos']:
            for dl in deltas:
                modelo.addConstr(y[k, p + dl, e] >= z[k, dl], name=f'desp_tramo[{k},{p},{dl}]')
                modelo.addConstr(t_dep[k, p + dl] >= td - gm.H * (1 - z[k, dl]), name=f'desp_ini[{k},{p},{dl}]')
                modelo.addConstr(t_dep[k, p + dl] <= td + gm.H * (1 - z[k, dl]), name=f'desp_fin[{k},{p},{dl}]')
                posibles[p + dl].add(e)
        for dl in deltas:
            if sf['n'] + dl + 1 <= S[k]:
                modelo.addConstr(u[k, sf['n'] + dl + 1] <= 1 - z[k, dl], name=f'desp_fin_rot[{k},{dl}]')
        for s, tramos in posibles.items():
            if s > sf['b'] + sf['dmax']:             # posición que no puede ser de la ventana
                for e in Ek[k]:
                    if e not in tramos:
                        y[k, s, e].UB = 0
    modelo.update()
    return z


def fijar_posiciones(modelo, st, libres):
    """Como fijar_aviones, pero libera solo las posiciones (k, s) de `libres`. La marca de última posición (l) y el
    mantenimiento después del vuelo s (m) quedan libres también si la posición siguiente es libre."""
    v = modelo._v
    for nombre in ('y', 'u', 'l', 't_dep', 't_arr', 'dia', 'm0', 'm'):
        for clave, var in v[nombre].items():
            if nombre == 'm0':
                libre = (clave, 1) in libres
            else:
                k, s = clave[0], clave[1]
                libre = (k, s) in libres or (nombre in ('l', 'm') and (k, s + 1) in libres)
            if not libre:
                var.LB = var.UB = st.get(var, 0.0)
    modelo.update()


def agregar_transbordos(modelo, datos, pares, ttr):
    """Variables y restricciones de los transbordos sobre el MILP de gurobi_model.py (con transbordos=True)."""
    v = modelo._v
    y, t_dep, t_arr, tin, tout = v['y'], v['t_dep'], v['t_arr'], v['tin'], v['tout']
    P, h_k, Ek = datos['P_k'], datos['h_k'], datos['Ek']
    dests = sorted({q[1] for q in datos['Q']})
    M = gm.H + ttr
    import gurobipy as gp

    def llega_a(k, s, a):
        return gp.quicksum(y[k, s, e] for e in Ek[k] if e[1] == a)

    def sale_de(k, s, a):
        return gp.quicksum(y[k, s, e] for e in Ek[k] if e[0] == a)

    # flujo transbordado por destino; handling al operador que recibe (con filiales exactas, el handling lo cobra
    # gurobi_model con la filial del vuelo que recibe: Bo incluye tin)
    exacto = getattr(modelo, '_filiales_exactas', False)
    claves_tr = [(p, d) for p in range(len(pares)) for d in dests]
    tr = modelo.addVars(claves_tr, lb=0,
                        ub=[min(P[pares[p]['i'][0]], P[pares[p]['j'][0]]) for p, d in claves_tr],
                        obj=[0 if exacto else -h_k[pares[p]['j'][0]] for p, d in claves_tr], name='tr')
    claves_c = [(p, a) for p, par in enumerate(pares) if par['tipo'] not in ('FF', 'SS') for a in par['aps']]
    c = modelo.addVars(claves_c, vtype=GRB.BINARY, name='c')
    if exacto:
        f, sop, ops = v['f'], datos['sop'], sorted(datos['exf'])
    for p, par in enumerate(pares):
        (k, s), (k2, s2) = par['i'], par['j']
        cap = min(P[k], P[k2])
        if par['tipo'] == 'SS':                  # horarios conocidos que cumplen el TTR: basta que los tramos calcen
            a = par['aps'][0]
            if par['i_libre']:
                modelo.addConstr(gp.quicksum(tr[p, d] for d in dests) <= cap * llega_a(k, s, a), name=f'T_ss_i[{p}]')
            if par['j_libre']:
                modelo.addConstr(gp.quicksum(tr[p, d] for d in dests) <= cap * sale_de(k2, s2, a), name=f'T_ss_j[{p}]')
            continue
        activo = 1 if par['tipo'] == 'FF' else gp.quicksum(c[p, a] for a in par['aps'])
        modelo.addConstr(gp.quicksum(tr[p, d] for d in dests) <= cap * activo, name=f'T_cap[{p}]')
        if par['tipo'] in ('LF', 'LL'):          # el vuelo libre que entrega llega a ese aeropuerto
            for a in par['aps']:
                modelo.addConstr(c[p, a] <= llega_a(k, s, a), name=f'T_llega[{p},{a}]')
        if par['tipo'] in ('FL', 'LL'):          # el vuelo libre que recibe sale de ese aeropuerto
            for a in par['aps']:
                modelo.addConstr(c[p, a] <= sale_de(k2, s2, a), name=f'T_sale[{p},{a}]')
        if par['tipo'] == 'LF':                  # TTR_H: aterrizaje del libre + TTR <= salida del fijo
            modelo.addConstr(t_arr[k, s] + ttr <= par['t_j'] + M * (1 - activo), name=f'T_ttr[{p}]')
        elif par['tipo'] == 'FL':
            modelo.addConstr(t_dep[k2, s2] >= par['t_i'] + ttr - M * (1 - activo), name=f'T_ttr[{p}]')
        elif par['tipo'] == 'LL':
            modelo.addConstr(t_dep[k2, s2] >= t_arr[k, s] + ttr - M * (1 - activo), name=f'T_ttr[{p}]')
        if exacto:   # las filiales que operan ambos vuelos están habilitadas en el aeropuerto del transbordo
            for a in par['aps']:
                for kk, ss, lado in ((k, s, 'i'), (k2, s2, 'j')):
                    sin = [o for o in ops if (a, o) not in sop]
                    if not sin:
                        continue
                    if par['tipo'] in ('FF', 'SS'):
                        modelo.addConstr(gp.quicksum(tr[p, d] for d in dests) <= cap * (1 - gp.quicksum(
                            f[kk, ss, o] for o in sin)), name=f'T_soporte_{lado}[{p},{a}]')
                    else:
                        modelo.addConstr(c[p, a] <= 1 - gp.quicksum(f[kk, ss, o] for o in sin),
                                         name=f'T_soporte_{lado}[{p},{a}]')
    # lo entregado y recibido por cada vuelo es la suma de sus transbordos
    sale_p, entra_p = defaultdict(list), defaultdict(list)
    for p, par in enumerate(pares):
        sale_p[par['i']].append(p)
        entra_p[par['j']].append(p)
    modelo.addConstrs((tout[d, k, s] == gp.quicksum(tr[p, d] for p in sale_p.get((k, s), []))
                       for d, k, s in tout.keys()), name='T_entrega')
    modelo.addConstrs((tin[d, k, s] == gp.quicksum(tr[p, d] for p in entra_p.get((k, s), []))
                       for d, k, s in tin.keys()), name='T_recibe')
    modelo.update()
    modelo._v.update(tr=tr, c=c)
    return tr, c


def armar_start(modelo, datos, itinerario, solucion, pares):
    """Start completo: vuelos, carga y transbordos de la corrida (paso final de la heurística con transbordos)."""
    st = ws.armar_start(modelo, itinerario, solucion['cargas'])
    v = modelo._v
    for r in solucion['cargas']:
        d, k, s = r['q'][1], r['k'], int(r['s'])
        if r.get('transfer_in', 0) > 1e-12:
            st[v['tin'][d, k, s]] += r['transfer_in']
        if r.get('transfer_out', 0) > 1e-12:
            st[v['tout'][d, k, s]] += r['transfer_out']
    par_de = {(par['i'], par['j'], a): p for p, par in enumerate(pares) for a in par['aps']}
    for t in solucion['transferencias']:
        clave = ((t['from_k'], int(t['from_s'])), (t['to_k'], int(t['to_s'])), t['airport'])
        if clave not in par_de:
            raise ValueError(f'el transbordo {t} no está entre los pares del modelo')
        p = par_de[clave]
        st[v['tr'][p, tuple(t['q'])[1]]] += t['tons']
        if pares[p]['tipo'] not in ('FF', 'SS'):
            st[v['c'][p, t['airport']]] = 1
    return st


def extraer_solucion(modelo, pares, ttr, redondeo=1e-6):
    """Solución del MILP tal cual, en el formato de src/validador.py (vuelos con su filial, carga por commodity y
    transbordos), sin volver a resolver la carga. El MILP agrega la carga por destino; para cada destino, los vuelos se
    recorren en orden de salida (el through va al vuelo siguiente y un transbordo a un vuelo que sale después, así que
    es un grafo acíclico) y lo que entrega cada vuelo se toma de su inventario por commodity, primero el commodity con
    más toneladas. Los flujos se redondean a `redondeo` t para no arrastrar ruido numérico de Gurobi."""
    v, datos = modelo._v, modelo._datos
    val = lambda td: {kk: round(x / redondeo) * redondeo for kk, x in modelo.getAttr('X', td).items()
                      if x > redondeo / 2}
    itinerario = ws.extraer_itinerario(modelo)
    if 'f' in v:
        fv = modelo.getAttr('X', v['f'])
        for k, legs in itinerario.items():
            for leg in legs:
                o = max(sorted(datos['exf']), key=lambda o: fv[k, leg['pos'], o])
                if o != datos['op'][k]:
                    leg['op'] = o
    bv, av, wv = val(v['b']), val(v['a']), val(v['w'])
    trv = val(v['tr']) if 'tr' in v else {}
    t_dep = {(k, l['pos']): l['t_dep'] for k, legs in itinerario.items() for l in legs}
    destino_aerop = {(k, l['pos']): l['tramo'][1] for k, legs in itinerario.items() for l in legs}
    entra_b, sale_tr = defaultdict(dict), defaultdict(list)
    for (q, k, s), t in bv.items():
        entra_b[k, s][q] = t
    for (p, d), t in trv.items():
        sale_tr[pares[p]['i'], d].append((pares[p]['j'], t))
    cargas, transferencias = {}, []
    recibe = defaultdict(lambda: defaultdict(float))    # (vuelo, destino) -> {q: toneladas} por through o transbordo
    for k, s in sorted(t_dep, key=lambda ks: (t_dep[ks], ks)):
        for d in {q[1] for q in entra_b[k, s]} | {dd for (ks, dd) in list(recibe) if ks == (k, s)} \
                | {dd for (i, dd) in sale_tr if i == (k, s)} | {dd for (dd, kk, ss) in av if (kk, ss) == (k, s)}:
            inv = defaultdict(float)
            for q, t in entra_b[k, s].items():
                if q[1] == d:
                    inv[q] += t
            for q, t in recibe[(k, s), d].items():
                inv[q] += t
            for q, t in inv.items():
                r = cargas.setdefault((k, s, q), {'q': list(q), 'k': k, 's': s, 'b': 0.0, 'x': 0.0, 'a': 0.0, 'w': 0.0})
                r['b'] = entra_b[k, s].get(q, 0.0) if q[1] == d else 0.0
                r['x'] = t

            def tomar(cantidad):   # saca `cantidad` del inventario, primero el commodity con más toneladas
                partes = []
                for q in sorted(inv, key=lambda q: -inv[q]):
                    if cantidad <= 1e-12:
                        break
                    t = min(inv[q], cantidad)
                    if t > 1e-12:
                        partes.append((q, t))
                        inv[q] -= t
                        cantidad -= t
                return partes

            for q, t in tomar(av.get((d, k, s), 0.0)):
                cargas[k, s, q]['a'] += t
            for q, t in tomar(wv.get((d, k, s), 0.0)):
                cargas[k, s, q]['w'] += t
                recibe[(k, s + 1), d][q] += t
            for j, ton in sale_tr.get(((k, s), d), []):
                for q, t in tomar(ton):
                    transferencias.append({'from_k': k, 'from_s': s, 'to_k': j[0], 'to_s': j[1],
                                           'airport': destino_aerop[k, s], 'q': list(q), 'tons': t})
                    recibe[j, d][q] += t
    return {'itinerario': itinerario, 'cargas': list(cargas.values()), 'transferencias': transferencias, 'ttr_h': ttr}


def fijar_aviones(modelo, st, libres):
    """Los aviones que no son libres quedan con los vuelos y horarios del start (su carga sigue libre)."""
    v = modelo._v
    for nombre in ('y', 'u', 'l', 't_dep', 't_arr', 'dia', 'm0', 'm'):
        for clave, var in v[nombre].items():
            k = clave if nombre == 'm0' else clave[0]
            if k not in libres:
                var.LB = var.UB = st.get(var, 0.0)
    modelo.update()


def main():
    ap = argparse.ArgumentParser(description='MILP completo con tiempo continuo y transbordos dentro del modelo.')
    ap.add_argument('--libres', default='todos',
                    help='aviones que decide el modelo: todos, ninguno, aviones (AC-06,AC-10) u operadores (BRA)')
    ap.add_argument('--corrida', default=ws.CORRIDA_2H, help='corrida de la heurística: start y vuelos de los aviones fijos')
    ap.add_argument('--ttr', type=float, default=None, help='TTR_H (h) de los transbordos; por defecto el de la corrida')
    ap.add_argument('--smax', default=str(gm.S_MAX),
                    help=f'posiciones por avión, o "cota" para el máximo demostrado de vuelos por semana de cada avión '
                         f'(gurobi_model.cota_posiciones; necesario para que el modelo sea exacto). Por defecto {gm.S_MAX}')
    ap.add_argument('--interchange', action='store_true',
                    help='permite que otra filial opere cada vuelo (docs/README_interchanges.md)')
    ap.add_argument('--exacto', action='store_true',
                    help='filiales exactas: la filial de cada vuelo es una variable con todas las reglas del validador '
                         '(docs/README_milp_exacto.md); la solución del MILP se valida tal cual, sin volver a resolver la carga')
    ap.add_argument('--borde', choices=('base', 'flexible'), default=None,
                    help='borde de la semana con interchanges: flexible (por defecto) o base')
    ap.add_argument('--tiempo', type=float, default=3600, help='segundos de Gurobi. Por defecto 3600')
    ap.add_argument('--norel', type=float, default=None,
                    help='segundos de la heurística NoRel de Gurobi antes de la raíz; por defecto, el valor '
                         'predeterminado de Gurobi')
    ap.add_argument('--gap', type=float, default=0.01, help='MIPGap. Por defecto 0.01')
    ap.add_argument('--mipfocus', type=int, default=1, help='MIPFocus de Gurobi. Por defecto 1')
    ap.add_argument('--hilos', type=int, default=0, help='Threads de Gurobi (0 = todos)')
    ap.add_argument('--memoria', type=float, default=0,
                    help='GB máximos para Gurobi; al superarlos se detiene y exporta. Por defecto 0: sin límite')
    ap.add_argument('--salida', default=None,
                    help='carpeta de salida (por defecto data/results/MILP_continuo_transbordos_<libres>)')
    ap.add_argument('--estimar', action='store_true', help='solo muestra el tamaño de los transbordos, sin armar')
    ap.add_argument('--solo-verificar', action='store_true', help='arma el modelo y revisa el start, sin optimizar')
    ap.add_argument('--forzar', action='store_true', help=f'arma el modelo aunque pase de {LIMITE_BINARIAS:,} binarias')
    args = ap.parse_args()

    t0 = time.time()
    corrida = os.path.normpath(args.corrida)
    nombre = os.path.basename(corrida)
    kpi = json.load(open(os.path.join(corrida, 'indicadores_kpi.json'), encoding='utf-8'))['kpis']
    ttr = args.ttr if args.ttr is not None else float(kpi.get('ttr_h', 2.0))
    gm.INTERCHANGE = gm.INTERCHANGE or args.interchange or args.exacto
    if args.borde:
        ws.CFG_VALIDADOR['borde_operador_base'] = args.borde == 'base'
    datos = gm.cargar_datos(s_max=args.smax if args.smax == 'cota' else int(args.smax))
    datos['borde_base'] = ws.CFG_VALIDADOR['borde_operador_base']
    datos['filiales_exactas'] = args.exacto
    libres = interpretar_libres(args.libres, datos)
    etiqueta = ('todos' if len(libres) == len(datos['K']) else 'ninguno' if not libres
                else '_'.join(sorted(libres)))
    itinerario = ws.leer_itinerario(corrida)

    smax_txt = (f'{min(datos["S_max_k"].values())}-{max(datos["S_max_k"].values())} (cota)' if args.smax == 'cota'
                else args.smax)
    filial_txt = (' | filiales exactas' if args.exacto else ' | interchanges (relajados)' if gm.INTERCHANGE else '')
    print(f'[1/5] MILP continuo con transbordos{filial_txt} | corrida {nombre} | TTR_H = {ttr:g} h | S_max = {smax_txt} | '
          f'aviones libres: {etiqueta} ({len(libres)} de {len(datos["K"])})')
    _, cuenta = enumerar_pares(datos, itinerario, libres, ttr, solo_contar=True)
    n_pares = sum(n for t, n in cuenta.items() if t != 'binarias')
    n_dest = len({q[1] for q in datos['Q']})
    print(f"      pares de transbordo: {n_pares:,} (fijo-fijo {cuenta.get('FF', 0):,}, libre-fijo {cuenta.get('LF', 0):,}, "
          f"fijo-libre {cuenta.get('FL', 0):,}, libre-libre {cuenta.get('LL', 0):,})")
    print(f"      binarias de transbordo: {cuenta.get('binarias', 0):,} | flujos transbordados: {n_pares * n_dest:,}")
    if args.estimar:
        return
    if cuenta.get('binarias', 0) > LIMITE_BINARIAS and not args.forzar:
        print(f'[!] Más de {LIMITE_BINARIAS:,} binarias de transbordo: no cabe en un computador de 8 GB. '
              f'Libere menos aviones o use --forzar en un equipo con mucha memoria.')
        sys.exit(2)

    print('[2/5] Start: vuelos de la corrida con su carga y transbordos (paso final de la heurística, gap 1e-6)...',
          flush=True)
    multi_ref, val_ref = ws.resolver_carga(itinerario, ttr)
    if gm.INTERCHANGE and val_ref['ok']:   # con la filial que eligió la asignación de filiales para cada vuelo
        itinerario = multi_ref['solucion']['itinerario']
    print(f'      margen validado de la corrida: {val_ref["margen"]:,.0f} USD | validador '
          f'{"OK" if val_ref["ok"] else val_ref["violaciones"][:5]}')

    print('[3/5] Armando el modelo...', flush=True)
    t1 = time.time()
    modelo = gm.construir_modelo_completo(datos, transbordos=True, exacto=args.exacto)
    pares, _ = enumerar_pares(datos, itinerario, libres, ttr)
    agregar_transbordos(modelo, datos, pares, ttr)
    print(f'      {modelo.NumVars:,} variables ({modelo.NumIntVars:,} enteras), {modelo.NumConstrs:,} restricciones, '
          f'{modelo.NumNZs:,} no ceros, armado en {time.time() - t1:.0f} s', flush=True)
    st = armar_start(modelo, datos, itinerario, multi_ref['solucion'], pares)
    fijar_aviones(modelo, st, libres)
    # los horarios exportados vienen redondeados a 5 decimales (hasta 1e-5 h de diferencia en un TAT)
    xs, violaciones, obj_start = ws.verificar_start(modelo, st, tol=1e-5)
    if violaciones:
        print('      [!] El start NO cumple el modelo:')
        for familia, lista in violaciones.items():
            print(f'          {familia}: {len(lista)} (ej. {lista[:3]})')
        sys.exit(1)
    print(f'      start factible: cumple todas las restricciones; objetivo {obj_start:,.0f} USD '
          f'(margen validado {val_ref["margen"]:,.0f})')
    if args.solo_verificar:
        return

    salida = args.salida or os.path.join('data', 'results', f'MILP_continuo_transbordos_{etiqueta}')
    os.makedirs(salida, exist_ok=True)
    modelo.setAttr('Start', modelo.getVars(), xs.tolist())
    prm = modelo.Params
    prm.TimeLimit = args.tiempo
    prm.MIPGap = args.gap
    prm.MIPFocus = args.mipfocus
    prm.NodefileStart = 0.5
    prm.FeasibilityTol = 1e-5          # los horarios del start vienen redondeados a 5 decimales
    prm.LogFile = os.path.join(salida, 'gurobi.log')
    if args.norel is not None:   # sin --norel se deja el valor predeterminado de Gurobi
        prm.NoRelHeurTime = args.norel
    if args.hilos:
        prm.Threads = args.hilos
    if args.memoria:
        prm.SoftMemLimit = args.memoria
    print(f'[4/5] Gurobi: hasta {args.tiempo:,.0f} s; heurística NoRel: '
          f'{"predeterminada de Gurobi" if args.norel is None else f"{args.norel:,.0f} s"} '
          f'(Ctrl+C para cortar y exportar la mejor solución)...', flush=True)
    modelo.optimize()

    info = {'modelo': 'MILP continuo con transbordos', 'corrida_start': nombre, 'aviones_libres': sorted(libres),
            'estado': ws.ESTADOS.get(modelo.Status, str(modelo.Status)),
            'objetivo_modelo': modelo.ObjVal if modelo.SolCount else None, 'cota': modelo.ObjBound,
            'gap': modelo.MIPGap if modelo.SolCount else None, 'tiempo_gurobi_s': modelo.Runtime,
            'tiempo_limite_s': args.tiempo, 'norel_s': modelo.Params.NoRelHeurTime, 'objetivo_start': obj_start,
            'margen_corrida_mismo_paso_final_usd': val_ref['margen'], 'margen_reportado_corrida_usd': kpi['margen_usd'],
            'pares_transbordo': n_pares, 'variables': modelo.NumVars, 'restricciones': modelo.NumConstrs,
            's_max': args.smax, 'ttr_h': ttr, 'filiales_exactas': args.exacto}
    if not modelo.SolCount:
        print('No hay solución para exportar.')
        return
    if args.exacto:   # la solución del MILP tal cual: su margen validado debe coincidir con el objetivo del modelo
        sol_directa = extraer_solucion(modelo, pares, ttr)
        val_directa = ws.validar(sol_directa, dict(ws.CFG_VALIDADOR, ttr_h=ttr), ruta_raw=gm.RAW)
        info.update(margen_validado_directo=val_directa['margen'], validador_directo_ok=val_directa['ok'])
        print(f'      solución del MILP validada tal cual: validador '
              f'{"OK" if val_directa["ok"] else val_directa["violaciones"][:5]} | margen {val_directa["margen"]:,.2f} '
              f'| objetivo del modelo {modelo.ObjVal:,.2f} (diferencia {val_directa["margen"] - modelo.ObjVal:+,.2f})')
    it_modelo = ws.extraer_itinerario(modelo)
    if args.exacto:
        it_modelo = sol_directa['itinerario']
    modelo.dispose()

    print('[5/5] Validación (paso final de la heurística sobre los vuelos del modelo) y exportación...', flush=True)
    multi, val = ws.resolver_carga(it_modelo, ttr)
    kpis = ws.exportar(salida, it_modelo, multi, val, datos, info, ttr)
    ruta_meta = os.path.join(salida, 'metadata.json')
    meta = json.load(open(ruta_meta, encoding='utf-8'))
    meta['descripcion'] = f'MILP continuo con transbordos, aviones libres: {etiqueta}, start desde {nombre}'
    json.dump(meta, open(ruta_meta, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    print('\n' + '=' * 78)
    print(f'Estado Gurobi: {info["estado"]} | objetivo del modelo {info["objetivo_modelo"]:,.0f} | '
          f'cota {info["cota"]:,.0f} | gap {100 * info["gap"]:.2f} %')
    print(f'Margen validado con transbordos: corrida {val_ref["margen"]:,.0f} -> MILP {kpis["margen_usd"]:,.0f} USD '
          f'({kpis["margen_usd"] - val_ref["margen"]:+,.0f})')
    print(f'Validador: {"OK" if val["ok"] else str(len(val["violaciones"])) + " violaciones"} | vuelos {kpis["vuelos"]} | '
          f'aviones {kpis["aviones_usados"]} | toneladas {kpis["toneladas"]:,.1f} | '
          f'frecuencias {kpis["frecuencias_cumplidas"]} | transbordos {kpis["conexiones_transferencia_usadas"]}')
    print(f'Resultados en {salida} (tiempo total {time.time() - t0:.0f} s)')


if __name__ == '__main__':
    main()
