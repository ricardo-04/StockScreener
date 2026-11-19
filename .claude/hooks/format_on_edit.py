#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Carlos André Ferreira. Part of the AI Quality Pack (https://github.com/CarlosAndreFerreira/ai-quality-pack), MIT License: keep this notice in copies.
"""Claude Code PostToolUse hook: format the edited file with the project's formatter, if installed.

Never blocks the agent: always exits 0. Stdlib only, cross-platform.
"""
import sys

if sys.version_info < (3, 9):  # this block must parse and run on Python 3.6: rerun with a newer Python, or say why not
    import os
    import shutil
    import subprocess

    def _rerun_with_newer_python():
        if os.environ.get("AGENT_PACK_RERUN"):
            return
        names = [os.environ.get("AGENT_PACK_PYTHON")] + ["python3.%d" % n for n in range(15, 8, -1)] + ["python3", "python"]
        for name in names:
            exe = shutil.which(name) if name else None
            if not exe or os.path.realpath(exe) == os.path.realpath(sys.executable):
                continue
            try:
                newer = subprocess.call([exe, "-c", "import sys; sys.exit(sys.version_info < (3, 9))"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30) == 0
            except Exception:
                newer = False
            if newer:
                sys.exit(subprocess.call([exe] + sys.argv, env=dict(os.environ, AGENT_PACK_RERUN="1")))

    _rerun_with_newer_python()
    sys.exit("%s needs Python 3.9 or newer. This is Python %d.%d, and no newer Python is on PATH. "
             "Install one, or set AGENT_PACK_PYTHON to the path of one."
             % (os.path.basename(sys.argv[0]), sys.version_info[0], sys.version_info[1]))

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=60).returncode in (0, 1)
    except Exception:
        return False


def project_root(data: dict) -> str:
    """The project folder. Claude Code gives CLAUDE_PROJECT_DIR. Other agents (Codex, OpenCode) give the session
    folder in `cwd`: the project is that folder, or the git top level above it, where the pack's files are."""
    for name in ("CLAUDE_PROJECT_DIR", "AGENT_PROJECT_DIR"):
        if os.environ.get(name) and Path(os.environ[name]).is_dir():
            return os.environ[name]
    cwd = str(data.get("cwd") or "") or os.getcwd()
    try:
        top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
    except Exception:
        top = ""
    for folder in (cwd, top):
        if folder and (Path(folder, ".agent-memory").is_dir() or Path(folder, ".agents").is_dir()):
            return folder
    return top or cwd


# Codex sends one patch for an edit. The files are in its header lines.
PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update) File: (.+?)\s*$|^\*\*\* Move to: (.+?)\s*$", re.M)


def edited_files(args: dict, base: str) -> list:
    """The files that an edit tool changed: `file_path` (Claude Code), `filePath` (OpenCode), or a patch (Codex)."""
    paths = [args[k] for k in ("file_path", "filePath", "path") if isinstance(args.get(k), str)]
    for k in ("command", "patchText", "patch", "input"):
        if isinstance(args.get(k), str) and "*** " in args[k]:
            paths += [a or b for a, b in PATCH_FILE.findall(args[k])]
    out = []
    for p in paths:
        full = p if os.path.isabs(p) else os.path.join(base, p)
        if os.path.isfile(full) and full not in out:
            out.append(full)
    return out


def ruff(path: str) -> None:
    # Prefer the project's pinned ruff without syncing the environment on every edit
    # (`uv run` would otherwise create .venv / rewrite uv.lock); fall back to ruff on PATH.
    if shutil.which("uv") and run(["uv", "run", "--no-sync", "--quiet", "ruff", "format", path]):
        run(["uv", "run", "--no-sync", "--quiet", "ruff", "check", "--fix", "--quiet", path])
    elif shutil.which("ruff"):
        run(["ruff", "format", path])
        run(["ruff", "check", "--fix", "--quiet", path])


def main():
    try:
        data = json.load(sys.stdin)
        args = data.get("tool_input") or {}
        assert isinstance(args, dict)
    except Exception:
        return
    base = str(data.get("cwd") or "") or os.getcwd()  # a relative path is relative to the folder of the session
    project = project_root(data)
    if project and Path(project).is_dir():
        os.chdir(project)
    for path in edited_files(args, base):
        format_file(path)


def format_file(path: str) -> None:
    ext = Path(path).suffix.lower()
    if ext in (".py", ".pyi"):
        ruff(path)
    elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".css", ".md"):
        if Path("node_modules/.bin/prettier").exists() or Path("node_modules/.bin/prettier.cmd").exists():
            run(["npx", "--no-install", "prettier", "--write", "--log-level", "warn", path])
    elif ext == ".go" and shutil.which("gofmt"):
        run(["gofmt", "-w", path])
    elif ext == ".rs" and shutil.which("rustfmt"):
        run(["rustfmt", path])


if __name__ == "__main__":
    main()
    sys.exit(0)
