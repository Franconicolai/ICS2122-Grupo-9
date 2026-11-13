"""
Crea un escenario de sensibilidad: copia 'data/raw data' en 'data/escenarios/<nombre>' y cambia los parametros pedidos.
Todos los codigos leen los datos desde la variable de entorno RAW, asi que basta apuntar RAW a esa carpeta.

Uso, desde la raiz del repositorio:
    py crear_escenario.py --nombre combustible_+20 --combustible 1.2
    py crear_escenario.py --nombre demanda_-10 --demanda 0.9
    py crear_escenario.py --nombre tarifas_+10 --tarifa 1.1
    py crear_escenario.py --nombre pac_como_bra --exfuel PAC=3800
    py crear_escenario.py --nombre base          (copia sin cambios, sirve de control)

--combustible, --demanda y --tarifa son factores que multiplican el valor original.
--exfuel fija el costo ex-fuel por hora (USD/h) de una filial; se puede repetir.
"""
import argparse, os, shutil, sys
import pandas as pd

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
RAW = os.path.join('data', 'raw data')


def main():
    ap = argparse.ArgumentParser(description='Crea un escenario de sensibilidad en data/escenarios/<nombre>.')
    ap.add_argument('--nombre', required=True)
    ap.add_argument('--combustible', type=float, default=1.0, help='factor sobre el precio del combustible (USD/gal)')
    ap.add_argument('--demanda', type=float, default=1.0, help='factor sobre la demanda diaria y semanal (t)')
    ap.add_argument('--tarifa', type=float, default=1.0, help='factor sobre las tarifas (USD/kg)')
    ap.add_argument('--exfuel', action='append', default=[], help='FILIAL=USD_por_hora, por ejemplo PAC=3800')
    a = ap.parse_args()

    dst = os.path.join('data', 'escenarios', a.nombre)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(RAW, dst)
    cambios = []

    cp = pd.read_csv(os.path.join(dst, 'cost_params.csv'))
    if a.combustible != 1.0:
        m = cp.param == 'fuel_price_usd_per_gal'
        cp.loc[m, 'value'] = cp.loc[m, 'value'] * a.combustible
        cambios.append(f'combustible x{a.combustible:g} -> {cp.loc[m, "value"].iloc[0]:.3f} USD/gal')
    for e in a.exfuel:
        filial, valor = e.split('=')
        m = (cp.param == 'ex_fuel_usd_per_block_hour') & (cp.operator == filial.strip().upper())
        if not m.any():
            sys.exit(f'filial desconocida: {filial}')
        cp.loc[m, 'value'] = float(valor)
        cambios.append(f'ex-fuel {filial.upper()} = {float(valor):,.0f} USD/h')
    cp.to_csv(os.path.join(dst, 'cost_params.csv'), index=False)

    if a.demanda != 1.0:
        dd = pd.read_csv(os.path.join(dst, 'demand_daily.csv'))
        dias = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
        dd[dias] = (dd[dias] * a.demanda).round(1)
        dd.to_csv(os.path.join(dst, 'demand_daily.csv'), index=False)
        cambios.append(f'demanda x{a.demanda:g}')
    dw = pd.read_csv(os.path.join(dst, 'demand_weekly.csv'))
    if a.demanda != 1.0:
        dw['tons_week'] = (dw['tons_week'] * a.demanda).round(1)
    if a.tarifa != 1.0:
        dw['tariff_usd_per_kg'] = (dw['tariff_usd_per_kg'] * a.tarifa).round(4)
        cambios.append(f'tarifas x{a.tarifa:g}')
    dw.to_csv(os.path.join(dst, 'demand_weekly.csv'), index=False)

    print(f'Escenario creado en {dst}: {", ".join(cambios) if cambios else "sin cambios (control)"}')


if __name__ == '__main__':
    main()
