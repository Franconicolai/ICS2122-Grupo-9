"""
MILP integrado en red espacio-tiempo: vuelos de cada avión, carga multitramo (through) y transbordos entre aviones
en un solo modelo, con la misma modelación de la heurística (semana en bloques de DT horas, red cíclica por avión,
arcos de vuelo que duran ceil((horas de bloque + TAT) / DT) bloques).

El parámetro --libres dice qué aviones decide el modelo. Los demás quedan fijos con los vuelos de la corrida de
partida, pero su carga y sus transbordos se vuelven a optimizar junto con los aviones libres:
    --libres todos          modelo integrado completo (todos los aviones libres)
    --libres AC-06,AC-10    solo esos aviones
    --libres BRA            todos los aviones de un operador (AND, BRA, PAC o SUR); se pueden combinar con comas
    --libres ninguno        vuelos fijos: solo carga y transbordos (control: reproduce el paso final de la heurística)

La solución de la corrida (por defecto, la de 2 h) se inyecta completa como MIP start, con sus transbordos.

Uso, desde la raíz del repositorio:
    python modelo_integrado_espacio_tiempo.py --libres AC-06,AC-10 --tiempo 1800
    python modelo_integrado_espacio_tiempo.py --libres todos --estimar     # solo el tamaño del modelo
    python modelo_integrado_espacio_tiempo.py --help

La formulación está en docs/README_modelo_integrado_espacio_tiempo.md.
"""
import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd
import scipy.sparse as sp
import yaml

import warm_start_milp as ws   # deja la raíz como directorio de trabajo y agrega src/ (submodelo de carga y validador)
import gurobi_model as gm
import gurobipy as gp
from gurobipy import GRB

OPERADORES = ('AND', 'BRA', 'PAC', 'SUR')


class _Variables:
    """Columnas del modelo, armadas por bloques de numpy para no crear millones de objetos de Python."""

    def __init__(self):
        self.n, self.lb, self.ub, self.obj, self.vt = 0, [], [], [], []

    def nuevas(self, cantidad, lb, ub, obj, vtype):
        idx = np.arange(self.n, self.n + cantidad, dtype=np.int64)
        self.n += cantidad
        for lista, valor in ((self.lb, lb), (self.ub, ub), (self.obj, obj)):
            lista.append(np.broadcast_to(np.asarray(valor, dtype=float), (cantidad,)).copy())
        self.vt.append(np.full(cantidad, vtype))
        return idx


class _Filas:
    """Restricciones por familias: sentido, lado derecho y entradas (fila, columna, coeficiente)."""

    def __init__(self):
        self.n, self.sentido, self.rhs, self.familias = 0, [], [], []
        self.i, self.j, self.v = [], [], []

    def nuevas(self, nombre, cantidad, sentido, rhs):
        inicio = self.n
        self.n += cantidad
        self.sentido.append(np.full(cantidad, sentido))
        self.rhs.append(np.broadcast_to(np.asarray(rhs, dtype=float), (cantidad,)).copy())
        self.familias.append((nombre, inicio, cantidad))
        return inicio

    def entradas(self, filas, columnas, valores):
        filas = np.asarray(filas, dtype=np.int64)
        self.i.append(filas.astype(np.int32))
        self.j.append(np.asarray(columnas, dtype=np.int64).astype(np.int32))
        self.v.append(np.broadcast_to(np.asarray(valores, dtype=float), filas.shape).copy())


def interpretar_libres(texto, datos):
    """'todos', 'ninguno', aviones (AC-06) u operadores (BRA), separados por comas."""
    texto = texto.strip()
    if texto.lower() == 'todos':
        return set(datos['K'])
    if texto.lower() == 'ninguno':
        return set()
    libres = set()
    for parte in (p.strip() for p in texto.split(',') if p.strip()):
        if parte.upper() in OPERADORES:
            libres |= {k for k in datos['K'] if datos['op'][k] == parte.upper()}
        elif parte in datos['K']:
            libres.add(parte)
        else:
            raise ValueError(f"'{parte}' no es un avión ni un operador; use p. ej. AC-06, BRA, todos o ninguno")
    return libres


def construir(datos, dt, ttr, itinerario, libres, estimar=False):
    """Arma la red espacio-tiempo integrada. Devuelve la matriz, las columnas y los índices necesarios para
    inyectar el start y leer la solución (o solo el tamaño, si estimar=True)."""
    K, E, Q, tau = datos['K'], datos['E'], datos['Q'], datos['tau_e']
    T = int(round(gm.H / dt))
    if abs(T * dt - gm.H) > 1e-9:
        raise ValueError('DT debe dividir las 168 h de la semana')
    A = datos['A']
    nA, ia = len(A), {a: i for i, a in enumerate(A)}
    dests = sorted({q[1] for q in Q})
    nD, idd = len(dests), {d: i for i, d in enumerate(dests)}
    nK, ik = len(K), {k: i for i, k in enumerate(K)}
    ie = {e: i for i, e in enumerate(E)}
    with open(os.path.join(gm.RAW, 'ops_rules.yaml'), encoding='utf-8') as fh:
        tat_lleno = yaml.safe_load(fh)['tat_minutes']['full_in_full_out'] / 60.0
    inter = pd.read_csv(os.path.join(gm.RAW, 'interchange_airports.csv'))
    ops = sorted(set(datos['op'].values()))
    sop = {(r.airport, o) for r in inter.itertuples() for o in ops if getattr(r, o) == 1}
    soporte = np.array([[(a, o) in sop for o in ops] for a in A])     # (aeropuerto, operador)
    es_tec = np.array([a in datos['tec'] for a in A])
    P = np.array([datos['P_k'][k] for k in K])
    SEG = np.array([datos['seguro_k'][k] for k in K])
    HK = np.array([datos['h_k'][k] for k in K])
    OPK = np.array([ops.index(datos['op'][k]) for k in K])
    dur = {e: max(1, math.ceil((tau[e] + datos['TAT_e'][e]) / dt - 1e-9)) for e in E}

    # ---------------------------------------------------------------- arcos de vuelo
    # libres: todo tramo de Ek[k] en todo bloque que termine dentro de la semana; fijos: sus vuelos de la corrida
    lista, arco_de, fijos = [], {}, defaultdict(list)
    for k in K:
        if k in libres:
            for e in datos['Ek'][k]:
                for t in range(T - dur[e] + 1):
                    arco_de[k, e, t] = len(lista)
                    lista.append((ik[k], ie[e], t, 1))
        else:
            for leg in sorted(itinerario.get(k, []), key=lambda l: l['pos']):
                e, t = tuple(leg['tramo']), int(round(leg['t_dep'] / dt))
                if abs(t * dt - leg['t_dep']) > 1e-6:
                    raise ValueError(f"{k}: el vuelo {leg} no cae en la grilla de {dt} h; use una corrida con ese DT")
                arco_de[k, e, t] = len(lista)
                fijos[k].append(len(lista))
                lista.append((ik[k], ie[e], t, 0))
    arr = np.array(lista, dtype=np.int64).reshape(-1, 4)
    a_k, a_e, a_t, a_libre = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3].astype(bool)
    nArc = len(arr)
    Eo = np.array([ia[e[0]] for e in E])
    Ed = np.array([ia[e[1]] for e in E])
    Etau = np.array([tau[e] for e in E])
    Edur = np.array([dur[e] for e in E])
    a_o, a_d = Eo[a_e], Ed[a_e]
    a_tarr = a_t + Edur[a_e]                                     # bloque en que el avión vuelve a estar disponible
    a_land = a_t * dt + Etau[a_e]                                # hora real de aterrizaje
    a_dia = np.floor(a_t * dt / 24 + 1e-9).astype(np.int64) + 1
    a_avail = np.ceil((a_land + ttr) / dt - 1e-9).astype(np.int64)   # bloque desde el que se puede transbordar
    a_P, a_seg, a_h, a_op = P[a_k], SEG[a_k], HK[a_k], OPK[a_k]
    a_costo = np.array([datos['c_ke'][K[k], E[e]] for k, e in zip(a_k, a_e)])
    a_dest = np.array([idd.get(A[d], -1) for d in a_d], dtype=np.int64)  # destino de carga del aeropuerto de llegada
    for k, ids in fijos.items():                                # los vuelos fijos deben calzar con la red
        for i, j in zip(ids, ids[1:]):
            if a_t[j] < a_tarr[i]:
                raise ValueError(f'{k}: un vuelo sale antes de que el anterior cumpla su TAT en la grilla de {dt} h')
        if a_t[ids[0]] + T < a_tarr[ids[-1]]:
            raise ValueError(f'{k}: la rotación no cierra en la grilla de {dt} h')

    # ---------------------------------------------------------------- dónde existe cada variable
    baja_ok = ~es_tec[a_d] & soporte[a_d, a_op] & (a_avail <= T - 1)
    XM = (a_tarr < T)[:, None] | (np.arange(nD)[None, :] == a_dest[:, None]) | baja_ok[:, None]
    DM = baja_ok[:, None] & XM
    Qarr = -np.ones((nA, nD, 8), dtype=np.int64)
    for qi, q in enumerate(Q):
        Qarr[ia[q[0]], idd[q[1]], q[2]] = qi
    BQ = Qarr[a_o[:, None], np.arange(nD)[None, :], a_dia[:, None]]
    BM = (BQ >= 0) & XM
    en_tierra = np.zeros((nK, nA, T), dtype=bool)     # avión en tierra entre tau y tau+1, con carga a bordo posible
    G_mask = np.zeros((nK, nA, T), dtype=bool)        # arcos de espera de la red cíclica de cada avión libre
    for k in K:
        if k in libres:
            aps = sorted({ia[a] for e in datos['Ek'][k] for a in e})
            en_tierra[ik[k], aps, :] = True
            G_mask[ik[k], aps, :] = True
        else:
            ids = fijos.get(k, [])
            for i, j in zip(ids, ids[1:]):
                en_tierra[ik[k], a_d[i], a_tarr[i]:a_t[j]] = True
    en_tierra[:, :, T - 1] = False                    # la carga no cruza el corte de la semana
    nodo_salida = np.zeros((nK, nA, T), dtype=bool)
    nodo_salida[a_k, a_o, a_t] = True
    PKM = nodo_salida & ~es_tec[None, :, None] & soporte[:, OPK].T[:, :, None]
    deposito_ok = ~es_tec & soporte.any(axis=1)
    # el depósito se lleva por avión que baja la carga: así se impide que un avión suba carga que él mismo bajó
    SP = np.zeros((nK, nA), dtype=bool)
    SP[a_k[baja_ok], a_d[baja_ok]] = True
    libres_idx = np.array(sorted(ik[k] for k in libres), dtype=np.int64)

    tam = {'aviones libres': len(libres), 'arcos de vuelo': nArc, 'binarias de vuelo (z)': int(a_libre.sum()),
           'carga en vuelo por destino (x)': int(XM.sum()), 'bajadas a depósito': int(DM.sum()),
           'embarques (b)': int(BM.sum()), 'carga a bordo en tierra (h)': int(en_tierra.sum()) * nD,
           'subidas desde depósito': int(PKM.sum()) * nD,
           'inventario en depósitos por avión que baja': 2 * int(SP.sum()) * nD * T}
    tam['variables'] = (sum(v for k, v in tam.items() if k not in ('aviones libres', 'arcos de vuelo'))
                        + 2 * nArc + int(a_libre.sum()) + int(G_mask.sum()) + len(libres_idx)
                        + int(deposito_ok.sum()) * nD * T)
    if estimar:
        return {'tamano': tam}

    # ---------------------------------------------------------------- variables
    V = _Variables()
    z = np.full(nArc, -1, dtype=np.int64)
    fr = np.nonzero(a_libre)[0]
    z[fr] = V.nuevas(len(fr), 0, 1, -a_costo[fr], GRB.BINARY)                    # vuelo del avión libre
    f = V.nuevas(nArc, 0, 1, 0, GRB.BINARY)                                      # el vuelo puede ir lleno
    L = V.nuevas(nArc, datos['min_ton'], a_P, 0, GRB.SEMICONT)                   # carga total: 0 o entre 10 t y payload
    X = np.full((nArc, nD), -1, dtype=np.int64)
    X[XM] = V.nuevas(int(XM.sum()), 0, np.broadcast_to(a_P[:, None], XM.shape)[XM], 0, GRB.CONTINUOUS)
    DL = np.full(nArc, -1, dtype=np.int64)
    con_entrega = np.nonzero(a_dest >= 0)[0]
    DL[con_entrega] = V.nuevas(len(con_entrega), 0, a_P[con_entrega], 0, GRB.CONTINUOUS)  # entrega en el destino
    DR = np.full((nArc, nD), -1, dtype=np.int64)
    DR[DM] = V.nuevas(int(DM.sum()), 0, np.broadcast_to(a_P[:, None], DM.shape)[DM], 0, GRB.CONTINUOUS)
    B = np.full((nArc, nD), -1, dtype=np.int64)
    bq = BQ[BM]
    Dq = np.array([datos['D_q'][q] for q in Q])
    rq = np.array([datos['r_q'][q] for q in Q])
    b_arc = np.nonzero(BM)[0]
    B[BM] = V.nuevas(int(BM.sum()), 0, np.minimum(Dq[bq], a_P[b_arc]), 1000 * rq[bq] - a_h[b_arc], GRB.CONTINUOUS)
    U = np.full(nK, -1, dtype=np.int64)
    U[libres_idx] = V.nuevas(len(libres_idx), 0, 1, 0, GRB.BINARY)               # avión libre en uso
    G = np.full((nK, nA, T), -1, dtype=np.int64)
    G[G_mask] = V.nuevas(int(G_mask.sum()), 0, 1, 0, GRB.CONTINUOUS)              # espera del avión libre
    HM = np.broadcast_to(en_tierra[..., None], (nK, nA, T, nD))
    Hd = np.full((nK, nA, T, nD), -1, dtype=np.int64)
    Hd[HM] = V.nuevas(int(HM.sum()), 0, np.broadcast_to(P[:, None, None, None], HM.shape)[HM], 0, GRB.CONTINUOUS)
    PM = np.broadcast_to(PKM[..., None], (nK, nA, T, nD))
    PK = np.full((nK, nA, T, nD), -1, dtype=np.int64)
    PK[PM] = V.nuevas(int(PM.sum()), 0, np.broadcast_to(P[:, None, None, None], PM.shape)[PM],
                      -np.broadcast_to(HK[:, None, None, None], PM.shape)[PM], GRB.CONTINUOUS)  # handling del receptor
    SKM = np.broadcast_to(SP[:, :, None, None], (nK, nA, nD, T)).copy()
    OKM = SKM.copy()
    SKM[..., T - 1] = False                                                       # el depósito no cruza la semana
    SK = np.full((nK, nA, nD, T), -1, dtype=np.int64)
    SK[SKM] = V.nuevas(int(SKM.sum()), 0, GRB.INFINITY, 0, GRB.CONTINUOUS)       # inventario bajado por cada avión
    OK = np.full((nK, nA, nD, T), -1, dtype=np.int64)
    OK[OKM] = V.nuevas(int(OKM.sum()), 0, GRB.INFINITY, 0, GRB.CONTINUOUS)       # sale de ese inventario hacia otro avión
    TM = np.zeros((nA, nD, T), dtype=bool)
    TM[deposito_ok] = True
    TOT = np.full((nA, nD, T), -1, dtype=np.int64)
    TOT[TM] = V.nuevas(int(TM.sum()), 0, GRB.INFINITY, 0, GRB.CONTINUOUS)        # total transbordado en el bloque
    constante = -float(a_costo[~a_libre].sum())                                   # costo de los vuelos fijos

    # ---------------------------------------------------------------- restricciones
    R = _Filas()

    def nodo(k, a, tt):
        return (k * nA + a) * (T + 1) + tt

    # (1) balance de carga por destino en cada nodo (avión, aeropuerto, bloque):
    #     llega en vuelo - entrega - bajada a depósito + a bordo desde el bloque anterior + subida desde depósito
    #   = sale en vuelo - embarque inicial + a bordo hacia el bloque siguiente
    ent = []
    ai, di = np.nonzero(XM)
    ent.append((nodo(a_k[ai], a_d[ai], a_tarr[ai]), di, X[ai, di], 1.0))
    ent.append((nodo(a_k[ai], a_o[ai], a_t[ai]), di, X[ai, di], -1.0))
    ce = con_entrega
    ent.append((nodo(a_k[ce], a_d[ce], a_tarr[ce]), a_dest[ce], DL[ce], -1.0))
    ai, di = np.nonzero(DM)
    ent.append((nodo(a_k[ai], a_d[ai], a_tarr[ai]), di, DR[ai, di], -1.0))
    ai, di = np.nonzero(BM)
    ent.append((nodo(a_k[ai], a_o[ai], a_t[ai]), di, B[ai, di], 1.0))
    hk_, ha_, ht_, hd_ = np.nonzero(HM)
    ent.append((nodo(hk_, ha_, ht_), hd_, Hd[hk_, ha_, ht_, hd_], -1.0))
    ent.append((nodo(hk_, ha_, ht_ + 1), hd_, Hd[hk_, ha_, ht_, hd_], 1.0))
    pk_, pa_, pt_, pd_ = np.nonzero(PM)
    ent.append((nodo(pk_, pa_, pt_), pd_, PK[pk_, pa_, pt_, pd_], 1.0))
    usada = np.zeros(nK * nA * (T + 1) * nD, dtype=bool)
    for n_, d_, _, _ in ent:
        usada[n_ * nD + d_] = True
    fila_de = np.cumsum(usada) - 1
    inicio = R.nuevas('balance de carga', int(usada.sum()), '=', 0)
    while ent:
        n_, d_, c_, v_ = ent.pop()
        R.entradas(inicio + fila_de[n_ * nD + d_], c_, v_)
    del usada, fila_de

    # (2) depósito de transbordos, llevado por avión que baja la carga:
    #  (2a) inventario del avión k: anterior + sus bajadas que quedan disponibles en el bloque (aterrizaje + TTR_H)
    #       - lo que sale hacia otros aviones = inventario siguiente (no cruza el corte de la semana)
    par = np.full((nK, nA), -1, dtype=np.int64)                 # solo los pares (avión, aeropuerto) que pueden bajar
    par[SP] = np.arange(int(SP.sum()))
    clave4 = lambda k, a, d, tt: (par[k, a] * nD + d) * T + tt
    inicio = R.nuevas('inventario del depósito por avión que baja', int(SP.sum()) * nD * T, '=', 0)
    sk_k, sk_a, sk_d, sk_t = np.nonzero(SKM)
    R.entradas(inicio + clave4(sk_k, sk_a, sk_d, sk_t), SK[sk_k, sk_a, sk_d, sk_t], -1.0)
    R.entradas(inicio + clave4(sk_k, sk_a, sk_d, sk_t + 1), SK[sk_k, sk_a, sk_d, sk_t], 1.0)
    ok_k, ok_a, ok_d, ok_t = np.nonzero(OKM)
    R.entradas(inicio + clave4(ok_k, ok_a, ok_d, ok_t), OK[ok_k, ok_a, ok_d, ok_t], -1.0)
    ai, di = np.nonzero(DM)
    R.entradas(inicio + clave4(a_k[ai], a_d[ai], di, a_avail[ai]), DR[ai, di], 1.0)
    #  (2b) en cada (aeropuerto, destino, bloque): lo que sale de los inventarios = total transbordado = lo que suben
    clave = lambda a, d, tt: (a * nD + d) * T + tt
    inicio = R.nuevas('salidas del depósito = total transbordado', nA * nD * T, '=', 0)
    R.entradas(inicio + clave(ok_a, ok_d, ok_t), OK[ok_k, ok_a, ok_d, ok_t], 1.0)
    ta, td, tt_ = np.nonzero(TM)
    R.entradas(inicio + clave(ta, td, tt_), TOT[ta, td, tt_], -1.0)
    inicio = R.nuevas('subidas = total transbordado', nA * nD * T, '=', 0)
    R.entradas(inicio + clave(pa_, pd_, pt_), PK[pk_, pa_, pt_, pd_], 1.0)
    R.entradas(inicio + clave(ta, td, tt_), TOT[ta, td, tt_], -1.0)
    #  (2c) un avión no sube carga que él mismo bajó: con lo que sale de su inventario (o_k) y lo que sube (p_k),
    #       o_k + p_k <= total asegura que todo se puede asignar entre aviones distintos (condición de Hall)
    ambos = OKM & PKM[:, :, None, :]
    ak_, aa_, ad_, at_ = np.nonzero(ambos)
    inicio = R.nuevas('sin re-embarque propio', len(ak_), '<', 0)
    filas = inicio + np.arange(len(ak_))
    R.entradas(filas, OK[ak_, aa_, ad_, at_], 1.0)
    R.entradas(filas, PK[ak_, aa_, at_, ad_], 1.0)
    R.entradas(filas, TOT[aa_, ad_, at_], -1.0)

    # (3) red de cada avión libre: conservación de flujo cíclica, un solo ciclo semanal y mantenimiento
    if len(libres_idx):
        cl = lambda k, a, tt: (k * nA + a) * T + tt
        inicio = R.nuevas('flujo de aviones', nK * nA * T, '=', 0)
        R.entradas(inicio + cl(a_k[fr], a_o[fr], a_t[fr]), z[fr], -1.0)
        R.entradas(inicio + cl(a_k[fr], a_d[fr], a_tarr[fr] % T), z[fr], 1.0)
        gk, ga, gt = np.nonzero(G_mask)
        R.entradas(inicio + cl(gk, ga, gt), G[gk, ga, gt], -1.0)
        R.entradas(inicio + cl(gk, ga, (gt + 1) % T), G[gk, ga, gt], 1.0)
        inicio = R.nuevas('cruce del corte semanal = avión en uso', nK, '=', 0)
        ck, ca = np.nonzero(G_mask[:, :, T - 1])
        R.entradas(inicio + ck, G[ck, ca, T - 1], 1.0)
        corte = fr[a_tarr[fr] == T]
        R.entradas(inicio + a_k[corte], z[corte], 1.0)
        R.entradas(inicio + libres_idx, U[libres_idx], -1.0)
        filas_m, cols_m, vals_m = [], [], []
        for k in sorted(libres):
            ap_m, h0, h1 = datos['mant'][k]
            for tt in range(int(math.floor(h0 / dt)), int(math.ceil(h1 / dt))):
                fila = len(filas_m) // 2
                filas_m += [fila, fila]
                cols_m += [G[ik[k], ia[ap_m], tt % T], U[ik[k]]]
                vals_m += [1.0, -1.0]
        inicio = R.nuevas('mantenimiento en tierra', len(filas_m) // 2, '>', 0)
        fm, cm, vm = np.array(filas_m, dtype=np.int64), np.array(cols_m, dtype=np.int64), np.array(vals_m)
        R.entradas(inicio + fm[cm >= 0], cm[cm >= 0], vm[cm >= 0])
        lk, la, lt = np.nonzero(en_tierra & G_mask)
        inicio = R.nuevas('carga en tierra solo donde está el avión', len(lk), '<', 0)
        filas = inicio + np.arange(len(lk))
        R.entradas(filas, G[lk, la, lt], -P[lk])
        for d in range(nD):
            R.entradas(filas, Hd[lk, la, lt, d], 1.0)

    # (4) carga de cada vuelo: L = suma de x (semicontinua: 0 o >= 10 t), a lo más el 95 % del payload salvo que
    #     f = 1, solo en vuelos que existen, y embarques y bajadas a depósito <= carga del vuelo
    todos = np.arange(nArc)
    inicio = R.nuevas('carga total del vuelo', nArc, '=', 0)
    R.entradas(inicio + todos, L, 1.0)
    ai, di = np.nonzero(XM)
    R.entradas(inicio + ai, X[ai, di], -1.0)
    inicio = R.nuevas('vuelo lleno solo si f = 1', nArc, '<', a_seg)
    R.entradas(inicio + todos, L, 1.0)
    R.entradas(inicio + todos, f, -(a_P - a_seg))
    if len(fr):
        inicio = R.nuevas('carga solo en vuelos que existen', len(fr), '<', 0)
        R.entradas(inicio + np.arange(len(fr)), L[fr], 1.0)
        R.entradas(inicio + np.arange(len(fr)), z[fr], -a_P[fr])
        inicio = R.nuevas('f solo en vuelos que existen', len(fr), '<', 0)
        R.entradas(inicio + np.arange(len(fr)), f[fr], 1.0)
        R.entradas(inicio + np.arange(len(fr)), z[fr], -1.0)
    arcos = np.nonzero(BM.any(axis=1))[0]
    pos = np.full(nArc, -1, dtype=np.int64)
    pos[arcos] = np.arange(len(arcos))
    inicio = R.nuevas('embarque <= carga del vuelo', len(arcos), '<', 0)
    ai, di = np.nonzero(BM)
    R.entradas(inicio + pos[ai], B[ai, di], 1.0)
    R.entradas(inicio + np.arange(len(arcos)), L[arcos], -1.0)
    # lo que se descarga al aterrizar (entrega + bajada a depósito) sale de lo que trae ese vuelo para ese destino:
    # impide "bajar" carga recién subida del depósito sin volarla (cambiaría de dueño o de día sin un vuelo real)
    UM = DM.copy()
    UM[con_entrega, a_dest[con_entrega]] = True
    ai, di = np.nonzero(UM)
    inicio = R.nuevas('descarga <= carga que trae el vuelo', len(ai), '<', 0)
    filas = inicio + np.arange(len(ai))
    R.entradas(filas, X[ai, di], -1.0)
    con_baja = DM[ai, di]
    R.entradas(filas[con_baja], DR[ai[con_baja], di[con_baja]], 1.0)
    es_dest = di == a_dest[ai]
    R.entradas(filas[es_dest], DL[ai[es_dest]], 1.0)

    # (5) TAT de 90 min si el avión llega y sale lleno: un vuelo que aterriza fuera de escala técnica y el
    #     siguiente vuelo del mismo avión que sale antes de 90 min no pueden ir ambos llenos (también en el cierre)
    salidas = defaultdict(list)
    for i in range(nArc):
        salidas[a_k[i], a_o[i], a_t[i]].append(i)
    grupos = defaultdict(list)
    for i in np.nonzero(~es_tec[a_d])[0]:
        ventana, tt = [], int(a_tarr[i])
        while tt * dt - a_land[i] < tat_lleno - 1e-9:
            ventana.append(tt % T)
            tt += 1
        if ventana:
            grupos[a_k[i], a_d[i], tuple(ventana)].append(i)
    filas_c, cols_c, n_c = [], [], 0
    for (k, a, ventana), llegadas in grupos.items():
        deps = [j for tt in ventana for j in salidas.get((k, a, tt), [])]
        if deps:
            cols = [f[i] for i in llegadas] + [f[j] for j in deps]
            filas_c += [n_c] * len(cols)
            cols_c += cols
            n_c += 1
    inicio = R.nuevas('regla de 90 min lleno-lleno', n_c, '<', 1)
    R.entradas(inicio + np.array(filas_c, dtype=np.int64), np.array(cols_c, dtype=np.int64), 1.0)

    # (6) frecuencias mínimas (los vuelos fijos cuentan como constante) y (7) demanda diaria
    filas_f, cols_f, rhs_f = [], [], []
    for od, fmin in datos['f_od'].items():
        if od not in ie:
            continue
        en_od = a_e == ie[od]
        falta = fmin - int((en_od & ~a_libre).sum())
        if falta > 0:
            libres_od = np.nonzero(en_od & a_libre)[0]
            filas_f += [len(rhs_f)] * len(libres_od)
            cols_f += list(z[libres_od])
            rhs_f.append(falta)
    inicio = R.nuevas('frecuencias mínimas', len(rhs_f), '>', np.array(rhs_f, dtype=float))
    R.entradas(inicio + np.array(filas_f, dtype=np.int64), np.array(cols_f, dtype=np.int64), 1.0)
    inicio = R.nuevas('demanda diaria', len(Q), '<', Dq)
    R.entradas(inicio + bq, B[BM], 1.0)

    # ---------------------------------------------------------------- matriz dispersa
    i_, j_, v_ = np.concatenate(R.i), np.concatenate(R.j), np.concatenate(R.v)
    R.i = R.j = R.v = None
    Amat = sp.csr_matrix((v_, (i_, j_)), shape=(R.n, V.n))
    del i_, j_, v_
    return {
        'A': Amat, 'sentido': np.concatenate(R.sentido), 'rhs': np.concatenate(R.rhs), 'familias': R.familias,
        'lb': np.concatenate(V.lb), 'ub': np.concatenate(V.ub), 'obj': np.concatenate(V.obj),
        'vt': np.concatenate(V.vt), 'n': V.n, 'constante': constante, 'tamano': tam,
        'T': T, 'dt': dt, 'K': K, 'E': E, 'Etau': Etau, 'ik': ik, 'ia': ia, 'idd': idd, 'libres': libres,
        'arco_de': arco_de, 'a_k': a_k, 'a_e': a_e, 'a_t': a_t, 'a_o': a_o, 'a_d': a_d, 'a_tarr': a_tarr,
        'a_avail': a_avail, 'a_libre': a_libre, 'a_seg': a_seg,
        'z': z, 'f': f, 'L': L, 'X': X, 'DL': DL, 'DR': DR, 'B': B, 'U': U, 'G': G, 'H': Hd, 'PK': PK,
        'SK': SK, 'OK': OK, 'TOT': TOT,
    }


def armar_start(red, itinerario, solucion):
    """MIP start completo: los vuelos de la corrida y su carga del paso final de la heurística (con transbordos)."""
    xs = np.zeros(red['n'])
    ik, ia, idd, T, dt = red['ik'], red['ia'], red['idd'], red['T'], red['dt']
    vuelo = {}
    for k, legs in itinerario.items():
        arcos = []
        for leg in sorted(legs, key=lambda l: l['pos']):
            clave = (k, tuple(leg['tramo']), int(round(leg['t_dep'] / dt)))
            if clave not in red['arco_de']:
                raise ValueError(f'{k}: el vuelo {leg} no existe en la red (grilla de {dt} h)')
            vuelo[k, leg['pos']] = red['arco_de'][clave]
            arcos.append(red['arco_de'][clave])
        if not arcos or k not in red['libres']:
            continue
        xs[red['U'][ik[k]]] = 1
        for i, arco in enumerate(arcos):                     # vuelo y espera hasta el siguiente (red cíclica)
            xs[red['z'][arco]] = 1
            sig = arcos[(i + 1) % len(arcos)]
            t_sig = red['a_t'][sig] + (T if i + 1 == len(arcos) else 0)
            for tt in range(red['a_tarr'][arco], t_sig):
                xs[red['G'][ik[k], red['a_d'][arco], tt % T]] = 1

    def poner(indice, valor, que):
        if indice < 0:
            raise ValueError(f'la solución usa {que}, que no existe en la red')
        xs[indice] += valor

    carga = np.zeros(len(red['a_k']))
    for c in solucion['cargas']:
        q, k = tuple(c['q']), c['k']
        arco, d = vuelo[k, c['s']], idd[q[1]]
        poner(red['X'][arco, d], c['x'], f"carga {q} en {k} pos {c['s']}")
        carga[arco] += c['x']
        if c['b'] > 1e-12:
            poner(red['B'][arco, d], c['b'], f'embarque de {q}')
        if c['a'] > 1e-12:
            poner(red['DL'][arco], c['a'], f'entrega de {q}')
        if c.get('transfer_out', 0) > 1e-12:
            poner(red['DR'][arco, d], c['transfer_out'], f'bajada a depósito de {q}')
        if c.get('transfer_in', 0) > 1e-12:
            poner(red['PK'][ik[k], red['a_o'][arco], red['a_t'][arco], d], c['transfer_in'], f'subida de {q}')
        if c['w'] > 1e-12:                                   # through: sigue a bordo mientras el avión espera
            sig = vuelo[k, c['s'] + 1]
            for tt in range(red['a_tarr'][arco], red['a_t'][sig]):
                poner(red['H'][ik[k], red['a_d'][arco], tt, d], c['w'], f'carga a bordo en tierra de {q}')
    for tr in solucion['transferencias']:
        desde, hacia = vuelo[tr['from_k'], tr['from_s']], vuelo[tr['to_k'], tr['to_s']]
        a, d, ks = ia[tr['airport']], idd[tuple(tr['q'])[1]], ik[tr['from_k']]
        t_sube = red['a_t'][hacia]
        if t_sube < red['a_avail'][desde]:
            raise ValueError(f'el transbordo {tr} no respeta TTR_H en la grilla')
        for tt in range(red['a_avail'][desde], t_sube):
            poner(red['SK'][ks, a, d, tt], tr['tons'], f"inventario en depósito de {tr['q']}")
        poner(red['OK'][ks, a, d, t_sube], tr['tons'], f"salida del depósito de {tr['q']}")
        poner(red['TOT'][a, d, t_sube], tr['tons'], f"total transbordado de {tr['q']}")
    xs[red['L']] = carga
    xs[red['f'][carga > red['a_seg'] + 1e-9]] = 1
    return xs


def verificar(red, xs, tol=1e-6):
    """Revisa el start contra cada restricción, cota, integralidad y semicontinuidad. Devuelve (violaciones, objetivo)."""
    act = red['A'] @ xs
    rhs, sentido = red['rhs'], red['sentido']
    exceso = np.where(sentido == '<', act - rhs, np.where(sentido == '>', rhs - act, np.abs(act - rhs)))
    violaciones = {}
    for nombre, inicio, cantidad in red['familias']:
        malas = int((exceso[inicio:inicio + cantidad] > tol).sum())
        if malas:
            violaciones[nombre] = malas
    lb, ub, vt = red['lb'], red['ub'], red['vt']
    semi = vt == GRB.SEMICONT
    fuera = (xs < np.where(semi, 0, lb) - tol) | (xs > ub + tol) | (semi & (xs > tol) & (xs < lb - tol))
    if fuera.any():
        violaciones['cotas'] = int(fuera.sum())
    no_entera = (vt == GRB.BINARY) & (np.abs(xs - np.round(xs)) > tol)
    if no_entera.any():
        violaciones['integralidad'] = int(no_entera.sum())
    return violaciones, float(red['obj'] @ xs + red['constante'])


def extraer_itinerario(red, x):
    """Vuelos de la solución: los arcos activos de los aviones libres y los vuelos de los aviones fijos."""
    activo = ~red['a_libre']
    libres = np.nonzero(red['a_libre'])[0]
    activo[libres] = x[red['z'][libres]] > 0.5
    itinerario = {k: [] for k in red['K']}
    for arco in np.nonzero(activo)[0]:
        e = red['E'][red['a_e'][arco]]
        t_dep = float(red['a_t'][arco] * red['dt'])
        itinerario[red['K'][red['a_k'][arco]]].append(
            {'tramo': e, 't_dep': t_dep, 't_arr': round(t_dep + red['Etau'][red['a_e'][arco]], 5)})
    for legs in itinerario.values():
        legs.sort(key=lambda l: l['t_dep'])
        for p, leg in enumerate(legs, start=1):
            leg['pos'] = p
    return itinerario


def main():
    ap = argparse.ArgumentParser(description='MILP integrado en red espacio-tiempo (vuelos, carga y transbordos).')
    ap.add_argument('--libres', default='todos',
                    help='aviones que decide el modelo: todos, ninguno, aviones (AC-06,AC-10) u operadores (BRA)')
    ap.add_argument('--corrida', default=ws.CORRIDA_2H, help='corrida de la heurística: start y vuelos de los aviones fijos')
    ap.add_argument('--dt', type=float, default=None, help='horas por bloque; por defecto el DT de la corrida')
    ap.add_argument('--ttr', type=float, default=None, help='TTR_H (h) de los transbordos; por defecto el de la corrida')
    ap.add_argument('--tiempo', type=float, default=3600, help='segundos de Gurobi. Por defecto 3600')
    ap.add_argument('--norel', type=float, default=None,
                    help='segundos de la heurística NoRel de Gurobi antes de la raíz; por defecto, el valor '
                         'predeterminado de Gurobi')
    ap.add_argument('--gap', type=float, default=0.01, help='MIPGap. Por defecto 0.01')
    ap.add_argument('--mipfocus', type=int, default=1, help='MIPFocus de Gurobi. Por defecto 1')
    ap.add_argument('--hilos', type=int, default=0, help='Threads de Gurobi (0 = todos)')
    ap.add_argument('--memoria', type=float, default=0,
                    help='GB máximos para Gurobi; al superarlos se detiene y exporta. Por defecto 0: sin límite')
    ap.add_argument('--presolve', type=int, default=-1,
                    help='Presolve de Gurobi (-1 automático, 0 apagado: ahorra la copia presolvida en modelos enormes)')
    ap.add_argument('--salida', default=None, help='carpeta de salida (por defecto data/results/MILP_integrado_TSN_<libres>)')
    ap.add_argument('--estimar', action='store_true', help='solo muestra el tamaño del modelo, sin armarlo')
    ap.add_argument('--solo-verificar', action='store_true', help='arma el modelo y revisa el start, sin optimizar')
    args = ap.parse_args()

    t0 = time.time()
    corrida = os.path.normpath(args.corrida)
    nombre = os.path.basename(corrida)
    ruta_meta = os.path.join(corrida, 'metadata.json')
    meta = json.load(open(ruta_meta, encoding='utf-8')) if os.path.exists(ruta_meta) else {}
    kpi = json.load(open(os.path.join(corrida, 'indicadores_kpi.json'), encoding='utf-8'))['kpis']
    dt = args.dt or float(meta.get('dt', 2))
    ttr = args.ttr if args.ttr is not None else float(kpi.get('ttr_h', 2.0))
    datos = gm.cargar_datos()
    libres = interpretar_libres(args.libres, datos)
    etiqueta = ('todos' if len(libres) == len(datos['K']) else 'ninguno' if not libres
                else '_'.join(sorted(libres)))
    itinerario = ws.leer_itinerario(corrida)

    print(f'[1/5] Modelo integrado espacio-tiempo | corrida {nombre} | DT = {dt:g} h | TTR_H = {ttr:g} h | '
          f'aviones libres: {etiqueta} ({len(libres)} de {len(datos["K"])})')
    for clave, valor in construir(datos, dt, ttr, itinerario, libres, estimar=True)['tamano'].items():
        print(f'      {clave}: {valor:,}')
    if args.estimar:
        return

    print('[2/5] Start: vuelos de la corrida con su carga y transbordos (paso final de la heurística, gap 1e-6)...',
          flush=True)
    multi_ref, val_ref = ws.resolver_carga(itinerario, ttr)
    print(f'      margen validado de la corrida: {val_ref["margen"]:,.0f} USD | validador '
          f'{"OK" if val_ref["ok"] else val_ref["violaciones"][:5]}')

    print('[3/5] Armando el modelo...', flush=True)
    t1 = time.time()
    try:
        red = construir(datos, dt, ttr, itinerario, libres)
    except MemoryError:
        print('[!] No hay memoria suficiente para armar el modelo. Pruebe con menos aviones libres (--libres).')
        sys.exit(2)
    n_ent = int(np.isin(red['vt'], [GRB.BINARY]).sum())
    print(f"      {red['n']:,} variables ({n_ent:,} binarias, {int((red['vt'] == GRB.SEMICONT).sum()):,} semicontinuas), "
          f"{red['A'].shape[0]:,} restricciones, {red['A'].nnz:,} no ceros, armado en {time.time() - t1:.0f} s",
          flush=True)
    xs = armar_start(red, itinerario, multi_ref['solucion'])
    violaciones, obj_start = verificar(red, xs)
    if violaciones:
        print(f'      [!] El start NO cumple el modelo: {violaciones}')
        sys.exit(1)
    print(f'      start factible: cumple todas las restricciones; objetivo {obj_start:,.0f} USD '
          f'(margen validado {val_ref["margen"]:,.0f})')
    if args.solo_verificar:
        return

    salida = args.salida or os.path.join('data', 'results', f'MILP_integrado_TSN_{etiqueta}')
    os.makedirs(salida, exist_ok=True)
    env = gp.Env(empty=True)
    env.setParam('OutputFlag', 1)
    env.start()
    modelo = gp.Model('MILP_integrado_espacio_tiempo', env=env)
    xv = modelo.addMVar(red['n'], lb=red['lb'], ub=red['ub'], obj=red['obj'], vtype=red['vt'])
    modelo.addMConstr(red['A'], xv, red['sentido'], red['rhs'])
    modelo.ModelSense = GRB.MAXIMIZE
    modelo.ObjCon = red['constante']
    red['A'] = None                                       # Gurobi ya tiene su copia
    xv.Start = xs
    prm = modelo.Params
    prm.TimeLimit = args.tiempo
    prm.MIPGap = args.gap
    prm.MIPFocus = args.mipfocus
    prm.NodefileStart = 0.5
    prm.LogFile = os.path.join(salida, 'gurobi.log')
    if args.norel is not None:   # sin --norel se deja el valor predeterminado de Gurobi
        prm.NoRelHeurTime = args.norel
    norel = prm.NoRelHeurTime
    if args.hilos:
        prm.Threads = args.hilos
    if args.memoria:
        prm.SoftMemLimit = args.memoria
    prm.Presolve = args.presolve
    print(f'[4/5] Gurobi: hasta {args.tiempo:,.0f} s; heurística NoRel: '
          f'{"predeterminada de Gurobi" if args.norel is None else f"{norel:,.0f} s"} '
          f'(Ctrl+C para cortar y exportar la mejor solución)...', flush=True)
    modelo.optimize()

    info = {'modelo': 'MILP integrado espacio-tiempo', 'corrida_start': nombre, 'aviones_libres': sorted(libres),
            'dt_h': dt, 'estado': ws.ESTADOS.get(modelo.Status, str(modelo.Status)),
            'objetivo_modelo': modelo.ObjVal if modelo.SolCount else None, 'cota': modelo.ObjBound,
            'gap': modelo.MIPGap if modelo.SolCount else None, 'tiempo_gurobi_s': modelo.Runtime,
            'tiempo_limite_s': args.tiempo, 'norel_s': norel, 'objetivo_start': obj_start,
            'margen_corrida_mismo_paso_final_usd': val_ref['margen'], 'margen_reportado_corrida_usd': kpi['margen_usd'],
            'variables': red['n'], 'restricciones': len(red['rhs']), 'ttr_h': ttr}
    if not modelo.SolCount:
        print('No hay solución para exportar.')
        return
    it_modelo = extraer_itinerario(red, xv.X)
    modelo.dispose()
    env.dispose()

    print('[5/5] Validación (paso final de la heurística sobre los vuelos del modelo) y exportación...', flush=True)
    multi, val = ws.resolver_carga(it_modelo, ttr)
    kpis = ws.exportar(salida, it_modelo, multi, val, datos, info, ttr)
    ruta_meta = os.path.join(salida, 'metadata.json')
    meta_s = json.load(open(ruta_meta, encoding='utf-8'))
    meta_s.update(descripcion=f'MILP integrado espacio-tiempo (DT {dt:g} h), aviones libres: {etiqueta}, '
                              f'start desde {nombre}', dt=f'{dt:g}')
    json.dump(meta_s, open(ruta_meta, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

    print('\n' + '=' * 78)
    print(f'Estado Gurobi: {info["estado"]} | objetivo del modelo {info["objetivo_modelo"]:,.0f} | '
          f'cota {info["cota"]:,.0f} | gap {100 * info["gap"]:.2f} %')
    print(f'Margen validado con transbordos: corrida {val_ref["margen"]:,.0f} -> modelo integrado '
          f'{kpis["margen_usd"]:,.0f} USD ({kpis["margen_usd"] - val_ref["margen"]:+,.0f})')
    print(f'Validador: {"OK" if val["ok"] else str(len(val["violaciones"])) + " violaciones"} | vuelos {kpis["vuelos"]} | '
          f'aviones {kpis["aviones_usados"]} | toneladas {kpis["toneladas"]:,.1f} | '
          f'frecuencias {kpis["frecuencias_cumplidas"]} | transbordos {kpis["conexiones_transferencia_usadas"]}')
    print(f'Resultados en {salida} (tiempo total {time.time() - t0:.0f} s)')


if __name__ == '__main__':
    main()
