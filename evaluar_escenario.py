"""
Evalua la solucion final en un escenario de sensibilidad, con los MISMOS vuelos (protocolo rapido).
Reasigna la filial de cada vuelo y la carga con los datos del escenario (warm_start_milp.resolver_carga_interchange),
valida con src/validador.py, exporta los resultados y agrega una fila a data/results/sensibilidad/resumen.csv.

Uso, desde la raiz del repositorio (rama milp-completo-warm-start):
    py evaluar_escenario.py --escenario combustible_+20
    py evaluar_escenario.py --escenario base --borde base        (borde de la semana estricto)
    py evaluar_escenario.py --escenario base --ttr 3             (tiempo minimo de transbordo de 3 h)

--escenario es una carpeta creada con crear_escenario.py (data/escenarios/<nombre>); 'raw' usa los datos originales.
"""
import argparse, csv, os, pickle, sys, time

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

CORRIDA_BASE = os.path.join('data', 'results', 'RamaB_interchange_continua')
RAW_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'raw data')


def _guardar_fila(resumen, fila):
    """Inserta o reemplaza una etiqueta para que repetir una corrida no duplique el resumen."""
    os.makedirs(os.path.dirname(resumen), exist_ok=True)
    filas = []
    if os.path.exists(resumen):
        with open(resumen, newline='', encoding='utf-8') as f:
            filas = [r for r in csv.DictReader(f) if r.get('etiqueta') != fila['etiqueta']]
        for anterior in filas:
            anterior.setdefault('interchange', 'True')
    filas.append(fila)
    temporal = resumen + '.tmp'
    with open(temporal, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(fila), lineterminator='\n')
        w.writeheader()
        w.writerows(filas)
    os.replace(temporal, resumen)


def evaluar(escenario, corrida=CORRIDA_BASE, borde='flexible', ttr=2.0, etiqueta=None, interchange=True):
    """Evalua un escenario y devuelve la fila consolidada de resultados."""
    if borde not in ('flexible', 'base'):
        raise ValueError(f'borde desconocido: {borde}')
    if escenario == 'raw':
        ruta_raw = RAW_BASE
    else:
        ruta_raw = os.path.abspath(os.path.join('data', 'escenarios', escenario))
        if not os.path.isdir(ruta_raw):
            raise FileNotFoundError(f'No existe {ruta_raw}: creelo antes con crear_escenario.py')

    # Los modulos se importan una sola vez en una bateria, pero sus parametros se actualizan por escenario.
    os.environ['RAW'] = ruta_raw
    os.environ['BORDE'] = borde
    os.environ['INTERCHANGE'] = '1' if interchange else '0'
    import warm_start_milp as ws
    import gurobi_model as gm
    gm.RAW = ruta_raw
    gm.INTERCHANGE = interchange
    ws.CFG_VALIDADOR['interchanges'] = interchange
    ws.CFG_VALIDADOR['borde_operador_base'] = borde == 'base'

    etiqueta = etiqueta or (escenario + ('' if borde == 'flexible' else '_borde_base')
                             + ('' if ttr == 2.0 else f'_ttr{ttr:g}'))
    salida = os.path.join('data', 'results', 'sensibilidad', etiqueta)
    t0 = time.time()
    with open(os.path.join(corrida, 'modelo_resuelto.pkl'), 'rb') as f:
        itinerario = pickle.load(f)['itinerario']
    if not interchange:
        itinerario = {k: [{campo: valor for campo, valor in leg.items() if campo != 'op'} for leg in legs]
                      for k, legs in itinerario.items()}
    datos = gm.cargar_datos(ruta_raw)
    multi, val = (ws.resolver_carga_interchange(itinerario, ttr, pasos=3) if interchange
                  else ws.resolver_carga(itinerario, ttr))
    if not val['ok']:
        raise RuntimeError(f'La solucion no es factible: {val["violaciones"][:5]}')
    info = {'modelo': 'Evaluacion con los mismos vuelos (sensibilidad)', 'estado': 'Vuelos fijos',
            'corrida_start': os.path.basename(corrida), 'tiempo_limite_s': None, 'aviones_libres': 'ninguno',
            'escenario': escenario, 'borde': borde, 'ttr_h': ttr, 'interchange': interchange}
    kpis = ws.exportar(salida, itinerario, multi, val, datos, info, ttr)
    fila = {'etiqueta': etiqueta, 'escenario': escenario, 'borde': borde, 'ttr_h': ttr,
            'interchange': interchange,
            'margen_usd': round(kpis['margen_usd']), 'ingresos_usd': round(kpis['ingresos_usd']),
            'costo_vuelos_usd': round(kpis['costo_vuelos_usd']),
            'costo_handling_usd': round(kpis['costo_handling_usd']),
            'toneladas': round(kpis['toneladas'], 1), 'pct_demanda': round(kpis['pct_demanda'], 2),
            'vuelos_vacios': kpis['vuelos_vacios'], 'interchanges': kpis.get('interchanges'),
            'vuelos_otra_filial': kpis.get('vuelos_otra_filial'),
            'factor_ocupacion_pct': round(kpis['factor_ocupacion_pct'], 2),
            'minutos': round((time.time() - t0) / 60, 1)}
    _guardar_fila(os.path.join('data', 'results', 'sensibilidad', 'resumen.csv'), fila)
    print(f'{etiqueta}: margen validado {fila["margen_usd"]:,} USD | demanda {fila["pct_demanda"]} % | '
          f'{fila["interchanges"]} interchanges | {fila["minutos"]} min | resultados en {salida}', flush=True)
    return fila


def main():
    ap = argparse.ArgumentParser(description='Evalua la solucion final en un escenario, con los mismos vuelos.')
    ap.add_argument('--escenario', required=True)
    ap.add_argument('--corrida', default=CORRIDA_BASE)
    ap.add_argument('--borde', choices=('flexible', 'base'), default='flexible')
    ap.add_argument('--ttr', type=float, default=2.0, help='tiempo minimo de transbordo (h)')
    ap.add_argument('--etiqueta', default=None, help='nombre de la fila y de la carpeta de salida')
    ap.add_argument('--sin-interchange', action='store_true', help='cada avion conserva su operador base')
    a = ap.parse_args()
    try:
        evaluar(a.escenario, a.corrida, a.borde, a.ttr, a.etiqueta, not a.sin_interchange)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        sys.exit(f'[!] {exc}')


if __name__ == '__main__':
    main()
