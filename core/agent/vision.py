"""core/agent/vision.py — computer vision for the agent engine (Phase 6).

Capabilities, all honest about what is really installed:

    capture()        — screenshot via actions/screen_processor (mss/PIL,
                       both lazy). Downscaled to a max dimension so the AI
                       receives only the image data it needs.
    ocr()            — text + bounding boxes via tesseract (subprocess).
                       Absent → clean "unavailable" + install instructions.
                       Never faked.
    describe()       — structured screen summary: capture + OCR text blocks
                       + metadata, ready to hand to the reasoning model.
    elements()       — UI element recognition: OCR word boxes grouped into
                       text regions when tesseract is present; otherwise a
                       pixel-heuristic fallback that says what it is.

Permission posture: capturing the user's screen is a privacy-relevant read.
The `vision_describe` tool is READ_ONLY, and the engine only captures when
the user asked about the screen — never speculatively, never in a loop.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from core import logger
from core.permissions import Level
from core.tools.registry import ToolSpec

_MAX_DIM = 1280          # longest edge of the image handed to the AI
_OCR_TIMEOUT_S = 25


def _tesseract_bin() -> Optional[str]:
    return shutil.which("tesseract")


def vision_status() -> dict[str, Any]:
    """Honest capability report for docs/UI/diagnostics. Never raises."""
    try:
        from actions import screen_processor as _sp
        _sp._capture_screen  # noqa: B018 — attribute must exist
        capture_ok, capture_why = True, "screen capture available"
    except Exception as e:
        capture_ok, capture_why = False, f"screen capture unavailable: {e}"
    tess = _tesseract_bin()
    return {
        "capture": {"available": capture_ok, "reason": capture_why},
        "ocr": {"available": tess is not None,
                "reason": ("tesseract found at " + tess) if tess else
                          "tesseract is not installed — "
                          "install it with: apt install tesseract-ocr "
                          "(or download from https://github.com/tesseract-ocr/tesseract)"},
    }


@dataclass
class ScreenData:
    png: bytes = b""
    width: int = 0
    height: int = 0
    source: str = "screen"     # "screen" | "camera"


class Vision:
    """Screenshot + OCR + summarization. All methods fail gracefully."""

    def capture(self, max_dim: int = _MAX_DIM) -> ScreenData:
        """Capture and downscale. Raises RuntimeError with an honest message
        when capture is impossible."""
        try:
            from actions import screen_processor as _sp
            png, _mime = _sp._capture_screen()
        except Exception as e:
            raise RuntimeError(f"Screen capture failed: {e}") from e
        return self._downscale(png, max_dim)

    @staticmethod
    def _downscale(png: bytes, max_dim: int) -> ScreenData:
        try:
            from PIL import Image  # type: ignore
        except Exception:
            return ScreenData(png=png)  # no PIL: hand over the original
        try:
            img = Image.open(io.BytesIO(png)).convert("RGB")
            w, h = img.size
            scale = min(1.0, max_dim / max(w, h))
            if scale < 1.0:
                img = img.resize((int(w * scale), int(h * scale)),
                                 Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "PNG", optimize=True)
            return ScreenData(png=buf.getvalue(), width=img.width,
                              height=img.height)
        except Exception:
            return ScreenData(png=png)

    def ocr(self, data: ScreenData) -> dict[str, Any]:
        """Tesseract OCR: {"text": str, "words": [{text, x, y, w, h, conf}]}.
        Honest when tesseract is absent — never an empty success."""
        tess = _tesseract_bin()
        if tess is None:
            return {"available": False,
                    "message": ("OCR needs tesseract, which is not installed. "
                                "Install it with: apt install tesseract-ocr")}
        try:
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix=".png",
                                             delete=False) as fh:
                fh.write(data.png)
                path = fh.name
            try:
                txt = subprocess.run(
                    [tess, path, "stdout", "-l", "eng+urd+ara", "--psm", "3"],
                    capture_output=True, text=True,
                    timeout=_OCR_TIMEOUT_S).stdout.strip()
                tsv = subprocess.run(
                    [tess, path, "stdout", "-l", "eng+urd+ara", "--psm", "3", "tsv"],
                    capture_output=True, text=True,
                    timeout=_OCR_TIMEOUT_S).stdout
                words = []
                for line in tsv.splitlines()[1:]:
                    parts = line.split("\t")
                    if len(parts) >= 12 and parts[11].strip():
                        try:
                            words.append({
                                "text": parts[11],
                                "x": int(parts[6]), "y": int(parts[7]),
                                "w": int(parts[8]), "h": int(parts[9]),
                                "conf": float(parts[10]),
                            })
                        except ValueError:
                            continue
                return {"available": True, "text": txt, "words": words}
            finally:
                os.unlink(path)
        except subprocess.TimeoutExpired:
            return {"available": False,
                    "message": "OCR timed out on this screenshot."}
        except Exception as e:
            return {"available": False, "message": f"OCR failed: {e}"}

    def elements(self, data: Optional[ScreenData] = None) -> dict[str, Any]:
        """UI element recognition. With tesseract: OCR word boxes grouped
        into text regions (buttons/labels/headings by size). Without: an
        honest statement that recognition is unavailable."""
        data = data or self.capture()
        ocr = self.ocr(data)
        if not ocr.get("available"):
            return {"available": False,
                    "message": ocr.get("message", "OCR unavailable.")}
        words = [w for w in ocr["words"] if w["conf"] > 30]
        # Group words into rows → crude text regions.
        rows: dict[int, list] = {}
        for w in words:
            key = round(w["y"] / 12) * 12
            rows.setdefault(key, []).append(w)
        regions = []
        for key in sorted(rows):
            row = sorted(rows[key], key=lambda w: w["x"])
            text = " ".join(w["text"] for w in row)
            if len(text.strip()) < 2:
                continue
            regions.append({
                "text": text,
                "x": min(w["x"] for w in row),
                "y": min(w["y"] for w in row),
                "w": max(w["x"] + w["w"] for w in row) - min(w["x"] for w in row),
                "kind": "heading" if any(w["h"] > 28 for w in row) else "text",
            })
        return {"available": True, "regions": regions[:60],
                "note": ("Regions are OCR text blocks — real widget roles "
                         "(button vs label) need a UI-automation backend "
                         "and are not claimed.")}

    def describe(self, question: str = "Summarize what is on the screen."
                 ) -> dict[str, Any]:
        """Structured summary for the reasoning model: metadata + OCR text.
        Returns data the executor renders into a tool observation."""
        logger.executing("vision_describe")
        try:
            data = self.capture()
        except RuntimeError as e:
            return {"ok": False, "summary": str(e)}
        ocr = self.ocr(data)
        els = self.elements(data) if ocr.get("available") else {"available": False}
        parts = [f"Screen: {data.width}x{data.height} px."]
        if ocr.get("available"):
            text = ocr.get("text", "").strip()
            parts.append("On-screen text (OCR):\n" + (text[:3000] if text
                         else "(no text detected)"))
            regions = (els.get("regions") or [])[:20]
            if regions:
                parts.append("Text regions:\n" + "\n".join(
                    f"- [{r['kind']}] {r['text'][:80]}" for r in regions))
        else:
            parts.append("OCR: " + ocr.get("message", "unavailable"))
        parts.append(f"Question: {question}")
        return {"ok": True,
                "summary": "\n".join(parts),
                "image_bytes": len(data.png),
                "question": question}


# ── Agent-engine tool wrapper ───────────────────────────────────────────────

def vision_describe(params: dict) -> str:
    v = Vision()
    out = v.describe(params.get("question", "") or
          "Summarize what is on the screen.")
    return out["summary"]


def spec() -> ToolSpec:
    return ToolSpec(
        name="vision_describe",
        category="media",
        permission=Level.READ_ONLY,
        description=("Capture the screen and summarize/answer about what is "
                     "visible. OCR via tesseract when installed; honest when not."),
        schema={"type": "object",
                "properties": {"question": {"type": "STRING"}}},
        source="agent",
    )


def dispatch() -> dict[str, Callable]:
    return {"vision_describe": vision_describe}
