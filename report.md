# Milestone 2 report

- Name: Rohit Ramesh
- UNI: rr3713

All results are from my own checks. The check results below come from rerunning every step check
(and, for Runs 5.1 and 5.2, the A and B regression suite) on each run's final `artifacts/app/` in a
fresh container built like the task container (`analysis/verify_app.sh`, `analysis/regression_app.sh`;
outputs in `analysis/verify/`). In every run they match the last result the agent itself saw for
each check. Transcript counts come from `analysis/transcript_stats.py` and
`analysis/transcript_evidence.py` (outputs in `analysis/*.json`). The spec and checks were frozen
before the first graded run, so Spec C and its checks are identical in Runs 1.1, 2.1, 3, 4 and 5.x,
and likewise for D and E.

## Runs

One row per repeat of each run (22 in total). "Run" is the run in the run plan in README Setup.
"Ran checks" counts every invocation of a step check or of the regression suite; for Runs 5.1 and
5.2 the number of regression-suite runs is given in brackets.

| Run | Component | Repeat | Job folder | Check results | Cost | Ran checks (times) | Submitted with failures? | Problems |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1.1 | C | 1 | `jobs/2026-11-06__09-13-24` | C: 34 passed, 0 failed | $0.98 | 38 | No | From command 51 of 80, ran the rule code directly (`FEEDBACK=verbose python3 /spec/c/checks/c_rules.py <step>`), which skips `lib.sh` and printed per-rule failures for C.2 to C.4 |
| 1.1 | C | 2 | not run | n/a | n/a | n/a | n/a | Not run: OpenAI credit limit reached |
| 1.2 | D | 1 | `jobs/2026-11-06__09-30-58` | D: 14 passed, 0 failed | $0.42 | 18 | No | None |
| 1.2 | D | 2 | not run | n/a | n/a | n/a | n/a | Not run: OpenAI credit limit reached |
| 1.3 | E | 1 | `jobs/2026-11-06__09-59-32` | E: 37 passed, 0 failed | $0.92 | 25 | No | None |
| 1.3 | E | 2 | not run | n/a | n/a | n/a | n/a | Not run: OpenAI credit limit reached |
| 2.1 | C | 1 | `jobs/2026-11-06__09-06-08` | C: 34 passed, 0 failed | $0.41 | 21 | No | None |
| 2.1 | C | 2 | not run | n/a | n/a | n/a | n/a | Not run: OpenAI credit limit reached |
| 2.2 | D | 1 | `jobs/2026-11-06__09-24-41` | D: 14 passed, 0 failed | $0.19 | 15 | No | None |
| 2.2 | D | 2 | not run | n/a | n/a | n/a | n/a | Not run: OpenAI credit limit reached |
| 2.3 | E | 1 | `jobs/2026-11-06__09-39-14` | E: 37 passed, 0 failed | $1.50 | 48 | No | E.5 (browser UI) needed 17 check runs |
| 2.3 | E | 2 | not run | n/a | n/a | n/a | n/a | Not run: OpenAI credit limit reached |
| 3 | C, D, E | 1 | `jobs/2026-11-06__08-24-14` | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $1.17 | 64 | No | None |
| 3 | C, D, E | 2 | not run | n/a | n/a | n/a | n/a | Not run: OpenAI credit limit reached |
| 4 | C, D, E | 1 | `jobs/2026-11-06__08-39-31` | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $2.15 | 68 | No | E.5 (browser UI) needed 19 check runs |
| 4 | C, D, E | 2 | not run | n/a | n/a | n/a | n/a | Not run: OpenAI credit limit reached |
| 5.1 | A to E | 1 | `jobs/2026-11-06__08-02-57` | A and B: 0 broken; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $2.63 | 60 (16) | No | Did not rerun the regression suite after each C/D/E step, only once at the end |
| 5.1 | A to E | 2 | not run | n/a | n/a | n/a | n/a | Not run: OpenAI credit limit reached |
| 5.2 | A to E | 1 | `jobs/2026-11-05__11-24-42` | A and B: 0 broken; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $1.54 | 52 (10) | No | Once tried to run a plain A check (`check_a_1.sh`), which does not exist in this run; reran the suite only at the end |
| 5.2 | A to E | 2 | `jobs/2026-11-05__11-39-55` | A and B: 0 broken; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $2.78 | 73 (12) | No | `/spec` also listed four empty folders (no files); reran the suite only at the end |
| 5.3 | A to E | 1 | `jobs/2026-11-05__10-44-14` | A: 48 passed, 0 failed; B: 52 passed, 0 failed; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $2.63 | 94 | No | B1.6 (topic permission) broke while C to E were built; found by a final A/B sweep and fixed before submitting |
| 5.3 | A to E | 2 | `jobs/2026-11-05__11-06-03` | A: 48 passed, 0 failed; B: 52 passed, 0 failed; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $2.23 | 97 | No | Reran A and B checks only once after they first passed, in a final sweep of every check |

Every run that was made finished inside the 50 minutes, submitted only after its last check outputs
were all passing, and had no format errors and no crash (`exception_info: null`). No agent wrote to
`/spec`.

Because every completed run passes 100% of its own checks, the final check results cannot separate
the settings. The analyses below therefore also compare the effort each run needed to get there:
cost, model calls, agent minutes, and how many check runs still showed failures ("failing runs").

## Experiments

### 1) Feedback

> Do you find that adding detailed output to the checks helps the agent?

#### Runs compared and why

Runs 1.1, 1.2 and 1.3 (`score_only`) against Runs 2.1, 2.2 and 2.3 (`verbose`). Each pair has the
same component, the same spec and the same checks; only the `FEEDBACK` setting in `lib.sh` differs.
So any difference in a pair comes from the feedback mode (plus run-to-run noise).

#### Supporting results

| Component | Run | Mode | Check results | Cost | Model calls | Agent minutes | Check runs (failing) | Reads of the rule code |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C | 1.1 | score_only | C: 34 passed, 0 failed | $0.98 | 80 | 8.1 | 36 (21) | 5 |
| C | 2.1 | verbose | C: 34 passed, 0 failed | $0.41 | 44 | 4.5 | 20 (10) | 6 |
| D | 1.2 | score_only | D: 14 passed, 0 failed | $0.42 | 50 | 6.1 | 18 (3) | 1 |
| D | 2.2 | verbose | D: 14 passed, 0 failed | $0.19 | 27 | 4.0 | 12 (0) | 2 |
| E | 1.3 | score_only | E: 37 passed, 0 failed | $0.92 | 81 | 13.5 | 25 (13) | 24 |
| E | 2.3 | verbose | E: 37 passed, 0 failed | $1.50 | 106 | 17.5 | 48 (37) | 8 |

Failing check runs per step:

| Run | Step 1 | Step 2 | Step 3 | Step 4 | Step 5 | Step 6 |
| --- | --- | --- | --- | --- | --- | --- |
| 1.1 (C) | 4 of 8 | 9 of 11 | 7 of 10 | 1 of 4 | 0 of 3 | |
| 2.1 (C) | 7 of 9 | 1 of 3 | 1 of 3 | 1 of 3 | 0 of 2 | |
| 1.3 (E) | 2 of 3 | 6 of 7 | 0 of 1 | 0 of 1 | 1 of 2 | 4 of 5 |
| 2.3 (E) | 5 of 6 | 9 of 11 | 3 of 5 | 2 of 4 | 15 of 17 | 3 of 5 |

#### Answer

Detailed output did not change the final result: every run passed all of its checks in both modes.
It did change the effort for C and D. With `verbose` output, the agent used about half the cost,
calls and time, and about half as many failing check runs (C: 10 against 21; D: 0 against 3).
Under `score_only` the agent only learns that a step is failing, not which rule. In 1.1 it retried
C.2 and C.3 eleven and ten times (nine and seven of them failing). In 2.1 each of those steps failed
once and was then fixed.

The agents also worked hard to get the detail back when it was withheld, which is itself evidence
that they need it. All three `score_only` agents did so:
- 1.1 first tried `FEEDBACK=verbose bash check_c_1.sh`, which had no effect because `lib.sh` sets the
  mode. From command 51 of 80 it then ran the rule code directly (`FEEDBACK=verbose python3
  /spec/c/checks/c_rules.py 3`), which skips `lib.sh` and printed the failing rule names for C.2 to
  C.4. So its second half was partly verbose, and it still cost more than 2.1.
- 1.2 wrote its own probe scripts with the check helpers.
- 1.3 read the rule code (`e_rules.py`) 24 times and built each UI step to match the exact selectors
  and assertions there, so E.3 to E.5 passed almost on the first run.

E is the one component where the `verbose` run cost more ($1.50 against $0.92). The 2.3 agent read
the rule code only 8 times. It worked from the failure messages instead and iterated 17 times on E.5
(browser sign-up, login and switching), where each failure message names one missing element at a time.

So detailed output helps: when the failure message is the agent's main source of information (C, D),
it halves the iterations. When it is withheld, the agent recovers the same information from the check
source in `/spec`, or by running the check code directly. That substitution worked best for E, whose
browser checks report one missing element per run. Feedback helps by saving iterations, not by making
a component passable.

#### Consistency across repeats

Each of these runs has one repeat, so the per-component differences are single observations. The
two repeats of Run 5.2, which had identical settings, differed by 1.8 times in cost ($1.54 against
$2.78) and by over 3 times in failing E check runs (6 against 20). The E reversal is within that
noise. The C and D results (2.2 to 2.4 times cheaper with `verbose`, and fewer failing runs in total
for both components) are larger and point the same way, so I treat them as the more reliable finding.
The C comparison understates the effect, because 1.1 got per-rule output for part of its run.

#### Other evidence

- Through the wrapper, the `score_only` output really is one line per check (`RESULT: x passed, y failed`).
  Before it bypassed the wrapper, the 1.1 agent narrowed C.2 down with its own diagnostic scripts ("I'll
  run C.2 one rule at a time to isolate the four failures"), work that 2.1 skipped by reading its `FAIL`
  lines.
- Run 1.1's direct run of `c_rules.py` worked because the check code treats a missing `FEEDBACK`
  setting as `verbose`, and only the wrappers set it to `score_only`. This is a weakness in my
  `score_only` setup. Making the check code default to `score_only` would have closed it.
- D is the component with the least benefit (3 failing runs in total under `score_only`).
  Its rules are behavioural (kill a node, kill Redis, measure latency), and they passed with few
  retries in every run once the cluster was built as the architecture describes.

### 2) Tests as the spec

> Can the checks alone replace the written rules?

#### Runs compared and why

Run 3 (Specs C, D and E plus their `verbose` checks) against Run 4 (the same `verbose` checks, the
README and no spec files). Both build all three components together with the same checks and
feedback mode, so the only difference is whether the written rules (and the architecture they
reference) are present.

#### Supporting results

| Run | What the agent had | Check results | Cost | Model calls | Agent minutes | Failing check runs C / D / E | Reads of the rule code |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3 | specs + checks | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $1.17 | 88 | 11.8 | 16 / 0 / 14 | 10 |
| 4 | checks only | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $2.15 | 125 | 24.6 | 13 / 1 / 26 | 10 |

| Step | Run 3 check runs (failing) | Run 4 check runs (failing) |
| --- | --- | --- |
| E.5 browser auth and navigation | 7 (5) | 19 (17) |
| E.2 IRC basics | 2 (0) | 5 (3) |
| D.3 performance | 2 (0) | 3 (1) |

Behaviour outside the C, D and E checks (the A and B regression suite on the same apps; neither run
was asked to build all of A and B):

| Run | A and B regression |
| --- | --- |
| 3 | 49 broken |
| 4 | 68 broken |

#### Answer

On the checks, yes: Run 4 reached the same 85 of 85 C, D and E rules as Run 3. It did not do so as
efficiently: it cost 1.8 times as much and took twice the time. Almost all of the extra work was in
the steps where the rules describe a design rather than a single response. Examples are the UI
(E.5: 19 check runs against 7) and the IRC gateway (E.2), where the agent had to discover the
expected structure one failure at a time.

The checks can only replace the rules they actually exercise. Behaviour the checks do not cover was
worse without the written spec: on the A and B rules, which neither run was told to build in full,
Run 4's app broke 68 against Run 3's 49. With the written specs, the agent picks up the conventions
(envelopes, status codes, membership rules) once and applies them everywhere. Without them it only
implements what a check forces. Since the hidden tests cover more than my checks do, I would not
replace the written rules with the checks. The checks work as a substitute for verification, not for
the specification.

#### Consistency across repeats

One repeat of each run, so the cost and time differences are single observations. They are about the
size of the run-to-run variation seen in Run 5.2 (1.8 times), so the cost difference alone is not
conclusive. The E.5 difference (17 failing runs against 5) is larger than the largest repeat-to-repeat
difference for the same step in 5.2 (5 against 11), so it is the stronger evidence.

#### Other evidence

- Both agents read the rule code 10 times. In Run 4 the rule code was the only written statement of
  the rules. Right after the README, its next two commands printed `c_rules.py` and `d_rules.py` and
  listed every rule in `e_rules.py`, using the check code as the spec.
- Run 4 also had no `architecture.md`, so it chose its own stack (it pinned different package
  versions and a different `start.sh` from the one the architecture gives). Its E.5 loop was spent
  discovering the UI structure one failing element at a time, which `e-4.md` and `e-5.md` state up front.

### 3) Scale

> Do you find that a component passes fewer of its checks when the agent builds it together with
> other components, rather than on its own?

#### Runs compared and why

Runs 2.1, 2.2 and 2.3 (each component alone, `verbose`) against Run 3 (C, D and E together,
`verbose`), with Runs 5.1 to 5.3 (all five components, `verbose`) as a larger scale. Same checks and
feedback mode in all of them; only the number of components built in one run changes.

#### Supporting results

| Component | Alone (2.x) | Together, C to E (3) | Whole app, A to E (5.1 / 5.2 / 5.2 / 5.3 / 5.3) |
| --- | --- | --- | --- |
| C passed | 34 of 34 | 34 of 34 | 34 of 34 in all five |
| C failing check runs | 10 of 20 | 16 of 28 | 4, 6, 11, 9, 12 |
| D passed | 14 of 14 | 14 of 14 | 14 of 14 in all five |
| D failing check runs | 0 of 12 | 0 of 6 | 0 in all five |
| E passed | 37 of 37 | 37 of 37 | 37 of 37 in all five |
| E failing check runs | 37 of 48 | 14 of 26 | 10, 6, 20, 22, 15 |

| Run | Components | Cost | Agent minutes |
| --- | --- | --- | --- |
| 2.1 + 2.2 + 2.3 | C, D, E separately | $2.10 in total | 26.0 in total |
| 3 | C, D, E together | $1.17 | 11.8 |
| 5.x (mean of five) | A to E | $2.36 | 17.9 |

#### Answer

No. Every component passed all of its checks whether it was built alone, with the other two, or as
part of the whole app. Building together did not make any component pass fewer checks, and it did not
consistently make a component harder to get right. C needed somewhat more failing runs in Run 3
than alone (16 against 10), but E needed far fewer (14 against 37), and D needed none anywhere.

Building C, D and E together in one run was cheaper than building them in three separate runs
($1.17 against $2.10). Each separate run has to rebuild the shared basics (login, workspaces,
channels, messages) and the cluster before starting its component. At this size the staged layout,
one check per step and a fixed order, keeps the agent on one step at a time, so the extra components
do not appear to interfere.

#### Consistency across repeats

Runs 2.x and 3 have one repeat each. The whole-app runs (five runs across two settings and repeats)
all pass every C, D and E rule, and their failing-run counts for E (6 to 22) bracket both the alone
and together values, so the effort differences between alone and together are inside run-to-run
variation. The "passes the same number of checks" result is consistent across all seven runs that
built these components.

#### Other evidence

- In every setting most of the failing check runs come from one step, E.5 (the browser sign-up, login
  and navigation check): 15 of 37 in 2.3, 5 of 14 in Run 3, and 5 to 11 in the whole-app runs. How hard
  a run is depends mostly on how quickly the agent gets the UI right, not on how many other components
  it is building.
- D needed no failing check run in any setting that had the written spec, alone or together (2.2, 3
  and all five whole-app runs). The shared architecture (one database, delivery by polling) makes the
  robustness rules hold once the cluster boots, whatever else is built alongside.

### 4) Regression

> a) Do you find that building components C, D, and E breaks features from A and B that were
> already working?

> b) Does it make a difference whether the agent has Specs A and B or only their regression
> tests?

> c) Does it make a difference whether the A and B checks are given as a regression suite or as
> plain checks?

#### Runs compared and why

- a) All whole-app runs (5.1, 5.2 twice, 5.3 twice). In each, the agent builds A and B first and must
  keep them passing while it builds C, D and E. I located the point in each transcript where A and B
  first fully passed, then looked for any later A or B failure, and checked the final app.
- b) Run 5.1 (regression suite only) against Run 5.2 (regression suite plus Specs A and B). The only
  difference is whether the A and B rules are written out.
- c) Run 5.2 (Specs A and B plus the regression suite) against Run 5.3 (Specs A and B plus the plain
  staged A and B checks). The only difference is the form the A and B checks take.

#### Supporting results

| Run | Repeat | A and B first all passing (agent minute, command) | A/B check runs before / after that point | A/B failures after that point | Final A and B (fresh container) | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| 5.1 | 1 | 7.4 min, command 56 of 135 | 15 / 1 (suite) | 0 | 0 broken | $2.63 |
| 5.2 | 1 | 5.0 min, command 36 of 95 | 9 / 1 (suite) | 0 | 0 broken | $1.54 |
| 5.2 | 2 | 7.5 min, command 54 of 135 | 11 / 1 (suite) | 0 | 0 broken | $2.78 |
| 5.3 | 1 | 5.0 min, command 45 of 142 | 21 / 14 (staged checks) | 1 (B1.6) | 0 broken | $2.63 |
| 5.3 | 2 | 6.0 min, command 52 of 117 | 33 / 9 (staged checks) | 0 | 0 broken | $2.23 |

The checks after that point were, in every run, one final re-check just before submitting: a single
suite run in 5.1 and 5.2, and a sweep of all nine A and B step checks (four commands in repeat 1,
one loop in repeat 2) in 5.3.

Regression-suite counts on the way to `0 broken`:

| Run | Broken at each suite run until clean |
| --- | --- |
| 5.1 repeat 1 | 86, 85, 54, 36, 23, 16, 14, 11, 10, 10, 6, 5, 2, 1, 0 |
| 5.2 repeat 1 | 60, 33, 20, 17, 14, 15, 6, 3, 0 |
| 5.2 repeat 2 | 85, 59, 37, 25, 18, 11, 7, 4, 1, 1, 0 |

#### Answer

a) Rarely, and never in a submitted app. All five whole-app apps pass every A and B rule in a fresh
container. I observed one real break: in 5.3 repeat 1, the agent changed channel permissions while
building C to E. That left `PATCH /api/channels/{id}` letting a workspace member who is not in the
channel change its topic (B1.6: expected 403, got 200). It was found only because the agent ran a
final sweep of all A and B checks, and it was fixed in one edit. The bigger finding is behavioural.
The 5.1 and 5.2 READMEs told the agent to rerun the regression suite after every step, but none of
the three agents did. The 5.3 README asked only for earlier steps of the same component to be
rerun, with all A and B checks as a condition for submitting. In all five runs the agent brought A
and B to clean, built C, D and E checking only their own steps, and re-checked A and B once just
before submitting. So the runs show few breaks partly because there were few chances to observe one in
between. Every final re-check either was clean or, in the one case above, was fixed before submitting.

b) Very little in my runs. Both settings reached `0 broken`, both submitted clean apps, and both
spent most of their A/B effort in the first 5 to 8 minutes. With the specs, one repeat (5.2 repeat 1)
started from a much better first draft (60 broken at its first suite run, against 85 and 86 in the
other two) and reached clean faster (9 suite runs against 15). The other 5.2 repeat looked like 5.1
(85 broken first, 11 suite runs). Without the specs, the agent recovered the rules from the
regression suite's `REGRESSION FAIL` messages and by reading `regression_rules.py`. Because the
suite runs in `verbose` mode, each failure names the rule and the expected and actual responses, so
the tests carried most of the information the specs would. Specs gave a better starting point in one
of two repeats.

c) It made no difference to the outcome (every app finished with all A and B rules passing). It did
change how the agent used the checks. With plain checks (5.3), the agent ran A and B as nine separate
step checks, 21 and 33 check runs before they were clean, and a full re-check meant nine checks. With
the suite (5.2), one command covered every A and B rule. The agent ran it 9 and 11 times to reach clean,
and the re-check at the end was a single command. Time to clean was the same (5.0 and 6.0 minutes with
plain checks, against 5.0 and 7.5 with the suite). The suite is the cheaper way to re-check A and B,
which is why only the suite setting's README asks for it after every step. In practice, agents with
either form re-checked only at the end, so this advantage was not used. The only
regression observed was in the plain-checks setting. That is because the 5.3 agent re-ran B.1 there,
not because plain checks cause more breaks: a suite run at the same point would have reported the same
`REGRESSION FAIL channels B1.6` line.

#### Consistency across repeats

The final result (`0 broken`, all A and B rules passing) is identical across all five whole-app runs.
Effort varies within a setting about as much as between settings (5.2's two repeats took 9 and 11
suite runs and cost $1.54 and $2.78, while 5.1 took 15 and cost $2.63), so the differences in b) and
c) are not large enough to separate the settings with one or two repeats. The skipped per-step reruns
are consistent across all five whole-app runs. The one observed break happened in one of two 5.3
repeats.

#### Other evidence

- In 5.2 repeat 1, the agent once tried `bash /spec/a/checks/check_a_1.sh`, which does not exist in
  that run. When it has the A and B specs, the agent expects plain staged checks to go with them.
- The transcripts show the agents treating the A and B re-check as part of the submit condition
  rather than the per-step routine. For example, 5.3 repeat 1 described its sweep as "I'll rerun the
  complete Spec A sweep after the final changes", with 30 minutes still left. Every README lists the
  final re-check as a condition for submitting, and that is the instruction every agent followed. The
  "after every step" instruction in 5.1 and 5.2 was ignored.
- Runs 3 and 4, which were told to build only the basics of A and B, finish with 49 and 68 A/B rules
  broken. That shows how much of A and B the regression suite and the A and B checks cause the
  agent to build in the 5.x runs.
