"""Report presets -- new in Vault; Nexus has no preset concept.

`PRESETS` maps a preset key to `(label, section_keys)`, where `section_keys`
is always canonically ordered (the order sections print in, `SECTION_KEYS`),
regardless of the order the requirement table lists them in.
"""

from .sections import SECTION_KEYS

_RAW: dict[str, tuple[str, set[str]]] = {
    "full": ("Full report", set(SECTION_KEYS)),
    "executive": ("Executive brief", {"summary", "availability", "compliance", "recommendations"}),
    "security": ("Security review",
                 {"summary", "servers", "updates", "home", "alerts", "fixes", "email", "compliance", "recommendations"}),
    "network": ("Network & devices", {"summary", "home", "devices", "presence", "internet", "dev_net"}),
    "ops": ("Operations", {"summary", "availability", "servers", "updates", "fixes", "internet", "alerts"}),
    "compliance": ("Compliance pack", {"summary", "compliance", "servers", "updates", "email", "alerts", "recommendations"}),
    "cameras": ("Cameras only", {"summary", "dev_cameras", "home", "alerts", "recommendations"}),
    "people": ("Phones, tablets & computers", {"summary", "dev_people", "presence", "home", "recommendations"}),
    "tvs": ("TVs & media only", {"summary", "dev_media", "presence", "home"}),
    "smart_home": ("Smart home", {"summary", "dev_smart", "home", "devices", "recommendations"}),
    "network_gear": ("Network gear", {"summary", "dev_net", "home", "internet", "devices"}),
}

_ORDER = {k: i for i, k in enumerate(SECTION_KEYS)}

PRESETS: dict[str, tuple[str, list[str]]] = {
    key: (label, sorted(keys, key=lambda k: _ORDER[k]))
    for key, (label, keys) in _RAW.items()
}

# `exec_report` is not a subset of `SECTION_KEYS` -- it is a wholly different
# document (`builder.build_executive_report`, the mockup at
# `design/4-executive-report.html`: one page, a verdict sentence, a score
# card, "handled without asking" / "needs a decision" panels, not the 19
# generic dashboard sections). It is deliberately kept out of `PRESETS`
# above, whose contract (see the module docstring, and
# `test_presets_are_subsets_in_canonical_order` /
# `test_presets_select_the_right_sections`) is "a subset of SECTION_KEYS
# that prints as those sections' `<h2>` headings" -- true of every other
# preset, never true of this one. `SPECIAL_PRESETS` is the parallel, tiny
# registry for report types outside that contract; `label_and_sections`
# below is the one lookup both `routers/reports.py` (the picker) and
# `builder.render_to_file` (the stored `Report.sections` metadata) call, so
# neither has to know which of the two registries a given key lives in.
SPECIAL_PRESETS: dict[str, tuple[str, list[str]]] = {
    "exec_report": ("Executive decision report", []),
    "vuln_report": ("Vulnerability scan report", []),
    "threat_report": ("Threats and protection", []),
}


def label_and_sections(key: str) -> tuple[str, list[str]]:
    """`(label, sections)` for any preset key, ordinary or special. Empty
    strings/list for an unknown key rather than a `KeyError`, since a
    caller here is always reporting metadata, not required to fail the
    request over it."""
    if key in PRESETS:
        return PRESETS[key]
    if key in SPECIAL_PRESETS:
        return SPECIAL_PRESETS[key]
    return "", []
