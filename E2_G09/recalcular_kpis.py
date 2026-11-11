"""Recalcula validación y KPI desde una solución ya guardada, sin resolver el modelo."""

import glob
import json
import os
import pickle
import sys

from validador import validar


def _calidad_optimizacion(carpeta, anterior):
    paso_1 = {}
    candidatos = sorted(glob.glob(os.path.join(carpeta, 'sol_dt*.pkl')))
    if candidatos:
        with open(candidatos[0], 'rb') as archivo:
            paso_1 = pickle.load(archivo).get('res', {})
    modelo_carga = anterior.get('modelo_carga', {})
    return {
        'metodo': 'secuencial_en_tres_pasos',
        'gap_global_disponible': False,
        'gap_global': None,
        'nota': ('No existe una cota común para la solución integrada: los gaps de los '
                 'subproblemas no se pueden sumar ni promediar para formar un gap global.'),
        'paso_1_red_agregada': paso_1,
        'paso_2_por_operador': [
            {clave: fila.get(clave) for clave in
             ('operador', 'estado', 'objetivo', 'cota', 'gap', 'tiempo_s')}
            for fila in anterior.get('por_operador', [])
        ],
        'paso_3_carga_condicionada_itinerarios': {
            clave: modelo_carga.get(clave) for clave in
            ('estado', 'objetivo_carga', 'cota', 'gap', 'tiempo_s')
        },
    }


def recalcular(carpeta):
    ruta_modelo = os.path.join(carpeta, 'modelo_resuelto.pkl')
    ruta_kpis = os.path.join(carpeta, 'indicadores_kpi.json')
    with open(ruta_modelo, 'rb') as archivo:
        guardado = pickle.load(archivo)
    solucion = guardado.get('solucion_carga', guardado)
    ttr_h = float(guardado.get('ttr_h', solucion.get('ttr_h', 2.0)))
    cfg = {
        'ciclico': True, 'tat_variable': True, 'min_ton_escala': True,
        'mantenimiento': True, 'frecuencias': True, 'demanda_diaria': True,
        'dist_frecuencias': False, 'transferencias': True, 'ttr_h': ttr_h,
    }
    validacion = validar(solucion, cfg)

    anterior = {}
    if os.path.exists(ruta_kpis):
        with open(ruta_kpis, encoding='utf-8') as archivo:
            anterior = json.load(archivo)
    kpis = dict(validacion['kpis'])
    estadisticas = anterior.get('modelo_carga', {}).get('estadisticas', {})
    kpis.update({
        'toneladas_recortadas_regla_90min': anterior.get('kpis', {}).get(
            'toneladas_recortadas_regla_90min', 0.0),
        'flujo_through_t': estadisticas.get('flujo_through_t'),
        'flujo_transferido_t': estadisticas.get('flujo_transferido_t'),
        'ttr_h': ttr_h,
    })
    salida = dict(anterior)
    salida.update({
        'kpis': kpis,
        'validador_ok': validacion['ok'],
        'violaciones': validacion['violaciones'],
        'calidad_optimizacion': _calidad_optimizacion(carpeta, anterior),
    })
    with open(ruta_kpis, 'w', encoding='utf-8') as archivo:
        json.dump(salida, archivo, indent=2, ensure_ascii=False, default=str)
        archivo.write('\n')
    ruta_visualizacion = os.path.join(carpeta, 'resultados.json')
    if os.path.exists(ruta_visualizacion):
        with open(ruta_visualizacion, encoding='utf-8') as archivo:
            visualizacion = json.load(archivo)
        visualizacion.update({
            'kpis': kpis,
            'validacion': {
                'ok': validacion['ok'],
                'violaciones': validacion['violaciones'],
            },
            'calidad_optimizacion': salida['calidad_optimizacion'],
        })
        with open(ruta_visualizacion, 'w', encoding='utf-8') as archivo:
            json.dump(visualizacion, archivo, indent=2, ensure_ascii=False, default=str)
            archivo.write('\n')
    print(f"Validador OK: {validacion['ok']} | violaciones: {len(validacion['violaciones'])}")
    print(f"KPI actualizados en {ruta_kpis}")


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Uso: python src/recalcular_kpis.py <carpeta_de_resultados>')
    recalcular(sys.argv[1])
