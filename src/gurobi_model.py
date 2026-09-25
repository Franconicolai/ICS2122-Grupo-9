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
    Ek, S_max_k = datos['Ek'], datos['S_max_k']
    H, TAT, M = datos['H'], datos['T_TAT'], datos['BigM']
    
    tau_e, Q_ke = datos['tau_e'], datos['Q_ke']
    D_q, r_q, c_ke = datos['D_q'], datos['r_q'], datos['c_ke']

    KS = [(k, s) for k in K for s in range(1, S_max_k[k] + 1)]
    KS_menos = [(k, s) for k in K for s in range(1, S_max_k[k])]
    KSE = [(k, s, e) for k in K for s in range(1, S_max_k[k] + 1) for e in Ek[k]]
    QKS = [(q, k, s) for q in Q for (k, s) in KS]

    # VARIABLES DE DECISIÓN
    y = modelo.addVars(KSE, vtype=GRB.BINARY, name="y")
    u = modelo.addVars(KS, vtype=GRB.BINARY, name="u")
    p = modelo.addVars(list(itertools.product(K, C)), vtype=GRB.BINARY, name="p")
    l = modelo.addVars(KS, vtype=GRB.BINARY, name="l")
    
    t_dep = modelo.addVars(KS, vtype=GRB.CONTINUOUS, lb=0, name="t_dep")
    t_arr = modelo.addVars(KS, vtype=GRB.CONTINUOUS, lb=0, name="t_arr")

    x = modelo.addVars(QKS, vtype=GRB.CONTINUOUS, lb=0, name="x")
    b = modelo.addVars(QKS, vtype=GRB.CONTINUOUS, lb=0, name="b")
    a = modelo.addVars(QKS, vtype=GRB.CONTINUOUS, lb=0, name="a")
    w = modelo.addVars(QKS, vtype=GRB.CONTINUOUS, lb=0, name="w")
    
    s_q = modelo.addVars(Q, vtype=GRB.CONTINUOUS, lb=0, name="s_q")

    # RESTRICCIONES DEL MODELO
    #P.D: los números R1, R4, R5... corresponden a la numeración de la guía de modelo del proyecto.
    #Se activa aquí solo el núcleo de programación de aeronaves + flujo básico de carga.
    
    # R1: un tramo por posición utilizada
    modelo.addConstrs((y.sum(k, s, '*') == u[k, s] for k, s in KS), name="R1")

    # R2: las posiciones se ocupan sin huecos
    modelo.addConstrs((u[k, s+1] <= u[k, s] for k, s in KS_menos), name="R2")

    # R3a: identificación de la última posición usada (k, s < S_max_k)
    modelo.addConstrs((l[k, s] == u[k, s] - u[k, s+1] for k, s in KS_menos), name="R3a")

    # R3b: identificación de la última posición usada (borde final, s = S_max_k)
    modelo.addConstrs((l[k, S_max_k[k]] == u[k, S_max_k[k]] for k in K), name="R3b")

    # R4 (versión simplificada):
    # continuidad espacial (el destino del tramo en s debe ser el origen del tramo en s+1).
    modelo.addConstrs((
        gp.quicksum(y[k, s, e] for e in Ek[k] if e[1] == e2[0]) >= y[k, s+1, e2] + u[k, s] - 1
        for k, s in KS_menos for e2 in Ek[k]
    ), name="R4_continuidad")

    # R5: hora de llegada = hora de salida + tiempo de vuelo del tramo elegido
    modelo.addConstrs((t_arr[k, s] == t_dep[k, s] + gp.quicksum(tau_e[e] * y[k, s, e] for e in Ek[k]) for k, s in KS), name="R5")

    # R6a: la salida no puede ocurrir si la posición no está en uso
    modelo.addConstrs((t_dep[k, s] <= H * u[k, s] for k, s in KS), name="R6a")

    # R6b: la llegada no puede ocurrir si la posición no está en uso
    modelo.addConstrs((t_arr[k, s] <= H * u[k, s] for k, s in KS), name="R6b")

    # R11: tiempo mínimo en tierra (TAT) entre vuelos consecutivos
    modelo.addConstrs((t_dep[k, s+1] >= t_arr[k, s] + TAT - M * (1 - u[k, s+1]) for k, s in KS_menos), name="R11_TAT")

    # R8: capacidad (la carga total no puede superar el payload del avión en esa operación)
    modelo.addConstrs((x.sum('*', k, s) <= gp.quicksum(Q_ke[k, e] * y[k, s, e] for e in Ek[k]) for k, s in KS), name="R8_capacidad")

    # R20a: balance al entrar — posición 1, sin operación previa del mismo avión
    modelo.addConstrs((x[q, k, 1] == b[q, k, 1] for q in Q for k in K), name="R20a")

    # R20b: balance al entrar — s > 1, puede venir de continuar (w) en el mismo avión
    modelo.addConstrs((x[q, k, s+1] == b[q, k, s+1] + w[q, k, s] for q in Q for k, s in KS_menos), name="R20b")

    # R21a: balance al salir — para s < S_max_k (existe posición siguiente)
    modelo.addConstrs((x[q, k, s] == a[q, k, s] + w[q, k, s] for q in Q for k, s in KS if s < S_max_k[k]), name="R21a")

    # R21b: balance al salir — última posición potencial (no hay w hacia afuera)
    modelo.addConstrs((x[q, k, S_max_k[k]] == a[q, k, S_max_k[k]] for q in Q for k in K), name="R21b")

    # W_pos_terminal: en la última posición de cada avión no puede haber carga "continuando" hacia una posición inexistente
    modelo.addConstrs((w[q, k, S_max_k[k]] == 0 for q in Q for k in K), name="W_pos_terminal")

    # R23: solo se puede embarcar carga nueva en un vuelo que parte del origen del commodity
    modelo.addConstrs((b[q, k, s] <= D_q[q] * gp.quicksum(y[k, s, e] for e in Ek[k] if e[0] == q[0]) for q, k, s in QKS), name="R23")

    # R25: solo se puede descargar carga en un vuelo que llega al destino del commodity
    modelo.addConstrs((a[q, k, s] <= D_q[q] * gp.quicksum(y[k, s, e] for e in Ek[k] if e[1] == q[1]) for q, k, s in QKS), name="R25")
    
    # Diccionario auxiliar: para cada commodity q, qué pares (avión, posición) puede usar.
    #porque antes fallo con q como tupla (limitación gurobi)
    ks_por_q = {}
    for (qq, k, s) in QKS:
        ks_por_q.setdefault(qq, []).append((k, s))

    # R26a: cantidad servida = total embarcado
    modelo.addConstrs((s_q[q] == gp.quicksum(b[q, k, s] for k, s in ks_por_q[q]) for q in Q), name="R26a")

    # R26b: cantidad servida = total descargado 
    modelo.addConstrs((s_q[q] == gp.quicksum(a[q, k, s] for k, s in ks_por_q[q]) for q in Q), name="R26b")

    # R26c: no se puede servir más de lo que hay disponible
    modelo.addConstrs((s_q[q] <= D_q[q] for q in Q), name="R26c")

    # FUNCIÓN OBJETIVO
    ingresos = gp.quicksum(map(lambda q: 1000 * r_q[q] * s_q[q], Q))
    costos = gp.quicksum(map(lambda kse: c_ke[kse[0], kse[2]] * y[kse[0], kse[1], kse[2]], KSE))
    
    modelo.setObjective(ingresos - costos, GRB.MAXIMIZE)
    modelo.update()
    
    return modelo
