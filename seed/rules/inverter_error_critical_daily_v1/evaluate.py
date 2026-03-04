def evaluate(rule, facts):
    threshold_error_number = float(
        (getattr(rule, "definition", None) or {}).get("threshold_error_number", 1.0)
    )

    error_count = facts.get("system_status_error_last_day_total")
    if error_count is None:
        return False, facts, "missing_fact:system_status_error_last_day_total"

    try:
        error_count_value = float(error_count)
    except Exception:
        return (
            False,
            {**facts, "system_status_error_last_day_total": error_count},
            "invalid_fact:system_status_error_last_day_total",
        )

    enriched = {
        **facts,
        "system_status_error_last_day_total": error_count_value,
        "threshold_error_number": threshold_error_number,
    }
    if error_count_value < threshold_error_number:
        return False, enriched, "condition_not_met"

    return True, enriched, None
