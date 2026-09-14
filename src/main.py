"""
Módulo: src/main.py
Descripción: Script exclusivo para la ejecución del modelo de Gurobi 
en la terminal (sin iniciar el servidor web o la interfaz gráfica).
"""
import os
import sys
import gurobipy as gp
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from estructuras_datos import cargar_estructuras_funcionales
from gurobi_model import construir_modelo_completo
from exportador_resultados import generar_reportes, extraer_kpis

def run_model_only():
    load_dotenv()
    
    nombre_bd = os.getenv('DB_PATH', 'data/data.sqlite')
    ruta_bd = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', nombre_bd))
    
    try:
        datos = cargar_estructuras_funcionales(ruta_bd)
        limite_tiempo = int(os.getenv('GUROBI_TIME_LIMIT', 60))
        
        modelo = construir_modelo_completo(datos)
        modelo.setParam('TimeLimit', limite_tiempo)
        
        print("\nOptimizando con Gurobi...")
        modelo.optimize()

        kpis = extraer_kpis(modelo)
        print("\nKPIs Obtenidos:")
        for k, v in kpis.items():
            print(f"  {k}: {v}")
            
        ruta_salida = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'resources', 'resultados.json'))
        generar_reportes(modelo, ruta_salida)
        print("\n¡Resultados exportados exitosamente!")
            
    except gp.GurobiError as e:
        print(f"\n[!] Error de Gurobi: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run_model_only()
