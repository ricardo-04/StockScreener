#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Carlos André Ferreira. Part of the AI Quality Pack (https://github.com/CarlosAndreFerreira/ai-quality-pack), MIT License: keep this notice in copies.
"""Claude Code Stop hook (opt-in): the completion gate must pass before the agent may finish.

- Runs `memory.py gate` (commands from .agent-memory/gate.json).
- Skipped when the workspace is unchanged since the last pass (cheap for Q&A turns).
- On failure: exit 2 with bounded output on stderr, so the agent keeps working on it.
- The message of a block tells the agent to give the answer to the task again in its last message. Without that,
  the last message is about the gate only and the answer to the task is lost (seen in a run on 2026-11-17).
- Never loops: if this stop was already triggered by the hook (stop_hook_active), it lets the agent stop.
  That release is written to the run log (`released-red` unless the gate passed for exactly the code
  that the agent leaves), and the next
  session-start digest reports it.
- `"require_evaluator": true` in the committed gate.json: after a gate pass, the stop also needs a PASS of the
  evaluator for exactly this code (`memory.py evaluated`). The agent then has work to do after a block (start the
  evaluator, correct the code, evaluate again), so the hook can block up to MAX_CHAIN times in one chain of stops,
  and then lets the agent stop (`released-red`). Without the option, nothing changes: one block.
- A gate that times out counts as failed (fail closed); the hook's own timeout is sized from gate.json.
Stdlib only; never blocks on its own errors.
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
import subprocess
import sys
from pathlib import Path

KEEP_ANSWER = ("This stop check does not change your task. After you act on it, give the complete answer to the "
               "task again in your last message: the user reads only that message.\n")
NOTHING_CHECKED = ("The completion gate has no commands, so it checked nothing. Do one of these:\n"
                   "- You changed code or tests: put the test and lint commands of this project in the gate file "
                   "(see below) and run the gate.\n"
                   "- The task changed nothing that a command can check (a question, a plan, a review): do not "
                   "write a gate file. Say in one line that no completion gate ran.\n")
CHAIN = Path(".agent-memory/.stop-chain")  # how many times this chain of stops was blocked (require_evaluator only)
MAX_CHAIN = 3
MEM = Path(".agents/skills/long-horizon/scripts/memory.py")
# a plugin install has no copy in the project: the script is in the plugin, three folders above this file
PLUGIN_MEM = Path(__file__).resolve().parents[3] / "skills" / "long-horizon" / "scripts" / "memory.py"


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


def record(outcome: str, env: dict) -> None:
    """One `stop` line in .agent-memory/events.jsonl. A failure here must not change the hook's result."""
    try:
        subprocess.run([sys.executable, str(MEM), "event", "--ev", "stop", "--field", f"outcome={outcome}"],
                       capture_output=True, timeout=20, env=env)
    except Exception:
        pass


def mem(args: list, env: dict, timeout: int = 120):
    """Run one memory.py command. None when it did not run: the hook never blocks on its own errors."""
    try:
        return subprocess.run([sys.executable, str(MEM)] + args, capture_output=True, text=True, timeout=timeout, env=env)
    except Exception:
        return None


def chain_count() -> int:
    try:
        return int(CHAIN.read_text(encoding="utf-8").strip() or 0)
    except Exception:
        return 0


def chain_set(n: int) -> None:
    try:
        if n:
            CHAIN.write_text(f"{n}\n", encoding="utf-8")
        elif CHAIN.exists():
            CHAIN.unlink()
    except Exception:
        pass


def block(outcome: str, text: str, env: dict, required: bool) -> int:
    """Stop the agent from finishing, with `text` for it. With require_evaluator, count the block of this chain."""
    record(outcome, env)
    if required:
        chain_set(chain_count() + 1)
    sys.stderr.write(text + KEEP_ANSWER)
    return 2


def evaluator_missing(env: dict, required: bool) -> str:
    """"" when the stop can go on. With require_evaluator and no PASS of the evaluator for this code: what to do."""
    if not required:
        return ""
    r = mem(["evaluated"], env)
    return (r.stdout + r.stderr)[-2000:].rstrip("\n") + "\n" if r is not None and r.returncode == 4 else ""


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    project = project_root(data if isinstance(data, dict) else {})
    if project and Path(project).is_dir():
        os.chdir(project)  # hooks may start in a subfolder; the pack paths are relative to the project root
    global MEM
    if not MEM.exists() and PLUGIN_MEM.is_file():
        MEM = PLUGIN_MEM
    gate_file = Path(".agent-memory/gate.json")
    if not MEM.exists():
        return 0
    if not gate_file.exists() and not Path(".agent-memory").is_dir():
        return 0  # the pack's memory was never started in this project: nothing to enforce
    # a project with the pack's memory and no gate file: the gate fails, because nothing would be checked
    env = dict(os.environ)
    if data.get("session_id"):
        env["AGENT_SESSION_ID"] = str(data["session_id"])  # the gate and stop events carry the session id
    asked = mem(["evaluated", "--is-required"], env)
    required = asked is not None and asked.returncode == 0  # the committed gate file asks for the evaluator
    if not data.get("stop_hook_active"):
        chain_set(0)  # a new chain of stops
    elif not (required and chain_count() < MAX_CHAIN):
        check = mem(["gate", "--check"], env)
        # green only for a pass on exactly this code: a pass from before the last edit does not count
        green = check is not None and check.returncode == 0 and not evaluator_missing(env, required)
        record("released-green" if green else "released-red", env)
        chain_set(0)
        return 0
    try:
        cfg = json.loads(gate_file.read_text(encoding="utf-8"))
        budget = int(cfg.get("timeout", 900)) * max(1, len(cfg.get("commands", []))) + 60
    except Exception:
        budget = 1800
    try:
        r = subprocess.run([sys.executable, str(MEM), "gate"], capture_output=True, text=True, timeout=budget, env=env)
    except subprocess.TimeoutExpired:
        return block("blocked-timeout", f"Completion gate did not finish within {budget}s. Treat this as a failure: "
                     "fix the hanging command, or explain to the user why the gate cannot pass.\n", env, required)
    except Exception:
        return 0
    if r.returncode == 1:
        out = (r.stdout + r.stderr)[-3000:].rstrip("\n") + "\n"
        if "Nothing was checked" in out:  # no gate file, or a gate file without commands: not a failed check
            return block("blocked", NOTHING_CHECKED + out, env, required)
        return block("blocked", "Completion gate failed. Fix this before finishing, or explain to the user why it "
                     "cannot pass:\n" + out, env, required)
    missing = evaluator_missing(env, required) if r.returncode == 0 else ""
    if missing:
        return block("blocked-evaluator", "The completion gate passed. The stop also needs the evaluator:\n" + missing,
                     env, required)
    if data.get("stop_hook_active"):  # a stop after a block, and now each check passes for this code
        record("released-green", env)
    chain_set(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
