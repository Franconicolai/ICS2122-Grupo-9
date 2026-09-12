import os
import sys
import gurobipy as gp
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from estructuras_datos import cargar_estructuras_funcionales
from gurobi_model import construir_modelo_completo
from exportador_resultados import generar_reportes, extraer_kpis

def main():

    load_dotenv()
    
    nombre_bd = os.getenv('DB_PATH', 'database.sqlite')
    ruta_bd = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', nombre_bd))
    
    datos = cargar_estructuras_funcionales(ruta_bd)
    
    try:
        limite_tiempo = int(os.getenv('GUROBI_TIME_LIMIT', 60))
        
        modelo = construir_modelo_completo(datos)
        modelo.setParam('TimeLimit', limite_tiempo)
        
        modelo.optimize()

        kpis = extraer_kpis(modelo)
        print(kpis)
        generar_reportes(modelo)
        print(f"Reporte generado en {ruta_salida}")
            
    except gp.GurobiError as e:
        print(f"\n[!] Error de Gurobi: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()