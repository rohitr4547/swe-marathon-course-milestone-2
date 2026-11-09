# Read this first: what to build, in what order, and when you may submit

`/app` starts empty. In this run you build the cluster and Spec A (roles and invitations), Spec B (channels and message history), Spec C (features built on messages), Spec D (cluster robustness) and Spec E (message edits, the IRC gateway and the web UI), written as staged
spec files: each step builds on the one before it and has its own check.

The task description you were given is still the grading contract. The files in `/spec` do not
change it; they say what to build now, in what order, and how to prove it works.

## Files in /spec

| File | What it is |
|---|---|
| `README.md` | This file: scope, order, time plan, how to verify, when to submit |
| `common/architecture.md` | **Read second.** The design, `start.sh`, and the function names every step uses |
| `common/restart.sh` | Restarts the cluster and waits for all three nodes |
| `common/ready.sh` | The submit gate: lists any check not yet passed on your latest code |
| `common/checklib.py` | Shared helpers the checks use (no need to read) |
| `a/a-1.md` to `a/a-4.md` | Spec A (roles and invitations): steps A.1 to A.4, 48 rules |
| `a/checks/check_a_1.sh` to `a/checks/check_a_4.sh` | One check per step of A |
| `a/checks/lib.sh` | Shared setting the A checks load |
| `a/checks/a_rules.py` | The rule code the A checks run (read it only to see what a failing rule sent) |
| `b/b-1.md` to `b/b-5.md` | Spec B (channels and message history): steps B.1 to B.5, 52 rules |
| `b/checks/check_b_1.sh` to `b/checks/check_b_5.sh` | One check per step of B |
| `b/checks/lib.sh` | Shared setting the B checks load |
| `b/checks/b_rules.py` | The rule code the B checks run (read it only to see what a failing rule sent) |
| `c/c-1.md` to `c/c-5.md` | Spec C (features built on messages): steps C.1 to C.5, 34 rules |
| `c/checks/check_c_1.sh` to `c/checks/check_c_5.sh` | One check per step of C |
| `c/checks/lib.sh` | Shared setting the C checks load |
| `c/checks/c_rules.py` | The rule code the C checks run (read it only to see what a failing rule sent) |
| `d/d-1.md` to `d/d-3.md` | Spec D (cluster robustness): steps D.1 to D.3, 14 rules |
| `d/checks/check_d_1.sh` to `d/checks/check_d_3.sh` | One check per step of D |
| `d/checks/lib.sh` | Shared setting the D checks load |
| `d/checks/d_rules.py` | The rule code the D checks run (read it only to see what a failing rule sent) |
| `e/e-1.md` to `e/e-6.md` | Spec E (message edits, the IRC gateway and the web UI): steps E.1 to E.6, 37 rules |
| `e/checks/check_e_1.sh` to `e/checks/check_e_6.sh` | One check per step of E |
| `e/checks/lib.sh` | Shared setting the E checks load |
| `e/checks/e_rules.py` | The rule code the E checks run (read it only to see what a failing rule sent) |

Each spec file has three parts: **Uses** (functions from earlier steps that you call, never
rewrite), **Adds** (functions this step introduces, with the names to use), and **Rules** (what must
be true, numbered to match the check output).

## Scope

Do not build anything else from the task description in this run. Never stop to polish one
feature while a check elsewhere is still failing.

## Where shared pieces live

- The IRC gateway, and its part of surviving a killed node, are in Spec E (E.2, E.3); Spec D covers HTTP, WebSocket delivery and replay.
- The web UI's reaction picker (E.6) uses the reactions routes from Spec C (C.1).

## Time plan (50 minutes; run `bash /app/timer.sh` to see what is left)

| Minutes | Work |
|---|---|
| 0 to 4 | Read this file and `common/architecture.md`. Write `start.sh`, `requirements.txt`, then `pip install -r /app/requirements.txt` |
| 4 to 20 | A.1 to A.4, then B.1 to B.5 |
| 20 to 30 | C |
| 30 to 34 | D |
| 34 to 45 | E |
| last 5 | Stop adding features: restart, run every check, run `ready.sh`, submit (see "Before you submit") |

If you fall more than 5 minutes behind, move on once the current step's check shows no more than 2
failures, and come back to them at the end.

## Order of work

1. Read `common/architecture.md`. Create `/app/start.sh` exactly as given there (`chmod +x`), `/app/requirements.txt`, `core.py` and `server.py`.
2. For each step A.1 to A.4: read `a/a-<step>.md`, implement it, run `bash /spec/common/restart.sh`, then `bash /spec/a/checks/check_a_<step>.sh`, and fix failures one at a time, lowest rule number first.
3. For each step B.1 to B.5: read `b/b-<step>.md`, implement it, run `bash /spec/common/restart.sh`, then `bash /spec/b/checks/check_b_<step>.sh`, and fix failures one at a time, lowest rule number first.
4. For each step C.1 to C.5: read `c/c-<step>.md`, implement it, run `bash /spec/common/restart.sh`, then `bash /spec/c/checks/check_c_<step>.sh`, and fix failures one at a time, lowest rule number first.
5. For each step D.1 to D.3: read `d/d-<step>.md`, implement it, run `bash /spec/common/restart.sh`, then `bash /spec/d/checks/check_d_<step>.sh`, and fix failures one at a time, lowest rule number first.
6. For each step E.1 to E.6: read `e/e-<step>.md`, implement it, run `bash /spec/common/restart.sh`, then `bash /spec/e/checks/check_e_<step>.sh`, and fix failures one at a time, lowest rule number first.
7. After every step, rerun the checks of the earlier steps of the same component. If anything that passed now fails, fix it before moving on.

Do not start a step while a check from an earlier step fails (except under the time rule above).

## How to verify

Run each of these as a single command:

```bash
bash /spec/common/restart.sh                 # after every code change (--fresh after a schema change)
bash /spec/common/ready.sh                   # what is still failing or not rerun since your last change
bash /spec/a/checks/check_a_1.sh            # likewise check_a_2.sh ... check_a_4.sh
bash /spec/b/checks/check_b_1.sh            # likewise check_b_2.sh ... check_b_5.sh
bash /spec/c/checks/check_c_1.sh            # likewise check_c_2.sh ... check_c_5.sh
bash /spec/d/checks/check_d_1.sh            # likewise check_d_2.sh ... check_d_3.sh
bash /spec/e/checks/check_e_1.sh            # likewise check_e_2.sh ... check_e_6.sh
```

Each check ends with a line such as `RESULT: 7 passed, 0 failed`.

- A check finishes in under 30 seconds and starts the cluster if it is down, but it does not restart
  the cluster after a code change: use `restart.sh`, which stops processes by process group and by
  port PID. Never kill processes by name (`pkill`, `killall`, `pgrep -f`, `ps | grep`).
- Some checks kill the node on `8001` or `redis-server` once, on purpose, to prove that `start.sh`
  brings them back.

## Do not edit /spec

Do not edit, move or delete any file in `/spec`, including the checks. If a check fails, fix the code
in `/app`, not the check.

## Mistakes to avoid

- One command block per reply. A reply with two blocks, or one cut off mid-file, is rejected. Write big
  files in parts of about 250 lines.
- Allocate each channel `event_id` inside the write's database transaction and deliver events by
  polling the database, so every node and the IRC gateway see one dense sequence even with Redis dead.
- Check the JSON type of every body field: bad input is a `400`, never a `500`.
- Never open `alert`, `confirm` or `prompt` in the UI, and put every pinned `data-testid` in the HTML
  that `GET /` serves before any JavaScript runs.
- Keep all UI behaviour in `static/app.js`, one handler per control. To fix a UI flow, replace its
  function in place; never append another `<script>`, handler or override on top of the old one.
- Keep `/app` bootable at every moment: the grader runs `pip install -r /app/requirements.txt` and
  `/app/start.sh` in a fresh container. Anything installed by hand is not there.

## Before you submit

You may run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` only when all of these are true, from output
you actually saw after your last code change and a `restart.sh`:

- every A check, run one at a time, ends with `0 failed` (`check_a_1.sh` 17, `check_a_2.sh` 9, `check_a_3.sh` 11, `check_a_4.sh` 11 rules);
- every B check, run one at a time, ends with `0 failed` (`check_b_1.sh` 18, `check_b_2.sh` 11, `check_b_3.sh` 7, `check_b_4.sh` 7, `check_b_5.sh` 9 rules);
- every C check, run one at a time, ends with `0 failed` (`check_c_1.sh` 9, `check_c_2.sh` 8, `check_c_3.sh` 6, `check_c_4.sh` 7, `check_c_5.sh` 4 rules);
- every D check, run one at a time, ends with `0 failed` (`check_d_1.sh` 8, `check_d_2.sh` 3, `check_d_3.sh` 3 rules);
- every E check, run one at a time, ends with `0 failed` (`check_e_1.sh` 9, `check_e_2.sh` 5, `check_e_3.sh` 4, `check_e_4.sh` 5, `check_e_5.sh` 7, `check_e_6.sh` 7 rules);
- `GET /api/health` returns `200` on ports `8000`, `8001` and `8002`;
- `bash /spec/common/ready.sh` prints `READY TO SUBMIT`. It records every complete check run against
  the current code in `/app`, so it lists any check that is failing, or that you have not rerun since your
  last change. If it prints `NOT READY`, rerun or fix what it lists; do not submit.

**Time-out rule:** when `bash /app/timer.sh` shows `0:04` or less, stop adding features. Run
`restart.sh`, confirm all three nodes answer `/api/health`, and submit. Never submit while the cluster
does not boot.

These checks are necessary, not sufficient. The full task in the grading contract still applies.
