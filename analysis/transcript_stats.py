"""Per-run statistics from each graded job's agent trajectory.

For every row of runs.tsv: model calls, cost, wall time, how many times each check (and the regression
suite) was run, and the last result the agent saw from each check before it submitted.
Only real command outputs count; a README or spec file echoed by `cat` is never mistaken for a result.
Usage: python3 analysis/transcript_stats.py [--json]
"""
import csv, glob, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS = os.path.join(ROOT, "tasks/slack-clone-course/jobs")
CMD = re.compile(r"```mswea_bash_command\n(.*?)```", re.S)
CHECK = re.compile(r"(check_[a-e]_\d+\.sh|regression\.sh)")
RESULT = re.compile(r"RESULT: (\d+) passed, (\d+) failed")
REGRESSION = re.compile(r"REGRESSION: (\d+) broken")


def stats(job):
    path = glob.glob(os.path.join(JOBS, job, "*/agent/mini-swe-agent.trajectory.json"))[0]
    d = json.load(open(path))
    msgs = d["messages"]
    runs, last, invocations = {}, {}, 0
    ts = [m.get("extra", {}).get("timestamp") for m in msgs if m.get("extra", {}).get("timestamp")]
    for i, m in enumerate(msgs):
        if m["role"] != "assistant":
            continue
        cmd = "\n".join(CMD.findall(str(m["content"])))
        # A check run is a bash invocation of a check script, not a cat/sed of it.
        called = [c for c in CHECK.findall(cmd) if re.search(r"bash\s+\S*" + re.escape(c), cmd)]
        if not called or i + 1 >= len(msgs):
            continue
        invocations += 1
        out = str(msgs[i + 1]["content"])
        # Outputs of several checks in one command arrive in order; pair them with the names called.
        results = [(int(p), int(f)) for p, f in RESULT.findall(out)]
        regress = [int(n) for n in REGRESSION.findall(out)]
        for c in called:
            runs[c] = runs.get(c, 0) + 1
            if c == "regression.sh":
                if regress:
                    last[c] = f"{regress.pop(0)} broken"
            elif results:
                p, f = results.pop(0)
                last[c] = f"{p}/{p + f}"
    failing = {c: v for c, v in last.items()
               if (c == "regression.sh" and v != "0 broken")
               or (c != "regression.sh" and v.split("/")[0] != v.split("/")[1])}
    return {
        "calls": d["info"]["model_stats"]["api_calls"],
        "cost": round(d["info"]["model_stats"]["instance_cost"], 4),
        "minutes": round((ts[-1] - ts[0]) / 60, 1) if len(ts) > 1 else None,
        "check_commands": invocations,
        "check_runs": sum(runs.values()),
        "runs_per_check": runs,
        "last_seen": last,
        "submitted_with_failures": sorted(failing),
        "exit_status": d["info"].get("exit_status"),
    }


def main():
    rows = list(csv.DictReader(open(os.path.join(ROOT, "analysis/runs.tsv")), delimiter="\t"))
    out = {f"{r['run']}:{r['repeat']}": dict(job=r["job"], **stats(r["job"])) for r in rows}
    if "--json" in sys.argv:
        print(json.dumps(out, indent=1))
        return
    for k, s in out.items():
        print(f"{k:6} {s['job']} calls={s['calls']} ${s['cost']} {s['minutes']}min "
              f"check_cmds={s['check_commands']} check_runs={s['check_runs']} exit={s['exit_status']} "
              f"failing_at_submit={s['submitted_with_failures'] or 'none'}")


if __name__ == "__main__":
    main()
