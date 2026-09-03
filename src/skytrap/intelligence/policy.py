"""Item 13 — EngineeringPolicy: a fixed set of engineering principles handed
to the model as policy, not derived from it. These are meant to shape how a
change is made, never to justify rewriting working code that wasn't asked
about.
"""

from __future__ import annotations

ENGINEERING_POLICY: tuple[str, ...] = (
    "Prefer simplicity over cleverness.",
    "Keep related logic cohesive; avoid scattering one concern across many files.",
    "Favor low coupling — depend on interfaces/behavior, not internal details of other modules.",
    "Don't repeat yourself, but don't introduce an abstraction for a single use — DRY without premature generalization.",
    "Keep functions short when that's reasonable for the language and problem.",
    "Validate inputs at boundaries (user input, external APIs); trust internal call sites.",
    "Handle errors explicitly — no silent failure, no bare except that swallows real problems.",
    "Default to secure behavior: no hardcoded secrets, no unsafe deserialization, no obvious injection surface.",
    "Stay compatible with the existing architecture unless there is concrete evidence it should change.",
    "Add or update tests for critical/changed behavior — don't leave a fix unverified.",
    "Don't add a dependency the project doesn't already use unless it's clearly justified.",
    "Don't reimplement something the project already provides — reuse the existing helper/service.",
)


def policy_prompt(languages: list[str] | None = None) -> str:
    """Renders the policy as a prompt fragment, lightly adapted to the
    detected languages/frameworks so the wording stays concrete rather than
    generic boilerplate."""
    lines = ["Engineering policy (apply throughout, do not use it to justify unrelated rewrites):"]
    lines.extend(f"- {item}" for item in ENGINEERING_POLICY)
    if languages:
        if "Python" in languages:
            lines.append("- Python: follow PEP 8 naming, use type hints on new/changed signatures.")
        if any(lang in languages for lang in ("JavaScript", "TypeScript")):
            lines.append("- JS/TS: prefer the project's existing module style (ESM vs CJS) — don't mix them.")
    return "\n".join(lines)
