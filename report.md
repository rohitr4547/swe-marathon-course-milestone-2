# Milestone 2 report

- Name: Rohit Ramesh
- UNI: rr3713

All results are from my own checks. The check results below come from rerunning every step check
(and, for Runs 3, 4 and 5.x, the A and B regression suite) on each run's final `artifacts/app/` in a
fresh container built like the task container, starting from an empty database
(`analysis/verify_app.sh`, `analysis/regression_app.sh`; outputs in `analysis/verify/`). Transcript
counts come from `analysis/transcript_stats.py` and `analysis/transcript_evidence.py` (outputs in
`analysis/*.json`).

**Setup common to all 22 runs.**
- **Spec.** The spec and checks were frozen before the first run, so Spec C and its checks are
  identical in Runs 1.1, 2.1, 3, 4 and 5.x, and likewise for D and E. Every run's `/spec` includes
  `common/ready.sh`, a submit gate: each complete check run records its result together with a
  fingerprint of the code in `/app`, and the gate prints `READY TO SUBMIT` only when every check has
  passed on the current code.
- **Model.** Every run used GPT 5.6 Luna through OpenRouter, pinned to the OpenAI provider, with
  reasoning effort `minimal`. The other settings failed: with none, or with `low`, the agent imagined
  a whole session on its first call and submitted an empty `/app`; with effort `none` it lost track
  of the task on long runs.
- **Cost.** "Cost" is mini-swe-agent's estimate with cached prompt tokens priced at the cache-read
  rate. It is within about 10% of what OpenRouter billed, and it is the figure the harness's $3
  per-run limit uses.
- **Excluded attempts.** Attempts in which the agent never did any work were repeated and are not
  reported: setup or network failures, and two attempts that submitted an empty `/app` after a
  single model call. Every attempt in which the agent worked is reported.

## Runs

One row per repeat of each run (22 in total). "Run" is the run in the run plan in README Setup.
"Check results" covers the components the run builds. "Ran checks" counts every invocation of a step
check or of the regression suite; for Runs 5.1 and 5.2 the number of regression-suite runs is given
in brackets. "Submitted with failures?" uses the last result the agent itself saw for each check.

| Run | Component | Repeat | Job folder | Check results | Cost | Ran checks (times) | Submitted with failures? | Problems |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1.1 | C | 1 | `jobs/2026-11-08__12-29-13` | C: 34 passed, 0 failed | $0.18 | 34 | No | Copied the checks to `/tmp`, switched the copy to `verbose` and got per-rule output twice |
| 1.1 | C | 2 | `jobs/2026-11-08__12-46-35` | C: 34 passed, 0 failed | $0.14 | 57 | No | Tried five times to get `verbose` output, without success |
| 1.2 | D | 1 | `jobs/2026-11-08__11-41-26` | D: 14 passed, 0 failed | $0.05 | 10 | No | None |
| 1.2 | D | 2 | `jobs/2026-11-08__11-57-09` | D: 14 passed, 0 failed | $0.03 | 6 | No | None |
| 1.3 | E | 1 | `jobs/2026-11-08__13-45-40` | E: 37 passed, 0 failed | $0.35 | 93 | No | Ran a `verbose` copy of the E checks 17 times |
| 1.3 | E | 2 | `jobs/2026-11-08__14-16-02` | E: 37 passed, 0 failed | $0.29 | 68 | No | Ran a `verbose` copy of the E checks 17 times; never ran `ready.sh` |
| 2.1 | C | 1 | `jobs/2026-11-08__12-04-30` | C: 34 passed, 0 failed | $0.09 | 35 | No | None |
| 2.1 | C | 2 | `jobs/2026-11-08__12-15-52` | C: 0 passed, 34 failed | $0.14 | 40 | No | Passed every check in its own container, but on a fresh database every registration fails (the handler reports any database error as `409 duplicate`) |
| 2.2 | D | 1 | `jobs/2026-11-08__11-26-42` | D: 14 passed, 0 failed | $0.01 | 4 | No | None |
| 2.2 | D | 2 | `jobs/2026-11-08__11-33-04` | D: 14 passed, 0 failed | $0.05 | 9 | No | None |
| 2.3 | E | 1 | `jobs/2026-11-08__13-00-56` | E: 37 passed, 0 failed | $0.13 | 52 | No | None |
| 2.3 | E | 2 | `jobs/2026-11-08__13-19-18` | E: 37 passed, 0 failed | $0.27 | 94 | No | Submitted while `ready.sh` listed four checks not rerun since its last edit |
| 3 | C, D, E | 1 | `jobs/2026-11-08__14-51-24` | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $0.30 | 94 | No | None |
| 3 | C, D, E | 2 | `jobs/2026-11-08__15-20-46` | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $0.14 | 55 | No | None |
| 4 | C, D, E | 1 | `jobs/2026-11-08__15-37-08` | C: 15 passed, 19 failed; D: 3 passed, 11 failed; E: 13 passed, 24 failed | $0.32 | 71 | Yes (C.1, C.5, D.1, E.1, E.3, E.5, E.6) | Node 8001 does not come back after being killed, so most later checks find no cluster |
| 4 | C, D, E | 2 | `jobs/2026-11-08__16-12-31` | C: 32 passed, 2 failed; D: 13 passed, 1 failed; E: 27 passed, 10 failed | $0.57 | 142 | Yes (C.1, E.1, E.5, E.6) | Spent 91 check runs on E, 87 of them failing |
| 5.1 | A to E | 1 | `jobs/2026-11-08__19-25-38` | A and B: 7 broken; C: 33 passed, 1 failed; D: 14 passed, 0 failed; E: 26 passed, 11 failed | $0.44 | 120 (31) | Yes (regression, C.4, E.5, E.6) | Never brought the regression suite to `0 broken` (85 down to 7) |
| 5.1 | A to E | 2 | `jobs/2026-11-08__19-59-01` | A and B: 47 broken; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 35 passed, 2 failed | $0.33 | 98 (2) | Yes (regression, D.3, E.1, E.6) | Ran the regression suite only twice (70, 68 broken) and never returned to A and B |
| 5.2 | A to E | 1 | `jobs/2026-11-08__18-24-52` | A and B: 0 broken; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $0.51 | 148 (36) | No | None |
| 5.2 | A to E | 2 | `jobs/2026-11-08__19-01-15` | A and B: 0 broken; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $0.29 | 84 (18) | No | None |
| 5.3 | A to E | 1 | `jobs/2026-11-08__16-59-11` | A: 48 passed, 0 failed; B: 52 passed, 0 failed; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 31 passed, 6 failed | $0.65 | 250 | Yes (E.6) | Building E.6 broke five A and B rules, which it found and fixed; submitted with E.6 failing |
| 5.3 | A to E | 2 | `jobs/2026-11-08__17-51-13` | A: 48 passed, 0 failed; B: 52 passed, 0 failed; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $0.49 | 141 | No | None |

No run crashed, timed out or hit the cost limit, and no agent wrote to `/spec`. Fifteen of the 22
agents reached `READY TO SUBMIT` before submitting.

Sixteen of the 22 apps pass every check of the components they were asked to build. For Run 5.3,
repeat 2 passes all 185 rules, and repeat 1 passes everything except six E rules. Both Run 5.3 apps
also pass all ten groups of a separate probe of COMPONENTS.md requirements that my checks do not test
directly (`analysis/probe_components.py`, outputs in `analysis/probe/`).

## Experiments

### 1) Feedback

> Do you find that adding detailed output to the checks helps the agent?

#### Runs compared and why

Runs 1.1, 1.2 and 1.3 (`score_only`) against Runs 2.1, 2.2 and 2.3 (`verbose`). Each pair has the
same component, the same spec and the same checks; only the `FEEDBACK` setting in `lib.sh` differs.
So any difference within a pair comes from the feedback mode, plus run-to-run noise.

#### Supporting results

| Component | Repeat | Run | Mode | Check results | Cost | Model calls | Agent minutes | Check runs (failing) | Reads of the rule code | Per-rule output obtained despite `score_only` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C | 1 | 1.1 | score_only | C: 34 passed, 0 failed | $0.18 | 92 | 11.5 | 33 (24) | 11 | 2 times |
| C | 1 | 2.1 | verbose | C: 34 passed, 0 failed | $0.09 | 56 | 6.2 | 31 (21) | 3 | n/a |
| C | 2 | 1.1 | score_only | C: 34 passed, 0 failed | $0.14 | 79 | 8.5 | 56 (43) | 7 | 0 (5 attempts) |
| C | 2 | 2.1 | verbose | C: 0 passed, 34 failed | $0.14 | 79 | 7.6 | 39 (29) | 2 | n/a |
| D | 1 | 1.2 | score_only | D: 14 passed, 0 failed | $0.05 | 37 | 2.6 | 10 (5) | 3 | 0 |
| D | 1 | 2.2 | verbose | D: 14 passed, 0 failed | $0.01 | 11 | 1.0 | 4 (1) | 0 | n/a |
| D | 2 | 1.2 | score_only | D: 14 passed, 0 failed | $0.03 | 24 | 1.9 | 6 (3) | 2 | 0 |
| D | 2 | 2.2 | verbose | D: 14 passed, 0 failed | $0.05 | 35 | 2.5 | 9 (6) | 2 | n/a |
| E | 1 | 1.3 | score_only | E: 37 passed, 0 failed | $0.35 | 157 | 24.8 | 92 (56) | 11 | 17 times |
| E | 1 | 2.3 | verbose | E: 37 passed, 0 failed | $0.13 | 76 | 13.4 | 51 (29) | 3 | n/a |
| E | 2 | 1.3 | score_only | E: 37 passed, 0 failed | $0.29 | 151 | 30.2 | 67 (55) | 28 | 17 times |
| E | 2 | 2.3 | verbose | E: 37 passed, 0 failed | $0.27 | 134 | 20.9 | 89 (73) | 5 | n/a |

#### Answer

Yes. The clearest evidence is what the agents did when the detail was withheld: they went out of
their way to get it back.
- In four of the six `score_only` runs, the agent tried to obtain per-rule output.
- In three, it succeeded, by copying the check files to `/tmp`, editing the copied `lib.sh` to
  `FEEDBACK=verbose` and running the copy. Both E runs did this 17 times each.
- The `score_only` agents also read the rule code more often in five of six pairs (11 against 3, 7
  against 2, 3 against 0, 11 against 3, 28 against 5; one pair tied at 2).

An agent that did not need the failure details would not rebuild them by hand.

Recovering the detail costs effort. With `verbose` output the run was cheaper in four of the six
pairs (one tied, one was dearer). The largest gaps were on E, where each failure names a missing UI element: the `score_only`
runs took 24.8 and 30.2 agent minutes against 13.4 and 20.9.

In the final results the mode made no difference: every `score_only` app and five of the six
`verbose` apps pass all of their checks. The one exception, 2.1 repeat 2, is a defect that only
appears on a fresh database (see "Other evidence"), not a feedback effect. Detailed output therefore
helps by saving the agent work, not by deciding whether a component gets built.

#### Consistency across repeats

All 22 runs used the same model access and settings, so the repeats are directly comparable. Some
findings hold in both repeats: the `score_only` E runs worked around the mode 17 times each, read the
rule code more, and took longer than the `verbose` E runs. Others vary: the D comparison reverses
between repeats ($0.05 against $0.01, then $0.03 against $0.05), and the C costs tie in repeat 2. D
runs are short (1 to 3 minutes), so their differences are within noise. The E result is the
consistent one.

#### Other evidence

- The workaround shows a limit of my `score_only` setup. The checks must ship in `/spec` as runnable
  code, so a determined agent can always copy them and change the mode. The spec reads the mode from
  the check's own `lib.sh`, which stops a plain `FEEDBACK=verbose` override, but not an edited copy.
  A truly blind setting would need the checks to run outside the agent's reach.
- In 2.1 repeat 2, every check passed in the agent's own container and `ready.sh` printed
  `READY TO SUBMIT`. On a fresh database, though, every registration fails, because the handler turns
  any database error into `409 duplicate`. The agent's container had a database built up step by
  step, which hid the defect. This is why I verify from an empty database: the grader does the same.

### 2) Tests as the spec

> Can the checks alone replace the written rules?

#### Runs compared and why

Run 3 (Specs C, D and E plus their `verbose` checks) against Run 4 (the same `verbose` checks and a
README, with no spec files and no `architecture.md`). Both build all three components together with
the same checks and feedback mode, so the only difference is whether the written rules are present.

#### Supporting results

| Repeat | Run | What the agent had | Check results | C + D + E passed | Cost | Model calls | Agent minutes | Failing check runs C / D / E |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 3 | specs + checks | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | 85 of 85 | $0.30 | 138 | 23.7 | 19 / 1 / 42 |
| 1 | 4 | checks only | C: 15 passed, 19 failed; D: 3 passed, 11 failed; E: 13 passed, 24 failed | 31 of 85 | $0.32 | 155 | 28.8 | 25 / 4 / 32 |
| 2 | 3 | specs + checks | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | 85 of 85 | $0.14 | 80 | 10.7 | 11 / 1 / 15 |
| 2 | 4 | checks only | C: 32 passed, 2 failed; D: 13 passed, 1 failed; E: 27 passed, 10 failed | 72 of 85 | $0.57 | 212 | 40.8 | 25 / 9 / 87 |

Behaviour outside the C, D and E checks (the A and B regression suite on the same apps; neither run
was asked to build all of A and B):

| Repeat | Run 3 | Run 4 |
| --- | --- | --- |
| 1 | 60 broken | 67 broken |
| 2 | 57 broken | 67 broken |

#### Answer

No. With the written rules, both Run 3 apps pass all 85 C, D and E rules. With only the checks, Run 4
finished at 31 and 72 of 85, using more model calls and more time. Both Run 4 agents submitted with
checks still failing.

The difference is largest where the rules describe a design rather than a single response:
- **Cluster supervision.** In repeat 1, node 8001 does not come back after being killed. That broke
  most later checks, which found no cluster. `architecture.md` prescribes the supervisor that prevents
  this.
- **The browser UI (E.5, E.6).** Repeat 2 spent 91 E check runs, 87 of them failing, discovering the
  expected UI structure one failing element at a time.

Behaviour outside the checks is also worse without the spec, in both repeats: 67 A and B rules broken
against 60 and 57. The checks can confirm that the rules are met, but they cannot replace the rules
as the description of what to build.

#### Consistency across repeats

The result is the same in both repeats. Run 3 passed everything both times; Run 4 fell short both
times and was worse on behaviour outside the checks both times. The size of the shortfall varies (54
and 13 rules missing), but its direction does not.

#### Other evidence

- Without the spec, the Run 4 agents read the rule code as their only written statement of the rules.
  They still could not recover the prescribed design (process supervision, delivery by polling) from
  assertions about its effects.

### 3) Scale

> Do you find that a component passes fewer of its checks when the agent builds it together with
> other components, rather than on its own?

#### Runs compared and why

Runs 2.1, 2.2 and 2.3 (each component alone, `verbose`) against Run 3 (C, D and E together,
`verbose`) and Runs 5.1 to 5.3 (all five components, `verbose`). Same checks and feedback mode in all
of them; only the number of components built in one run changes.

#### Supporting results

| Component | Alone (2.x), repeats 1 / 2 | Together C to E (3), repeats 1 / 2 | Whole app A to E: 5.1, 5.2, 5.3 (repeat 1 / repeat 2) |
| --- | --- | --- | --- |
| C passed (of 34) | 34 / 0 | 34 / 34 | 33 / 34, 34 / 34, 34 / 34 |
| D passed (of 14) | 14 / 14 | 14 / 14 | 14 / 14, 14 / 14, 14 / 14 |
| E passed (of 37) | 37 / 37 | 37 / 37 | 26 / 35, 37 / 37, 31 / 37 |

| Repeat | Alone: 2.1 + 2.2 + 2.3 | Together: Run 3 |
| --- | --- | --- |
| 1 | $0.24, 20.6 agent minutes | $0.30, 23.7 agent minutes |
| 2 | $0.46, 31.0 agent minutes | $0.14, 10.7 agent minutes |

#### Answer

Not when building C, D and E together. Run 3 passed every C, D and E rule in both repeats, the same
as building each component alone. The single exception in the "alone" column, C in 2.1 repeat 2, is
the fresh-database defect described in question 1, not a scale effect.

At the whole-app scale there is a moderate effect on the component built last. E, the final step,
fell short in three of the six whole-app runs (26, 35 and 31 of 37). C and D passed everywhere except
one C rule in 5.1 repeat 1. In those three runs the agent spent much of its effort on A and B first
and reached the E browser steps with less attention left. All three submitted while E.6 still
failed. So adding components did not make the agent build any one of them worse; at five components,
the last one was sometimes left unfinished.

#### Consistency across repeats

The "together equals alone" result holds in both repeats. The whole-app E shortfall appears in both
repeats (5.1 and 5.3 in repeat 1, 5.1 in repeat 2), but not in Run 5.2 at all. Cost does not point
either way: building together was dearer in repeat 1 and cheaper in repeat 2.

#### Other evidence

- E.5 and E.6, the browser steps, account for most failing check runs in every setting, and they are
  where every incomplete whole-app run stopped. How hard a run is depends mostly on how quickly the
  agent gets the UI right.
- D passed everywhere. The shared architecture (one database, delivery by polling) makes the
  robustness rules hold once the cluster boots, whatever else is built alongside.

### 4) Regression

> a) Do you find that building components C, D, and E breaks features from A and B that were
> already working?

> b) Does it make a difference whether the agent has Specs A and B or only their regression
> tests?

> c) Does it make a difference whether the A and B checks are given as a regression suite or as
> plain checks?

#### Runs compared and why

- a) All six whole-app runs. In each, the agent builds A and B first and must keep them passing while
  it builds C, D and E. I looked for any A or B rule that failed after having passed, noted which step
  the agent was on at the time, and checked the final app.
- b) Run 5.1 (regression suite only) against Run 5.2 (regression suite plus Specs A and B). The only
  difference is whether the A and B rules are written out.
- c) Run 5.2 (Specs A and B plus the regression suite) against Run 5.3 (Specs A and B plus the plain
  staged A and B checks). The only difference is the form the A and B checks take.

#### Supporting results

| Run | Repeat | A and B first all passing (agent minute) | A/B check runs before / after that point | A and B rules that broke after passing | Final A and B (fresh container) | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| 5.1 | 1 | never (85 broken at first, 7 at the end) | 35 / 0 (suite) | none observed | 7 broken | $0.44 |
| 5.1 | 2 | never (70, then 68 broken) | 3 / 0 (suite) | none observed | 47 broken | $0.33 |
| 5.2 | 1 | 28.7 | 37 / 1 (suite) | none after passing | 0 broken | $0.51 |
| 5.2 | 2 | 7.2 | 18 / 1 (suite) | none after passing | 0 broken | $0.29 |
| 5.3 | 1 | 7.9 | 41 / 48 (staged checks) | A3.8, A3.10, A3.11, A4.9, B2.11 while building E.6; all fixed | 0 broken | $0.65 |
| 5.3 | 2 | 27.0 | 41 / 0 (staged checks) | none after passing | 0 broken | $0.49 |

Regression-suite counts at each full suite run:

| Run | Broken at each suite run |
| --- | --- |
| 5.1 repeat 1 | 85, 67, 58, 54, 53, 47, 37, 29, 28, 27, 26, 24, 20, 18, 14, 14, 15, 15, 15, 12, 11, 10, 10, 10, 10, 9, 9, 9, 8, 7, 7 |
| 5.1 repeat 2 | 70, 68 |
| 5.2 repeat 1 | 70, 56, 53, 49, 41, 37, 25, 25, 23, 21, 21, 21, 34, 20, 18, 17, 16, 14, 14, 14, 14, 14, 11, 11, 8, 7, 6, 5, 5, 4, 4, 3, 3, 3, 0, 0 |
| 5.2 repeat 2 | 72, 61, 51, 41, 34, 25, 23, 33, 13, 10, 8, 9, 6, 5, 2, 1, 0, 0 |

#### Answer

a) Yes, sometimes. In 5.3 repeat 1, building the last UI step (E.6) broke five A and B rules that
had been passing: three role and permission rules in A.3, one invitation rule in A.4, and one channel
rule in B.2. The agent found them by re-running its A and B checks and fixed all five; the final app
passes all 100 A and B rules. The suite counts also rise in three places (21 to 34 and 23 to 33 in
5.2, 14 to 15 in 5.1), but each rise came before the agent had run any C, D or E check. Those were
changes made while finishing A and B themselves, not breaks caused by C, D and E. So the breakage
exists, but it is caught when the agent re-runs the A and B checks, and in every run that brought A
and B to a passing state the final app still passes them.

b) Yes, clearly. With Specs A and B (5.2), both runs brought the suite to `0 broken`, and both final
apps pass every A and B rule. With only the regression suite (5.1), neither run did: the final apps
have 7 and 47 broken. 5.1 repeat 1 worked the suite down from 85 broken to 7 over 31 runs and never
finished. 5.1 repeat 2 ran the suite twice and moved on without returning. The failure messages name
each broken rule and what was expected, but without the written rules the agent had to rebuild A and
B one failure at a time, and that did not converge within the time it gave A and B.

c) It made no difference to the outcome: all four 5.2 and 5.3 apps pass every A and B rule. It did
change how the agents used the checks. With plain staged checks (5.3), repeat 1 kept re-running its A
and B checks while it built C to E (48 runs after A and B first passed), and that is how it caught
the five breaks in a). With the suite (5.2), both agents re-ran it once at the end. The suite is one
command for all 100 rules, so it is cheaper to re-run, but in these runs the agents with plain checks
re-checked more.

#### Consistency across repeats

All 22 runs used the same model access and settings, so the repeats are comparable.
- b) holds in both repeats: 5.2 passed A and B both times, 5.1 failed both times.
- The final result in c) holds in both repeats (all four apps at `0 broken`).
- The A and B break in a) appeared in one of the two 5.3 repeats; the other built C to E without
  breaking A or B.
- Time to bring A and B to a passing state varied more within a setting than between settings (7.2
  and 28.7 minutes with the suite, 7.9 and 27.0 with plain checks), so I draw no conclusion about
  speed.

#### Other evidence

- The broken counts are not monotone even while the agent works only on A and B (for example 23, 33,
  13 in 5.2 repeat 2). A fix for one rule often broke another, and the suite showed it at once.
- Runs 3 and 4, which were told to build only the basics of A and B, finish with 57 to 67 A and B
  rules broken. That shows how much of A and B the A and B checks or regression suite cause the agent
  to build in the 5.x runs.
