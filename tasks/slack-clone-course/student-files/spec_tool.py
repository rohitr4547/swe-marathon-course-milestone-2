#!/usr/bin/env python3
"""Builds, checks and stages the /spec folder for each run of the milestone 2 run plan.

  python3 spec_tool.py build                 build every run's /spec from spec-src/ into spec-copies/<run>/
  python3 spec_tool.py lint                  syntax, README coverage, rule ids, testing mode, wording, for every copy
  python3 spec_tool.py stage <run>           put spec-copies/<run>/ into milestone-2-specs/ (hash-verified)
  python3 spec_tool.py verify <run>          confirm milestone-2-specs/ still matches spec-copies/<run>/
  python3 spec_tool.py status                show which run milestone-2-specs/ holds

spec-src/ is the only place to edit. spec-copies/ and milestone-2-specs/ are generated from it.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "spec-src")
REPO = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
COPIES = os.path.join(REPO, "spec-copies")
SPECS = os.path.normpath(os.path.join(HERE, "..", "course-staff-files", "final_task", "environment",
                                      "milestone-specs", "milestone-2-specs"))
LINK = os.path.join(HERE, "spec")
LINK_TARGET = "../course-staff-files/final_task/environment/milestone-specs/milestone-2-specs"

COMP = {"a": ("A", "roles and invitations", ["A0 A1", "A2", "A3", "A4"], 48),
        "b": ("B", "channels and message history", ["B0 B1", "B2", "B3", "B4", "B5"], 52),
        "c": ("C", "features built on messages", ["C0 C1", "C2", "C3", "C4", "C5"], 34),
        "d": ("D", "cluster robustness", ["D0 D1", "D2", "D3"], 14),
        "e": ("E", "message edits, the IRC gateway and the web UI", ["E0 E1", "E2", "E3", "E4", "E5", "E6"], 37)}
STEP_RESULTS = {"a": [17, 9, 11, 11], "b": [18, 11, 7, 7, 9], "c": [9, 8, 6, 7, 4], "d": [8, 3, 3],
                "e": [9, 5, 4, 5, 7, 7]}
# run: (components with specs and checks, testing mode, spec files given, A/B handling)
RUNS = {"1.1": ("c", "score_only", True, None), "1.2": ("d", "score_only", True, None),
        "1.3": ("e", "score_only", True, None), "2.1": ("c", "verbose", True, None),
        "2.2": ("d", "verbose", True, None), "2.3": ("e", "verbose", True, None),
        "3": ("cde", "verbose", True, None), "4": ("cde", "verbose", False, None),
        "5.1": ("cde", "verbose", True, "regression"), "5.2": ("cde", "verbose", True, "regression+specs"),
        "5.3": ("cde", "verbose", True, "plain")}
FORBIDDEN = re.compile(r"milestone|score_only|verbose|FEEDBACK|testing mode|hidden test|staff|grader runs your|run plan",
                       re.I)


def die(msg):
    print(f"error: {msg}")
    sys.exit(1)


def components(run):
    comps, _, _, ab = RUNS[run]
    return ("ab" if ab == "plain" else "") + comps


# ---------------------------------------------------------------- README

def readme(run):
    comps, _, specs, ab = RUNS[run]
    built = ("ab" if ab in ("plain", "regression+specs") else "") + comps
    names = [f"Spec {COMP[x][0]} ({COMP[x][1]})" for x in built]
    regression = ab in ("regression", "regression+specs")
    no_ab = ab is None
    out = ["# Read this first: what to build, in what order, and when you may submit", ""]
    if specs:
        scope = ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
        out += [f"`/app` starts empty. In this run you build the cluster and {scope}, written as staged",
                "spec files: each step builds on the one before it and has its own check."]
    else:
        listed = ", ".join(f"component {COMP[x][0]} ({COMP[x][1]})" for x in comps)
        out += [f"`/app` starts empty. In this run you build the cluster and {listed}. There are no written",
                "rules in this run: the checks are the specification. Each check sends real requests and reports",
                "which behaviours your code still gets wrong."]
    if regression:
        out += ["", "The features of Specs A (roles and invitations) and B (channels and message history) are covered by",
                "a regression suite: one command that reruns every A and B rule and reports what is broken."]
    out += ["", "The task description you were given is still the grading contract. The files in `/spec` do not",
            "change it; they say what to build now, in what order, and how to prove it works.", ""]

    out += ["## Files in /spec", "", "| File | What it is |", "|---|---|",
            "| `README.md` | This file: scope, order, time plan, how to verify, when to submit |"]
    if specs:
        out.append("| `common/architecture.md` | **Read second.** The design, `start.sh`, and the function names every step uses |")
    out += ["| `common/restart.sh` | Restarts the cluster and waits for all three nodes |",
            "| `common/ready.sh` | The submit gate: lists any check not yet passed on your latest code |",
            "| `common/checklib.py` | Shared helpers the checks use (no need to read) |"]
    if regression:
        out += ["| `checks/regression.sh` | The A and B regression suite: prints each broken rule, then `REGRESSION: <n> broken` |",
                "| `checks/regression_rules.py` | The rule code the regression suite runs |"]
    for x in built:
        L, desc, steps, total = COMP[x]
        n = len(steps)
        if specs:
            out.append(f"| `{x}/{x}-1.md` to `{x}/{x}-{n}.md` | Spec {L} ({desc}): steps {L}.1 to {L}.{n}, {total} rules |")
        if x in comps or ab == "plain":
            out += [f"| `{x}/checks/check_{x}_1.sh` to `{x}/checks/check_{x}_{n}.sh` | One check per step of {L} |",
                    f"| `{x}/checks/lib.sh` | Shared setting the {L} checks load |",
                    f"| `{x}/checks/{x}_rules.py` | The rule code the {L} checks run (read it only to see what a failing rule sent) |"]
    out.append("")
    if specs:
        out += ["Each spec file has three parts: **Uses** (functions from earlier steps that you call, never",
                "rewrite), **Adds** (functions this step introduces, with the names to use), and **Rules** (what must",
                "be true, numbered to match the check output).", ""]

    out += ["## Scope", ""]
    if no_ab or regression and not ab.endswith("specs"):
        if no_ab:
            out += ["Specs A and B are not part of this run. Their features that these components depend on are the",
                    "**basics** below: build only these, exactly as the task description defines them, then the",
                    "components of this run. Do not build roles, invitations, pins, search or read state.", ""]
        else:
            out += ["Specs A and B are not written out in this run. Build every feature the regression suite checks,",
                    "exactly as the task description defines it, until the suite reports `REGRESSION: 0 broken`.", ""]
        if no_ab:
            out += ["- **Auth:** `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`.",
                    "- **Workspaces:** `POST /api/workspaces` (the creator is owner; `#general` is created and joined),",
                    "  `GET /api/workspaces`, `GET /api/workspaces/{slug}` (with its channels), `GET /api/workspaces/{slug}/members`.",
                    "- **Channels:** `POST /api/workspaces/{slug}/channels`, `POST /api/channels/{id}/join` (in an open",
                    "  workspace a non-member becomes a member and joins `#general`), `POST /api/channels/{id}/leave`,",
                    "  `GET /api/channels/{id}/members`, `PATCH /api/channels/{id}` (topic), archive and unarchive (workspace",
                    "  owner only; writes to an archived channel return `423`).",
                    "- **Messages:** `POST /api/channels/{id}/messages` (with `parent_id` for thread replies),",
                    "  `GET /api/channels/{id}/messages` (newest first, `limit` and `cursor`), `GET /api/messages/{id}/replies`.",
                    "- **Mentions:** `@username` (workspace members) and `@channel` resolve into the message's `mentions`.",
                    "- **WebSocket:** `/api/ws?token=` with `subscribe`, `resume` and live channel events carrying a dense",
                    "  per-channel `event_id`.",
                    ]
            if "e" in comps and "c" not in comps:
                out.append("- **Reactions:** `POST /api/messages/{id}/reactions` and `DELETE /api/messages/{id}/reactions/{emoji}` (the UI's emoji picker uses them).")
            out.append("")
    out += ["Do not build anything else from the task description in this run. Never stop to polish one",
            "feature while a check elsewhere is still failing.", ""]

    shared = []
    if "e" in comps:
        shared.append("- The IRC gateway, and its part of surviving a killed node, are in Spec E (E.2, E.3)"
                      + ("; Spec D covers HTTP, WebSocket delivery and replay." if "d" in comps else "."))
        if "c" in comps:
            shared.append("- The web UI's reaction picker (E.6) uses the reactions routes from Spec C (C.1).")
    if shared:
        out += ["## Where shared pieces live", ""] + shared + [""]

    out += ["## Time plan (50 minutes; run `bash /app/timer.sh` to see what is left)", "", "| Minutes | Work |", "|---|---|"]
    setup = "Read this file" + (" and `common/architecture.md`" if specs else "") + ". Write `start.sh`, `requirements.txt`, then `pip install -r /app/requirements.txt`"
    out.append(f"| 0 to 4 | {setup} |")
    if no_ab:
        if len(comps) == 1:
            out += ["| 4 to 12 | The basics |", f"| 12 to 45 | Spec {COMP[comps][0]}, one step at a time |"]
        else:
            out += ["| 4 to 12 | The basics |", "| 12 to 24 | C |", "| 24 to 30 | D |", "| 30 to 45 | E |"]
    else:
        ab_work = "A and B until `regression.sh` reports `REGRESSION: 0 broken`" if regression else "A.1 to A.4, then B.1 to B.5"
        out += [f"| 4 to 20 | {ab_work} |", "| 20 to 30 | C |", "| 30 to 34 | D |", "| 34 to 45 | E |"]
    out += ["| last 5 | Stop adding features: restart, run every check, run `ready.sh`, submit (see \"Before you submit\") |", "",
            "If you fall more than 5 minutes behind, move on once the current step's check shows no more than 2",
            "failures, and come back to them at the end.", ""]

    out += ["## Order of work", ""]
    first = ("Read `common/architecture.md`. Create `/app/start.sh` exactly as given there (`chmod +x`),"
             " `/app/requirements.txt`, `core.py` and `server.py`.") if specs else \
            ("Create `/app/start.sh` (it must stay in the foreground, run `redis-server` on `127.0.0.1:6379`, the three"
             " HTTP nodes and the IRC gateway, and restart any of them that dies), `/app/requirements.txt`, and the"
             " server code.")
    out.append(f"1. {first}")
    k = 2
    if no_ab:
        out.append(f"{k}. Build the basics listed under Scope."); k += 1
    if regression:
        if ab == "regression+specs":
            out.append(f"{k}. Read `a/a-1.md` to `a/a-4.md` and `b/b-1.md` to `b/b-5.md`. Build A and B from them until"
                       " `bash /spec/checks/regression.sh` reports `REGRESSION: 0 broken`.")
        else:
            out.append(f"{k}. Run `bash /spec/checks/regression.sh`. Build the A and B features of the task description"
                       " until it reports `REGRESSION: 0 broken`.")
        k += 1
    order = (["a", "b"] if ab == "plain" else []) + list(comps)
    for x in order:
        L, _, steps, _ = COMP[x]
        n = len(steps)
        if specs:
            out.append(f"{k}. For each step {L}.1 to {L}.{n}: read `{x}/{x}-<step>.md`, implement it, run"
                       f" `bash /spec/common/restart.sh`, then `bash /spec/{x}/checks/check_{x}_<step>.sh`, and fix failures"
                       " one at a time, lowest rule number first.")
        else:
            out.append(f"{k}. For each check `{x}/checks/check_{x}_1.sh` to `check_{x}_{n}.sh` in order: run it, implement"
                       " what it reports as failing (the task description defines every behaviour it tests), run"
                       " `bash /spec/common/restart.sh`, and rerun it until it passes.")
        k += 1
    out.append(f"{k}. After every step, rerun the checks of the earlier steps of the same component"
               + (", and `bash /spec/checks/regression.sh`" if regression else "")
               + ". If anything that passed now fails, fix it before moving on.")
    out += ["", "Do not start a step while a check from an earlier step fails (except under the time rule above).", ""]

    out += ["## How to verify", "", "Run each of these as a single command:", "", "```bash",
            "bash /spec/common/restart.sh                 # after every code change (--fresh after a schema change)",
            "bash /spec/common/ready.sh                   # what is still failing or not rerun since your last change"]
    if regression:
        out.append("bash /spec/checks/regression.sh              # every A and B rule")
    for x in order:
        out.append(f"bash /spec/{x}/checks/check_{x}_1.sh            # likewise check_{x}_2.sh ... check_{x}_{len(COMP[x][2])}.sh")
    out += ["```", "", "Each check ends with a line such as `RESULT: 7 passed, 0 failed`."
            + (" The regression suite ends with a line such as `REGRESSION: 0 broken`." if regression else ""), "",
            "- A check finishes in under 30 seconds and starts the cluster if it is down, but it does not restart",
            "  the cluster after a code change: use `restart.sh`, which stops processes by process group and by",
            "  port PID. Never kill processes by name (`pkill`, `killall`, `pgrep -f`, `ps | grep`).",
            "- Some checks kill the node on `8001` or `redis-server` once, on purpose, to prove that `start.sh`",
            "  brings them back.", "",
            "## Do not edit /spec", "",
            "Do not edit, move or delete any file in `/spec`, including the checks. If a check fails, fix the code",
            "in `/app`, not the check.", "",
            "## Mistakes to avoid", "",
            "- One command block per reply. A reply with two blocks, or one cut off mid-file, is rejected. Write big",
            "  files in parts of about 250 lines.",
            "- Allocate each channel `event_id` inside the write's database transaction and deliver events by",
            "  polling the database, so every node and the IRC gateway see one dense sequence even with Redis dead.",
            "- Check the JSON type of every body field: bad input is a `400`, never a `500`.",
            "- Never open `alert`, `confirm` or `prompt` in the UI, and put every pinned `data-testid` in the HTML",
            "  that `GET /` serves before any JavaScript runs.",
            "- Keep `/app` bootable at every moment: the grader runs `pip install -r /app/requirements.txt` and",
            "  `/app/start.sh` in a fresh container. Anything installed by hand is not there.", "",
            "## Before you submit", "",
            "You may run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` only when all of these are true, from output",
            "you actually saw after your last code change and a `restart.sh`:", ""]
    if regression:
        out.append("- `bash /spec/checks/regression.sh` ends with `REGRESSION: 0 broken`;")
    for x in order:
        L, _, steps, _ = COMP[x]
        res = ", ".join(f"`check_{x}_{i + 1}.sh` {v}" for i, v in enumerate(STEP_RESULTS[x]))
        out.append(f"- every {L} check, run one at a time, ends with `0 failed` ({res} rules);")
    out += ["- `GET /api/health` returns `200` on ports `8000`, `8001` and `8002`;",
            "- `bash /spec/common/ready.sh` prints `READY TO SUBMIT`. It records every complete check run against",
            "  the current code in `/app`, so it lists any check that is failing, or that you have not rerun since your",
            "  last change. If it prints `NOT READY`, rerun or fix what it lists; do not submit.", "",
            "**Time-out rule:** when `bash /app/timer.sh` shows `0:04` or less, stop adding features. Run",
            "`restart.sh`, confirm all three nodes answer `/api/health`, and submit. Never submit while the cluster",
            "does not boot.", "",
            "These checks are necessary, not sufficient. The full task in the grading contract still applies.", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- building

def regression_module():
    parts = ['"""Every Spec A and Spec B rule in one module. Run it through regression.sh, not directly."""',
             "import http.client", "import os", "import sys", "import threading", "import time",
             "from types import SimpleNamespace", "",
             'sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))',
             "from checklib import *  # noqa: E402,F401,F403", ""]
    for x in "ab":
        s = open(os.path.join(SRC, x, "checks", f"{x}_rules.py")).read()
        s = s[re.search(r"from checklib import \(.*?\)\n", s, re.S).end():]
        s = s[:s.index("\nSTEPS = ")].replace("GROUPS = [", f"{x.upper()}_GROUPS = [")
        parts.append(f"# ---------------------------------------------------------------- Spec {x.upper()} rules\n{s.strip()}\n")
    parts.append('if __name__ == "__main__":\n    run(A_GROUPS + B_GROUPS, sys.argv[1:], "bash /spec/checks/regression.sh [A0 ... B5]",\n'
                 '        labels={"A": "roles", "B": "channels"})\n')
    return "\n\n".join(parts)


def copy_component(dst, x, with_specs, with_checks, mode):
    os.makedirs(os.path.join(dst, x), exist_ok=True)
    if with_specs:
        for f in sorted(os.listdir(os.path.join(SRC, x))):
            if f.endswith(".md"):
                shutil.copy2(os.path.join(SRC, x, f), os.path.join(dst, x, f))
    if with_checks:
        shutil.copytree(os.path.join(SRC, x, "checks"), os.path.join(dst, x, "checks"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "check_*_all.sh"))
        lib = os.path.join(dst, x, "checks", "lib.sh")
        s = open(lib).read()
        assert "FEEDBACK=verbose   # or: score_only" in s, lib
        open(lib, "w").write(s.replace("FEEDBACK=verbose   # or: score_only", f"FEEDBACK={mode}   # or: {'verbose' if mode == 'score_only' else 'score_only'}"))


def build_run(run):
    comps, mode, specs, ab = RUNS[run]
    dst = os.path.join(COPIES, run)
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(os.path.join(dst, "common"))
    for f in ("checklib.py", "restart.sh", "ready.sh") + (("architecture.md",) if specs else ()):
        shutil.copy2(os.path.join(SRC, "common", f), os.path.join(dst, "common", f))
    if ab == "plain":
        for x in "ab":
            copy_component(dst, x, True, True, mode)
    elif ab == "regression+specs":
        for x in "ab":
            copy_component(dst, x, True, False, mode)
    if ab in ("regression", "regression+specs"):
        os.makedirs(os.path.join(dst, "checks"))
        shutil.copy2(os.path.join(SRC, "regression", "regression.sh"), os.path.join(dst, "checks", "regression.sh"))
        open(os.path.join(dst, "checks", "regression_rules.py"), "w").write(regression_module())
    for x in comps:
        copy_component(dst, x, specs, True, mode)
    open(os.path.join(dst, "README.md"), "w").write(readme(run))


def cmd_build():
    for run in RUNS:
        build_run(run)
    print(f"built {len(RUNS)} run folders in spec-copies/")
    cmd_lint()


# ---------------------------------------------------------------- checks on the built copies

def files_in(root):
    out = []
    for dp, dn, fs in os.walk(root):
        dn[:] = [d for d in dn if d != "__pycache__"]
        out += [os.path.relpath(os.path.join(dp, f), root) for f in fs if not f.endswith(".pyc")]
    return sorted(out)


def named(readme_text):
    names = set(re.findall(r"[\w./-]+\.(?:py|sh|md)", readme_text))
    for prefix, stem, lo, hi in re.findall(r"([\w/]*?)(\w+?)_(\d+)\.sh` to `[\w/]*?\w+?_(\d+)\.sh", readme_text):
        names |= {f"{prefix}{stem}_{n}.sh" for n in range(int(lo), int(hi) + 1)}
    for prefix, stem, lo, hi in re.findall(r"`([\w/]*?)(\w)-(\d+)\.md` to `[\w/]*?\w-(\d+)\.md", readme_text):
        names |= {f"{prefix}{stem}-{n}.md" for n in range(int(lo), int(hi) + 1)}
    return names


def rule_ids_md(root, x):
    text = "".join(open(os.path.join(root, x, f)).read() for f in sorted(os.listdir(os.path.join(root, x))) if f.endswith(".md"))
    return re.findall(r"^([A-E]\d\.\d+) \*\*", text, re.M)


def rule_ids_checks(path):
    text = open(path).read()
    ids = re.findall(r'c\.rule\("([A-E]\d\.\d+)"', text)
    # The cluster baseline rules X0.1 to X0.6 come from checklib's baseline_rules / crash_rule helpers.
    for prefix in re.findall(r'baseline_ids\("([A-E]0)"\)', text):
        ids += [f"{prefix}.{i}" for i in range(1, 7)]
    return ids


def cmd_lint():
    problems = []
    if not os.path.isdir(COPIES):
        die("nothing built yet; run 'build'")
    for run in RUNS:
        root = os.path.join(COPIES, run)
        comps, mode, specs, ab = RUNS[run]
        rd = open(os.path.join(root, "README.md")).read()
        names = named(rd)
        for rel in files_in(root):
            path = os.path.join(root, rel)
            if rel.endswith(".py"):
                try:
                    compile(open(path).read(), path, "exec")
                except SyntaxError as e:
                    problems.append(f"{run}/{rel}: {e}")
            if rel.endswith(".sh"):
                r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
                if r.returncode:
                    problems.append(f"{run}/{rel}: {r.stderr.strip()}")
                if os.path.basename(rel).startswith(("check_", "regression")) and not os.access(path, os.X_OK):
                    problems.append(f"{run}/{rel}: not executable")
            if rel != "README.md" and rel not in names and os.path.basename(rel) not in names:
                problems.append(f"{run}/README.md does not name {rel}")
            if rel.endswith(".md") and FORBIDDEN.search(open(path).read()):
                problems.append(f"{run}/{rel}: wording leaks course or testing-mode terms: {FORBIDDEN.search(open(path).read()).group(0)!r}")
            if rel.endswith("lib.sh") and f"FEEDBACK={mode} " not in open(path).read():
                problems.append(f"{run}/{rel}: FEEDBACK is not {mode}")
        for name in names:
            if "/" in name and not name.startswith("/") and not os.path.exists(os.path.join(root, name)):
                problems.append(f"{run}/README.md names {name}, which is not in this run")
        for x in components(run) + ("ab" if ab == "regression+specs" else ""):
            md_ids = rule_ids_md(root, x) if specs else None
            ck = os.path.join(root, x, "checks", f"{x}_rules.py")
            ck_ids = rule_ids_checks(ck) if os.path.exists(ck) else None
            if md_ids is not None and ck_ids is not None and sorted(md_ids) != sorted(ck_ids):
                problems.append(f"{run}/{x}: spec and check rule ids differ: {sorted(set(md_ids) ^ set(ck_ids))[:6]}")
            if not specs and any(f.endswith(".md") for f in os.listdir(os.path.join(root, x))):
                problems.append(f"{run}/{x}: has spec files in a checks-only run")
        if ab and ab.startswith("regression"):
            reg = rule_ids_checks(os.path.join(root, "checks", "regression_rules.py"))
            want = rule_ids_checks(os.path.join(SRC, "a", "checks", "a_rules.py")) + rule_ids_checks(os.path.join(SRC, "b", "checks", "b_rules.py"))
            if sorted(reg) != sorted(want):
                problems.append(f"{run}: regression suite rules differ from Specs A and B")
    a, b = readme_without_parts("2.1"), readme_without_parts("1.1")
    if a != b:
        problems.append("README differs between the two testing modes (runs 1.1 and 2.1)")
    for p in problems:
        print("PROBLEM", p)
    print(f"lint: {len(problems)} problem(s)")
    if problems:
        sys.exit(1)


def readme_without_parts(run):
    return open(os.path.join(COPIES, run, "README.md")).read()


# ---------------------------------------------------------------- staging

def digests(root):
    return {rel: hashlib.sha256(open(os.path.join(root, rel), "rb").read()).hexdigest() for rel in files_in(root)}


def spec_entries():
    return [e for e in os.listdir(SPECS) if e != ".gitkeep"]


def cmd_stage(run):
    if run not in RUNS:
        die(f"unknown run {run}; choose from {' '.join(RUNS)}")
    src = os.path.join(COPIES, run)
    if not os.path.isdir(src):
        die("run 'build' first")
    for e in spec_entries():
        p = os.path.join(SPECS, e)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    for e in os.listdir(src):
        s, d = os.path.join(src, e), os.path.join(SPECS, e)
        shutil.copytree(s, d) if os.path.isdir(s) else shutil.copy2(s, d)
    cmd_verify(run)
    if os.path.realpath(LINK) != SPECS:
        if os.path.lexists(LINK):
            os.remove(LINK)
        os.symlink(LINK_TARGET, LINK)
    print(f"staged run {run}: milestone-2-specs/ now holds {' '.join(sorted(spec_entries()))}")


def cmd_verify(run):
    a, b = digests(SPECS), digests(os.path.join(COPIES, run))
    a.pop(".gitkeep", None)
    if a != b:
        die(f"milestone-2-specs/ differs from spec-copies/{run}/: {sorted(set(a) ^ set(b)) or [k for k in a if a[k] != b.get(k)]}")
    print(f"verified: milestone-2-specs/ matches spec-copies/{run}/ ({len(b)} files)")


def cmd_status():
    for run in RUNS:
        if os.path.isdir(os.path.join(COPIES, run)) and digests(os.path.join(COPIES, run)) == {k: v for k, v in digests(SPECS).items() if k != ".gitkeep"}:
            print(f"milestone-2-specs/ holds run {run}")
            return
    print(f"milestone-2-specs/ holds: {' '.join(sorted(spec_entries())) or '(empty)'} (matches no built run)")


if __name__ == "__main__":
    args = sys.argv[1:]
    cmds = {"build": cmd_build, "lint": cmd_lint, "stage": cmd_stage, "verify": cmd_verify, "status": cmd_status}
    if not args or args[0] not in cmds:
        print(__doc__)
        sys.exit(2)
    cmds[args[0]](*args[1:])
