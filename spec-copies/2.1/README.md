# Read this first: what to build, in what order, and when you may submit

`/app` starts empty. In this run you build the cluster and Spec C (features built on messages), written as staged
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
| `c/c-1.md` to `c/c-5.md` | Spec C (features built on messages): steps C.1 to C.5, 34 rules |
| `c/checks/check_c_1.sh` to `c/checks/check_c_5.sh` | One check per step of C |
| `c/checks/lib.sh` | Shared setting the C checks load |
| `c/checks/c_rules.py` | The rule code the C checks run (read it only to see what a failing rule sent) |

Each spec file has three parts: **Uses** (functions from earlier steps that you call, never
rewrite), **Adds** (functions this step introduces, with the names to use), and **Rules** (what must
be true, numbered to match the check output).

## Scope

Specs A and B are not part of this run. Their features that these components depend on are the
**basics** below: build only these, exactly as the task description defines them, then the
components of this run. Do not build roles, invitations, pins, search or read state.

- **Auth:** `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`.
- **Workspaces:** `POST /api/workspaces` (the creator is owner; `#general` is created and joined),
  `GET /api/workspaces`, `GET /api/workspaces/{slug}` (with its channels), `GET /api/workspaces/{slug}/members`.
- **Channels:** `POST /api/workspaces/{slug}/channels`, `POST /api/channels/{id}/join` (in an open
  workspace a non-member becomes a member and joins `#general`), `POST /api/channels/{id}/leave`,
  `GET /api/channels/{id}/members`, `PATCH /api/channels/{id}` (topic), archive and unarchive (workspace
  owner only; writes to an archived channel return `423`).
- **Messages:** `POST /api/channels/{id}/messages` (with `parent_id` for thread replies),
  `GET /api/channels/{id}/messages` (newest first, `limit` and `cursor`), `GET /api/messages/{id}/replies`.
- **Mentions:** `@username` (workspace members) and `@channel` resolve into the message's `mentions`.
- **WebSocket:** `/api/ws?token=` with `subscribe`, `resume` and live channel events carrying a dense
  per-channel `event_id`.

Do not build anything else from the task description in this run. Never stop to polish one
feature while a check elsewhere is still failing.

## Time plan (50 minutes; run `bash /app/timer.sh` to see what is left)

| Minutes | Work |
|---|---|
| 0 to 4 | Read this file and `common/architecture.md`. Write `start.sh`, `requirements.txt`, then `pip install -r /app/requirements.txt` |
| 4 to 12 | The basics |
| 12 to 45 | Spec C, one step at a time |
| last 5 | Stop adding features: restart, run every check, run `ready.sh`, submit (see "Before you submit") |

If you fall more than 5 minutes behind, move on once the current step's check shows no more than 2
failures, and come back to them at the end.

## Order of work

1. Read `common/architecture.md`. Create `/app/start.sh` exactly as given there (`chmod +x`), `/app/requirements.txt`, `core.py` and `server.py`.
2. Build the basics listed under Scope.
3. For each step C.1 to C.5: read `c/c-<step>.md`, implement it, run `bash /spec/common/restart.sh`, then `bash /spec/c/checks/check_c_<step>.sh`, and fix failures one at a time, lowest rule number first.
4. After every step, rerun the checks of the earlier steps of the same component. If anything that passed now fails, fix it before moving on.

Do not start a step while a check from an earlier step fails (except under the time rule above).

## How to verify

Run each of these as a single command:

```bash
bash /spec/common/restart.sh                 # after every code change (--fresh after a schema change)
bash /spec/common/ready.sh                   # what is still failing or not rerun since your last change
bash /spec/c/checks/check_c_1.sh            # likewise check_c_2.sh ... check_c_5.sh
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
  `/app/start.sh` in a fresh container. Anything installed or started by hand is not there, so start
  every service, the IRC gateway included, from `start.sh`, and start `start.sh` only through `restart.sh`.

## Before you submit

You may run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` only when all of these are true, from output
you actually saw after your last code change and a `restart.sh`:

- every C check, run one at a time, ends with `0 failed` (`check_c_1.sh` 9, `check_c_2.sh` 8, `check_c_3.sh` 6, `check_c_4.sh` 7, `check_c_5.sh` 4 rules);
- `GET /api/health` returns `200` on ports `8000`, `8001` and `8002`;
- `bash /spec/common/ready.sh` prints `READY TO SUBMIT`. It records every complete check run against
  the current code in `/app`, so it lists any check that is failing, or that you have not rerun since your
  last change. If it prints `NOT READY`, rerun or fix what it lists; do not submit.

**Time-out rule:** when `bash /app/timer.sh` shows `0:04` or less, stop adding features. Run
`restart.sh`, confirm all three nodes answer `/api/health`, and submit. Never submit while the cluster
does not boot.

These checks are necessary, not sufficient. The full task in the grading contract still applies.
