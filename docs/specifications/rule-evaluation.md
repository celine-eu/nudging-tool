# Rule evaluation

From a validated event to a rendered message. Every step here can drop an event without
an error, and each reports itself only as a status and an audit row (REQ-0017).

---

### REQ-0020 — the *shape* of the sender's time value is the frequency

There is no `frequency` field. `_infer_time_scope` reads the first non-empty of `time`,
`date`, `week`, `period` — in that order — and matches it against four anchored patterns:

| Value | Frequency |
|---|---|
| `2026-08-15` | daily |
| `2026-W33` | weekly |
| `2026-08` | monthly |
| `2026` | yearly |

Surrounding whitespace is forgiven. So a sender changes *which rule runs* by changing how
it formats a date: `2026-08` and `2026-08-15` select different rules for the same day.

### REQ-0021 — an unrecognised time value is a refusal, not a default

`2026/08/15`, `2026-8-1`, `2026-W3`, a non-string, or nothing at all: `missing_facts`
with `missing_or_invalid_time_scope`, and a `422`.

The patterns are anchored, so a nearly-right date is as good as no date. This is the most
likely way for a new sender to be silently ignored — accepted, audited, delivered to
nobody.

### REQ-0022 — the inferred scope is written back into the facts

`time` is set, and one of `date` (daily), `week` (weekly) or `period` (monthly and
yearly). The facts are copied, not mutated.

Templates and evaluators read those keys directly, so a sender that sent only `time`
still renders a message that names the period.

### REQ-0023 — a scenario resolves to rules from the database, then settings, then itself

Three sources, in order:

1. enabled rules whose `scenarios` column **or** `definition.scenarios` contains it —
   both are read, because the seed loader writes both;
2. `SCENARIO_TO_RULE_IDS` from the environment;
3. the scenario read as a rule id.

The third makes the `scenario_not_mapped` branch unreachable: a non-empty scenario always
resolves to something, and an empty one has already been refused (REQ-0012). An unknown
scenario is therefore reported as `no_rule_for_inferred_frequency`, which is the less
accurate of the two messages.

### REQ-0024 — a rule runs only if its `dedup_window` equals the inferred frequency

`_filter_rule_ids_by_definition` keeps rules whose `definition.dedup_window`, lower-cased,
equals the frequency from REQ-0020. Case-insensitive; a rule with no window matches
nothing.

So `dedup_window` does two unrelated jobs — it decides how long a duplicate is suppressed
(REQ-0034) *and* whether the rule is eligible at all — and a monthly rule is invisible to
a daily event even when both name the same scenario.

**Four seeded rules can therefore never fire**, because their window is not one of the
four frequencies: `welcome` (`once`), `botanswer` (`always`), and two `two_weeks` rules.
All four values are meaningful to `_dedup_scope`. Filed as
[#34](https://github.com/celine-eu/nudging-tool/issues/34).

### REQ-0025 — a disabled rule is invisible

It claims no scenario and cannot be loaded by name. A rule disabled for a community by an
override is refused later and separately (REQ-0031).

### REQ-0026 — required facts are checked for presence, before the evaluator runs

`definition.required_facts` is a key check, not a value check: a fact that arrives as
`null` or `0` counts as present. A missing one is `missing_facts` naming the absent keys,
and the evaluator is never called.

This list is also **the only thing standing between a template and a `500`**. Thirty of
the shipped templates raise on an undefined value — they round, divide or compare it —
and nothing enforces that a rule requires every variable its templates name. It holds for
every shipped rule today, and the suite is what keeps it holding.

### REQ-0027 — the evaluator is the rule's own Python, and its failures read as "did not fire"

`evaluate(rule, facts)` returns `(triggered, facts, reason)`. The returned facts replace
the originals for everything downstream: the template context, the notification and the
audit row.

Three failures are indistinguishable from a rule that decided to stay quiet — all produce
`not_triggered`, differing only in a reason string nothing reads:

| Situation | Reason |
|---|---|
| the file is missing, does not parse, or exports no `evaluate` | `evaluator_path_not_found` |
| the module does not import or exports no `evaluate` | `evaluator_module_not_found` |
| the rule declares no evaluator at all | `evaluator_not_configured` |

A load failure is swallowed. A **call** failure is not: an exception inside `evaluate`
propagates out of the engine and the whole event is a `500`.

### REQ-0028 — where the evaluator is looked for

`definition.evaluator_path`, resolved against `SEED_DIR`:

| Written as | Resolved to |
|---|---|
| `evaluate.py` (a bare name) | `<SEED_DIR>/rules/<rule id>/evaluate.py` |
| `shared/common.py` | `<SEED_DIR>/shared/common.py` |
| an absolute path | itself |

The rule's **id** is the directory name, so renaming a rule id without moving its
directory stops it firing. `definition.evaluator_module` is the alternative form, and
`evaluator_path` wins when both are present.

Loaded modules are cached by path for the life of the process: editing an evaluator on
disk has no effect until a restart.

### REQ-0029 — the language is the event's, then the participant's, then the default

`facts.lang` wins, reduced to its primary subtag (`IT-ch` → `it`). Failing that, the
stored preference; failing that, `DEFAULT_LANG`.

**A participant who has both a generic and a community-scoped preference row makes this
raise**, losing the whole event as a `500` before any rule is evaluated — the query
matches both rows and calls `scalar_one_or_none()`. An event carrying `facts.lang` skips
the path entirely, which is why it has not been noticed. Filed as
[#33](https://github.com/celine-eu/nudging-tool/issues/33).

### REQ-0030 — a template falls back to the default language, then to English

In that order, de-duplicated. A participant who asked for Catalan and a rule translated
only into English gets the English message.

A rule with **no** template at all raises, and the raise is caught one frame up and
recorded as `unknown_scenario` — a poor name for it, but no empty notification is
written.

### REQ-0031 — a community override changes the rule for that community only

`rule_overrides` is keyed on (rule, community). `definition_override` is merged **deeply**,
so a community that changes one threshold keeps everything else the rule declares —
including `required_facts`, which a shallow merge would drop.

`enabled_override: false` refuses the rule for that community, reported as
`not_triggered` with `disabled_by_override` — the same status as a rule that simply did
not match, distinguishable only in the audit row.

An event with no community is never overridden.

### REQ-0032 — the message is rendered once, from the evaluator's facts

The context is `now` (the render time, UTC ISO-8601), `user_id`, `community_id`, and then
the facts — **merged last**, so a sender that puts a `user_id` in its facts renders that
one into the message while the notification is stored against the real recipient.

Title and body are rendered from the same context, compiled per call. Nothing is escaped:
the email body is sent as `text/plain`, so markup is text a reader sees rather than markup
a reader runs.

The rendered strings are stored on the notification. Re-seeding a template does not
rewrite what was already sent.

**An undefined variable renders as an empty string** — `You saved {{ saved_kwh }} kWh`
becomes "You saved  kWh" and is delivered to a person, with nothing raised and nothing
logged. The same missing fact is fatal as soon as the template does anything with it.
Filed as [#35](https://github.com/celine-eu/nudging-tool/issues/35); REQ-0026 is what
keeps it from happening in practice.
