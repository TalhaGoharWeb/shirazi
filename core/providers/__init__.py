"""core/providers/ — AI provider abstraction (Phase 4).

One `AIProvider` interface, many implementations. The app talks to
`core.providers.registry` ("generate this text, use the configured chain"),
never to a vendor SDK directly. See docs/PHASE4.md for the wiring map.

Cost honesty (FREE-FIRST, hard line):
    "Local (Free)"       — runs on this machine, no key, no quota (Ollama).
    "Free API Tier"      — vendor free tier: keyed, QUOTA-LIMITED and
                           rate-limited. Never advertised as unlimited.
    "Optional Paid Provider" — would need the user's own paid key.
"""
