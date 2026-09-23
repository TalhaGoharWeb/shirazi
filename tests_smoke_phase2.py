"""Phase-2 headless smoke test: import main.py with sandbox stubs.

Stubs cover libs that cannot load in this Linux sandbox:
  - sounddevice (needs system libportaudio2)
  - PyQt6.* (needs libEGL; no display)
  - cv2, pynvml (heavy/optional)
Everything else (PyQt6 API surface is mocked) imports for real.
"""
import sys
import types
from unittest.mock import MagicMock


def install_stubs() -> None:
    sd = types.ModuleType("sounddevice")

    class _Stream:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    sd.InputStream = _Stream
    sd.OutputStream = _Stream
    sd.Stream = _Stream
    sd.query_devices = lambda *a, **k: []
    sd.sleep = lambda ms: None
    sys.modules["sounddevice"] = sd

    for name in [
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.QtMultimedia",
        "cv2",
        "pynvml",
    ]:
        sys.modules[name] = MagicMock(name=name)


if __name__ == "__main__":
    install_stubs()
    import main

    print("main.py import OK; LIVE_MODEL =", main.LIVE_MODEL)
    print("ShiraziLive class:", main.ShiraziLive)
    print("main() callable:", callable(main.main))
    print("TOOL_DECLARATIONS:", len(main.TOOL_DECLARATIONS))
