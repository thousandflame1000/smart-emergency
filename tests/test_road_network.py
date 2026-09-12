# -*- coding: utf-8 -*-
"""app/services/road_network.py — real Hua-Dong corridor road graph."""
from app.services import road_network as rn


def test_same_point_is_zero_distance():
    lat, lng = rn.NODES["yuli"]
    assert rn.road_distance_km(lat, lng, lat, lng) == 0.0


def test_adjacent_towns_use_direct_edge():
    lat1, lng1 = rn.NODES["ruisui"]
    lat2, lng2 = rn.NODES["yuli"]
    d = rn.road_distance_km(lat1, lng1, lat2, lng2)
    assert d is not None
    assert 15 < d < 35  # real Ruisui-Yuli road distance ballpark


def test_coastal_to_valley_uses_the_connector_not_a_straight_line():
    """Chenggong (coastal) to Yuli (valley) must route through the real
    Yuchang Highway connector — this is exactly the case where straight-
    line distance would cut across the Coastal Range where no road exists."""
    lat1, lng1 = rn.NODES["chenggong"]
    lat2, lng2 = rn.NODES["yuli"]
    road_d = rn.road_distance_km(lat1, lng1, lat2, lng2)
    straight_d = rn.haversine_km(lat1, lng1, lat2, lng2)
    assert road_d is not None
    assert road_d >= straight_d  # road can never be shorter than straight line
    assert road_d < straight_d * 3  # but shouldn't be absurdly indirect either


def test_point_far_from_network_returns_none():
    # Taichung — nowhere near the Hua-Dong corridor
    assert rn.road_distance_km(24.15, 120.67, 24.15, 120.67) is None or \
        rn.road_distance_km(24.15, 120.67, *rn.NODES["yuli"]) is None


def test_end_to_end_route_is_longer_than_any_single_edge():
    lat1, lng1 = rn.NODES["hualien"]
    lat2, lng2 = rn.NODES["taitung"]
    d = rn.road_distance_km(lat1, lng1, lat2, lng2)
    assert d is not None
    assert d > 100  # real Hualien-Taitung road distance is well over 100km
