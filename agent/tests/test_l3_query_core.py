from __future__ import annotations

from l3_query.models import ObservationFrame
from l3_query.observe import render_observation_frame
from l3_query.query_core import apply_filters, parse_pagination, ref_value, representatives, sort_facts
from l3_query.schema import SCHEMAS, get_schema, validate_filters, validate_schema_aggregates, validate_sort


def _entity(ref: str, type_: str, x: int, y: int, **payload):
    return {
        "ref": {"value": ref},
        "type": type_,
        "fact_kind": type_,
        "position": {"x": x, "y": y},
        type_: payload,
    }


def test_ref_value_is_opaque_passthrough():
    fact = _entity("world_entity:tree:farm:41:12#wt_1", "tree", 41, 12)
    assert ref_value(fact) == "world_entity:tree:farm:41:12#wt_1"


def test_filter_supports_eq_in_range_and_exists():
    facts = [
        _entity("a", "tree", 1, 1, has_moss=True, growth_stage=5, tree_type="oak"),
        _entity("b", "tree", 2, 2, has_moss=False, growth_stage=2, tree_type="pine"),
    ]
    assert len(apply_filters(facts, {"has_moss": True})) == 1
    assert len(apply_filters(facts, {"tree_type": {"in": ["oak", "maple"]}})) == 1
    assert len(apply_filters(facts, {"growth_stage": {"min": 3}})) == 1
    assert len(apply_filters(facts, {"tree_type": {"exists": True}})) == 2


def test_representatives_are_sorted_by_derived_distance():
    facts = [
        {**_entity("far", "tree", 10, 0), "distance": 10.0},
        {**_entity("near", "tree", 1, 0), "distance": 1.0},
    ]
    assert [ref_value(fact) for fact in sort_facts(facts, "distance")] == ["near", "far"]
    assert representatives(facts, limit=1)["items"] == ["near"]
    assert representatives(facts, limit=1)["has_more"] is True


def test_parse_pagination_rejects_values_that_llm_may_pass_in_wrong_shape():
    assert parse_pagination("2", "1") == (2, 1, None)
    assert parse_pagination("bad", 0)[2] == "limit 必须是整数，收到 'bad'"
    assert parse_pagination(2, "later")[2] == "offset 必须是整数，收到 'later'"
    assert parse_pagination(0, 0)[2] == "limit 必须大于等于 1，收到 0"
    assert parse_pagination(2, -1)[2] == "offset 必须大于等于 0，收到 -1"


def test_observe_frame_groups_facts_without_hardcoding_in_observe():
    frame = ObservationFrame(
        runtime={
            "time_of_day": 1000,
            "season": "spring",
            "day_of_month": 5,
            "player_location": {"map_id": "Farm", "x": 0, "y": 0},
        },
        world_region={
            "map_id": "Farm",
            "entities": [
                _entity(
                    "crop-1",
                    "crop",
                    1,
                    0,
                    display_name="Parsnip",
                    watered=False,
                    ready_for_harvest=False,
                    dead=False,
                ),
                _entity(
                    "crop-2",
                    "crop",
                    2,
                    0,
                    display_name="Parsnip",
                    watered=True,
                    ready_for_harvest=True,
                    dead=False,
                ),
                _entity(
                    "tree-1",
                    "tree",
                    3,
                    0,
                    display_name="Oak Tree",
                    has_moss=True,
                    is_stump=False,
                ),
            ],
            "characters": [],
        },
    )

    result = render_observation_frame(frame)
    groups = {group["fact_type"]: group for group in result["data"]["groups"]}

    assert result["has_proto_facts"] is True
    assert "当前视野内有 2 个庄稼" in result["description"]
    assert groups["crop"]["summary"]["needs_watering"] == 1
    assert groups["crop"]["summary"]["ready_to_harvest"] == 1
    assert groups["crop"]["representatives"]["items"] == ["crop-1", "crop-2"]
    assert groups["tree"]["summary"]["has_moss"] == 1


def test_query_schema_declares_supported_filter_sort_aggregate_fields():
    schema = get_schema("tree")

    assert schema is not None
    assert "has_moss" in schema.filter_fields()
    assert "growth_stage" in schema.sort_fields()
    assert "tree_type" in schema.aggregate_fields()
    assert validate_filters(schema, {"has_moss": True}) == []
    assert validate_filters(schema, {"unknown_field": True})
    assert validate_sort(schema, "growth_stage") == []
    assert validate_sort(schema, "has_moss")


def test_schema_aggregate_specs_are_self_consistent():
    errors = {
        fact_type: validate_schema_aggregates(schema)
        for fact_type, schema in SCHEMAS.items()
    }

    assert errors == {fact_type: [] for fact_type in SCHEMAS}
