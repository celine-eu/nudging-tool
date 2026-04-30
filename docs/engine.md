# Engine

The engine is the core rule evaluation component. It maps incoming events to notification decisions using per-rule Python evaluators.

## Rule Evaluation

For each incoming event:
1. The engine resolves matching rules by `rule_id` list or event scenario mapping
2. Each rule's custom Python evaluator is loaded from `seed/rules/<rule_id>/evaluate.py`
3. The evaluator decides whether the rule should fire given the event payload
4. If it fires, the appropriate Jinja2 template is rendered per channel and language
5. Per-community rule overrides may modify rule behavior

## NudgeType

| NudgeType | Description |
|---|---|
| `informative` | General information, low urgency |
| `opportunity` | Actionable suggestion or flexibility window |
| `alert` | Attention required, higher urgency |

## Jinja2 Templates

Templates use the event `facts` payload as context variables:

```
{% if delta_pct > 20 %}
Your import from the grid increased by {{ delta_pct|round(1) }}% compared to {{ prev_period }}.
{% else %}
Your import changed by {{ delta_pct|round(1) }}%.
{% endif %}
```

Templates are organized per channel (web, email) and language. The engine selects the template matching the user's preferred language or falls back to `DEFAULT_LANG`.

## Deduplication Scopes

Deduplication prevents sending the same rule to the same user multiple times within a window:

| Scope | Window |
|---|---|
| `daily` | Same rule not repeated today |
| `weekly` | Same rule not repeated this ISO week |
| `monthly` | Same rule not repeated this month |
| `yearly` | Same rule not repeated this year |

The dedup key is: `rule_id:user_id:community_id:scope`.

## Notification Kinds

`active_kinds.yaml` defines the catalog of notification kinds with i18n labels. Users can opt in or out of specific kinds via their preferences.

## Rule Overrides

Per-community overrides allow customizing rule behavior (e.g., different thresholds, templates, or enabled status) without modifying the base rule definition.

## Seed Directory Structure

Rules are seeded from a directory structure (default `./seed`):

```
seed/
  rules/
    imported_up/
      rule.yaml           # Rule definition (kind, nudge_type, severity, etc.)
      evaluate.py          # Custom Python evaluator
      templates/
        web_en.j2          # Web push template (English)
        web_it.j2          # Web push template (Italian)
        email_en.j2        # Email template (English)
    flexibility_opportunity/
      rule.yaml
      evaluate.py
      templates/
        web_en.j2
    ...
  active_kinds.yaml        # Notification kind catalog with i18n
```

Each rule directory contains:
- `rule.yaml` — rule metadata (kind, nudge_type, severity, definition)
- `evaluate.py` — Python module with an evaluate function that receives the event and returns a decision
- `templates/` — Jinja2 templates named `{channel}_{lang}.j2`

## Seed Management

```bash
# Apply seed data via CLI
nudging-cli seed apply ./seed --client-id celine-cli --client-secret celine-cli

# Or via taskfile
task seed
```

The CLI authenticates and calls `POST /admin/seed/apply` to upsert rules and templates from the seed directory.
