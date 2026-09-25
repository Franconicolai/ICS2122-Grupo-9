from src.estructuras_datos import cargar_estructuras_funcionales
from src.gurobi_model import construir_modelo_completo

datos = cargar_estructuras_funcionales('data/data.sqlite')

# Un solo avión, elegido porque tiene un tramo barato (BRU-FRA) que calza
# con un commodity real de alta tarifa el día 5
aviones_prueba = ['AC-01']
commodity_prueba = ('BRU', 'FRA', 5)

datos_reducidos = dict(datos)
datos_reducidos['K'] = aviones_prueba
datos_reducidos['Ek'] = {k: datos['Ek'][k] for k in aviones_prueba}
datos_reducidos['S_max_k'] = {k: 4 for k in aviones_prueba}
datos_reducidos['Q'] = [commodity_prueba]
datos_reducidos['Q_ke'] = {(k, e): v for (k, e), v in datos['Q_ke'].items() if k in aviones_prueba}
datos_reducidos['c_ke'] = {(k, e): v for (k, e), v in datos['c_ke'].items() if k in aviones_prueba}

modelo = construir_modelo_completo(datos_reducidos)
print('Variables:', modelo.NumVars, '| Restricciones:', modelo.NumConstrs)
modelo.optimize()

print()
print('Status:', modelo.Status)
if modelo.SolCount > 0:
    print('Valor objetivo:', modelo.ObjVal)
    for v in modelo.getVars():
        if v.X > 0.5 and (v.VarName.startswith('y') or v.VarName.startswith('u')):
            print(v.VarName, '=', v.X)
    for v in modelo.getVars():
        if v.X > 0.01 and v.VarName.startswith('s_q'):
            print(v.VarName, '=', v.X)