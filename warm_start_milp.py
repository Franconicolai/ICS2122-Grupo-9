"""
Resuelve el MILP completo (gurobi_model.py) partiendo de la solución de una corrida de la heurística (MIP start).

Uso, desde la raíz del repositorio:
    python warm_start_milp.py                       # corrida de 2 h como start, 1 hora de Gurobi
    python warm_start_milp.py --tiempo 14400        # 4 horas de Gurobi
    python warm_start_milp.py --solo-verificar      # arma el modelo y revisa el start, sin optimizar
    python warm_start_milp.py --help                # todas las opciones

Pasos:
 1. Lee el itinerario de la corrida (itinerario_vuelos.csv).
 2. Recalcula la carga de esos vuelos sin transbordos con el submodelo de la heurística
    (src/asignacion_carga_multitramos.py), porque el MILP no tiene transbordos entre aviones.
 3. Arma el MIP start (un valor para cada variable del MILP) y revisa que cumpla todas sus restricciones.
 4. Resuelve el MILP con Gurobi desde ese start. La heurística NoRel, que en las pruebas fue la que mejoró el start,
    usa el valor predeterminado de Gurobi salvo que se indique --norel. Se puede cortar con Ctrl+C: se exporta la
    mejor solución.
 5. Sobre los vuelos del MILP agrega los transbordos con el mismo submodelo de la heurística (TTR_H), valida
    con src/validador.py y exporta los resultados con el mismo formato que main.py (sirven para el visualizador).
"""
import argparse
import json
import os
import pickle
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

RAIZ = os.path.dirname(os.path.abspath(__file__))
os.chdir(RAIZ)                                   # main.generar_json_visualizacion usa rutas relativas a la raíz
os.environ.setdefault('SOLVER', 'gurobi')        # solver del submodelo de carga (se lee al importar solver_util)
sys.path.insert(0, os.path.join(RAIZ, 'src'))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(errors='replace')

from gurobipy import GRB                                          # noqa: E402

import gurobi_model as gm                                         # noqa: E402
from asignacion_carga_multitramos import asignar_carga_multitramos  # noqa: E402
from validador import validar                                     # noqa: E402

CORRIDA_2H = os.path.join('data', 'results', 'Ejecucion_con_intervalos_de_2_horas')
SIN_TRANSBORDOS = 1e6    # TTR_H enorme: el submodelo de carga no puede armar transbordos
GAP_CARGA = 1e-6         # el 0,1 % por defecto del submodelo deja hasta ~6.000 USD sin asignar y ensucia la comparación
CFG_VALIDADOR = {'ciclico': True, 'tat_variable': True, 'min_ton_escala': True, 'mantenimiento': True,
                 'frecuencias': True, 'demanda_diaria': True, 'dist_frecuencias': False, 'transferencias': True}
ESTADOS = {GRB.OPTIMAL: 'Optimo', GRB.TIME_LIMIT: 'Limite de tiempo', GRB.INTERRUPTED: 'Interrumpido (Ctrl+C)',
           GRB.MEM_LIMIT: 'Limite de memoria', GRB.INFEASIBLE: 'Infactible', GRB.SUBOPTIMAL: 'Suboptimo'}


def leer_itinerario(carpeta):
    """Itinerario por avión desde itinerario_vuelos.csv (mismo formato que exporta la heurística)."""
    df = pd.read_csv(os.path.join(carpeta, 'itinerario_vuelos.csv'))
    itinerario = {}
    for r in df.sort_values(['avion', 'pos']).itertuples():
        itinerario.setdefault(r.avion, []).append({'pos': int(r.pos), 'tramo': (r.origen, r.destino),
                                                   't_dep': float(r.t_dep_h), 't_arr': float(r.t_arr_h)})
    return itinerario


def resolver_carga(itinerario, ttr_h):
    """Submodelo de carga de la heurística con los vuelos fijos, y su validación independiente."""
    multi = asignar_carga_multitramos(itinerario, ruta_raw=gm.RAW, ttr_h=ttr_h, tlim=600, gap=GAP_CARGA)
    if not multi['ok']:
        raise RuntimeError(f"El submodelo de carga no encontró solución: {multi['estado']}")
    return multi, validar(multi['solucion'], dict(CFG_VALIDADOR, ttr_h=ttr_h), ruta_raw=gm.RAW)


def armar_start(modelo, itinerario, cargas):
    """Valor de cada variable del MILP para un itinerario y una carga sin transbordos (las demás valen 0)."""
    v, datos = modelo._v, modelo._datos
    seguro, tec = datos['seguro_k'], datos['tec']
    st = defaultdict(float)
    carga_vuelo = defaultdict(float)
    for c in cargas:
        carga_vuelo[c['k'], c['s']] += c['x']
    for k in datos['K']:
        legs = sorted(itinerario.get(k, []), key=lambda leg: leg['pos'])
        n = len(legs)
        if n > datos['S_max_k'][k]:
            raise ValueError(f"{k} tiene {n} vuelos y el MILP {datos['S_max_k'][k]} posiciones: aumenta --smax")
        lleno = {}
        for s, leg in enumerate(legs, start=1):
            e = tuple(leg['tramo'])
            if leg['pos'] != s or (k, s, e) not in v['y']:
                raise ValueError(f"{k}: el vuelo {leg} no calza con las posiciones o tramos del MILP")
            st[v['u'][k, s]] = 1
            st[v['y'][k, s, e]] = 1
            st[v['t_dep'][k, s]] = leg['t_dep']
            st[v['t_arr'][k, s]] = leg['t_arr']
            st[v['dia'][k, s, int(leg['t_dep'] // 24) + 1]] = 1
            if carga_vuelo[k, s] > 1e-6:
                st[v['lc'][k, s]] = 1
            lleno[s] = carga_vuelo[k, s] > seguro[k] + 1e-9
            if lleno[s]:
                st[v['ll'][k, s]] = 1
        if not n:
            continue
        st[v['l'][k, n]] = 1
        for s in range(1, n):
            if lleno[s] and lleno[s + 1] and legs[s - 1]['tramo'][1] not in tec:
                st[v['g'][k, s]] = 1
        if lleno[n] and lleno[1] and legs[-1]['tramo'][1] not in tec:
            st[v['gc'][k, n]] = 1
        # mantenimiento: mismo criterio que src/validador.py
        ap_m, ini, fin = datos['mant'][k]
        if legs[0]['tramo'][0] == ap_m and legs[0]['t_dep'] >= fin - 1e-9:
            st[v['m0'][k]] = 1
        else:
            for s, leg in enumerate(legs, start=1):
                if (leg['tramo'][1] == ap_m and leg['t_arr'] <= ini + 1e-9
                        and (s == n or legs[s]['t_dep'] >= fin - 1e-9)):
                    st[v['m'][k, s]] = 1
                    break
            else:
                raise ValueError(f"{k}: el itinerario no respeta su ventana de mantenimiento")
    for c in cargas:
        q, k, s = tuple(c['q']), c['k'], int(c['s'])
        if c['b'] > 1e-9:
            if (q, k, s) not in v['b']:
                raise ValueError(f"{k} embarca {q} en la posición {s}, pero el modelo no lo permite")
            st[v['b'][q, k, s]] += c['b']
            st[v['s_q'][q]] += c['b']
        if (q[1], k, s) not in v['x']:
            raise ValueError(f"{k} lleva {q} en la posición {s}, pero no lo puede llevar sin transbordo")
        st[v['x'][q[1], k, s]] += c['x']
        st[v['a'][q[1], k, s]] += c['a']
        if c['w'] > 1e-9:
            st[v['w'][q[1], k, s]] += c['w']
    return st


def verificar_start(modelo, start, tol=1e-6):
    """Revisa el start contra cada restricción, cota e integralidad del modelo. Devuelve (vector, violaciones, objetivo)."""
    variables, restricciones = modelo.getVars(), modelo.getConstrs()
    xs = np.zeros(len(variables))
    for var, valor in start.items():
        xs[var.index] = valor
    act = modelo.getA() @ xs
    rhs = np.array(modelo.getAttr('RHS', restricciones))
    sentido = np.array(modelo.getAttr('Sense', restricciones))
    exceso = np.where(sentido == '<', act - rhs, np.where(sentido == '>', rhs - act, np.abs(act - rhs)))
    violaciones = defaultdict(list)
    nombres = modelo.getAttr('ConstrName', restricciones)
    for i in np.where(exceso > tol)[0]:
        violaciones[nombres[i].split('[')[0]].append((nombres[i], round(float(exceso[i]), 6)))
    lb, ub = np.array(modelo.getAttr('LB', variables)), np.array(modelo.getAttr('UB', variables))
    for i in np.where((xs < lb - tol) | (xs > ub + tol))[0]:
        violaciones['cotas'].append((variables[i].VarName, float(xs[i])))
    enteras = np.array(modelo.getAttr('VType', variables)) != GRB.CONTINUOUS
    for i in np.where(enteras & (np.abs(xs - np.round(xs)) > tol))[0]:
        violaciones['integralidad'].append((variables[i].VarName, float(xs[i])))
    objetivo = float(np.dot(modelo.getAttr('Obj', variables), xs)) + modelo.ObjCon
    return xs, violaciones, objetivo


def extraer_itinerario(modelo):
    """Vuelos de la mejor solución del MILP, en el formato de itinerario de la heurística."""
    v, datos = modelo._v, modelo._datos
    yv, uv, tv = modelo.getAttr('X', v['y']), modelo.getAttr('X', v['u']), modelo.getAttr('X', v['t_dep'])
    itinerario = {}
    for k in datos['K']:
        legs = []
        for s in range(1, datos['S_max_k'][k] + 1):
            if uv[k, s] < 0.5:
                break
            e = max(datos['Ek'][k], key=lambda tramo: yv[k, s, tramo])
            t_dep = round(tv[k, s], 5)   # evita que 47.9999999 quede en el día anterior
            legs.append({'pos': s, 'tramo': e, 't_dep': t_dep, 't_arr': round(t_dep + datos['tau_e'][e], 5)})
        itinerario[k] = legs
    return itinerario


def exportar(carpeta, itinerario, multi, val, datos, info, ttr_h):
    """Mismos archivos y columnas que exporta la heurística (main.py), para el visualizador."""
    os.makedirs(carpeta, exist_ok=True)
    s = multi['solucion']
    op_de, h_k = datos['op'], datos['h_k']
    leg = {(k, l['pos']): l for k, legs in itinerario.items() for l in legs}
    costo_v = {(k, p): datos['c_ke'][k, tuple(l['tramo'])] for (k, p), l in leg.items()}
    carga_v, hand_v, ing_v, por_q = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(list)
    for reg in s['cargas']:
        carga_v[reg['k'], reg['s']] += reg['x']
        hand_v[reg['k'], reg['s']] += (reg['b'] + reg.get('transfer_in', 0.0)) * h_k[reg['k']]
        por_q[tuple(reg['q'])].append(reg)
    # ingreso de cada commodity repartido entre sus vuelos según toneladas-hora (igual que la heurística)
    for q, regs in por_q.items():
        total = 1000 * datos['r_q'][q] * sum(reg['a'] for reg in regs)
        pesos = [reg['x'] * (leg[reg['k'], reg['s']]['t_arr'] - leg[reg['k'], reg['s']]['t_dep']) for reg in regs]
        if total > 0 and sum(pesos) > 0:
            for reg, peso in zip(regs, pesos):
                ing_v[reg['k'], reg['s']] += total * peso / sum(pesos)

    filas = [{'avion': k, 'operador': op_de[k], 'pos': l['pos'], 'origen': l['tramo'][0], 'destino': l['tramo'][1],
              't_dep_h': l['t_dep'], 't_arr_h': l['t_arr'], 'dia': int(l['t_dep'] // 24) + 1,
              'carga_t': round(carga_v[k, l['pos']], 3), 'costo_usd': round(costo_v[k, l['pos']], 2),
              'handling_usd': round(hand_v[k, l['pos']], 2), 'ingreso_usd': round(ing_v[k, l['pos']], 2)}
             for k, legs in itinerario.items() for l in legs]
    pd.DataFrame(filas).to_csv(os.path.join(carpeta, 'itinerario_vuelos.csv'), index=False)
    pd.DataFrame([{'avion': r['k'], 'pos': r['s'], 'origen_q': r['q'][0], 'destino_q': r['q'][1], 'dia_q': r['q'][2],
                   'a_bordo_t': r['x'], 'embarque_inicial_t': r['b'], 'entrega_t': r['a'], 'through_t': r['w'],
                   'transfer_in_t': r.get('transfer_in', 0.0), 'transfer_out_t': r.get('transfer_out', 0.0)}
                  for r in s['cargas']]).to_csv(os.path.join(carpeta, 'carga_transportada.csv'), index=False)
    pd.DataFrame(s['transferencias'], columns=['q', 'from_k', 'from_s', 'to_k', 'to_s', 'tons', 'airport',
                                               'from_operator', 'to_operator']).to_csv(
        os.path.join(carpeta, 'transferencias_carga.csv'), index=False)

    dem_tot = sum(datos['D_q'].values())
    horas = sum(l['t_arr'] - l['t_dep'] for legs in itinerario.values() for l in legs)
    cap = sum(datos['P_k'][k] for k, legs in itinerario.items() for l in legs)
    freq = defaultdict(int)
    for legs in itinerario.values():
        for l in legs:
            freq[tuple(l['tramo'])] += 1
    est = multi['estadisticas']
    kpis = {'margen_usd': val['margen'], 'ingresos_usd': val['ingresos'], 'costo_vuelos_usd': val['costo_vuelos'],
            'costo_handling_usd': val['handling'], 'toneladas': val['toneladas_entregadas'],
            'pct_demanda': 100 * val['toneladas_entregadas'] / dem_tot, 'vuelos': val['vuelos'],
            'aviones_usados': sum(1 for legs in itinerario.values() if legs), 'horas_bloque': horas,
            'factor_ocupacion_pct': 100 * val['toneladas_entregadas'] / cap if cap else 0,
            'frecuencias_cumplidas': f"{sum(freq[od] >= f for od, f in datos['f_od'].items())}/{len(datos['f_od'])}",
            'flujo_through_t': est['flujo_through_t'], 'flujo_transferido_t': est['flujo_transferido_t'],
            'conexiones_transferencia_usadas': est['conexiones_transferencia_usadas'],
            'transferencias_entre_operadores': est['transferencias_entre_operadores'], 'ttr_h': ttr_h}
    with open(os.path.join(carpeta, 'indicadores_kpi.json'), 'w', encoding='utf-8') as f:
        json.dump({'kpis': kpis, 'validador_ok': val['ok'], 'violaciones': val['violaciones'], 'milp': info,
                   'modelo_carga': {k: x for k, x in multi.items() if k != 'solucion'}},
                  f, indent=2, ensure_ascii=False, default=str)
    with open(os.path.join(carpeta, 'modelo_resuelto.pkl'), 'wb') as f:
        pickle.dump({'itinerario': itinerario, 'cargas': dict(carga_v), 'solucion_carga': s,
                     'transferencias': s['transferencias'], 'ttr_h': ttr_h, 'milp': info}, f)
    with open(os.path.join(carpeta, 'metadata.json'), 'w', encoding='utf-8') as f:
        json.dump({'version': os.path.basename(carpeta), 'fecha': time.strftime('%Y-%m-%d %H:%M:%S'),
                   'descripcion': f"MILP completo con warm start desde {info['corrida_start']}",
                   'solver': 'gurobi', 'dt': 'continuo (MILP)', 'tlim': info['tiempo_limite_s']},
                  f, indent=2, ensure_ascii=False)
    try:
        from main import generar_json_visualizacion
        generar_json_visualizacion(carpeta)
    except Exception as e:   # el visualizador es opcional; los resultados ya quedaron exportados
        print(f'[!] No se pudo generar resultados.json para el visualizador: {e}')
    return kpis


def main():
    ap = argparse.ArgumentParser(description='MILP completo con warm start desde una corrida de la heurística.')
    ap.add_argument('--corrida', default=CORRIDA_2H, help='carpeta de resultados usada como start (por defecto, la de 2 h)')
    ap.add_argument('--tiempo', type=float, default=3600, help='segundos de Gurobi (TimeLimit). Por defecto 3600')
    ap.add_argument('--smax', type=int, default=gm.S_MAX, help=f'posiciones por avión. Por defecto {gm.S_MAX}')
    ap.add_argument('--ttr', type=float, default=None, help='TTR_H (h) de los transbordos del paso final; por defecto el de la corrida')
    ap.add_argument('--salida', default=None, help='carpeta de salida (por defecto data/results/MILP_completo_<corrida>)')
    ap.add_argument('--gap', type=float, default=0.01, help='MIPGap. Por defecto 0.01, igual que la heurística')
    ap.add_argument('--mipfocus', type=int, default=1, help='MIPFocus de Gurobi. Por defecto 1 (buscar mejores soluciones)')
    ap.add_argument('--norel', type=float, default=None,
                    help='segundos de la heurística NoRel de Gurobi antes de la raíz (mejora el start sin resolver el LP); '
                         'por defecto, el valor predeterminado de Gurobi')
    ap.add_argument('--hilos', type=int, default=0, help='Threads de Gurobi (0 = todos)')
    ap.add_argument('--memoria', type=float, default=0,
                    help='GB máximos para Gurobi; al superarlos se detiene y exporta. Por defecto 0: sin límite')
    ap.add_argument('--solo-verificar', action='store_true', help='arma el modelo y revisa el start, sin optimizar')
    ap.add_argument('--sin-start', action='store_true', help='resuelve el MILP sin warm start (para comparar)')
    args = ap.parse_args()

    corrida = os.path.normpath(args.corrida)
    nombre = os.path.basename(corrida)
    salida = args.salida or os.path.join('data', 'results', f'MILP_completo_{nombre}')
    kpi_corrida = json.load(open(os.path.join(corrida, 'indicadores_kpi.json'), encoding='utf-8'))['kpis']
    ttr_h = args.ttr if args.ttr is not None else float(kpi_corrida.get('ttr_h', 2.0))
    t0 = time.time()

    print(f'[1/5] Corrida de partida: {corrida} (margen heurística {kpi_corrida["margen_usd"]:,.0f} USD con transbordos)')
    itinerario = leer_itinerario(corrida)

    print('[2/5] Carga de esos vuelos con el submodelo de la heurística (sin transbordos y con transbordos)...',
          flush=True)
    multi0, val0 = resolver_carga(itinerario, SIN_TRANSBORDOS)
    _, val_ref = resolver_carga(itinerario, ttr_h)   # referencia: mismos vuelos y mismo paso final que el MILP
    print(f'      margen validado sin transbordos (start): {val0["margen"]:,.0f} USD | validador '
          f'{"OK" if val0["ok"] else val0["violaciones"][:5]}')
    print(f'      margen validado con transbordos (referencia): {val_ref["margen"]:,.0f} USD')

    print(f'[3/5] Armando el MILP (S_max = {args.smax} posiciones por avión)...', flush=True)
    t1 = time.time()
    datos = gm.cargar_datos(s_max=args.smax)
    modelo = gm.construir_modelo_completo(datos)
    print(f'      {modelo.NumVars:,} variables ({modelo.NumIntVars:,} enteras), {modelo.NumConstrs:,} restricciones, '
          f'{modelo.NumNZs:,} no ceros, armado en {time.time() - t1:.0f} s')
    start = armar_start(modelo, itinerario, multi0['solucion']['cargas'])
    xs, violaciones, obj_start = verificar_start(modelo, start)
    if violaciones:
        print('      [!] El start NO cumple el modelo:')
        for familia, lista in violaciones.items():
            print(f'          {familia}: {len(lista)} (ej. {lista[:3]})')
        sys.exit(1)
    print(f'      start factible: cumple todas las restricciones; objetivo {obj_start:,.0f} USD '
          f'(margen validado {val0["margen"]:,.0f})')
    if args.solo_verificar:
        return

    os.makedirs(salida, exist_ok=True)
    if not args.sin_start:
        modelo.setAttr('Start', modelo.getVars(), xs.tolist())
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
    print(f'[4/5] Gurobi: hasta {args.tiempo:,.0f} s; heurística NoRel: '
          f'{"predeterminada de Gurobi" if args.norel is None else f"{norel:,.0f} s"} '
          f'(Ctrl+C para cortar y exportar la mejor solución)...', flush=True)
    modelo.optimize()

    info = {'corrida_start': nombre, 'estado': ESTADOS.get(modelo.Status, str(modelo.Status)),
            'objetivo_milp_sin_transbordos': modelo.ObjVal if modelo.SolCount else None,
            'cota_milp': modelo.ObjBound, 'gap_milp': modelo.MIPGap if modelo.SolCount else None,
            'tiempo_gurobi_s': modelo.Runtime, 'tiempo_limite_s': args.tiempo, 'norel_s': norel,
            'objetivo_start': obj_start,
            'warm_start': not args.sin_start, 'margen_heuristica_usd': kpi_corrida['margen_usd'],
            'margen_heuristica_mismo_paso_final_usd': val_ref['margen'],
            'margen_start_sin_transbordos_usd': val0['margen'], 's_max': args.smax,
            'variables': modelo.NumVars, 'variables_enteras': modelo.NumIntVars, 'restricciones': modelo.NumConstrs}
    if not modelo.SolCount:
        print('No hay solución para exportar.')
        return
    it_milp = extraer_itinerario(modelo)
    modelo.dispose()   # libera memoria antes del submodelo de carga

    print('[5/5] Transbordos sobre los vuelos del MILP, validación y exportación...', flush=True)
    _, val_sin = resolver_carga(it_milp, SIN_TRANSBORDOS)
    info['margen_milp_sin_transbordos_validado_usd'] = val_sin['margen']
    multi, val = resolver_carga(it_milp, ttr_h)
    kpis = exportar(salida, it_milp, multi, val, datos, info, ttr_h)

    print('\n' + '=' * 78)
    print(f'Estado Gurobi: {info["estado"]} | objetivo MILP {info["objetivo_milp_sin_transbordos"]:,.0f} | '
          f'cota {info["cota_milp"]:,.0f} | gap {100 * info["gap_milp"]:.2f} %')
    print(f'Margen validado sin transbordos: start {val0["margen"]:,.0f} -> MILP {val_sin["margen"]:,.0f} USD')
    print(f'Margen validado con transbordos: heurística {val_ref["margen"]:,.0f} -> MILP {kpis["margen_usd"]:,.0f} USD '
          f'({kpis["margen_usd"] - val_ref["margen"]:+,.0f}); la corrida original reportó {kpi_corrida["margen_usd"]:,.0f}')
    print(f'Validador: {"OK" if val["ok"] else str(len(val["violaciones"])) + " violaciones"} | '
          f'vuelos {kpis["vuelos"]} | aviones {kpis["aviones_usados"]} | toneladas {kpis["toneladas"]:,.1f} | '
          f'frecuencias {kpis["frecuencias_cumplidas"]}')
    print(f'Resultados en {salida} (tiempo total {time.time() - t0:.0f} s)')


if __name__ == '__main__':
    main()
