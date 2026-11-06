"""Evidence from the agent transcripts for the four analysis questions.

For each graded run it walks the check outputs in order and reports:
- per component: check runs, runs that showed failures, and the step at which each step check first passed;
- A and B breakage: after A and B were first fully passing, every later check output (regression suite or
  A/B step check) that showed a failure, and which C/D/E step the agent was on at the time;
- whether the agent read a rule file (`*_rules.py` or `regression_rules.py`) and how many times it read spec text.
Usage: python3 analysis/transcript_evidence.py [--json]
"""
import csv, glob, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS = os.path.join(ROOT, "tasks/slack-clone-course/jobs")
CMD = re.compile(r"```mswea_bash_command\n(.*?)```", re.S)
CHECK = re.compile(r"(check_([a-e])_(\d+)\.sh|regression\.sh)")
RESULT = re.compile(r"RESULT: (\d+) passed, (\d+) failed")
REGRESSION = re.compile(r"REGRESSION: (\d+) broken")
FAILED_RULE = re.compile(r"^(?:REGRESSION )?FAIL (?:roles |channels )?([A-E]\d+\.\d+)", re.M)
STEPS = {"a": 4, "b": 5}


def evidence(job):
    d = json.load(open(glob.glob(os.path.join(JOBS, job, "*/agent/mini-swe-agent.trajectory.json"))[0]))
    msgs = d["messages"]
    comp = {}                      # component -> {"runs", "failing_runs", "steps": {step: [results...]}}
    ab_state = {}                  # "a_1".. "b_5" or "regression" -> last result was clean?
    ab_clean_at = None             # index of the command at which A and B were first all clean
    breaks, current_step, n_cmd = [], None, 0
    rule_reads, spec_reads = 0, 0
    for i, m in enumerate(msgs):
        if m["role"] != "assistant":
            continue
        cmd = "\n".join(CMD.findall(str(m["content"])))
        if not cmd:
            continue
        n_cmd += 1
        if re.search(r"(cat|sed|head|tail|grep|less|python3 -c)[^\n]*_rules\.py", cmd):
            rule_reads += 1
        if re.search(r"(cat|sed|head|tail)[^\n]*/spec/[a-e]/[a-e]-\d\.md", cmd):
            spec_reads += 1
        called = [c for c in CHECK.finditer(cmd) if re.search(r"bash\s+\S*" + re.escape(c.group(1)), cmd)]
        if not called or i + 1 >= len(msgs):
            continue
        out = str(msgs[i + 1]["content"])
        results = [(int(p), int(f)) for p, f in RESULT.findall(out)]
        regress = [int(n) for n in REGRESSION.findall(out)]
        failed_rules = FAILED_RULE.findall(out)
        for c in called:
            if c.group(1) == "regression.sh":
                if not regress:
                    continue
                n = regress.pop(0)
                key, clean, x = "regression", n == 0, "ab"
            else:
                if not results:
                    continue
                p, f = results.pop(0)
                x, step = c.group(2), int(c.group(3))
                key, clean = f"{x}_{step}", f == 0
                s = comp.setdefault(x, {"runs": 0, "failing_runs": 0, "first_pass_cmd": {}})
                s["runs"] += 1
                s["failing_runs"] += 0 if clean else 1
                if clean:
                    s["first_pass_cmd"].setdefault(step, n_cmd)
                if x in "cde":
                    current_step = f"{x.upper()}.{step}"
            if x in "ab" or key == "regression":
                ab_state[key] = clean
                all_ab = ab_state.get("regression") or all(
                    ab_state.get(f"{y}_{k}") for y, n in STEPS.items() for k in range(1, n + 1))
                if ab_clean_at is None and all_ab:
                    ab_clean_at = n_cmd
                elif ab_clean_at is not None and not clean:
                    breaks.append({"cmd": n_cmd, "check": key, "during": current_step,
                                   "rules": sorted(set(r for r in failed_rules if r[0] in "AB"))})
    return {
        "commands": n_cmd,
        "components": {x: {"check_runs": s["runs"], "failing_runs": s["failing_runs"],
                           "steps_passed": len(s["first_pass_cmd"])} for x, s in sorted(comp.items())},
        "ab_first_clean_cmd": ab_clean_at,
        "ab_breaks_after_clean": breaks,
        "rule_file_reads": rule_reads,
        "spec_file_reads": spec_reads,
    }


def main():
    rows = list(csv.DictReader(open(os.path.join(ROOT, "analysis/runs.tsv")), delimiter="\t"))
    out = {f"{r['run']}:{r['repeat']}": evidence(r["job"]) for r in rows}
    if "--json" in sys.argv:
        print(json.dumps(out, indent=1))
        return
    for k, e in out.items():
        comps = " ".join(f"{x.upper()}:{s['check_runs']}runs/{s['failing_runs']}fail" for x, s in e["components"].items())
        br = "; ".join(f"{b['check']}@{b['during']}{b['rules']}" for b in e["ab_breaks_after_clean"])
        print(f"{k:6} cmds={e['commands']} {comps} rule_reads={e['rule_file_reads']} spec_reads={e['spec_file_reads']} "
              f"ab_clean_at={e['ab_first_clean_cmd']} breaks={len(e['ab_breaks_after_clean'])} {br}")


if __name__ == "__main__":
    main()
