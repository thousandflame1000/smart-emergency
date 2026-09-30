"""匈牙利演算法的正確性：用窮舉比對，不依賴 scipy。

test_hungarian.py 用 scipy 當標準答案，但 scipy 不在執行期依賴裡，沒裝時整個檔案會被略過，
派遣最核心的演算法就等於沒有測試在跑。小矩陣（最多 6×6、720 種排法）可以直接窮舉出最佳解。
"""
import itertools
import random

import pytest

from app.services.hungarian import min_cost_assignment


def _brute_force_best(cost):
    rows, cols = len(cost), len(cost[0])
    if rows <= cols:
        return min(sum(cost[r][c] for r, c in enumerate(pick)) for pick in itertools.permutations(range(cols), rows))
    return min(sum(cost[r][c] for c, r in enumerate(pick)) for pick in itertools.permutations(range(rows), cols))


def _check(cost):
    assignment = min_cost_assignment(cost)
    rows, cols = len(cost), len(cost[0])
    assert len(assignment) == min(rows, cols), "較小的一邊每個都要配到"
    assert len(set(assignment.values())) == len(assignment), "同一欄不能被配兩次"
    assert sum(cost[r][c] for r, c in assignment.items()) == pytest.approx(_brute_force_best(cost))


@pytest.mark.parametrize("seed", range(60))
def test_matches_brute_force_on_random_matrices(seed):
    rng = random.Random(seed)
    rows, cols = rng.randint(1, 6), rng.randint(1, 6)
    _check([[rng.randint(0, 30) for _ in range(cols)] for _ in range(rows)])


def test_ties_and_large_sentinel_costs():
    blocked = 10 ** 6  # 派遣用大數字代表「不能配」（例如物資種類不合）
    _check([[1, blocked, blocked], [blocked, 1, blocked], [blocked, blocked, 1]])
    _check([[5, 5, 5], [5, 5, 5]])
    _check([[0.5, 2.25], [1.75, 0.25], [3.0, 3.0]])


def test_empty_input():
    assert min_cost_assignment([]) == {}
