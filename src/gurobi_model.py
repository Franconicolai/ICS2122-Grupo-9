import gurobipy as gp
from gurobipy import GRB
import itertools
from typing import Dict, Any

def construir_modelo_completo(datos: Dict[str, Any]) -> gp.Model:
 
    env = gp.Env(empty=True)
    env.setParam("OutputFlag", 1)
    env.start()
    modelo = gp.Model("ICS2122_GRUPO_9_FLEET_ASSIGNMENT_Y_CARGO_ROUTING", env=env)
    
    # SETS DE DATOS PRE-CALCULADOS EN LA CAPA DE DATOS
    K, E, C, Q = datos['K'], datos['E'], datos['C'], datos['Q']
    S_max, H, TAT, M = datos['S_max'], datos['H'], datos['T_TAT'], datos['BigM']
    
    S = list(range(1, S_max + 1))
    S_menos = list(range(1, S_max))
    
    tau_e, Q_ke = datos['tau_e'], datos['Q_ke']
    D_q, r_q, c_ke = datos['D_q'], datos['r_q'], datos['c_ke']

    # VARIABLES DE DECISIÓN
    y = modelo.addVars(K, S, E, vtype=GRB.BINARY, name="y")
    u = modelo.addVars(K, S, vtype=GRB.BINARY, name="u")
    p = modelo.addVars(K, S_menos, C, vtype=GRB.BINARY, name="p")
    l = modelo.addVars(K, S, vtype=GRB.BINARY, name="l")
    
    t_dep = modelo.addVars(K, S, vtype=GRB.CONTINUOUS, lb=0, name="t_dep")
    t_arr = modelo.addVars(K, S, vtype=GRB.CONTINUOUS, lb=0, name="t_arr")

    x = modelo.addVars(Q, K, S, vtype=GRB.CONTINUOUS, lb=0, name="x")
    b = modelo.addVars(Q, K, S, vtype=GRB.CONTINUOUS, lb=0, name="b")
    a = modelo.addVars(Q, K, S, vtype=GRB.CONTINUOUS, lb=0, name="a")
    w = modelo.addVars(Q, K, S, vtype=GRB.CONTINUOUS, lb=0, name="w")

    s_q = modelo.addVars(Q, vtype=GRB.CONTINUOUS, lb=0, name="s_q")

    # RESTRICCIONES DEL MODELO

    modelo.addConstrs((y.sum(k, s, '*') == u[k, s] for k, s in itertools.product(K, S)), name="R1")

    modelo.addConstrs((u[k, s+1] <= u[k, s] for k, s in itertools.product(K, S_menos)), name="R2a")
    modelo.addConstrs((l[k, s] == u[k, s] - u[k, s+1] for k, s in itertools.product(K, S_menos)), name="R2b")
    modelo.addConstrs((l[k, S[-1]] == u[k, S[-1]] for k in K), name="R2c")

    modelo.addConstrs((t_arr[k, s] == t_dep[k, s] + gp.quicksum(tau_e[e] * y[k, s, e] for e in E) 
                       for k, s in itertools.product(K, S)), name="R4a")
    modelo.addConstrs((t_dep[k, s] <= H * u[k, s] for k, s in itertools.product(K, S)), name="R4c")
    modelo.addConstrs((t_arr[k, s] <= H * u[k, s] for k, s in itertools.product(K, S)), name="R4d")
    
    modelo.addConstrs((t_dep[k, s+1] >= t_arr[k, s] + TAT - M * (1 - u[k, s+1]) 
                       for k, s in itertools.product(K, S_menos)), name="R4b")

    modelo.addConstrs((x.sum('*', k, s) <= gp.quicksum(Q_ke[k, e] * y[k, s, e] for e in E) 
                       for k, s in itertools.product(K, S)), name="R6")

    modelo.addConstrs((x[q, k, 1] == b[q, k, 1] for q, k in itertools.product(Q, K)), name="R7a_1")
    modelo.addConstrs((x[q, k, 1] == a[q, k, 1] + w[q, k, 1] for q, k in itertools.product(Q, K)), name="R7b_1")
    
    modelo.addConstrs((x[q, k, s+1] == b[q, k, s+1] + w[q, k, s] for q, k, s in itertools.product(Q, K, S_menos)), name="R7a_S")
    modelo.addConstrs((x[q, k, s+1] == a[q, k, s+1] + w[q, k, s+1] for q, k, s in itertools.product(Q, K, S_menos)), name="R7b_S")
    
    modelo.addConstrs((w[q, k, S[-1]] == 0 for q, k in itertools.product(Q, K)), name="W_terminal")

    modelo.addConstrs(
        (b[q, k, s] <= D_q[q] * gp.quicksum(y[k, s, e] for e in filter(lambda leg: leg[0] == q[0], E))
         for q, k, s in itertools.product(Q, K, S)), name="R9a"
    )
    modelo.addConstrs(
        (a[q, k, s] <= D_q[q] * gp.quicksum(y[k, s, e] for e in filter(lambda leg: leg[1] == q[1], E))
         for q, k, s in itertools.product(Q, K, S)), name="R9b"
    )
    modelo.addConstrs((s_q[q] == b.sum(q, '*', '*') for q in Q), name="R9c")
    modelo.addConstrs((s_q[q] == a.sum(q, '*', '*') for q in Q), name="R9d")
    modelo.addConstrs((s_q[q] <= D_q[q] for q in Q), name="R9e")

    # FUNCIÓN OBJETIVO
    ingresos = gp.quicksum(map(lambda q: 1000 * r_q[q] * s_q[q], Q))
    costos = gp.quicksum(map(lambda args: c_ke[args[0], args[2]] * y[args[0], args[1], args[2]], itertools.product(K, S, E)))
    
    modelo.setObjective(ingresos - costos, GRB.MAXIMIZE)
    modelo.update()
    
    return modelo
