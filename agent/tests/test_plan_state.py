from __future__ import annotations

from slow_loop.plan_state import PlanItem, PlanState


def test_plan_state_starts_first_item_and_tracks_remaining():
    plan = PlanState.from_items(
        source_input="先浇水，再收菜",
        items=[
            PlanItem(label="浇水", tool="skill-water_crops"),
            PlanItem(label="收菜", tool="skill-harvest_crops"),
        ],
    )

    item = plan.start_next(job_id="job_water")

    assert item is not None
    assert item.label == "浇水"
    assert plan.status == "active"
    assert plan.active is not None
    assert plan.active.job_id == "job_water"
    assert [step.label for step in plan.remaining] == ["收菜"]


def test_plan_state_ok_completion_moves_active_to_done():
    plan = PlanState.from_items(
        source_input="先浇水，再收菜",
        items=[
            PlanItem(label="浇水", tool="skill-water_crops"),
            PlanItem(label="收菜", tool="skill-harvest_crops"),
        ],
    )
    plan.start_next(job_id="job_water")

    result = {
        "job_id": "job_water",
        "success": True,
        "action": "skill-water_crops",
        "message": "浇完了 12 块地",
        "data": {"remaining_unwatered": 0},
    }
    plan.apply_completion(result)

    assert plan.status == "planned"
    assert [step.label for step in plan.done] == ["浇水"]
    assert plan.done[0].result_status == "ok"
    assert plan.active is None
    assert [step.label for step in plan.remaining] == ["收菜"]
    assert plan.last_result == result


def test_plan_state_partial_completion_blocks_plan():
    plan = PlanState.from_items(
        source_input="先收菜，再睡觉",
        items=[
            PlanItem(label="收菜", tool="skill-harvest_crops"),
            PlanItem(label="睡觉", tool="skill-go_bed"),
        ],
    )
    plan.start_next(job_id="job_harvest")

    plan.apply_completion({
        "job_id": "job_harvest",
        "success": True,
        "action": "skill-harvest_crops",
        "message": "背包满了",
        "data": {"remaining_harvestable": 8},
    })

    assert plan.status == "blocked"
    assert plan.blocked_reason == "partial"
    assert plan.active is None
    assert [step.label for step in plan.remaining] == ["睡觉"]


def test_plan_state_failed_completion_blocks_plan():
    plan = PlanState.from_items(
        source_input="去睡觉",
        items=[PlanItem(label="睡觉", tool="skill-go_bed")],
    )
    plan.start_next(job_id="job_sleep")

    plan.apply_completion({
        "job_id": "job_sleep",
        "success": False,
        "action": "skill-go_bed",
        "error_code": "NO_PATH",
        "message": "找不到床",
    })

    assert plan.status == "blocked"
    assert plan.blocked_reason == "NO_PATH"
    assert plan.active is None
    assert [step.label for step in plan.done] == []
