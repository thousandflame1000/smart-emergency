# -*- coding: utf-8 -*-
"""
Pure-Python Hungarian algorithm (Kuhn-Munkres, O(n^3) shortest augmenting
path formulation) for the rectangular assignment problem. No scipy/numpy
dependency on purpose: this project already had one production outage from
an unpinned transitive C-extension dependency (numpy via pgvector); adding
scipy (a much larger compiled dependency) for a single small function is
not worth repeating that risk for matrices this size (a single
neighborhood's open needs vs. available resources — realistically tens,
not thousands, of rows/columns).

Reference formulation: https://cp-algorithms.com/graph/hungarian-algorithm.html
Correctness is verified against scipy.optimize.linear_sum_assignment in
tests/test_hungarian.py (scipy is a dev-only tool there, never a runtime
dependency of the shipped app).
"""
INF = float("inf")


def min_cost_assignment(cost: list[list[float]]) -> dict[int, int]:
    """
    cost[i][j] = cost of assigning row i to column j (minimize).
    Returns {row_index: col_index} for every row in the smaller dimension
    (all rows get assigned to some column if n_rows <= n_cols, and vice
    versa) — callers that use a large sentinel cost for incompatible
    pairs should discard any returned pair whose cost is >= that sentinel,
    since a forced assignment can still happen when rows/cols are matched
    1:1 and there's no better alternative in the matrix.
    """
    n_rows = len(cost)
    if n_rows == 0:
        return {}
    n_cols = len(cost[0])
    if n_cols == 0:
        return {}

    transposed = n_rows > n_cols
    if transposed:
        a = [[cost[i][j] for i in range(n_rows)] for j in range(n_cols)]
        n, m = n_cols, n_rows
    else:
        a = [row[:] for row in cost]
        n, m = n_rows, n_cols
    # a is n x m with n <= m, 0-indexed; algorithm below uses 1-indexed
    # internal arrays (standard formulation), padded with a leading dummy.

    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)   # p[j] = row (1-indexed) assigned to column j
    way = [0] * (m + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = -1
            for j in range(1, m + 1):
                if not used[j]:
                    cur = a[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    result: dict[int, int] = {}
    for j in range(1, m + 1):
        if p[j] != 0:
            row = p[j] - 1
            col = j - 1
            if transposed:
                result[col] = row  # swap back: original row=resource-index col, col=need-index row
            else:
                result[row] = col
    return result
