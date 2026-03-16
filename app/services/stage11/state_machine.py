from __future__ import annotations


def can_transition(current_mode: str, target_mode: str, *, manual_approve: bool) -> tuple[bool, str]:
    current = str(current_mode or "SHADOW").upper()
    target = str(target_mode or current).upper()
    if current == target:
        return True, "no_change"
    if target not in {"SHADOW", "LIMITED_EXECUTION", "FULL_EXECUTION"}:
        return False, "invalid_target_mode"
    if target == "SHADOW":
        return True, "rollback_to_shadow"
    if not manual_approve:
        return False, "manual_approve_required"
    if current == "SHADOW" and target == "LIMITED_EXECUTION":
        return True, "approved_shadow_to_limited"
    if current == "LIMITED_EXECUTION" and target == "FULL_EXECUTION":
        return True, "approved_limited_to_full"
    return False, "invalid_transition"

