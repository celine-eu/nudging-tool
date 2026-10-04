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
# is_admin: scope nudging.admin  OR  the realm role "platform-admin"
#
# The realm role is the only platform-wide grant. No group grants anything here:
# not a realm group (retired; one still present in a token is ignored) and not an
# organisation's own group, because no decision in this service is about an
# organisation. input.subject.roles carries realm_access.roles only.
# ---------------------------------------------------------------------------
default is_admin := false

is_admin if {
    "nudging.admin" in input.subject.scopes
}

is_admin if {
    "platform-admin" in input.subject.roles
}

# ---------------------------------------------------------------------------
# is_ingest: scope nudging.ingest  OR  is_admin (admins can also ingest)
# ---------------------------------------------------------------------------
default is_ingest := false

is_ingest if {
    "nudging.ingest" in input.subject.scopes
}

is_ingest if {
    is_admin
}

# ---------------------------------------------------------------------------
# is_analytics: narrow aggregate read scope OR is_admin
# ---------------------------------------------------------------------------
default is_analytics := false

is_analytics if {
    "nudging.analytics.read" in input.subject.scopes
}

is_analytics if {
    is_admin
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
