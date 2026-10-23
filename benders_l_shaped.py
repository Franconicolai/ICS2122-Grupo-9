"""
Método integer L-shaped (Laporte y Louveaux, 1993) para el modelo integrado en red espacio-tiempo.

  Maestro (MILP):      vuelos de cada avión (z), espera en tierra (g), avión en uso (u) y theta.
  Subproblema (MIP):   el paso final de la heurística completo para esos vuelos: carga multitramo, transbordos,
                       mínimo de 10 t por vuelo cargado y regla de 90 min lleno-lleno (binarias propias).

Cortes:
  - Cortes de subgradiente: de la relajación lineal del subproblema, theta <= Q_LP(y^) + g'(y - y^). Son válidos
    porque Q_LP >= Q (la relajación nunca vale menos que el subproblema entero).
  - Cortes L-shaped enteros: en cada solución entera y^, con Q_sup >= Q(y^) (cota del subproblema entero) y U una
    cota de Q para cualquier solución:
        theta <= Q_sup + (U - Q_sup) * [ sum_{i: y^_i = 1} (1 - y_i) + sum_{i: y^_i = 0} y_i ]
    Es exacto en y^ y no corta ninguna otra solución entera.

Algoritmo:
  Fase 1: relajación lineal del maestro + cortes de subgradiente hasta que no mejoran (cierra gran parte de la brecha).
  Fase 2: un solo árbol del maestro entero; en cada solución entera se resuelven el subproblema entero (valor real) y
          su relajación (corte de subgradiente), y se agregan ambos cortes como restricciones perezosas.
Converge al óptimo del problema completo con la grilla de DT horas; la cota del maestro es una cota global válida.

Uso, desde la raíz del repositorio:
    python benders_l_shaped.py --dt 0.5                       # problema completo, nodos cada 30 min
    python benders_l_shaped.py --dt 2 --libres AC-06,AC-10 --tiempo 1800
    python benders_l_shaped.py --dt 0.25 --estimar            # 15 min: solo el tamaño
    python benders_l_shaped.py --help
"""
from benders_comun import ejecutar

if __name__ == '__main__':
    ejecutar('l_shaped', 'Integer L-shaped (Benders entero) del modelo integrado espacio-tiempo.')
