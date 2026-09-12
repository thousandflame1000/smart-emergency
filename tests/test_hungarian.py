# -*- coding: utf-8 -*-
"""
app/services/hungarian.py 是自寫的純 Python 匈牙利演算法（取代 scipy，
避免重演 numpy 那次因為隱性 C 擴充套件依賴造成的正式環境部署失敗）。
這裡用 scipy.optimize.linear_sum_assignment 交叉驗證正確性——scipy
只在測試環境用來當「正確答案」，不是正式程式碼的執行期依賴。
"""
import random

import pytest

scipy_opt = pytest.importorskip("scipy.optimize")

from app.services.hungarian import min_cost_assignment


def _total_cost(cost, assignment):
    return sum(cost[r][c] for r, c in assignment.items())


def _scipy_optimal_cost(cost):
    import numpy as np
    row_ind, col_ind = scipy_opt.linear_sum_assignment(np.array(cost))
    return sum(cost[r][c] for r, c in zip(row_ind, col_ind))


@pytest.mark.parametrize("seed", range(50))
def test_matches_scipy_on_random_square_matrices(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 8)
    cost = [[rng.uniform(-50, 50) for _ in range(n)] for _ in range(n)]
    ours = min_cost_assignment(cost)
    assert len(ours) == n
    assert abs(_total_cost(cost, ours) - _scipy_optimal_cost(cost)) < 1e-6


@pytest.mark.parametrize("seed", range(50))
def test_matches_scipy_on_random_rectangular_matrices(seed):
    rng = random.Random(seed)
    n_rows = rng.randint(1, 8)
    n_cols = rng.randint(1, 8)
    cost = [[rng.uniform(-50, 50) for _ in range(n_cols)] for _ in range(n_rows)]
    ours = min_cost_assignment(cost)
    assert len(ours) == min(n_rows, n_cols)
    assert abs(_total_cost(cost, ours) - _scipy_optimal_cost(cost)) < 1e-6


def test_more_rows_than_cols_assigns_every_column():
    cost = [[1, 9], [9, 1], [5, 5]]
    result = min_cost_assignment(cost)
    assert len(result) == 2
    assert set(result.values()) == {0, 1}
    assert abs(_total_cost(cost, result) - 2) < 1e-9


def test_more_cols_than_rows_assigns_every_row():
    cost = [[1, 9, 9], [9, 1, 9]]
    result = min_cost_assignment(cost)
    assert len(result) == 2
    assert result[0] == 0 and result[1] == 1


def test_classic_counterexample_matches_manual_dispatch_scenario():
    """
    A: X=10成本(即分數10, cost=-10), Y=cost -9
    B: X=cost -10, Y=不相容(用極大值代表)
    貪婪法（先處理 A）：A 拿 X，B 沒有東西可拿。
    最優解：A 拿 Y、B 拿 X，兩邊都被滿足。
    """
    BIG = 1e6
    cost = [
        [-10, -9],
        [-10, BIG],
    ]
    result = min_cost_assignment(cost)
    assert result[0] == 1  # A -> Y
    assert result[1] == 0  # B -> X


def test_empty_matrix_returns_empty():
    assert min_cost_assignment([]) == {}
