package celine.nudging.authz

import rego.v1

# ---------------------------------------------------------------------------
# allow: any request carrying a valid, non-anonymous token
# ---------------------------------------------------------------------------
default allow := false

allow if {
    input.subject != null
    input.subject.type != "anonymous"
    input.subject.id != ""
}

# ---------------------------------------------------------------------------
# is_admin: scope nudging.admin  OR  group membership "admin"
# ---------------------------------------------------------------------------
default is_admin := false

is_admin if {
    "nudging.admin" in input.subject.scopes
}

is_admin if {
    "admin" in input.subject.groups
}

# ---------------------------------------------------------------------------
# filters: row-level predicate injected for user tokens
# Service accounts (type == "service") get no filter → see everything.
# ---------------------------------------------------------------------------
filters := [] if {
    input.subject.type == "service"
}

filters := [{"field": "user_id", "operator": "eq", "value": input.subject.id}] if {
    input.subject.type == "user"
}

# ---------------------------------------------------------------------------
# reason strings (useful for debug / audit logs)
# ---------------------------------------------------------------------------
reason := "allowed" if { allow }

reason := "unauthenticated" if { not allow }
