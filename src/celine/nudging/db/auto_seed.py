"""Auto-seed support.

Called during the FastAPI lifespan if SEED_DIR is configured.
Reads the three YAML files and calls the same upsert logic used by
the HTTP endpoint — so startup seeding and CLI seeding are identical.
"""

from __future__ import annotations

import logging
from pathlib import Path

from celine.nudging.config.settings import settings
from celine.nudging.seed import (
    load_seed_dir,
)
from celine.nudging.db.seed_db import (
    upsert_preference,
    upsert_rule,
    upsert_template,
)
from celine.nudging.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def auto_seed() -> None:
    """Idempotently seed the database from SEED_DIR if it is configured.

    Safe to call every startup — all operations are upserts.
    Skips silently when SEED_DIR is not set or the directory is empty.
    """
    if not settings.SEED_DIR:
        logger.debug("SEED_DIR not set — skipping auto-seed.")
        return

    seed_dir = Path(settings.SEED_DIR)
    if not seed_dir.exists():
        logger.warning("SEED_DIR %s does not exist — skipping auto-seed.", seed_dir)
        return

    seeds = load_seed_dir(seed_dir)

    rules_data = seeds.rules
    tmpl_data = seeds.templates
    pref_data = seeds.preferences

    if not any([rules_data, tmpl_data, pref_data]):
        logger.info("No seed data found in %s — skipping.", seed_dir)
        return

    async with AsyncSessionLocal() as db:
        for r in rules_data:
            await upsert_rule(db, r)
        for t in tmpl_data:
            await upsert_template(db, t)
        for p in pref_data:
            await upsert_preference(db, p)
        await db.commit()

    logger.info(
        "Auto-seed complete: %d rules, %d templates, %d preferences.",
        len(rules_data),
        len(tmpl_data),
        len(pref_data),
    )
