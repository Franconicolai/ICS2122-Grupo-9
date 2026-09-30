"""
Resolución de un MILP de maximización dado en forma matricial:  max c'x  s.a.  lo <= A x <= hi,  lb <= x <= ub.
Permite elegir el solver con la variable de entorno SOLVER: 'highs' (por defecto, gratuito) o 'gurobi'.
THREADS fija los hilos (por defecto, todos los disponibles).
"""
import os, time
import numpy as np
from scipy.sparse import csr_matrix

SOLVER = os.environ.get('SOLVER', 'highs').lower()
THREADS = int(os.environ.get('THREADS', 0))


def resolver_matriz(lb, ub, obj, integ, A, lo, hi, tlim, gap=0.01, log=True, relax=False):
    """Función resolver_matriz: [Descripción pendiente]."""
    lb, ub, obj = np.asarray(lb, float), np.asarray(ub, float), np.asarray(obj, float)
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    integ = np.asarray(integ, bool) & (not relax)
    t0 = time.time()
    if SOLVER == 'gurobi':
        import gurobipy as gp
        from gurobipy import GRB
        A = csr_matrix(A)
        m = gp.Model()
        m.Params.OutputFlag = 1 if log else 0
        m.Params.TimeLimit = tlim
        m.Params.MIPGap = gap
        if THREADS: m.Params.Threads = THREADS
        vt = np.where(integ, GRB.INTEGER, GRB.CONTINUOUS)
        x = m.addMVar(len(lb), lb=lb, ub=np.where(np.isinf(ub), GRB.INFINITY, ub), obj=obj, vtype=vt)
        m.ModelSense = GRB.MAXIMIZE
        eq = np.isfinite(lo) & np.isfinite(hi) & (np.abs(lo - hi) < 1e-12)
        ge = np.isfinite(lo) & ~eq
        le = np.isfinite(hi) & ~eq
        if eq.any(): m.addConstr(A[eq] @ x == lo[eq])
        if ge.any(): m.addConstr(A[ge] @ x >= lo[ge])
        if le.any(): m.addConstr(A[le] @ x <= hi[le])
        m.optimize()
        ok = m.SolCount > 0
        estados = {GRB.OPTIMAL: 'Optimal', GRB.TIME_LIMIT: 'Time limit reached', GRB.INFEASIBLE: 'Infeasible',
                   GRB.INF_OR_UNBD: 'Infeasible or unbounded'}
        mip = bool(integ.any())
        return dict(estado=estados.get(m.Status, str(m.Status)), obj=m.ObjVal if ok else None,
                    cota=(m.ObjBound if mip else (m.ObjVal if ok else None)),
                    gap=(m.MIPGap if (mip and ok) else None), tiempo=time.time() - t0,
                    sol=list(x.X) if ok else None)
    # ---- HiGHS
    import highspy
    from scipy.sparse import csc_matrix
    A = csc_matrix(A)
    h = highspy.Highs()
    h.setOptionValue('output_flag', log); h.setOptionValue('time_limit', tlim); h.setOptionValue('mip_rel_gap', gap)
    if THREADS: h.setOptionValue('threads', THREADS)
    lp = highspy.HighsLp(); lp.num_col_, lp.num_row_ = len(lb), len(lo)
    lp.col_cost_, lp.col_lower_, lp.col_upper_ = obj, lb, ub
    lp.row_lower_, lp.row_upper_ = lo, hi
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_, lp.a_matrix_.index_, lp.a_matrix_.value_ = A.indptr, A.indices, A.data
    lp.sense_ = highspy.ObjSense.kMaximize
    if integ.any():
        lp.integrality_ = [highspy.HighsVarType.kInteger if z else highspy.HighsVarType.kContinuous for z in integ]
    h.passModel(lp); h.run()
    inf = h.getInfo()
    ok = inf.primal_solution_status == 2
    return dict(estado=h.modelStatusToString(h.getModelStatus()), obj=inf.objective_function_value if ok else None,
                cota=getattr(inf, 'mip_dual_bound', None), gap=getattr(inf, 'mip_gap', None),
                tiempo=time.time() - t0, sol=list(h.getSolution().col_value) if ok else None)


def filas_a_matriz(rows, nc):
    """rows: lista de (dict col->coef, lo, hi[, nombre]). Devuelve (A csr, lo, hi)."""
    data, ri, ci = [], [], []
    lo, hi = np.empty(len(rows)), np.empty(len(rows))
    for i, r in enumerate(rows):
        coef, l, u = r[0], r[1], r[2]
        lo[i], hi[i] = l, u
        for j, v in coef.items():
            if v != 0: ri.append(i); ci.append(j); data.append(v)
    return csr_matrix((data, (ri, ci)), shape=(len(rows), nc)), lo, hi
