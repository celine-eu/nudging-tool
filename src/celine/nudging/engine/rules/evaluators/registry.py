from __future__ import annotations

import importlib
import importlib.util
import logging
from pathlib import Path
from typing import Callable

from celine.nudging.config.settings import settings
from celine.nudging.db.models import Rule

logger = logging.getLogger(__name__)


def _load_custom_evaluator(module_path: str) -> Callable | None:
    try:
        mod = importlib.import_module(module_path)
        fn = getattr(mod, "evaluate", None)
        if callable(fn):
            return fn
    except Exception:
        logger.exception("Failed loading evaluator module: %s", module_path)
    return None


_PATH_CACHE: dict[str, Callable] = {}


def _load_evaluator_from_path(path: str) -> Callable | None:
    if path in _PATH_CACHE:
        return _PATH_CACHE[path]
    try:
        module_name = f"rule_eval_{abs(hash(path))}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            fn = getattr(mod, "evaluate", None)
            if callable(fn):
                _PATH_CACHE[path] = fn
                return fn
    except Exception:
        logger.exception("Failed loading evaluator path: %s", path)
    return None


def evaluate_rule(rule: Rule, facts: dict) -> tuple[bool, dict, str | None]:
    evaluator_path = (rule.definition or {}).get("evaluator_path")
    if isinstance(evaluator_path, str) and evaluator_path:
        p = Path(evaluator_path)
        if not p.is_absolute():
            if p.name == evaluator_path:
                p = (
                    Path(settings.SEED_DIR or "./seed")
                    / "rules"
                    / str(rule.id)
                    / evaluator_path
                )
            else:
                p = Path(settings.SEED_DIR or "./seed") / evaluator_path
        fn = _load_evaluator_from_path(str(p))
        if fn:
            return fn(rule, facts)
        return False, dict(facts), "evaluator_path_not_found"

    module_path = (rule.definition or {}).get("evaluator_module")
    if isinstance(module_path, str) and module_path:
        fn = _load_custom_evaluator(module_path)
        if fn:
            return fn(rule, facts)
        return False, dict(facts), "evaluator_module_not_found"

    return False, dict(facts), "evaluator_not_configured"
