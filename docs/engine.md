# Engine

The engine is the core rule evaluation component of the nudging-tool. It maps incoming Digital Twin events to nudge decisions.

## Rule Evaluation

For each incoming event:
1. The engine queries the database for rules matching the `event_type`.
2. Each rule is evaluated against the event's `facts` dictionary (conditions may check field presence, thresholds, or scenario values).
3. The first matching rule's `nudge_type` and `severity` are selected.
4. The appropriate Jinja2 template is rendered using the facts as context variables.

## NudgeType and Severity

| NudgeType | Description |
|---|---|
| `energy_alert` | Significant deviation in energy behavior |
| `savings_opportunity` | Actionable suggestion to reduce consumption or increase sharing |
| `community_milestone` | Community-level achievement or threshold crossed |
| `incentive_update` | Changes to incentive rates or GSE values |

| Severity | Description |
|---|---|
| `info` | Informational, low urgency |
| `warning` | Attention required |
| `critical` | Immediate action suggested |

## Jinja2 Templates

Templates use the `facts` payload as context variables:

```
{% if delta_pct > 20 %}
Your import from the grid increased by {{ delta_pct|round(1) }}% compared to {{ prev_period }}.
{% else %}
Your import changed by {{ delta_pct|round(1) }}%.
{% endif %}
```

Templates are language-specific. The engine selects the template matching the user's preferred language (from `UserPreference`) or falls back to `DEFAULT_LANG`.

## Deduplication Scopes

Deduplication prevents sending the same nudge type to the same user multiple times within a window:

| Scope | Window |
|---|---|
| `daily` | Same nudge type not repeated today |
| `weekly` | Same nudge type not repeated this ISO week |
| `monthly` | Same nudge type not repeated this month |
| `yearly` | Same nudge type not repeated this year |

The dedup key is composed of: `user_id + community_id + nudge_type + scope`.

## YAML Seed Format

Rules, templates, and preferences are seeded from YAML files at DB initialization:

**seed/rules.yaml:**
```yaml
- event_type: imported_up
  conditions:
    delta_pct:
      gte: 10.0
  nudge_type: energy_alert
  severity: warning
  frequency: daily
```

**seed/templates.yaml:**
```yaml
- nudge_type: energy_alert
  severity: warning
  language: en
  title: "Grid import increased"
  body: "Your grid import rose by {{ delta_pct|round(1) }}% compared to last {{ period }}."
```

**seed/preferences.yaml:**
```yaml
- user_id: user-it
  community_id: COMM1
  language: it
  max_per_day: 3
  enabled: true
```

## Seed Data Management

Seed data is applied by `python -m db.init_db`, which performs `drop_all + create_all` followed by seed loading. To update rules without resetting, modify YAML files and re-run the init script in a dev environment. Production updates should use Alembic migrations or direct DB writes.
