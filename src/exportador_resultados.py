import os
import json
import pandas as pd
import gurobipy as gp
from typing import Dict, Any

def extraer_kpis(modelo: gp.Model) -> Dict[str, float]:

    return {
        "estado_optimizacion": modelo.Status,
        "funcion_objetivo": modelo.ObjVal if hasattr(modelo, 'ObjVal') else None,
        "tiempo_resolucion_segundos": modelo.Runtime,
        "gap_mip_porcentaje": modelo.MIPGap * 100 if hasattr(modelo, "MIPGap") else 0.0,
        "total_variables": modelo.NumVars,
        "total_restricciones": modelo.NumConstrs
    }

def generar_reportes(modelo: gp.Model, ruta_salida: str):

    if modelo.Status != gp.GRB.OPTIMAL:
        raise ValueError("El modelo no encontró una solución óptima para exportar.")

    os.makedirs(ruta_salida, exist_ok=True)

    with open(os.path.join(ruta_salida, "kpis.json"), "w", encoding="utf-8") as f:
        json.dump(extraer_kpis(modelo), f, indent=4)

    vars_activas = list(filter(lambda v: v.X > 0.001, modelo.getVars()))

    vuelos = list(map(
        lambda v: v.VarName.replace('y[', '').replace(']', '').split(','),
        filter(lambda v: v.VarName.startswith('y['), vars_activas)
    ))
    
    tiempos = {v.VarName: v.X for v in filter(lambda v: v.VarName.startswith('t_'), modelo.getVars())}
    
    itinerario = list(map(
        lambda v: {
            "aeronave": v[0],
            "posicion": int(v[1]),
            "origen": v[2],
            "destino": v[3],
            "hora_salida": tiempos.get(f"t_dep[{v[0]},{v[1]}]", 0.0),
            "hora_llegada": tiempos.get(f"t_arr[{v[0]},{v[1]}]", 0.0)
        }, vuelos
    ))
    
    pd.DataFrame(itinerario).to_csv(os.path.join(ruta_salida, "itinerario_vuelos.csv"), index=False)

    demanda = list(map(
        lambda v: {
            "origen": v.VarName.replace('s_q[', '').replace(']', '').split(',')[0],
            "destino": v.VarName.replace('s_q[', '').replace(']', '').split(',')[1],
            "toneladas_servidas": v.X
        },
        filter(lambda v: v.VarName.startswith('s_q['), vars_activas)
    ))
    
    pd.DataFrame(demanda).to_csv(os.path.join(ruta_salida, "demanda_servida.csv"), index=False)
