"""
Descomposición de Benders del modelo integrado en red espacio-tiempo, con las binarias de la carga en el maestro.

  Maestro (MILP):  vuelos de cada avión (z), espera en tierra (g), avión en uso (u) y, por cada vuelo, las binarias
                   que antes estaban en el paso final de la heurística: lc (lleva carga, >= 10 t) y f (puede ir
                   lleno, regla de 90 min). Más theta, la estimación de la ganancia por carga.
  Subproblema (LP): con esas decisiones fijas, la carga se rutea como un flujo continuo: embarque, through,
                   transbordos por depósito (TTR_H, soporte de operadores, sin re-embarque propio) y entrega.

Como el subproblema es un LP, sus duales dan cortes de optimalidad exactos (Benders clásico):
    theta <= Q(y^) + g'(y - y^),   g = T' pi
El mínimo de 10 t se escribe L + s >= 10 lc con una holgura s penalizada (100.000 USD/t), así el subproblema
siempre es factible; con esa penalización, mayor que cualquier valor dual, la solución óptima no usa holgura.

Algoritmo:
  Fase 1: se resuelve la relajación lineal del maestro y se le agregan cortes hasta que no mejoran (cierra gran
          parte de la brecha antes de ramificar).
  Fase 2: un solo árbol de ramificación del maestro entero (branch-and-Benders-cut): cada solución entera se evalúa
          en el subproblema y, si theta la sobreestima, se agrega el corte como restricción perezosa.
Converge al óptimo del problema completo con la grilla de DT horas; la cota del maestro es una cota global válida.

Uso, desde la raíz del repositorio:
    python benders_binarias_maestro.py --dt 0.5                 # problema completo, nodos cada 30 min
    python benders_binarias_maestro.py --dt 2 --libres AC-06,AC-10 --tiempo 1800
    python benders_binarias_maestro.py --dt 0.75 --estimar      # 45 min: solo el tamaño
    python benders_binarias_maestro.py --help
"""
from benders_comun import ejecutar

if __name__ == '__main__':
    ejecutar('binarias_maestro', 'Benders del modelo integrado espacio-tiempo con las binarias de carga en el maestro.')
