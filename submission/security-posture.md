# Security Posture & Known Limitations

For the "Fortified Enterprise Fleet" track. This is written to be folded into the Devpost write-up.
We ran an adversarial red-team on our own submission (three independent auditors, cross-vendor) after the design-loop review. Full report: `design/autonomous-agent/reviews/security-synthesis.md`.

## What is enforced in code (the security story we stand behind)

- **The human approval boundary is structural, not cosmetic.** The autonomous agent may observe, detect, explore, evaluate, and prepare — but there is *no code path* that contacts a person without the owner clicking "Ask for 15 min." `contact_mode` is fixed to `always_ask`; server-side enforcement, not a UI toggle.
- **Autonomy is a graduated, server-enforced policy.** Monitor ⊇ Search ⊇ Ask ⊇ Prepare. A disabled upper permission structurally prevents every lower autonomous action; enforced in the sweep pipeline, normalized on save, not trusted from the client.
- **Private profile items are masked fail-closed by the type system, never by LLM self-report.** Masking keys off the real `visibility` field and unknown-key-fail-closed logic, so a model that lies about what it cited cannot unmask anything.
- **Per-candidate isolated inference.** Each candidate is evaluated in its own call; data boundary = process boundary. Cross-user leakage is structurally impossible, not policed after the fact.
- **The scheduled endpoint is OIDC-authenticated.** `/internal/autonomous-sweep` verifies signature, audience, Google issuer, `email_verified`, and an exact invoker-service-account match. A Google-signed token from an unrelated project fails the email match. Unset config fails closed (404). No API key is accepted on this route.
- **Tenant isolation is caller-independent.** The Firestore database is selected solely from the resolved principal's tenant; there is no request-supplied tenant override anywhere.
- **The audit trail of an autonomous run is counts-only before approval.** `sweep_run` / `policy_limited` carry only numbers and enums, validated by exact-key match plus projection — no names, no task titles, no free text. Names appear only after a human approves. Private recommendation → human approval → auditable named interaction.
- **Delivery is not authorized by an LLM score alone.** A missing, non-numeric, non-finite, or out-of-range connection score is treated fail-closed as "no connection" (hardened after the red-team; regression-tested).

## Deliberate demo simplifications (what we did NOT harden, and why)

These are conscious scoping decisions for a hackathon demo, documented here rather than hidden:

- **Shared demo API key = tenant-scoped god-mode.** The public demo runs in `AUTH_MODE=demo_key`: one shared key grants full access *within its tenant* (it cannot cross tenants). This lets a judge drive the whole flow — request, approve, respond — from a single URL without setting up SSO. The production design path is `AUTH_MODE=iap`, which binds every request to an individual employee identity and activates the per-user `_require_self_*` guards that are dormant in demo mode. The auth layer, the guard table, and the IAP resolver are all implemented and tested; only the deployed *mode* is relaxed for the demo.
- **The audit dashboard is tenant-wide readable.** Bridge Trace deliberately shows the whole tenant's connection activity, because the demo's point is to make agent behavior transparent and inspectable. In production this belongs behind an audit-admin role with per-user scoping for ordinary employees.
- **No rate limiting / input-size caps on the demo.** Acceptable for a fixed-seed single-tenant demo; a multi-tenant production deployment needs per-principal rate limits, request-size caps, and per-sweep work budgets.

## If this went to production (the one-line answer)

Switch the deployment from the shared `demo_key` to IAP with per-employee principals. That single change activates the horizontal-authorization guards already in the code and closes both the god-mode and the tenant-wide-audit exposure. Everything else above is already enforced.
