"""core/avatar_styles.py — avatar styles and render modes (Phase 5).

The *description* of each style lives here (headless, testable); the Qt
painter in ui.py / core/avatar.py turns the description into pixels. This
split keeps cultural choices reviewable as data and keeps the painting code
free of hard-coded style logic.

Config contract (see mission spec):
    {"avatar_style": "default", "avatar_ears": true, "avatar_beard": true,
     "render_mode": "realistic"}

Render modes:
    REALISTIC_3D — shaded mesh head (core/avatar_mesh.py geometry).
    HOLOGRAM     — wireframe hologram (core/avatar.py holographic head).
    REACTOR      — the reactor core visualisation (core/reactor.py model).

Styles (headwear is respectful, never caricature):
    default — modern futuristic humanoid, no headwear.
    kofia   — Omani-inspired embroidered kofia cap + dishdasha-inspired
              collar band. Geometric embroidery motif, muted gold on deep
              teal — pattern, not a costume.
    turban  — Islamic scholar-style turban: layered wrapped bands in soft
              white/sand over a dark cap.
    kufi    — taqiyah skull cap: close-fitting cap with a subtle geometric
              band.
"""

from __future__ import annotations

from enum import Enum

from core.reactor import RenderMode  # noqa: F401  (single source of truth)

AVATAR_STYLES = ("default", "kofia", "turban", "kufi")
DEFAULT_STYLE = "default"
DEFAULT_RENDER_MODE = RenderMode.REALISTIC_3D.value


class HeadwearKind(str, Enum):
    NONE = "none"
    KOFIA = "kofia"      # embroidered cap + collar band
    TURBAN = "turban"    # wrapped bands
    KUFI = "kufi"        # skull cap


# Per-style paint parameters consumed by the avatar painter. Colours are
# (r, g, b) hints; the painter maps them through the active theme accent.
STYLE_DESCRIPTIONS: dict[str, dict] = {
    "default": {
        "headwear": HeadwearKind.NONE.value,
        "headwear_color": (0, 212, 255),
        "trim_color": (212, 175, 55),     # restrained gold
        "collar": False,
        "notes": "Modern futuristic humanoid; no headwear.",
    },
    "kofia": {
        "headwear": HeadwearKind.KOFIA.value,
        "headwear_color": (8, 60, 72),    # deep teal cap
        "trim_color": (198, 160, 60),     # muted gold embroidery
        "pattern": "diamond",             # geometric embroidery motif
        "collar": True,                   # dishdasha-inspired collar band
        "collar_color": (240, 238, 230),
        "notes": "Omani-inspired embroidered kofia; geometric motif.",
    },
    "turban": {
        "headwear": HeadwearKind.TURBAN.value,
        "headwear_color": (232, 228, 216),  # soft white/sand wraps
        "trim_color": (120, 110, 95),
        "wraps": 5,                        # visible wrapped bands
        "cap_color": (20, 30, 36),         # dark under-cap
        "collar": False,
        "notes": "Scholar-style wrapped turban over a dark cap.",
    },
    "kufi": {
        "headwear": HeadwearKind.KUFI.value,
        "headwear_color": (16, 44, 56),
        "trim_color": (198, 160, 60),
        "pattern": "band",                 # single geometric band
        "collar": False,
        "notes": "Taqiyah skull cap with a subtle geometric band.",
    },
}


def normalize_style(name: str | None) -> str:
    """Map any input to a known style; unknown → 'default' (never raises)."""
    n = (name or "").strip().lower()
    return n if n in AVATAR_STYLES else DEFAULT_STYLE


def normalize_render_mode(mode: str | None) -> str:
    """Map any input to a known render mode value; unknown → 'realistic'."""
    m = (mode or "").strip().lower()
    try:
        return RenderMode(m).value
    except ValueError:
        return DEFAULT_RENDER_MODE


def describe_style(name: str | None) -> dict:
    """The paint-parameter dict for a style (a copy; mutate freely)."""
    return dict(STYLE_DESCRIPTIONS[normalize_style(name)])


def style_label(name: str | None, lang: str = "en") -> str:
    """Display name for the settings UI. i18n keys live in the catalogs;
    this is the English fallback the picker uses when a key is missing."""
    return {
        "default": "Modern",
        "kofia": "Kofia",
        "turban": "Turban",
        "kufi": "Kufi",
    }.get(normalize_style(name), "Modern")


def render_mode_label(mode: str | None) -> str:
    m = normalize_render_mode(mode)
    return {
        "realistic": "Realistic 3D",
        "hologram": "Hologram",
        "reactor": "Reactor Core",
    }[m]
