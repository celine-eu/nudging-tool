def evaluate(rule, facts):
    """Fire when the report carries at least one line at or above the threshold."""
    days = facts.get("days") or []
    has_lines = any(
        vector.get("top_lines")
        for day in days
        if isinstance(day, dict)
        for vector in (day.get("vectors") or [])
        if isinstance(vector, dict)
    )
    if not has_lines:
        return False, dict(facts), "no_line_at_or_above_threshold"
    return True, dict(facts), None
