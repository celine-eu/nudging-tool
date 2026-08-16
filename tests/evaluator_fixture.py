"""An importable evaluator, for the `evaluator_module` branch of the registry.

No seeded rule uses that branch; it exists so the branch is exercised by something other
than its own failure path.
"""


def evaluate(rule, facts):
    return True, dict(facts), None
