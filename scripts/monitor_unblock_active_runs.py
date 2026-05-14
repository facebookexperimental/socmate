#!/usr/bin/env python3
"""Poll active SocMate runs and unblock headless escalation files.

This is intentionally conservative: it does not blindly accept design
interrupts. It supplies missing answer/feedback fields, and asks the triage
agent to make explicit decisions when a decision packet appears.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


RUNS = [
    {
        "name": "codec-cavlc",
        "root": Path("/home/ubuntu/socmate"),
        "session": "socmate-cavlc",
        "log": Path("/home/ubuntu/socmate/.socmate/run-20260514-035643-cavlc.log"),
    },
    {
        "name": "transformer-flash",
        "root": Path("/home/ubuntu/socmate-runs/transformer-flash-20260514-0209"),
        "session": "socmate-transformer-flash",
        "log": Path(
            "/home/ubuntu/socmate-runs/transformer-flash-20260514-0209/.socmate/"
            "run-20260514-035643-transformer.log"
        ),
    },
    {
        "name": "adder32-fullflow",
        "root": Path("/home/ubuntu/socmate-runs/adder32-fullflow-20260514-0313"),
        "session": "socmate-adder32-fullflow",
        "log": Path(
            "/home/ubuntu/socmate-runs/adder32-fullflow-20260514-0313/.socmate/"
            "run-20260514-051225-adder32-fullflow-fixed.log"
        ),
    },
]

LOG = Path("/home/ubuntu/socmate/.socmate/cron-unblock.log")
LOCK = Path("/tmp/socmate-cron-unblock.lock")


def log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"[{stamp}] {message}\n")


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"{path}: failed to read JSON: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def tmux_alive(session: str) -> bool:
    return subprocess.run(
        ["tmux", "has-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def answer_questions(path: Path) -> None:
    answers_path = path.with_name(path.stem + ".answers.json")
    if answers_path.exists():
        return
    packet = read_json(path)
    questions = packet.get("questions") or []
    if not isinstance(questions, list) or not questions:
        return

    answers: dict[str, str] = {}
    for index, item in enumerate(questions):
        if not isinstance(item, dict):
            continue
        qid = item.get("id") or f"q{index}"
        if item.get("suggested_answer"):
            answers[qid] = str(item["suggested_answer"])
            continue
        options = item.get("options") or []
        if options:
            first = options[0]
            answers[qid] = first.get("label", "") if isinstance(first, dict) else str(first)
            continue
        category = str(item.get("category") or "").lower()
        if "technology" in category:
            answers[qid] = "Sky130 sky130_fd_sc_hd, continue with documented assumptions."
        elif "clock" in category or "timing" in category:
            answers[qid] = "50 MHz target clock unless the prompt specifies otherwise."
        elif "verification" in category or "kpi" in category:
            answers[qid] = "Preserve the measurable KPI from the prompt and verify it in validation DV."
        else:
            answers[qid] = "Use the most conservative implementation consistent with the prompt."

    if answers:
        write_json(answers_path, answers)
        log(f"{path}: wrote answers file {answers_path}")


def decision_path_for(packet_path: Path, packet: dict) -> Path:
    explicit = packet.get("decision_file")
    if explicit:
        return Path(explicit)
    return packet_path.with_name(packet_path.stem + ".decision.json")


def ensure_feedback_field(decision_path: Path) -> None:
    if not decision_path.exists():
        return
    decision = read_json(decision_path)
    if decision.get("action") != "feedback" or decision.get("feedback"):
        return
    evidence = decision.get("evidence") or []
    if isinstance(evidence, list) and evidence:
        evidence_text = "\n".join(f"- {item}" for item in evidence[:10])
    else:
        evidence_text = "- No additional evidence was provided by triage."
    feedback = (
        f"{decision.get('rationale') or 'Revise according to triage findings.'}\n\n"
        f"Root cause: {decision.get('root_cause') or 'unspecified'}\n\n"
        f"Required revision evidence:\n{evidence_text}"
    )
    decision["feedback"] = feedback
    write_json(decision_path, decision)
    log(f"{decision_path}: added missing feedback field")


def run_triage(root: Path, packet_path: Path, decision_path: Path) -> None:
    if decision_path.exists():
        ensure_feedback_field(decision_path)
        return
    triage = root / "scripts" / "triage_escalation.py"
    if not triage.exists():
        triage = Path("/home/ubuntu/socmate/scripts/triage_escalation.py")
    env = os.environ.copy()
    env.update(
        {
            "SOCMATE_LLM_PROVIDER": "codex",
            "SOCMATE_CODEX_MODEL": "gpt-5.5",
            "SOCMATE_TRIAGE_MODEL": "gpt-5.5",
            "SOCMATE_CODEX_SANDBOX": "danger-full-access",
        }
    )
    log_file = packet_path.with_suffix(".triage.log")
    with log_file.open("a", encoding="utf-8") as fh:
        rc = subprocess.run(
            [sys.executable, str(triage), "--escalation", str(packet_path)],
            cwd=str(root),
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            timeout=1500,
            check=False,
        ).returncode
    log(f"{packet_path}: triage rc={rc}")
    ensure_feedback_field(decision_path)


def handle_escalations(root: Path) -> None:
    esc_dir = root / ".socmate" / "escalations"
    if not esc_dir.exists():
        return
    for path in sorted(esc_dir.glob("*.json")):
        name = path.name
        if name.endswith(".answers.json") or name.endswith(".decision.json"):
            continue
        packet = read_json(path)
        if not packet:
            continue
        if packet.get("questions") is not None or name.endswith("_questions.json"):
            answer_questions(path)
        if packet.get("allowed_actions") is not None or packet.get("decision_file"):
            run_triage(root, path, decision_path_for(path, packet))


def tail_summary(path: Path, lines: int = 12) -> str:
    if not path.exists():
        return "log missing"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"log unreadable: {exc}"
    return "\n".join(text.splitlines()[-lines:])


def main() -> int:
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 1800:
        log("previous poll still within lock window; exiting")
        return 0
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    try:
        log("poll start")
        for run in RUNS:
            root = run["root"]
            alive = tmux_alive(str(run["session"]))
            log(f"{run['name']}: session_alive={alive} root={root}")
            handle_escalations(root)
            summary = tail_summary(run["log"])
            log(f"{run['name']} tail:\n{summary}")
        openroad = Path("/home/ubuntu/openroad-src/build/src/openroad")
        log(f"openroad_ready={openroad.exists() and os.access(openroad, os.X_OK)}")
        log("poll end")
        return 0
    finally:
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
