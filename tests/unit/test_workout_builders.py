import json
import os

import pytest

from garmin_mcp.workout_builders import (
    build_walk_run_json,
    build_z2_walk_json,
    build_strength_json,
)

SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "captured")


def test_build_walk_run_json_matches_poc_snapshot():
    """The walk/run builder must produce the exact JSON that Garmin accepted in the POC."""
    result = build_walk_run_json(
        name="POC Walk/Run 7x1m/3m Z3",
        run_seconds=60,
        walk_seconds=180,
        repeats=7,
        warmup_min=10,
        cooldown_min=8,
        hr_zone="Z3",
    )

    # Compare against the validated POC snapshot
    snapshot_path = os.path.join(SNAPSHOT_DIR, "poc_walk_run.json")
    with open(snapshot_path, "r", encoding="utf-8") as f:
        expected = json.load(f)

    assert result == expected


def _repeat_steps(result):
    """Return the run/walk steps inside the repeat group of a walk/run workout."""
    repeat = result["workoutSegments"][0]["workoutSteps"][1]
    assert repeat["type"] == "RepeatGroupDTO"
    return repeat["workoutSteps"]


def test_build_walk_run_json_distance_goals():
    """Distance intervals end on distance instead of time."""
    result = build_walk_run_json(
        name="Run 5x400m",
        repeats=5,
        warmup_min=10,
        cooldown_min=5,
        run_distance_m=400,
        walk_distance_m=200,
        hr_zone="Z4",
    )
    run_step, walk_step = _repeat_steps(result)
    assert run_step["endCondition"] == {"conditionTypeId": 3, "conditionTypeKey": "distance"}
    assert run_step["endConditionValue"] == 400.0
    assert walk_step["endConditionValue"] == 200.0
    # Distance + HR zone target preserved
    assert run_step["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert run_step["zoneNumber"] == 4
    assert "400m run / 200m walk" in result["description"]


def test_build_walk_run_json_pace_target():
    """Pace targets produce a pace.zone speed window in m/s."""
    result = build_walk_run_json(
        name="Tempo intervals",
        repeats=4,
        warmup_min=10,
        cooldown_min=5,
        run_seconds=300,
        walk_seconds=120,
        target_type="pace",
        run_pace="5:00",
        walk_pace="8:00",
        pace_tolerance_sec=10,
    )
    run_step, walk_step = _repeat_steps(result)
    assert run_step["targetType"] == {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
    assert "zoneNumber" not in run_step
    # 5:00/km = 300s/km. With +/-10s window: 310s (slow) -> 1000/310 m/s, 290s (fast) -> 1000/290 m/s
    assert run_step["targetValueOne"] == pytest.approx(1000.0 / 310.0, rel=1e-4)
    assert run_step["targetValueTwo"] == pytest.approx(1000.0 / 290.0, rel=1e-4)
    assert run_step["targetValueOne"] < run_step["targetValueTwo"]
    assert walk_step["targetValueOne"] == pytest.approx(1000.0 / 490.0, rel=1e-4)


def test_build_walk_run_json_pace_and_distance_combined():
    result = build_walk_run_json(
        name="Pace by distance",
        repeats=3,
        warmup_min=10,
        cooldown_min=5,
        run_distance_m=1000,
        walk_distance_m=400,
        target_type="pace",
        run_pace="4:30",
        walk_pace="7:00",
    )
    run_step, _ = _repeat_steps(result)
    assert run_step["endCondition"]["conditionTypeKey"] == "distance"
    assert run_step["targetType"]["workoutTargetTypeKey"] == "pace.zone"


def test_build_walk_run_json_pace_mile_unit():
    result = build_walk_run_json(
        name="Mile pace",
        repeats=2,
        warmup_min=10,
        cooldown_min=5,
        run_seconds=300,
        walk_seconds=120,
        target_type="pace",
        run_pace="8:00",
        walk_pace="12:00",
        pace_tolerance_sec=0,
        pace_unit="mi",
    )
    run_step, _ = _repeat_steps(result)
    # 8:00/mile = 480s/mile; tolerance 0 -> low == high == 1609.344/480
    assert run_step["targetValueOne"] == pytest.approx(1609.344 / 480.0, rel=1e-4)


def test_build_walk_run_json_pace_requires_pace_values():
    with pytest.raises(ValueError, match="requires a pace value"):
        build_walk_run_json(
            name="bad",
            repeats=2,
            warmup_min=10,
            cooldown_min=5,
            run_seconds=300,
            walk_seconds=120,
            target_type="pace",
        )


def test_build_walk_run_json_distance_requires_both():
    with pytest.raises(ValueError, match="both run_distance_m and walk_distance_m"):
        build_walk_run_json(
            name="bad",
            repeats=2,
            warmup_min=10,
            cooldown_min=5,
            run_distance_m=400,
        )


def test_build_walk_run_json_time_requires_both():
    with pytest.raises(ValueError, match="run_seconds and walk_seconds"):
        build_walk_run_json(
            name="bad",
            repeats=2,
            warmup_min=10,
            cooldown_min=5,
        )


def test_build_walk_run_json_invalid_target_type():
    with pytest.raises(ValueError, match="Invalid target_type"):
        build_walk_run_json(
            name="bad",
            repeats=2,
            warmup_min=10,
            cooldown_min=5,
            run_seconds=300,
            walk_seconds=120,
            target_type="power",
        )


def test_build_z2_walk_json_structure():
    result = build_z2_walk_json(
        name="Z2 Walk 30m",
        duration_min=30,
        hr_min=110,
        hr_max=130,
    )
    assert result["workoutName"] == "Z2 Walk 30m"
    assert result["sportType"]["sportTypeKey"] == "walking"
    assert result["sportType"]["sportTypeId"] == 12
    steps = result["workoutSegments"][0]["workoutSteps"]
    assert len(steps) == 3
    assert steps[1]["zoneNumber"] == 2
    assert steps[1]["endConditionValue"] == 1800.0


def test_build_strength_json_structure():
    result = build_strength_json(
        name="Full Body A",
        exercises=[
            {"name": "Sentadillas", "sets": 3, "reps": 12, "rest_seconds": 90},
            {"name": "Flexiones", "sets": 3, "reps": 15, "rest_seconds": 60},
        ],
    )
    assert result["workoutName"] == "Full Body A"
    assert result["sportType"]["sportTypeKey"] == "strength_training"
    assert result["sportType"]["sportTypeId"] == 5
    steps = result["workoutSegments"][0]["workoutSteps"]
    # 2 exercises + 1 rest between them = 3 steps
    assert len(steps) == 3
    assert steps[0]["exerciseName"] == "Sentadillas"
    assert steps[2]["exerciseName"] == "Flexiones"
