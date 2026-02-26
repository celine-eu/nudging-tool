import asyncio
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.nudging.config.settings import settings
from celine.nudging.db.models import Rule, RuleOverride, Template, UserPreference
from celine.nudging.db.session import AsyncSessionLocal
from celine.nudging.seed import load_seed_dir, validate_seed

logger = logging.getLogger(__name__)

SEED_DIR = Path(settings.SEED_DIR or (Path.cwd() / "seed"))

if not SEED_DIR.exists():
    logger.error(f"Seed dir {SEED_DIR} does not exists. Provide with SEED_DIR env.")


def _tpl_id(rule_id: str, lang: str) -> str:
    # deterministic id (stable across DBs)
    safe_rule = rule_id.replace("/", "_").replace(" ", "_")
    safe_lang = lang.replace("/", "_").replace(" ", "_")
    return f"tpl_{safe_rule}_{safe_lang}"


async def upsert_rule(db: AsyncSession, r: dict):
    existing = await db.execute(select(Rule).where(Rule.id == r["id"]))
    obj = existing.scalar_one_or_none()
    if obj is None:
        obj = Rule(id=r["id"])
        db.add(obj)

    obj.name = r["name"]
    obj.enabled = bool(r.get("enabled", True))
    obj.family = r["family"]
    obj.type = r["type"]
    obj.severity = r["severity"]
    obj.version = int(r.get("version", 1))
    obj.definition = r.get("definition", {})
    obj.scenarios = (r.get("definition") or {}).get("scenarios") or r.get("scenarios") or []


async def upsert_template(db: AsyncSession, t: dict):
    # logical keys
    rule_id = t["rule_id"]
    lang = (t.get("lang") or settings.DEFAULT_LANG or "en").strip()

    # upsert by logical unique key: (rule_id, lang)
    existing = await db.execute(
        select(Template).where(Template.rule_id == rule_id, Template.lang == lang)
    )
    obj = existing.scalar_one_or_none()

    # allow explicit id in YAML, otherwise deterministic
    template_id = t.get("id") or _tpl_id(rule_id, lang)

    if obj is None:
        obj = Template(id=template_id)
        db.add(obj)
    else:
        # keep stable IDs across DBs if you want: ensure obj.id is not changed
        # If you want to "force" deterministic ids even on existing rows, uncomment:
        # obj.id = obj.id or template_id
        pass

    obj.rule_id = rule_id
    obj.lang = lang
    obj.title_jinja = t["title_jinja"]
    obj.body_jinja = t["body_jinja"]


async def upsert_preference(db: AsyncSession, p: dict):
    user_id = p["user_id"]
    community_id = p.get("community_id")

    existing = await db.execute(
        select(UserPreference).where(
            UserPreference.user_id == user_id,
            UserPreference.community_id == community_id,
        )
    )
    obj = existing.scalar_one_or_none()
    if obj is None:
        obj = UserPreference(user_id=user_id, community_id=community_id)
        db.add(obj)

    # NEW: language (preferred)
    pref_lang = p.get("lang")
    if isinstance(pref_lang, str) and pref_lang.strip():
        obj.lang = pref_lang.strip()
    else:
        # if missing, keep existing or default
        if not getattr(obj, "lang", None):
            obj.lang = settings.DEFAULT_LANG

    obj.channel_web = bool(p.get("channel_web", True))
    obj.channel_email = bool(p.get("channel_email", False))
    obj.channel_telegram = bool(p.get("channel_telegram", False))
    obj.channel_whatsapp = bool(p.get("channel_whatsapp", False))

    obj.email = p.get("email")
    obj.telegram_chat_id = p.get("telegram_chat_id")
    obj.whatsapp_phone = p.get("whatsapp_phone")

    obj.max_per_day = int(p.get("max_per_day", settings.MAX_PER_DAY_DEFAULT))
    obj.consents = p.get("consents", {})


async def upsert_rule_override(db: AsyncSession, o: dict):
    rule_id = o["rule_id"]
    community_id = o["community_id"]

    existing = await db.execute(
        select(RuleOverride).where(
            RuleOverride.rule_id == rule_id,
            RuleOverride.community_id == community_id,
        )
    )
    obj = existing.scalar_one_or_none()
    if obj is None:
        obj = RuleOverride(rule_id=rule_id, community_id=community_id)
        db.add(obj)

    if "enabled_override" in o:
        obj.enabled_override = o.get("enabled_override")
    if "definition_override" in o:
        obj.definition_override = o.get("definition_override") or {}


async def main():
    seed = load_seed_dir(SEED_DIR)
    seed, errors = validate_seed(seed)
    if errors:
        for e in errors:
            logger.error("Seed error: %s", e)
        raise SystemExit(1)

    rules_y = seed.rules
    tmpl_y = seed.templates
    pref_y = seed.preferences
    overrides_y = seed.overrides

    async with AsyncSessionLocal() as db:
        for r in rules_y:
            await upsert_rule(db, r)

        for t in tmpl_y:
            await upsert_template(db, t)

        for p in pref_y:
            await upsert_preference(db, p)

        for o in overrides_y:
            await upsert_rule_override(db, o)

        await db.commit()

    print(
        f"Seed completed: {len(rules_y)} rules, {len(tmpl_y)} templates, "
        f"{len(pref_y)} preferences, {len(overrides_y)} overrides."
    )


if __name__ == "__main__":
    asyncio.run(main())
