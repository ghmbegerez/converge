"""Calibration: data-driven threshold adjustment from historical risk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from converge import event_log
from converge.defaults import QUERY_LIMIT_LARGE
from converge.models import Event, EventType, now_iso
from converge.policy import calibrate_profiles, load_config


def run_calibration(
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Calibrate policy profiles from historical risk data."""
    risk_events = event_log.query(event_type=EventType.RISK_EVALUATED, limit=QUERY_LIMIT_LARGE)
    historical = [e["payload"] for e in risk_events]

    config = load_config()
    new_profiles = calibrate_profiles(historical, config.profiles)

    result: dict[str, Any] = {
        "calibrated_profiles": new_profiles,
        "data_points": len(historical),
        "timestamp": now_iso(),
    }

    path = Path(output_path or ".converge/calibrated_profiles.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(new_profiles, f, indent=2)
    result["output_path"] = str(path)

    event_log.append(Event(
        event_type=EventType.CALIBRATION_COMPLETED,
        payload=result,
        evidence={"data_points": len(historical)},
    ))

    return result
