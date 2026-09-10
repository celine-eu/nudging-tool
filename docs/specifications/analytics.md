# Manager analytics

| | |
|---|---|
| REQ-0076 | Manager analytics require `nudging.analytics.read`; ordinary participants and ingest-only services are denied, while administrators retain access. |
| REQ-0077 | Analytics are scoped to exactly one community and an inclusive UTC date range, and return aggregate data without participant IDs, destinations, addresses, rendered titles or message bodies. |
| REQ-0078 | Analytics report the nudging funnel, effective rule state and activity, classified delivery failures, and web-push/email reachability from operational nudging records. Funnel and health aggregates cover all observed nudging activity, while the returned rule catalogue contains only rule IDs declared by `active_kinds.yaml`. |
