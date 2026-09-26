# -*- coding: utf-8 -*-
"""每一種需求類型都必須配得出來，否則它就是一條看不見的死路。

`demo_water` 有中文標籤、存得進資料庫、在後台和 LINE 上都顯示成「飲用水」，
但它不在 TYPE_AFFINITY 裡。少了那一列，候選清單永遠是空的，而且不報錯——
正式環境六筆需求就這樣靜靜地永遠派不出去，直到有人真的去按那顆按鈕。

標籤表和媒合矩陣是兩份各自維護的表；只要有人往其中一份加東西而忘了另一份，
就會再生一條死路。這裡把兩份綁在一起。
"""
import pytest

from app.labels import NEED_TYPE_ZH
from app.services.dispatch import TYPE_AFFINITY

# sos 不走物資媒合，它走的是求救單與聯絡流程。
NOT_MATCHED_BY_SUPPLY = {"sos"}


def test_every_labelled_need_type_can_find_candidates():
    labelled = set(NEED_TYPE_ZH) - NOT_MATCHED_BY_SUPPLY
    missing = sorted(labelled - set(TYPE_AFFINITY))
    assert not missing, (
        f"這些需求類型看得到中文名字，卻不在 TYPE_AFFINITY 裡，"
        f"候選會永遠是空的而且不報錯：{missing}")


def test_every_affinity_entry_has_a_name_people_can_read():
    """反過來也要成立，否則畫面會印出英文代碼。"""
    unnamed = sorted(set(TYPE_AFFINITY) - set(NEED_TYPE_ZH))
    assert not unnamed, f"這些類型配得出來，卻沒有中文說法：{unnamed}"


@pytest.mark.parametrize("need_type", sorted(set(NEED_TYPE_ZH) - NOT_MATCHED_BY_SUPPLY))
def test_each_type_maps_to_at_least_one_resource_type(need_type):
    from app.validation import RESOURCE_TYPES

    pairs = TYPE_AFFINITY[need_type]
    assert pairs, f"{need_type} 的候選資源類型是空的"
    for resource_type, weight in pairs:
        assert resource_type in RESOURCE_TYPES, (
            f"{need_type} 指向不存在的物資類型 {resource_type!r}")
        assert 0 < weight <= 1.0, f"{need_type} → {resource_type} 的權重是 {weight}"
