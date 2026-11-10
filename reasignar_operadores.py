"""
Interchanges sobre una corrida ya exportada: mismos vuelos y horarios, eligiendo la filial que opera cada vuelo.

Alterna dos pasos exactos hasta que el margen validado deja de subir:
 1. Con la carga fija, la programación dinámica de src/asignar_operadores.py elige la filial de cada vuelo.
 2. Con las filiales fijas, el submodelo de carga de la heurística (gap 1e-6) vuelve a asignar la carga y los
    transbordos, ahora con el handling y el soporte de la filial que opera cada vuelo.
Cada paso no empeora el anterior. El resultado se valida y se exporta con los mismos archivos que las demás corridas;
la columna 'operador' de itinerario_vuelos.csv pasa a ser la filial que opera cada vuelo.

Uso, desde la raíz del repositorio:
    python reasignar_operadores.py --corrida data/results/Busqueda_ventanas_desplazar_d2 --salida data/results/Interchange_desde_mejor
    python reasignar_operadores.py --corrida ... --borde base     # el primer y el último vuelo, del operador base
"""
import argparse
import json
import os
import sys

import gurobi_model as gm
import warm_start_milp as ws
from src.asignar_operadores import asignar_operadores, cargar_datos_operadores

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def main():
    ap = argparse.ArgumentParser(description='Interchanges sobre una corrida exportada (vuelos fijos).')
    ap.add_argument('--corrida', required=True, help='carpeta con itinerario_vuelos.csv')
    ap.add_argument('--salida', default=None, help='carpeta de salida (por defecto <corrida>_interchange)')
    ap.add_argument('--borde', choices=('base', 'flexible'), default='flexible',
                    help="base: el primer y el último vuelo los opera el operador base; flexible: basta volver al "
                         "operador base en el aeropuerto del borde de la semana")
    ap.add_argument('--ttr', type=float, default=None, help='TTR_H (h); por defecto el de la corrida')
    ap.add_argument('--iteraciones', type=int, default=5)
    args = ap.parse_args()
    salida = args.salida or args.corrida.rstrip('/\\') + '_interchange'
    kpi = json.load(open(os.path.join(args.corrida, 'indicadores_kpi.json'), encoding='utf-8'))
    ttr = args.ttr if args.ttr is not None else float(kpi.get('kpis', kpi).get('ttr_h', 2.0))
    borde_base = args.borde == 'base'
    cfg = dict(ws.CFG_VALIDADOR, ttr_h=ttr, borde_operador_base=borde_base)

    itinerario = ws.leer_itinerario(args.corrida)
    multi, val = ws.resolver_carga(itinerario, ttr)
    val = ws.validar(multi['solucion'], cfg, ruta_raw=gm.RAW)
    inicial = val['margen']
    print(f'Corrida {args.corrida}: margen validado {inicial:,.0f} USD | validador {"OK" if val["ok"] else "con violaciones"}'
          f' | borde {args.borde} | TTR_H {ttr:g} h', flush=True)
    D = cargar_datos_operadores(gm.RAW)
    resumen = None
    for i in range(1, args.iteraciones + 1):
        it_nuevo, res = asignar_operadores(multi['solucion'], D, borde_base=borde_base)
        m_nuevo, _ = ws.resolver_carga(it_nuevo, ttr)
        v_nuevo = ws.validar(m_nuevo['solucion'], cfg, ruta_raw=gm.RAW)
        print(f'  iteración {i}: filiales -{res["ahorro_usd"]:,.0f} USD de costo | {res["interchanges"]} interchanges, '
              f'{res["vuelos_otra_filial"]} vuelos de otra filial | carga reasignada -> margen {v_nuevo["margen"]:,.0f} '
              f'USD | validador {"OK" if v_nuevo["ok"] else v_nuevo["violaciones"][:3]}', flush=True)
        if not v_nuevo['ok'] or v_nuevo['margen'] <= val['margen'] + 1:
            break
        itinerario, multi, val, resumen = it_nuevo, m_nuevo, v_nuevo, res

    if resumen is None:
        print('Sin mejora: no se exporta.')
        return
    info = {'modelo': f'Interchanges con vuelos fijos (programación dinámica por avión, borde {args.borde})',
            'corrida_start': args.corrida, 'estado': 'Óptimo para los vuelos fijos', 'aviones_libres': 'ninguno',
            'tiempo_limite_s': None, 'ttr_h': ttr, 'margen_corrida_usd': inicial, 'borde': args.borde,
            'interchanges': resumen['por_avion']}
    datos = gm.cargar_datos()
    ws.exportar(salida, itinerario, multi, val, datos, info, ttr)
    ruta_meta = os.path.join(salida, 'metadata.json')
    meta = json.load(open(ruta_meta, encoding='utf-8'))
    meta.update(descripcion=f'Interchanges (borde {args.borde}) sobre los vuelos de {args.corrida}', dt='continuo')
    json.dump(meta, open(ruta_meta, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    print(f'Margen validado: {inicial:,.0f} -> {val["margen"]:,.0f} USD (+{val["margen"] - inicial:,.0f}) | '
          f'{resumen["interchanges"]} interchanges | exportado en {salida}')


if __name__ == '__main__':
    main()
