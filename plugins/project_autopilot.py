"""
SHIRAZI plugin: project_autopilot.

Scaffolds a new project workspace: creates the folder tree, starter files
(README, .gitignore, main entry), and opens it in VS Code. Kinds:
python | web | cpp | notes. Everything stays inside ~/Projects/SHIRAZI.
"""

from __future__ import annotations

import os as _os
import subprocess as _sp
import sys as _sys
from pathlib import Path as _Path

PLUGIN = {
    "name": "project_autopilot",
    "description": (
        "Scaffold a new project workspace with starter files and open it "
        "in VS Code. Trigger on 'new project', 'scaffold <name>', 'start "
        "a python project'. Kinds: python | web | cpp | notes."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING", "description": "Project folder name."},
            "kind": {
                "type": "STRING",
                "description": "python | web | cpp | notes (default python).",
            },
        },
        "required": ["name"],
    },
}

_TEMPLATES = {
    "python": {
        "main.py": '"""Entry point."""\n\n\ndef main():\n    print("Hello from {name}")\n\n\nif __name__ == "__main__":\n    main()\n',
        "requirements.txt": "# add dependencies here\n",
        "README.md": "# {name}\n\nCreated by SHIRAZI project autopilot.\n",
        ".gitignore": "__pycache__/\n*.pyc\n.venv/\n.env\n",
    },
    "web": {
        "index.html": ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                       '<title>{name}</title>\n<link rel="stylesheet" href="style.css">\n'
                       '</head>\n<body>\n<h1>{name}</h1>\n<script src="app.js"></script>\n'
                       '</body>\n</html>\n'),
        "style.css": "body { font-family: system-ui; background: #0a0f14; color: #d8f8ff; }\n",
        "app.js": 'console.log("{name} ready");\n',
        "README.md": "# {name}\n\nCreated by SHIRAZI project autopilot.\n",
    },
    "cpp": {
        "main.cpp": ('#include <iostream>\n\nint main() {\n    std::cout << "Hello from {name}\\n";\n'
                     '    return 0;\n}\n'),
        "README.md": "# {name}\n\nCreated by SHIRAZI project autopilot.\n",
        ".gitignore": "build/\n*.o\n*.exe\n",
    },
    "notes": {
        "notes.md": "# {name} — notes\n\n- \n",
        "todo.md": "# todo\n\n- [ ] \n",
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    raw = str(parameters.get("name") or "").strip()
    kind = str(parameters.get("kind") or "python").strip().lower()
    # Strict folder name: alphanumerics, dash, underscore only.
    name = "".join(c for c in raw if c.isalnum() or c in "-_")[:60]
    if not name:
        return "Sir, give the project a simple name (letters, numbers, - _)."
    if kind not in _TEMPLATES:
        kind = "python"

    root = _Path(_os.path.expanduser("~")) / "Projects" / "SHIRAZI" / name
    if root.exists():
        return f"Sir, ~/Projects/SHIRAZI/{name} already exists."
    try:
        root.mkdir(parents=True)
        for fname, tpl in _TEMPLATES[kind].items():
            (root / fname).write_text(tpl.format(name=name), encoding="utf-8")
    except Exception as e:
        return f"Sir, scaffolding failed: {e}"

    opened = False
    try:
        if _sys.platform.startswith("win"):
            _sp.Popen(["code", str(root)],
                       stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        else:
            _sp.Popen(["code", str(root)],
                       stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        opened = True
    except Exception:
        pass
    text = (f"{kind} project '{name}' scaffolded at ~/Projects/SHIRAZI/{name}, sir"
            + (" and opened in VS Code." if opened else "."))
    if player:
        try:
            player.write_log(f"SHIRAZI: {text}")
        except Exception:
            pass
    return text
