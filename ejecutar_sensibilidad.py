"""Ejecuta secuencialmente la bateria de sensibilidad con vuelos fijos.

Reutiliza un solo proceso Python para evitar recargar las dependencias y nunca escribe en la
carpeta congelada de Rama B. Por defecto salta una etiqueta si su resultado ya existe y fue validado.
"""
import argparse
import json
import os
import sys
import time

from crear_escenario import crear_escenario
from evaluar_escenario import evaluar


ESCENARIOS = {
    'base': {},
    'pac_3800': {'exfuel': ('PAC=3800',)},
    'pac_4250': {'exfuel': ('PAC=4250',)},
    'pac_4450': {'exfuel': ('PAC=4450',)},
    'combustible_-20': {'combustible': 0.8},
    'combustible_+20': {'combustible': 1.2},
    'combustible_+40': {'combustible': 1.4},
    'demanda_-20': {'demanda': 0.8},
    'demanda_-10': {'demanda': 0.9},
    'demanda_+10': {'demanda': 1.1},
    'demanda_+20': {'demanda': 1.2},
    'tarifas_-10': {'tarifa': 0.9},
    'tarifas_+10': {'tarifa': 1.1},
}


GRUPOS = {
    'control': [
        ('base', 'flexible', 2.0, 'base', True),
    ],
    'prioridad1': [
        ('pac_3800', 'flexible', 2.0, None, True),
        ('pac_4250', 'flexible', 2.0, None, True),
        ('pac_4450', 'flexible', 2.0, None, True),
        ('combustible_-20', 'flexible', 2.0, None, True),
        ('combustible_+20', 'flexible', 2.0, None, True),
        ('combustible_+40', 'flexible', 2.0, None, True),
        ('demanda_-20', 'flexible', 2.0, None, True),
        ('demanda_-10', 'flexible', 2.0, None, True),
        ('demanda_+10', 'flexible', 2.0, None, True),
        ('demanda_+20', 'flexible', 2.0, None, True),
    ],
    'prioridad2': [
        ('base', 'base', 2.0, 'base_borde_base', True),
        ('base', 'flexible', 2.0, 'base_sin_interchange', False),
        ('tarifas_-10', 'flexible', 2.0, None, True),
        ('tarifas_+10', 'flexible', 2.0, None, True),
    ],
    'prioridad3': [
        ('base', 'flexible', 1.0, 'base_ttr1', True),
        ('base', 'flexible', 3.0, 'base_ttr3', True),
    ],
}


def etiqueta_de(escenario, borde, ttr, etiqueta):
    return etiqueta or (escenario + ('' if borde == 'flexible' else '_borde_base')
                         + ('' if ttr == 2.0 else f'_ttr{ttr:g}'))


def resultado_validado(etiqueta):
    ruta = os.path.join('data', 'results', 'sensibilidad', etiqueta, 'indicadores_kpi.json')
    try:
        with open(ruta, encoding='utf-8') as f:
            return json.load(f).get('validador_ok') is True
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def asegurar_escenarios(grupos, recrear=False):
    """Crea solo los datos derivados requeridos, o los regenera si se solicita expresamente."""
    requeridos = {escenario for grupo in grupos
                  for escenario, _borde, _ttr, _etiqueta, _interchange in GRUPOS[grupo]}
    for nombre in sorted(requeridos):
        ruta = os.path.join('data', 'escenarios', nombre)
        if recrear or not os.path.isdir(ruta):
            crear_escenario(nombre, reemplazar=recrear, **ESCENARIOS[nombre])


def main():
    ap = argparse.ArgumentParser(description='Bateria secuencial de sensibilidad con vuelos fijos.')
    ap.add_argument('--grupo', choices=tuple(GRUPOS) + ('todos',), default='todos')
    ap.add_argument('--repetir', action='store_true', help='repite incluso resultados ya validados')
    ap.add_argument('--recrear-escenarios', action='store_true',
                    help='regenera los datos derivados antes de ejecutar')
    ap.add_argument('--detener-error', action='store_true')
    a = ap.parse_args()
    grupos = list(GRUPOS) if a.grupo == 'todos' else [a.grupo]
    asegurar_escenarios(grupos, recrear=a.recrear_escenarios)
    fallos = []
    inicio = time.time()
    for grupo in grupos:
        for escenario, borde, ttr, etiqueta, interchange in GRUPOS[grupo]:
            nombre = etiqueta_de(escenario, borde, ttr, etiqueta)
            if not a.repetir and resultado_validado(nombre):
                print(f'[skip] {nombre}: resultado validado existente', flush=True)
                continue
            print(f'[run] {nombre}', flush=True)
            try:
                evaluar(escenario, borde=borde, ttr=ttr, etiqueta=etiqueta, interchange=interchange)
            except Exception as exc:  # registra el escenario fallido y permite continuar la bateria
                fallos.append((nombre, str(exc)))
                print(f'[fallo] {nombre}: {exc}', flush=True)
                if a.detener_error:
                    raise
    print(f'Bateria terminada en {(time.time() - inicio) / 60:.1f} min; fallos={len(fallos)}', flush=True)
    for nombre, mensaje in fallos:
        print(f'  - {nombre}: {mensaje}', flush=True)
    return 1 if fallos else 0


if __name__ == '__main__':
    sys.exit(main())
