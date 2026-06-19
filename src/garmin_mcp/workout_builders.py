"""
High-level workout builders for Garmin Connect MCP Server.

These tools construct the internal Garmin Connect JSON internally and delegate
to the existing upload_workout / schedule_workout endpoints.
"""
import json
from typing import Any, Dict, List, Optional

# The garmin_client will be set by the main file
garmin_client = None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


# =============================================================================
# JSON BUILDERS
# =============================================================================

HR_ZONE_MAP = {
    "Z1": 1,
    "Z2": 2,
    "Z3": 3,
    "Z4": 4,
    "Z5": 5,
}


def _zone_number(zone: str) -> int:
    """Resolve a human-friendly zone string like 'Z3' to Garmin's zoneNumber."""
    zone_upper = zone.strip().upper()
    if zone_upper in HR_ZONE_MAP:
        return HR_ZONE_MAP[zone_upper]
    # Fallback: if user passed a digit directly
    try:
        z = int(zone_upper)
        if 1 <= z <= 5:
            return z
    except ValueError:
        pass
    raise ValueError(f"Invalid hr_zone '{zone}'. Use Z1-Z5 or 1-5.")


# Distance (in meters) covered by one unit of pace, keyed by accepted unit aliases.
_PACE_UNIT_METERS = {
    "km": 1000.0,
    "kilometer": 1000.0,
    "kilometre": 1000.0,
    "mi": 1609.344,
    "mile": 1609.344,
    "miles": 1609.344,
}


def _pace_to_seconds(pace: str) -> int:
    """Parse a 'MM:SS' pace string into total seconds."""
    parts = str(pace).strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid pace '{pace}'. Use 'MM:SS' (e.g. '7:30').")
    try:
        minutes, seconds = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid pace '{pace}'. Use 'MM:SS' (e.g. '7:30').")
    total = minutes * 60 + seconds
    if total <= 0:
        raise ValueError(f"Invalid pace '{pace}'. Must be greater than zero.")
    return total


def _pace_to_speed_range(pace: str, tolerance_sec: int, unit: str) -> tuple[float, float]:
    """Convert a 'MM:SS' target pace into a (low_speed, high_speed) m/s range.

    Garmin pace targets are stored as a speed window in meters/second.
    ``targetValueOne`` is the slower bound (lower speed) and ``targetValueTwo`` the
    faster bound (higher speed). The window is the target pace +/- ``tolerance_sec``.
    """
    distance_m = _PACE_UNIT_METERS.get(str(unit).strip().lower())
    if distance_m is None:
        raise ValueError(f"Invalid pace_unit '{unit}'. Use 'km' or 'mi'.")
    target_sec = _pace_to_seconds(pace)
    tol = max(0, int(tolerance_sec))
    slow_sec = target_sec + tol           # slower pace -> lower speed
    fast_sec = max(1, target_sec - tol)   # faster pace -> higher speed
    low_speed = distance_m / slow_sec
    high_speed = distance_m / fast_sec
    return round(low_speed, 6), round(high_speed, 6)


def _interval_target_fields(
    target_type: str,
    hr_zone: str,
    pace: Optional[str],
    pace_tolerance_sec: int,
    pace_unit: str,
) -> tuple[dict, str]:
    """Return (step target fields, short target label) for a run/walk interval step."""
    if target_type == "heart_rate":
        zone = _zone_number(hr_zone)
        fields = {
            "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
            "zoneNumber": zone,
        }
        return fields, f"Z{zone}"
    if target_type == "pace":
        if not pace:
            raise ValueError("target_type 'pace' requires a pace value (e.g. '7:30').")
        low_speed, high_speed = _pace_to_speed_range(pace, pace_tolerance_sec, pace_unit)
        fields = {
            "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
            "targetValueOne": low_speed,
            "targetValueTwo": high_speed,
        }
        return fields, f"@{pace}/{pace_unit}"
    raise ValueError(f"Invalid target_type '{target_type}'. Use 'heart_rate' or 'pace'.")


def _interval_end_condition(seconds: Optional[int], distance_m: Optional[float]) -> tuple[dict, float, str]:
    """Return (endCondition block, endConditionValue, short label) for an interval step."""
    if distance_m is not None:
        return (
            {"conditionTypeId": 3, "conditionTypeKey": "distance"},
            float(distance_m),
            f"{distance_m:g}m",
        )
    return (
        {"conditionTypeId": 2, "conditionTypeKey": "time"},
        float(seconds),
        f"{seconds}s",
    )


def build_walk_run_json(
    name: str,
    repeats: int,
    warmup_min: int,
    cooldown_min: int,
    run_seconds: Optional[int] = None,
    walk_seconds: Optional[int] = None,
    run_distance_m: Optional[float] = None,
    walk_distance_m: Optional[float] = None,
    hr_zone: str = "Z3",
    target_type: str = "heart_rate",
    run_pace: Optional[str] = None,
    walk_pace: Optional[str] = None,
    pace_tolerance_sec: int = 15,
    pace_unit: str = "km",
) -> dict:
    """Build the Garmin Connect JSON for a walk/run interval workout.

    Supports two independent dimensions:

    * **End condition** - intervals end by time (``run_seconds`` / ``walk_seconds``)
      or by distance (``run_distance_m`` / ``walk_distance_m``).
    * **Target** - ``target_type='heart_rate'`` uses ``hr_zone`` (Z1-Z5); a
      ``target_type='pace'`` uses ``run_pace`` / ``walk_pace`` ('MM:SS' per
      ``pace_unit``) with a +/- ``pace_tolerance_sec`` window.

    Parameters match create_walk_run_workout exactly.
    """
    # --- Resolve and validate the end condition (time vs distance) ---
    use_distance = run_distance_m is not None or walk_distance_m is not None
    if use_distance:
        if run_distance_m is None or walk_distance_m is None:
            raise ValueError(
                "Distance intervals require both run_distance_m and walk_distance_m."
            )
        run_seconds = walk_seconds = None
    else:
        if run_seconds is None or walk_seconds is None:
            raise ValueError(
                "Time intervals require both run_seconds and walk_seconds "
                "(or provide run_distance_m and walk_distance_m for distance goals)."
            )

    # --- Resolve and validate the target (heart rate vs pace) ---
    run_target, run_target_label = _interval_target_fields(
        target_type, hr_zone, run_pace, pace_tolerance_sec, pace_unit
    )
    walk_target, walk_target_label = _interval_target_fields(
        target_type, hr_zone, walk_pace, pace_tolerance_sec, pace_unit
    )

    run_cond, run_cond_value, run_dur_label = _interval_end_condition(run_seconds, run_distance_m)
    walk_cond, walk_cond_value, walk_dur_label = _interval_end_condition(walk_seconds, walk_distance_m)

    run_step = {
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
        "description": f"Run {run_dur_label} {run_target_label}",
        "endCondition": run_cond,
        "endConditionValue": run_cond_value,
        **run_target,
    }
    walk_step = {
        "type": "ExecutableStepDTO",
        "stepOrder": 2,
        "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
        "description": f"Walk {walk_dur_label} {walk_target_label}",
        "endCondition": walk_cond,
        "endConditionValue": walk_cond_value,
        **walk_target,
    }

    return {
        "workoutName": name,
        "description": (
            f"{warmup_min}m warmup + {repeats}x({run_dur_label} run / {walk_dur_label} walk) "
            f"{run_target_label} + {cooldown_min}m cooldown"
        ),
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                    "description": f"Warmup {warmup_min} min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(warmup_min * 60),
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
                {
                    "type": "RepeatGroupDTO",
                    "stepOrder": 2,
                    "numberOfIterations": repeats,
                    "workoutSteps": [run_step, walk_step],
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 3,
                    "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                    "description": f"Cooldown {cooldown_min} min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(cooldown_min * 60),
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
            ],
        }],
    }


def build_z2_walk_json(
    name: str,
    duration_min: int,
    hr_min: int,
    hr_max: int,
) -> dict:
    """Build the Garmin Connect JSON for a steady Z2 walking workout with absolute HR range."""
    return {
        "workoutName": name,
        "description": f"Walk {duration_min} min at Z2 ({hr_min}-{hr_max} bpm)",
        "sportType": {"sportTypeId": 12, "sportTypeKey": "walking"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 12, "sportTypeKey": "walking"},
            "workoutSteps": [
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 1,
                    "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup"},
                    "description": "Warmup 5 min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": 300.0,
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 2,
                    "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
                    "description": f"Walk {duration_min} min Z2",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": float(duration_min * 60),
                    "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
                    "zoneNumber": 2,
                },
                {
                    "type": "ExecutableStepDTO",
                    "stepOrder": 3,
                    "stepType": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
                    "description": "Cooldown 5 min",
                    "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                    "endConditionValue": 300.0,
                    "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
                },
            ],
        }],
    }


# Simplified internal exercise catalog (English → Garmin exerciseName key or fallback)
# Garmin strength workouts use exerciseName as a free-text label when the exercise
# is not in their catalog. For structured strength, we use "Other" (generic) and
# put the user name in description / exerciseName.

def build_strength_json(
    name: str,
    exercises: List[Dict[str, Any]],
) -> dict:
    """Build the Garmin Connect JSON for a strength workout.

    Each exercise maps to a generic step; if the name is not recognised in the
    Garmin catalog we use 'Other' and put the original name in exerciseName.
    """
    steps: List[dict] = []
    step_order = 1

    for ex in exercises:
        ex_name = ex.get("name", "Exercise")
        sets = int(ex.get("sets", 1))
        reps = int(ex.get("reps", 1))
        rest_seconds = int(ex.get("rest_seconds", 60))

        # Work step
        steps.append({
            "type": "ExecutableStepDTO",
            "stepOrder": step_order,
            "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
            "description": f"{ex_name}: {sets} sets x {reps} reps",
            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
            "endConditionValue": float(sets * 45),  # rough estimate: 45s per set
            "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            "exerciseName": ex_name,
        })
        step_order += 1

        # Rest step (skip after last exercise)
        if rest_seconds > 0 and ex != exercises[-1]:
            steps.append({
                "type": "ExecutableStepDTO",
                "stepOrder": step_order,
                "stepType": {"stepTypeId": 4, "stepTypeKey": "recovery"},
                "description": f"Rest {rest_seconds}s",
                "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
                "endConditionValue": float(rest_seconds),
                "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            })
            step_order += 1

    return {
        "workoutName": name,
        "description": f"Strength: {len(exercises)} exercises",
        "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
            "workoutSteps": steps,
        }],
    }


# =============================================================================
# MCP TOOLS
# =============================================================================

def register_tools(app):
    """Register all high-level workout builder tools with the MCP server app"""

    @app.tool()
    async def create_walk_run_workout(
        name: str,
        repeats: int,
        warmup_min: int,
        cooldown_min: int,
        run_seconds: Optional[int] = None,
        walk_seconds: Optional[int] = None,
        run_distance_m: Optional[float] = None,
        walk_distance_m: Optional[float] = None,
        hr_zone: str = "Z3",
        target_type: str = "heart_rate",
        run_pace: Optional[str] = None,
        walk_pace: Optional[str] = None,
        pace_tolerance_sec: int = 15,
        pace_unit: str = "km",
    ) -> str:
        """Create a walk/run interval workout and upload it to Garmin Connect.

        Builds the internal Garmin JSON automatically and returns the new workout ID.

        Each run/walk interval can end by **time** or by **distance**, and can target
        either a **heart-rate zone** or a **pace** window.

        End condition (pick one pair):
            - Time goals: provide run_seconds and walk_seconds.
            - Distance goals: provide run_distance_m and walk_distance_m (in meters);
              when set, these take precedence over the *_seconds values.

        Target (set via target_type):
            - "heart_rate" (default): uses hr_zone.
            - "pace": uses run_pace and walk_pace.

        Args:
            name: Workout name (e.g. "W3 Mié 2:2")
            repeats: Number of run/walk repetitions
            warmup_min: Warmup duration in minutes
            cooldown_min: Cooldown duration in minutes
            run_seconds: Duration of each run interval in seconds (time goals)
            walk_seconds: Duration of each walk/recovery interval in seconds (time goals)
            run_distance_m: Distance of each run interval in meters (distance goals)
            walk_distance_m: Distance of each walk interval in meters (distance goals)
            hr_zone: Target heart-rate zone (Z1-Z5, default Z3) when target_type="heart_rate"
            target_type: "heart_rate" (default) or "pace"
            run_pace: Run target pace as "MM:SS" per pace_unit (required when target_type="pace")
            walk_pace: Walk target pace as "MM:SS" per pace_unit (required when target_type="pace")
            pace_tolerance_sec: Half-width of the pace window in seconds (default 15)
            pace_unit: Unit for pace values, "km" (default) or "mi"
        """
        try:
            workout_json = build_walk_run_json(
                name=name,
                repeats=repeats,
                warmup_min=warmup_min,
                cooldown_min=cooldown_min,
                run_seconds=run_seconds,
                walk_seconds=walk_seconds,
                run_distance_m=run_distance_m,
                walk_distance_m=walk_distance_m,
                hr_zone=hr_zone,
                target_type=target_type,
                run_pace=run_pace,
                walk_pace=walk_pace,
                pace_tolerance_sec=pace_tolerance_sec,
                pace_unit=pace_unit,
            )
            result = garmin_client.upload_workout(workout_json)

            if isinstance(result, dict):
                curated = {
                    "status": "success",
                    "workout_id": result.get("workoutId"),
                    "name": result.get("workoutName"),
                    "message": "Workout uploaded successfully",
                }
                curated = {k: v for k, v in curated.items() if v is not None}
                return json.dumps(curated, indent=2)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error creating walk/run workout: {str(e)}"

    @app.tool()
    async def create_z2_walk_workout(
        name: str,
        duration_min: int,
        hr_min: int,
        hr_max: int,
    ) -> str:
        """Create a steady Z2 walking workout and upload it to Garmin Connect.

        Args:
            name: Workout name
            duration_min: Main walking block duration in minutes
            hr_min: Minimum heart rate in bpm (used for description; target is Z2)
            hr_max: Maximum heart rate in bpm (used for description; target is Z2)
        """
        try:
            workout_json = build_z2_walk_json(
                name=name,
                duration_min=duration_min,
                hr_min=hr_min,
                hr_max=hr_max,
            )
            result = garmin_client.upload_workout(workout_json)

            if isinstance(result, dict):
                curated = {
                    "status": "success",
                    "workout_id": result.get("workoutId"),
                    "name": result.get("workoutName"),
                    "message": "Workout uploaded successfully",
                }
                curated = {k: v for k, v in curated.items() if v is not None}
                return json.dumps(curated, indent=2)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error creating Z2 walk workout: {str(e)}"

    @app.tool()
    async def create_strength_workout(
        name: str,
        exercises: List[Dict[str, Any]],
    ) -> str:
        """Create a strength workout and upload it to Garmin Connect.

        Each exercise is mapped to a generic step; unsupported names fallback to
        "Other" with the original name stored in exerciseName.

        Args:
            name: Workout name
            exercises: List of dicts with keys: name, sets, reps, rest_seconds
        """
        try:
            workout_json = build_strength_json(name=name, exercises=exercises)
            result = garmin_client.upload_workout(workout_json)

            if isinstance(result, dict):
                curated = {
                    "status": "success",
                    "workout_id": result.get("workoutId"),
                    "name": result.get("workoutName"),
                    "message": "Workout uploaded successfully",
                }
                curated = {k: v for k, v in curated.items() if v is not None}
                return json.dumps(curated, indent=2)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error creating strength workout: {str(e)}"

    @app.tool()
    async def schedule_week(week: List[Dict[str, Any]]) -> str:
        """Schedule a list of workouts for the week in a single call.

        Idempotent: if a workout is already scheduled for that date, it is
        reported as already scheduled and the POST is skipped (avoids
        duplicating calendar entries).

        Args:
            week: List of dicts with keys: date (YYYY-MM-DD), workout_id (int)
        """
        # Imported here (not at module top) to avoid any import-time ordering
        # surprises between sibling modules. Both modules share the same
        # garmin_client instance via configure() in __main__.
        from garmin_mcp.workouts import _is_already_scheduled

        try:
            results = []
            for item in week:
                calendar_date = item["date"]
                workout_id = int(item["workout_id"])

                if _is_already_scheduled(workout_id, calendar_date):
                    results.append({
                        "date": calendar_date,
                        "workout_id": workout_id,
                        "status": "already_scheduled",
                        "idempotent": True,
                    })
                    continue

                # garminconnect 0.3.2 dropped the .garth attribute; use .client.
                url = f"workout-service/schedule/{workout_id}"
                response = garmin_client.client.post(
                    "connectapi", url, json={"date": calendar_date}
                )
                if response.status_code == 200:
                    results.append({
                        "date": calendar_date,
                        "workout_id": workout_id,
                        "status": "scheduled",
                    })
                else:
                    results.append({
                        "date": calendar_date,
                        "workout_id": workout_id,
                        "status": "failed",
                        "http_status": response.status_code,
                    })
            return json.dumps({
                "status": "complete",
                "scheduled": results,
            }, indent=2)
        except Exception as e:
            return f"Error scheduling week: {str(e)}"

    return app
