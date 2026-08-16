# ADR-0001 — the requirements are read out of the code, and say what it does today

**Date:** 2026-08-15
**Status:** accepted

## Context

This repository had no tests and no written requirements. Writing tests needs something to
assert; the only statement of intent available was the code itself.

Three questions were open when the work started, and one of them —
*are the suppression rules a product decision?* — is exactly this problem. Deduplication
windows, the daily cap and the notification catalogue are the kind of thing somebody
chose. Nobody wrote down what they chose or why, and the people who could say are not the
ones reading the code.

Waiting for that conversation would have left the service in the state the plan describes:
two symmetrical, silent failure modes — a notification that should have gone and did not,
and one that went and should not have — and nothing anywhere in the platform that would
notice either.

## Decision

Distil the requirements from the code, and state **what it does**, not what it should do.

- Every `REQ-####` in `docs/specifications/` is something the service does today.
- Where the behaviour is a defect, the requirement still describes the behaviour, names
  the issue, and says it is a defect. It does not describe the fix.
- Where the current value is arbitrary rather than chosen — the default of three a day,
  the daily window, the three-entry catalogue — the requirement says so, so that changing
  it later is a decision rather than a regression.

Requirements that would need a product answer are marked as such in prose rather than
invented.

## Consequences

**A requirement can be wrong in a way a test cannot catch.** The suite proves the service
does what the document says; nothing here proves the document says what anyone wanted. A
reader must not take `REQ-0039` as evidence that three a day was chosen.

**Five requirements describe defects.** They are listed at the top of
`docs/specifications/index.md`. Each is a place where the test and the requirement have to
change together with the fix, and where a passing suite is *not* an argument that the
behaviour is right.

**The temptation is to quietly "fix" the requirement** when the behaviour is embarrassing
— to write what the code was clearly meant to do. That produces a document that reads
well and a matrix that reports coverage for something nobody verified, which is worse than
having neither.

When the product conversation happens, the requirements it settles supersede these, and
the change is visible as a diff against a statement of what the code used to do.
