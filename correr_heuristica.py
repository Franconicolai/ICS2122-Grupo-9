"""
Corre la heurística de tres pasos (igual que la opción 1 de main.py) sin el menú interactivo, para encadenar corridas.
Exporta la misma carpeta de resultados que main.py (se ve en el visualizador).

Uso, desde la raíz del repositorio:
    python correr_heuristica.py --salida data/results/Heuristica_dt1 --dt 1
    python correr_heuristica.py --salida data/results/Heuristica_dt1_desde_2h --dt 1 --start data/results/Ejecucion_con_intervalos_de_2_horas
    python correr_heuristica.py --salida <carpeta> --solo-paso-2      # reutiliza el paso 1 ya guardado en <carpeta>

--start usa una corrida anterior como punto de partida de los pasos 1 y 2 y conserva la mejor de las dos soluciones
(ver src/red_espacio_tiempo.py). Las demás opciones se pasan como variables de entorno, igual que en main.py
(GAP, ENLACE_FUERTE, AMPLIAR, TTR_H, TLIM_CARGA, LOG).
"""
import argparse
import ctypes
import json
import os
import shutil
import sys
import time

RAIZ = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description='Heurística de tres pasos sin menú interactivo.')
    ap.add_argument('--salida', required=True, help='carpeta de resultados (se crea si no existe)')
    ap.add_argument('--dt', default=os.environ.get('DT', '1'), help='horas por bloque. Por defecto 1')
    ap.add_argument('--start', default='', help='carpeta de una corrida anterior como punto de partida')
    ap.add_argument('--tlim', default=os.environ.get('TLIM', '1500'), help='segundos del paso 1. Por defecto 1500')
    ap.add_argument('--tlim-op', default=os.environ.get('TLIM_OP', '420'), help='segundos por operador en el paso 2')
    ap.add_argument('--solver', default=os.environ.get('SOLVER', 'gurobi'))
    ap.add_argument('--solo-paso-2', action='store_true', help='reutiliza el paso 1 guardado en la carpeta de salida')
    ap.add_argument('--descripcion', default='', help='texto para metadata.json')
    args = ap.parse_args()

    os.chdir(RAIZ)
    os.environ.update(DT=str(args.dt), TLIM=str(args.tlim), TLIM_OP=str(args.tlim_op), SOLVER=args.solver)
    if args.start:
        os.environ['START'] = args.start
    else:
        os.environ.pop('START', None)
    sys.path.insert(0, os.path.join(RAIZ, 'src'))
    import red_espacio_tiempo as red          # leen DT, TLIM, etc. al importarse
    import reoptimizacion_por_avion as reopt
    from main import generar_json_visualizacion

    if os.name == 'nt':                       # que Windows no se suspenda mientras corre
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    t0 = time.time()
    os.makedirs(args.salida, exist_ok=True)
    pkl = os.path.join(args.salida, f'sol_dt{args.dt}.pkl')
    if not args.solo_paso_2 and not red.run_paso_1(salida_pkl=pkl):
        sys.exit('El paso 1 no generó solución.')
    reopt.main(pkl)
    base = pkl.replace('.pkl', '_poravion')
    for origen, nuevo in {f'{base}_kpis.json': 'indicadores_kpi.json', f'{base}_itinerario.csv': 'itinerario_vuelos.csv',
                          f'{base}_carga_multitramos.csv': 'carga_transportada.csv',
                          f'{base}_transferencias.csv': 'transferencias_carga.csv',
                          f'{base}.pkl': 'modelo_resuelto.pkl'}.items():
        if os.path.exists(origen):
            shutil.move(origen, os.path.join(args.salida, nuevo))
    ruta_kpis = os.path.join(args.salida, 'indicadores_kpi.json')
    try:                                      # versiones anteriores lo escribían en cp1252 en Windows
        open(ruta_kpis, encoding='utf-8').read()
    except UnicodeDecodeError:
        texto = open(ruta_kpis, encoding='cp1252').read()
        open(ruta_kpis, 'w', encoding='utf-8').write(texto)
    generar_json_visualizacion(args.salida)
    json.dump({'version': os.path.basename(os.path.normpath(args.salida)),
               'descripcion': args.descripcion or f'Heurística DT {args.dt} h' + (f', desde {args.start}' if args.start else ''),
               'fecha': time.strftime('%Y-%m-%d %H:%M:%S'), 'solver': args.solver, 'dt': str(args.dt),
               'tlim': str(args.tlim), 'tlim_op': str(args.tlim_op), 'start': args.start,
               'enlace_fuerte': os.environ.get('ENLACE_FUERTE', '1')},
              open(os.path.join(args.salida, 'metadata.json'), 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    kpis = json.load(open(os.path.join(args.salida, 'indicadores_kpi.json'), encoding='utf-8'))['kpis']
    print(f"\nMargen validado: {kpis['margen_usd']:,.0f} USD | vuelos {kpis['vuelos']} | "
          f"aviones {kpis['aviones_usados']} | {(time.time() - t0) / 60:.0f} min | resultados en {args.salida}")


if __name__ == '__main__':
    main()
