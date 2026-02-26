def evaluate(rule, facts):
    threshold_pct = float(rule.definition.get("threshold_pct", 20.0))

    delta_pct = facts.get("delta_pct")
    if delta_pct is None:
        return False, facts, "missing_fact:delta_pct"

    try:
        triggered = float(delta_pct) > threshold_pct
    except Exception:
        return False, {**facts, "delta_pct": delta_pct}, "invalid_fact:delta_pct"

    enriched = {**facts, "threshold_pct": threshold_pct}
    if not triggered:
        return False, enriched, "condition_not_met"

    return True, enriched, None
