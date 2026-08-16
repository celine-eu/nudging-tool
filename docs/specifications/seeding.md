# Seeding

The seed directory is the product configuration of this service: what each rule requires,
what every message says, and what a participant is allowed to switch off.

It is read at startup, by the CLI, and **on every preferences request** — so a malformed
directory is not a deployment problem but a request-time one.

---

### REQ-0068 — a rule is a directory, and its directory name is its id

```text
seed/
    active_kinds.yaml
    rules/
        <rule id>/
            rule.yaml
            evaluate.py
            templates/
                en.yaml
                it.yaml
```

The id is filled in from the directory when `rule.yaml` omits it, and the same directory
name is where the evaluator is looked for (REQ-0028) — the two cannot be separated.

A template takes its rule from its parent directory and its language from its filename,
so neither has to be repeated in the file, and a template moved to the wrong directory is
silently a template for another rule.

Older layouts — `rules.yaml`, `templates/`, `preferences/`, `overrides/` at the top
level — are still read when the per-rule directories yield nothing.

### REQ-0069 — `active_kinds.yaml` is mandatory and its translations are checked

A missing or unparseable catalogue raises, which is why a service cannot start without
one (REQ-0050): with no catalogue there would be no active kinds and everything would be
suppressed as unknown.

Each entry needs `kind`, and `label`, `description` and `cadence` as maps carrying **it**,
**en** and **es**. Catalan is not required, though `/preferences` accepts `ca` — a Catalan
reader gets English (REQ-0075).

`rule_ids` names the rules a kind covers. Nothing enforces the link — suppression matches
on `definition.kind`, not on this list — so an id here that names no rule is a
documentation error the code cannot see. `sensor_not_working` is one such today.

### REQ-0070 — a repeated kind is dropped on load and reported on validation

Loading keeps the first and discards the rest, so the service starts. `validate_seed`,
which the CLI runs and the startup path does not, reports it. A duplicate is therefore
invisible in production and visible in `nudging-cli seed apply`.

Every seed model forbids unknown fields, so a misspelled key is an error rather than a
setting that silently does nothing — the one place in this service where a typo in
configuration is loud.

### REQ-0071 — `scenarios` may be written at the top level of a rule

It is merged into `definition.scenarios` for authoring convenience, and does **not**
overwrite a `scenarios` already written inside the definition. `upsert_rule` then fills
the `scenarios` column from the definition, which is why the engine can read either
(REQ-0023).

### REQ-0072 — seeding is an upsert on the logical key, and is safe to repeat

It runs on every boot of every replica. The keys are: a rule by its id, a template by
(rule, language), a preference by (participant, community), an override by (rule,
community).

An override's absent fields are left alone rather than reset, so an override carrying only
`enabled_override` does not wipe a `definition_override` seeded earlier. A preference with
no `lang` keeps the one it has.

`POST /admin/seed/apply` performs the same upserts over HTTP for an administrator, so a
rule can be changed without a deploy. That route does **not** run `validate_seed`, so a
definition it accepts may be one the engine cannot use.

### REQ-0073 — a template's id is derived, not generated

`tpl_<rule>_<lang>`, with `/` and spaces replaced. The same seed therefore produces the
same ids in every environment, and re-seeding a translation replaces it rather than
leaving a second row for the engine to pick between arbitrarily.

### REQ-0074 — an unknown kind is allowed, and skips the rest of validation

`validate_rule_definition` requires `definition.kind`. If the kind is not in the
catalogue it warns and **returns early**, so `required_facts` and `scenarios` are never
checked — a rule can be seeded with a malformed definition simply by naming a kind nobody
declared.

For known kinds: `required_facts` and `scenarios` must be lists of strings,
`threshold_pct` must be a number for `imported_up`/`imported_down`, and `kpi_conditions`
must carry a non-empty `conditions` list whose entries each have a `fact_key`, a `value`
and an `op` drawn from `< <= > >= == !=`.

### REQ-0075 — localisation falls back to English, then to whatever exists

An unknown language reads in English; a kind with no English entry falls back to its first
translation. A guess, but a label in the wrong language beats an empty one on a
preferences screen.
