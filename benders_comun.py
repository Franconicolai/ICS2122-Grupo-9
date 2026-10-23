"""
Piezas comunes de las dos descomposiciones de Benders del modelo integrado en red espacio-tiempo
(benders_binarias_maestro.py y benders_l_shaped.py).

El modelo completo es el de modelo_integrado_espacio_tiempo.py: vuelos de cada avión, carga multitramo y transbordos
exactos, con bloques de DT horas. Aquí se divide automáticamente en:
  - maestro: variables de los aviones (y, según la variante, las binarias por vuelo de carga);
  - subproblema: la carga. Las restricciones que mezclan variables de ambos lados quedan en el subproblema con su
    lado derecho dependiente del maestro: A_s x (<=,=,>=) b_s - T y.

El valor del subproblema Q(y) (ganancia por carga) es cóncavo en y cuando el subproblema es un LP, así que cada
evaluación da un corte de optimalidad (corte de subgradiente):  theta <= Q(y^) + g' (y - y^),  g = T' pi,
donde pi son los duales del subproblema escrito como minimización de -ganancia.
"""
import math
import os
import time

import numpy as np
import scipy.sparse as sp
import gurobipy as gp
from gurobipy import GRB

import warm_start_milp as ws
import gurobi_model as gm
import modelo_integrado_espacio_tiempo as mi

TOL_CORTE = 1e-6       # violación relativa mínima para agregar un corte
PENALIZACION = 1e5     # USD por tonelada faltante para el mínimo de 10 t (variante A); exacta si supera el valor dual


def dt_valido(dt):
    T = gm.H / dt
    if abs(T - round(T)) > 1e-9:
        raise ValueError(f'DT = {dt} h no divide las 168 h de la semana (use p. ej. 0.25, 0.5, 0.75, 1, 2)')
    return int(round(T))


def itinerario_en_grilla(itinerario, dt):
    """True si todas las salidas de la corrida caen en la grilla de DT horas."""
    return all(abs(l['t_dep'] / dt - round(l['t_dep'] / dt)) < 1e-6 for legs in itinerario.values() for l in legs)


def construir_descomposicion(datos, dt, ttr, itinerario, libres, binarias_en_maestro):
    """Arma el modelo integrado completo y lo separa en maestro y subproblema.
    binarias_en_maestro=True (variante A): z, u, g, f y una binaria nueva lc (vuelo con carga >= 10 t) van al
        maestro; el subproblema queda como LP (con holgura penalizada para el mínimo de 10 t).
    binarias_en_maestro=False (variante B): solo z, u, g van al maestro; f y la carga semicontinua quedan en el
        subproblema, que es un MIP."""
    red = mi.construir(datos, dt, ttr, itinerario, libres)
    A, n = red['A'].tocsr(), red['n']
    lb, ub, obj, vt = red['lb'].copy(), red['ub'].copy(), red['obj'].copy(), red['vt'].copy()
    rhs, sentido = red['rhs'].copy(), red['sentido'].copy()
    nArc = len(red['a_k'])
    a_P = np.array([datos['P_k'][red['K'][k]] for k in red['a_k']])
    maestro = np.zeros(n, dtype=bool)
    for nombre in ('z', 'U'):
        idx = red[nombre][red[nombre] >= 0]
        maestro[idx] = True
    maestro[red['G'][red['G'] >= 0]] = True
    extra = {}
    if binarias_en_maestro:
        maestro[red['f']] = True
        # L deja de ser semicontinua: lc (maestro) decide si el vuelo lleva carga; holgura s penalizada
        L = red['L']
        vt[L] = GRB.CONTINUOUS
        lb[L] = 0.0
        lc = np.arange(n, n + nArc)
        s = np.arange(n + nArc, n + 2 * nArc)
        n2 = n + 2 * nArc
        lb = np.concatenate([lb, np.zeros(nArc), np.zeros(nArc)])
        ub = np.concatenate([ub, np.ones(nArc), np.full(nArc, datos['min_ton'])])
        obj = np.concatenate([obj, np.zeros(nArc), np.full(nArc, -PENALIZACION)])
        vt = np.concatenate([vt, np.full(nArc, GRB.BINARY), np.full(nArc, GRB.CONTINUOUS)])
        maestro = np.concatenate([maestro, np.ones(nArc, dtype=bool), np.zeros(nArc, dtype=bool)])
        filas = np.arange(nArc)
        # L + s - 10 lc >= 0 ;  L - P lc <= 0 ;  lc - z <= 0 (libres) ; lc <= 1 (fijos, por cota)
        nuevas = [sp.csr_matrix((np.concatenate([np.ones(nArc), np.ones(nArc), -datos['min_ton'] * np.ones(nArc)]),
                                 (np.concatenate([filas, filas, filas]), np.concatenate([L, s, lc]))), shape=(nArc, n2)),
                  sp.csr_matrix((np.concatenate([np.ones(nArc), -a_P]), (np.concatenate([filas, filas]),
                                 np.concatenate([L, lc]))), shape=(nArc, n2))]
        libres_arc = np.nonzero(red['z'] >= 0)[0]
        nuevas.append(sp.csr_matrix((np.concatenate([np.ones(len(libres_arc)), -np.ones(len(libres_arc))]),
                                     (np.concatenate([np.arange(len(libres_arc))] * 2),
                                      np.concatenate([lc[libres_arc], red['z'][libres_arc]]))),
                                    shape=(len(libres_arc), n2)))
        A = sp.vstack([sp.hstack([A, sp.csr_matrix((A.shape[0], 2 * nArc))])] + nuevas).tocsr()
        rhs = np.concatenate([rhs, np.zeros(nArc), np.zeros(nArc), np.zeros(len(libres_arc))])
        sentido = np.concatenate([sentido, np.full(nArc, '>'), np.full(nArc, '<'), np.full(len(libres_arc), '<')])
        extra = {'lc': lc, 's': s}
        n = n2
    patron = A.copy()
    patron.data[:] = 1.0
    en_m = patron @ maestro.astype(float)
    en_s = patron @ (~maestro).astype(float)
    filas_m = (en_m > 0) & (en_s == 0)
    filas_s = en_s > 0
    cols_m, cols_s = np.nonzero(maestro)[0], np.nonzero(~maestro)[0]
    As = A[filas_s]
    return {
        'red': red, 'datos': datos, 'n': n, 'extra': extra, 'cols_m': cols_m, 'cols_s': cols_s,
        'A_m': A[filas_m][:, cols_m], 'rhs_m': rhs[filas_m], 'sen_m': sentido[filas_m],
        'lb_m': lb[cols_m], 'ub_m': ub[cols_m], 'obj_m': obj[cols_m], 'vt_m': vt[cols_m],
        'A_s': As[:, cols_s], 'T': As[:, cols_m].tocsr(), 'rhs_s': rhs[filas_s], 'sen_s': sentido[filas_s],
        'lb_s': lb[cols_s], 'ub_s': ub[cols_s], 'obj_s': obj[cols_s], 'vt_s': vt[cols_s],
        'constante': red['constante'],
        'filas': {'maestro': int(filas_m.sum()), 'subproblema': int(filas_s.sum()),
                  'acopladas': int(((en_m > 0) & (en_s > 0)).sum())},
    }


class Subproblema:
    """Subproblema de carga: max ganancia s.a. A_s x (sen) rhs_s - T y. Se escribe como min -ganancia para
    leer los duales con la convención estándar de Gurobi. relajado=True usa la relajación lineal."""

    def __init__(self, d, relajado, hilos=0, tiempo=None, gap=1e-6, nombre='subproblema'):
        self.d, self.relajado = d, relajado
        self.env = gp.Env(empty=True)
        self.env.setParam('OutputFlag', 0)
        self.env.start()
        m = gp.Model(nombre, env=self.env)
        vt = d['vt_s'].copy()
        lb = d['lb_s'].copy()
        if relajado:
            semi = vt == GRB.SEMICONT
            lb[semi] = 0.0
            vt[:] = GRB.CONTINUOUS
        self.x = m.addMVar(len(vt), lb=lb, ub=d['ub_s'], obj=-d['obj_s'], vtype=vt)
        self.c = m.addMConstr(d['A_s'], self.x, d['sen_s'], d['rhs_s'])
        m.ModelSense = GRB.MINIMIZE
        if hilos:
            m.Params.Threads = hilos
        if relajado:
            m.Params.Method = 1          # simplex dual: reaprovecha la base entre evaluaciones
            m.Params.LPWarmStart = 2     # con la base previa pero sin saltarse el presolve (sin esto, un punto
                                         # lejano puede tardar 100 veces más)
        else:
            m.Params.MIPGap = gap
            if tiempo:
                m.Params.TimeLimit = tiempo
        self.m = m
        self.evaluaciones, self.segundos = 0, 0.0

    def evaluar(self, y):
        """Devuelve (Q, cota_superior_de_Q, subgradiente g sobre las columnas del maestro o None, x)."""
        t0 = time.time()
        self.c.setAttr('RHS', self.d['rhs_s'] - self.d['T'] @ y)
        self.m.optimize()
        self.evaluaciones += 1
        self.segundos += time.time() - t0
        if self.m.Status not in (GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL) or self.m.SolCount == 0:
            raise RuntimeError(f'subproblema sin solución (estado {self.m.Status})')
        q = -self.m.ObjVal
        if self.relajado:
            pi = self.c.Pi
            g = self.d['T'].T @ pi        # dQ/dy = -(dObjMin/drhs)(-T) = T' pi
            return q, q, g, self.x.X
        return q, -self.m.ObjBound, None, self.x.X


def agregar_corte(modelo, theta, yvars, q, g, y_hat, callback=False):
    """theta <= q + g'(y - y_hat). Devuelve el lado derecho constante para revisar violación."""
    nz = np.nonzero(np.abs(g) > 1e-9)[0]
    constante = q - float(g[nz] @ y_hat[nz])
    expr = gp.LinExpr(list(-g[nz]), [yvars[i] for i in nz])
    expr.add(theta)
    if callback:
        modelo.cbLazy(expr <= constante)
    else:
        modelo.addConstr(expr <= constante)
    return constante


def start_maestro(d, itinerario, solucion_carga):
    """Valores del maestro (y la carga) para la solución de la corrida, si cae en la grilla."""
    red = d['red']
    xs = mi.armar_start(red, itinerario, solucion_carga)
    if d['extra']:                               # variante A: lc = vuelo con carga; L ya está en xs
        xs = np.concatenate([xs, (xs[red['L']] > 1e-6).astype(float), np.zeros(len(red['a_k']))])
    return xs[d['cols_m']], xs


def cota_superior_carga(datos):
    """Cota simple de Q: todo el ingreso posible de la demanda (sin costos)."""
    return float(sum(1000 * datos['r_q'][q] * datos['D_q'][q] for q in datos['Q']))


def itinerario_desde_maestro(d, y):
    x = np.zeros(d['n'])
    x[d['cols_m']] = y
    return mi.extraer_itinerario(d['red'], x)


def corte_l_shaped(modelo, theta, yvars, binarias, y_hat, q_sup, U, callback=False):
    """Corte de optimalidad entero (Laporte y Louveaux, 1993) para maestro binario, versión maximización:
    theta <= q_sup + (U - q_sup) * [ sum_{i: y^_i = 1} (1 - y_i) + sum_{i: y^_i = 0} y_i ]
    Es exacto en y^ y no restringe ninguna otra solución entera (cuyo valor es <= U)."""
    uno = binarias[y_hat[binarias] > 0.5]
    cero = binarias[y_hat[binarias] <= 0.5]
    a = U - q_sup
    expr = gp.LinExpr([a] * len(cero) + [-a] * len(uno), [yvars[i] for i in cero] + [yvars[i] for i in uno])
    expr = theta - expr
    rhs = q_sup + a * len(uno)
    if callback:
        modelo.cbLazy(expr <= rhs)
    else:
        modelo.addConstr(expr <= rhs)


def ejecutar(variante, descripcion):
    """variante 'binarias_maestro' (A) o 'l_shaped' (B). Ver los docstrings de cada script."""
    import argparse
    import json
    import sys
    A_ = variante == 'binarias_maestro'
    ap = argparse.ArgumentParser(description=descripcion)
    ap.add_argument('--libres', default='todos',
                    help='aviones que decide el modelo: todos (problema completo), ninguno, aviones (AC-06,AC-10) u '
                         'operadores (BRA). Por defecto todos')
    ap.add_argument('--dt', type=float, default=None,
                    help='horas entre nodos de la red espacio-tiempo (0.25 = 15 min, 0.5, 0.75 = 45 min, 1, 2...); '
                         'por defecto el DT de la corrida')
    ap.add_argument('--corrida', default=ws.CORRIDA_2H, help='corrida de la heurística usada como solución inicial')
    ap.add_argument('--ttr', type=float, default=None, help='TTR_H (h) de los transbordos; por defecto el de la corrida')
    ap.add_argument('--tiempo', type=float, default=3600, help='segundos de la fase 2 (árbol con cortes). Por defecto 3600')
    ap.add_argument('--fase1-tiempo', type=float, default=1800, help='segundos máximos de la fase 1. Por defecto 1800')
    ap.add_argument('--fase1-iter', type=int, default=500, help='iteraciones máximas de la fase 1. Por defecto 500')
    ap.add_argument('--sin-fase1', action='store_true', help='omite la fase 1 (relajación lineal con cortes)')
    if not A_:
        ap.add_argument('--tiempo-sub', type=float, default=600,
                        help='segundos máximos de cada subproblema entero. Por defecto 600')
    ap.add_argument('--gap', type=float, default=1e-4, help='gap de término de la fase 2. Por defecto 1e-4')
    ap.add_argument('--hilos', type=int, default=0, help='Threads de Gurobi (0 = todos)')
    ap.add_argument('--memoria', type=float, default=0, help='GB máximos para el maestro (0 = sin límite)')
    ap.add_argument('--salida', default=None, help='carpeta de salida (por defecto data/results/Benders_<variante>_...)')
    ap.add_argument('--estimar', action='store_true', help='solo muestra el tamaño de la descomposición')
    args = ap.parse_args()

    t0 = time.time()
    corrida = os.path.normpath(args.corrida)
    nombre = os.path.basename(corrida)
    ruta_meta = os.path.join(corrida, 'metadata.json')
    meta = json.load(open(ruta_meta, encoding='utf-8')) if os.path.exists(ruta_meta) else {}
    kpi = json.load(open(os.path.join(corrida, 'indicadores_kpi.json'), encoding='utf-8'))['kpis']
    dt = args.dt or float(meta.get('dt', 2))
    T = dt_valido(dt)
    ttr = args.ttr if args.ttr is not None else float(kpi.get('ttr_h', 2.0))
    datos = gm.cargar_datos()
    libres = mi.interpretar_libres(args.libres, datos)
    etiqueta = ('todos' if len(libres) == len(datos['K']) else 'ninguno' if not libres
                else '_'.join(sorted(libres)))
    itinerario = ws.leer_itinerario(corrida)
    en_grilla = itinerario_en_grilla(itinerario, dt)
    if not en_grilla and len(libres) < len(datos['K']):
        raise SystemExit(f'La corrida {nombre} no cae en la grilla de {dt:g} h y hay aviones fijos: use --libres todos '
                         f'o una corrida con un DT compatible.')
    nombre_alg = 'Benders con binarias en el maestro' if A_ else 'Benders integer L-shaped'
    print(f'[1/5] {nombre_alg} | DT = {dt:g} h ({T} bloques) | TTR_H = {ttr:g} h | aviones libres: {etiqueta} '
          f'({len(libres)} de {len(datos["K"])}) | start: {"corrida " + nombre if en_grilla else "ninguno (otra grilla)"}')
    for clave, valor in mi.construir(datos, dt, ttr, itinerario, libres, estimar=True)['tamano'].items():
        print(f'      {clave}: {valor:,}')
    if args.estimar:
        return

    print('[2/5] Armando y separando el modelo integrado...', flush=True)
    t1 = time.time()
    d = construir_descomposicion(datos, dt, ttr, itinerario, libres, binarias_en_maestro=A_)
    nm = len(d['cols_m'])
    print(f"      maestro: {nm:,} variables ({int((d['vt_m'] != GRB.CONTINUOUS).sum()):,} enteras), "
          f"{d['filas']['maestro']:,} restricciones | subproblema: {len(d['cols_s']):,} variables, "
          f"{d['filas']['subproblema']:,} restricciones ({d['filas']['acopladas']:,} dependen del maestro) | "
          f"{time.time() - t1:.0f} s", flush=True)
    sub_lp = Subproblema(d, relajado=True, hilos=args.hilos, nombre='subproblema_LP')
    sub_ip = None if A_ else Subproblema(d, relajado=False, hilos=args.hilos, tiempo=args.tiempo_sub,
                                         nombre='subproblema_MIP')
    U = cota_superior_carga(datos)
    binarias = np.nonzero(d['vt_m'] == GRB.BINARY)[0]
    obj_m, cte = d['obj_m'], d['constante']

    def evaluar(y):
        """(objetivo real con y, Q factible, cota de Q, subgradiente LP, Q de la relajación LP)."""
        q_lp, _, g, _ = sub_lp.evaluar(y)
        if A_:
            return float(obj_m @ y) + cte + q_lp, q_lp, q_lp, g, q_lp
        q_int, q_sup, _, _ = sub_ip.evaluar(y)
        return float(obj_m @ y) + cte + q_int, q_int, q_sup, g, q_lp

    mejor = {'obj': -math.inf, 'y': None, 'q': None}
    y0 = None
    if en_grilla:
        print('      solución inicial: vuelos y carga de la corrida (paso final de la heurística, gap 1e-6)...', flush=True)
        multi_ref, val_ref = ws.resolver_carga(itinerario, ttr)
        y0, _ = start_maestro(d, itinerario, multi_ref['solucion'])
        f0, q0, q0s, g0, q0lp = evaluar(y0)
        mejor.update(obj=f0, y=y0.copy(), q=q0)
        print(f'      solución inicial: objetivo {f0:,.0f} USD (margen validado de la corrida {val_ref["margen"]:,.0f})')

    # ---------------- maestro
    env = gp.Env(empty=True)
    env.setParam('OutputFlag', 1)
    env.start()
    m = gp.Model('maestro', env=env)
    yv = m.addMVar(nm, lb=d['lb_m'], ub=d['ub_m'], obj=obj_m, vtype=d['vt_m'])
    theta = m.addVar(lb=-GRB.INFINITY, ub=U, obj=1.0, name='theta')
    m.addMConstr(d['A_m'], yv, d['sen_m'], d['rhs_m'])
    m.ModelSense = GRB.MAXIMIZE
    m.ObjCon = cte
    m.update()
    yvars = yv.tolist()
    cortes = []
    if y0 is not None:
        cortes.append((q0lp, g0, y0.copy()))
        agregar_corte(m, theta, yvars, q0lp, g0, y0)

    # ---------------- fase 1: relajación lineal del maestro + cortes de subgradiente
    cota_f1 = None
    if not args.sin_fase1:
        print('[3/5] Fase 1: relajación lineal del maestro con cortes de subgradiente...', flush=True)
        rel = m.relax()
        rel.Params.OutputFlag = 0
        rel.Params.Method = 1
        rel.Params.LPWarmStart = 2
        rv = rel.getVars()
        ryv, rth = rv[:nm], rv[nm]
        t_f1 = time.time()
        for it in range(1, args.fase1_iter + 1):
            rel.optimize()
            if rel.Status != GRB.OPTIMAL:
                print(f'      maestro relajado sin óptimo (estado {rel.Status})')
                break
            y_hat = np.array(rel.getAttr('X', ryv))
            th_hat = rth.X
            cota_f1 = rel.ObjVal
            q_lp, _, g, _ = sub_lp.evaluar(y_hat)
            viol = th_hat - q_lp
            if it == 1 or it % 10 == 0:
                print(f'      it {it:4d} | cota (maestro LP) {cota_f1:,.0f} | Q LP en y^ {q_lp:,.0f} | '
                      f'theta^ - Q {viol:,.0f} | {time.time() - t_f1:.0f} s', flush=True)
            if viol <= TOL_CORTE * max(1.0, abs(q_lp)) or time.time() - t_f1 > args.fase1_tiempo:
                break
            cortes.append((q_lp, g, y_hat))
            agregar_corte(rel, rth, ryv, q_lp, g, y_hat)
            agregar_corte(m, theta, yvars, q_lp, g, y_hat)
        print(f'      fase 1 terminada: {len(cortes)} cortes | cota de la relajación {cota_f1:,.0f} USD | '
              f'{time.time() - t_f1:.0f} s')
        rel.dispose()

    # ---------------- fase 2: árbol del maestro entero con cortes perezosos
    print(f'[4/5] Fase 2: maestro entero con cortes {"de Benders" if A_ else "L-shaped y de subgradiente"} '
          f'(hasta {args.tiempo:,.0f} s)...', flush=True)
    pendiente = {'sol': None}
    estad = {'lazy': 0, 'ls': 0}

    def cb(modelo, donde):
        if donde == GRB.Callback.MIPSOL:
            y_hat = np.array(modelo.cbGetSolution(yvars))
            th_hat = modelo.cbGetSolution(theta)
            y_hat[binarias] = np.round(y_hat[binarias])
            f, q, q_sup, g, q_lp = evaluar(y_hat)
            if f > mejor['obj'] + 1e-6:
                mejor.update(obj=f, y=y_hat.copy(), q=q)
                pendiente['sol'] = (y_hat.copy(), q)
                print(f'      nueva mejor solución: {f:,.0f} USD', flush=True)
            if th_hat > q_lp + TOL_CORTE * max(1.0, abs(q_lp)):
                agregar_corte(modelo, theta, yvars, q_lp, g, y_hat, callback=True)
                estad['lazy'] += 1
            if not A_ and th_hat > q_sup + TOL_CORTE * max(1.0, abs(q_sup)):
                corte_l_shaped(modelo, theta, yvars, binarias, y_hat, q_sup, U, callback=True)
                estad['ls'] += 1
        elif donde == GRB.Callback.MIPNODE and pendiente['sol'] is not None:
            if modelo.cbGet(GRB.Callback.MIPNODE_STATUS) == GRB.OPTIMAL:
                y_s, q_s = pendiente['sol']
                modelo.cbSetSolution(yvars + [theta], list(y_s) + [q_s])
                modelo.cbUseSolution()
                pendiente['sol'] = None

    prm = m.Params
    prm.LazyConstraints = 1
    prm.TimeLimit = args.tiempo
    prm.MIPGap = args.gap
    salida = args.salida or os.path.join('data', 'results', f'Benders_{variante}_{etiqueta}_dt{dt:g}')
    os.makedirs(salida, exist_ok=True)
    prm.LogFile = os.path.join(salida, 'gurobi.log')
    if args.hilos:
        prm.Threads = args.hilos
    if args.memoria:
        prm.SoftMemLimit = args.memoria
    if mejor['y'] is not None:
        yv.Start = mejor['y']
        theta.Start = mejor['q']
    m.optimize(cb)

    cota = m.ObjBound if m.SolCount else (cota_f1 if cota_f1 is not None else math.inf)
    if cota_f1 is not None:
        cota = min(cota, cota_f1)
    gap = (cota - mejor['obj']) / abs(mejor['obj']) if mejor['y'] is not None and mejor['obj'] else None
    estado = ws.ESTADOS.get(m.Status, str(m.Status))
    print(f'      fase 2: {estado} | cortes de subgradiente {estad["lazy"]} | cortes L-shaped {estad["ls"]} | '
          f'evaluaciones LP {sub_lp.evaluaciones} ({sub_lp.segundos:.0f} s)'
          + ('' if A_ else f' | MIP {sub_ip.evaluaciones} ({sub_ip.segundos:.0f} s)'))
    if mejor['y'] is None:
        print('No hay solución para exportar.')
        return
    info = {'modelo': nombre_alg, 'dt_h': dt, 'corrida_start': nombre if en_grilla else None,
            'aviones_libres': sorted(libres), 'estado': estado, 'objetivo_modelo': mejor['obj'], 'cota': cota,
            'gap': gap, 'cota_fase1': cota_f1, 'tiempo_gurobi_s': m.Runtime, 'tiempo_limite_s': args.tiempo,
            'cortes_fase1': len(cortes), 'cortes_lazy': estad['lazy'], 'cortes_l_shaped': estad['ls'], 'ttr_h': ttr}
    m.dispose()
    print('[5/5] Validación (paso final de la heurística sobre los vuelos) y exportación...', flush=True)
    kpis, val = exportar_resultado(salida, d, mejor['y'], ttr, info, nombre, etiqueta)
    print('\n' + '=' * 78)
    print(f'{nombre_alg}: mejor solución {mejor["obj"]:,.0f} USD | cota global {cota:,.0f} USD | '
          f'gap {100 * gap:.3f} %' if gap is not None else '')
    print(f'Margen validado con transbordos: {kpis["margen_usd"]:,.0f} USD | validador '
          f'{"OK" if val["ok"] else str(len(val["violaciones"])) + " violaciones"} | vuelos {kpis["vuelos"]} | '
          f'frecuencias {kpis["frecuencias_cumplidas"]}')
    print(f'Resultados en {salida} (tiempo total {time.time() - t0:.0f} s)')


def exportar_resultado(salida, d, y, ttr, info, nombre_corrida, etiqueta):
    """Vuelos del maestro -> carga final con el paso final de la heurística -> validador -> archivos."""
    it = itinerario_desde_maestro(d, y)
    multi, val = ws.resolver_carga(it, ttr)
    kpis = ws.exportar(salida, it, multi, val, d['datos'], info, ttr)
    import json
    ruta = os.path.join(salida, 'metadata.json')
    meta = json.load(open(ruta, encoding='utf-8'))
    meta.update(descripcion=f"{info['modelo']} (DT {info['dt_h']:g} h), aviones libres: {etiqueta}, "
                            f"start desde {nombre_corrida}", dt=f"{info['dt_h']:g}")
    json.dump(meta, open(ruta, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    return kpis, val
