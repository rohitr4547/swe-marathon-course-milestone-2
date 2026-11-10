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
- **Spec.** Spec C and its checks are identical in Runs 1.1, 2.1, 3, 4 and 5.x, and likewise for D
  and E. Every run's `/spec` includes
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
| 1.3 | E | 1 | `jobs/2026-11-09__23-06-24` | E: 37 passed, 0 failed | $0.16 | 50 | No | Tried twice to get `verbose` output by overriding `FEEDBACK`, without success |
| 1.3 | E | 2 | `jobs/2026-11-09__23-27-46` | E: 37 passed, 0 failed | $0.30 | 93 | No | Tried four times to get `verbose` output by overriding `FEEDBACK`, without success |
| 2.1 | C | 1 | `jobs/2026-11-08__12-04-30` | C: 34 passed, 0 failed | $0.09 | 35 | No | None |
| 2.1 | C | 2 | `jobs/2026-11-08__12-15-52` | C: 0 passed, 34 failed | $0.14 | 40 | No | Passed every check in its own container, but on a fresh database every registration fails (the handler reports any database error as `409 duplicate`) |
| 2.2 | D | 1 | `jobs/2026-11-08__11-26-42` | D: 14 passed, 0 failed | $0.01 | 4 | No | None |
| 2.2 | D | 2 | `jobs/2026-11-08__11-33-04` | D: 14 passed, 0 failed | $0.05 | 9 | No | None |
| 2.3 | E | 1 | `jobs/2026-11-09__22-14-23` | E: 35 passed, 2 failed | $0.44 | 135 | Yes (E.5) | Spent most of its run on the E.5 UI step and submitted with it failing |
| 2.3 | E | 2 | `jobs/2026-11-09__22-49-02` | E: 37 passed, 0 failed | $0.13 | 65 | No | None |
| 3 | C, D, E | 1 | `jobs/2026-11-09__05-23-47` | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $0.26 | 95 | No | Breaks A and B (62 regression rules), which it was not asked to keep |
| 3 | C, D, E | 2 | `jobs/2026-11-09__08-25-57` | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $0.22 | 93 | No | Breaks A and B (60 regression rules) |
| 4 | C, D, E | 1 | `jobs/2026-11-09__08-45-19` | C: 4 passed, 30 failed; D: 14 passed, 0 failed; E: 19 passed, 18 failed | $0.32 | 112 | Yes (C.2, C.4, C.5, D.1 to D.3, E.1 to E.3, E.5, E.6) | Cluster does not restart cleanly, so most later checks find no cluster |
| 4 | C, D, E | 2 | `jobs/2026-11-09__09-08-41` | C: 34 passed, 0 failed; D: 11 passed, 3 failed; E: 27 passed, 10 failed | $0.81 | 282 | Yes (D.1, D.2, E.5) | Spent most of its run on E without converging |
| 5.1 | A to E | 1 | `jobs/2026-11-09__04-35-03` | A and B: 44 broken; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 35 passed, 2 failed | $0.27 | 70 (5) | Yes (regression, E.6) | Never brought the regression suite to `0 broken` |
| 5.1 | A to E | 2 | `jobs/2026-11-09__05-01-25` | A and B: 34 broken; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $0.29 | 69 (22) | Yes (regression, C.1) | Never brought the regression suite to `0 broken` |
| 5.2 | A to E | 1 | `jobs/2026-11-09__03-47-59` | A and B: 0 broken; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $0.39 | 95 (34) | No | None |
| 5.2 | A to E | 2 | `jobs/2026-11-09__04-14-20` | A and B: 10 broken; C: 1 passed, 33 failed; D: 13 passed, 1 failed; E: 3 passed, 34 failed | $0.12 | 13 (13) | Yes (regression) | Submitted early, before C and E worked |
| 5.3 | A to E | 1 | `jobs/2026-11-09__02-48-31` | A: 48 passed, 0 failed; B: 52 passed, 0 failed; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $0.37 | 94 | No | None |
| 5.3 | A to E | 2 | `jobs/2026-11-09__03-13-53` | A: 48 passed, 0 failed; B: 52 passed, 0 failed; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $0.42 | 103 | No | None |

No run crashed or hit the cost limit, and no agent wrote to `/spec`.

Runs 3, 4 and 5.x (and both Run 5.3 repeats) use the final spec, in which the UI lives in a single
`static/app.js` and `start.sh` must start every service. Runs 1.1, 1.2, 2.1 and 2.2 use the
previous revision, whose C and D text is the same; for E it differs only in how the
UI code is organised. "Ran checks" is given only where transcript counts were recomputed.

Both Run 5.3 apps pass all 185 rules of A to E with the regression suite at `0 broken`, and both pass
all ten groups of a separate probe of COMPONENTS.md requirements (`analysis/probe_components.py`,
outputs in `analysis/probe/`). Run 5.2 repeat 1 also passes all 185 rules.

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
| E | 1 | 1.3 | score_only | E: 37 passed, 0 failed | $0.16 | 97 | 15.0 | 48 (30) | 10 | 0 (2 attempts) |
| E | 1 | 2.3 | verbose | E: 35 passed, 2 failed | $0.44 | 175 | 28.2 | 121 (91) | 7 | n/a |
| E | 2 | 1.3 | score_only | E: 37 passed, 0 failed | $0.30 | 159 | 24.5 | 86 (61) | 10 | 0 (4 attempts) |
| E | 2 | 2.3 | verbose | E: 37 passed, 0 failed | $0.13 | 76 | 10.5 | 57 (37) | 3 | n/a |

#### Answer

Yes, mainly by saving the agent work. The clearest evidence is what the agents did when the detail
was withheld: they went out of their way to get it back.
- In four of the six `score_only` runs, the agent tried to obtain per-rule output.
- In one (C repeat 1), it succeeded twice, by copying the check files to `/tmp`, editing the copied
  `lib.sh` to `FEEDBACK=verbose` and running the copy. Both E agents tried to override `FEEDBACK`
  (two and four times); the checks read the mode from their own `lib.sh`, so this had no effect.
- The `score_only` agents also read the rule code more often in five of six pairs (11 against 3, 7
  against 2, 3 against 0, 10 against 7, 10 against 3; one pair tied at 2).

An agent that did not need the failure details would not try to rebuild them by hand.

With `verbose` output the run was cheaper in three of the six pairs, tied in one and dearer in two.
The largest gap is on E repeat 2, where each failure names a missing UI element: 24.5 agent minutes
and $0.30 with `score_only` against 10.5 minutes and $0.13 with `verbose`. E repeat 1 goes the other way: the `verbose` agent
spent 121 check runs on the E.5 UI step and submitted with two E rules failing.

In the final results the mode made little difference: every `score_only` app and four of the six
`verbose` apps pass all of their checks. The two exceptions are 2.1 repeat 2, a defect that only
appears on a fresh database (see "Other evidence"), and 2.3 repeat 1, which submitted with E.5
failing. Detailed output therefore helps by saving the agent work, not by deciding whether a
component gets built.

#### Consistency across repeats

All runs used the same model access and settings, so the repeats are directly comparable. The extra
reading of the rule code under `score_only` holds in both repeats for C and E. Costs vary: the D
comparison reverses between repeats ($0.05 against $0.01, then $0.03 against $0.05), and E favours
`verbose` in repeat 2 but not in repeat 1. D runs are short (1 to 3 minutes), so their differences are
within noise. The direction of the effect (more effort to recover detail without `verbose`) is
consistent; its size is not.

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

| Repeat | Run | What the agent had | Check results | C + D + E passed | Cost |
| --- | --- | --- | --- | --- | --- |
| 1 | 3 | specs + checks | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | 85 of 85 | $0.26 |
| 1 | 4 | checks only | C: 4 passed, 30 failed; D: 14 passed, 0 failed; E: 19 passed, 18 failed | 37 of 85 | $0.32 |
| 2 | 3 | specs + checks | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | 85 of 85 | $0.22 |
| 2 | 4 | checks only | C: 34 passed, 0 failed; D: 11 passed, 3 failed; E: 27 passed, 10 failed | 72 of 85 | $0.81 |

Behaviour outside the C, D and E checks (the A and B regression suite on the same apps; neither run
was asked to build all of A and B):

| Repeat | Run 3 | Run 4 |
| --- | --- | --- |
| 1 | 62 broken | regression suite could not start the cluster |
| 2 | 60 broken | 66 broken |

#### Answer

No. With the written rules, both Run 3 apps pass all 85 C, D and E rules. With only the checks, Run 4
finished at 37 and 72 of 85, at higher cost, and both Run 4 agents submitted with checks still failing.

The difference is largest where the rules describe a design rather than a single response:
- **Cluster supervision.** In repeat 1 the cluster does not restart cleanly, so most later checks find
  no cluster. `architecture.md` prescribes the supervisor and the single `start.sh` that prevent this.
- **The browser UI (E.5, E.6).** Without the spec, repeat 2 spent most of its run discovering the
  expected UI structure one failing element at a time and finished with 10 E rules failing.

Behaviour outside the checks is also worse without the spec. The checks can confirm that the rules
are met, but they cannot replace the rules as the description of what to build.

#### Consistency across repeats

The result is the same in both repeats. Run 3 passed everything both times; Run 4 fell short both
times. The size of the shortfall varies (48 and 13 rules missing), but its direction does not.

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
| C passed (of 34) | 34 / 0 | 34 / 34 | 34 / 34, 34 / 1, 34 / 34 |
| D passed (of 14) | 14 / 14 | 14 / 14 | 14 / 14, 14 / 13, 14 / 14 |
| E passed (of 37) | 35 / 37 | 37 / 37 | 35 / 37, 37 / 3, 37 / 37 |

| Repeat | Alone: 2.1 + 2.2 + 2.3 | Together: Run 3 |
| --- | --- | --- |
| 1 | $0.55 | $0.26 |
| 2 | $0.32 | $0.22 |

#### Answer

Not when building C, D and E together. Run 3 passed every C, D and E rule in both repeats, the same
as building each component alone, and at lower cost in both repeats. The two shortfalls in the
"alone" column, C in 2.1 repeat 2 (the fresh-database defect described in question 1) and E in 2.3
repeat 1 (two E.5 rules), are not scale effects: building more at once did no worse.

At the whole-app scale the results depend on the run. Both Run 5.3 apps and 5.2 repeat 1 pass every
C, D and E rule, and 5.1 repeat 2 passes all of them too. Two runs fell short: 5.1 repeat 1 left two
E rules failing, and 5.2 repeat 2 submitted early, before C and E worked. So building more components
in one run did not make the agent build any one of them worse in most runs; when it did fall short,
it was on the components built last.

#### Consistency across repeats

The "together equals alone" result holds in both repeats. At the whole-app scale, four of the six
runs pass every C, D and E rule, including both Run 5.3 repeats; the two shortfalls are in different
settings and different repeats.

#### Other evidence

- E.5 and E.6, the browser steps, are where most failing check runs occur. The final spec keeps the
  whole UI in one `static/app.js` with one handler per control, and in these runs the agents that
  reached E.6 finished it.
- D passed almost everywhere. The shared architecture (one database, delivery by polling) makes the
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

Final state of each whole-app run, measured in a fresh container:

| Run | Repeat | Final A and B (regression suite) | C, D, E passed (of 34, 14, 37) | Cost |
| --- | --- | --- | --- | --- |
| 5.1 | 1 | 44 broken | 34, 14, 35 | $0.27 |
| 5.1 | 2 | 34 broken | 34, 14, 37 | $0.29 |
| 5.2 | 1 | 0 broken | 34, 14, 37 | $0.39 |
| 5.2 | 2 | 10 broken | 1, 13, 3 | $0.12 |
| 5.3 | 1 | 0 broken (A 48/48, B 52/52) | 34, 14, 37 | $0.37 |
| 5.3 | 2 | 0 broken (A 48/48, B 52/52) | 34, 14, 37 | $0.42 |

For comparison, Runs 3 and 4, which were told to build only the basics of A and B and were given no A
and B checks, finish with 60 to 66 A and B regression rules broken.

#### Answer

a) Building C, D and E puts A and B at risk, but the risk is controlled when the agent keeps re-running
the A and B checks. In both Run 5.3 repeats and in 5.2 repeat 1 the final app passes all 100 A and B
rules after C, D and E were built on top. Runs 3 and 4, which had no A and B checks, end with 60 to 66
A and B rules broken, which shows how much of A and B is lost when nothing guards it.

b) Yes, clearly. With Specs A and B (5.2), repeat 1 brought the suite to `0 broken` and repeat 2 ended
with 10 broken after submitting early. With only the regression suite (5.1), both final apps have many
more broken (44 and 34). The failure messages name each broken rule, but without the written rules the
agent had to rebuild A and B one failure at a time, and that did not converge within the time it gave
A and B.

c) The plain staged checks did at least as well. With plain A and B checks (5.3), both repeats pass
every A and B rule and every C, D and E rule. With the regression suite (5.2), one repeat did the same
and the other submitted early. The staged checks keep A and B in the same per-step loop the agent uses
for C, D and E, which is why Run 5.3 is the setting whose spec I submit.

#### Consistency across repeats

All whole-app runs used the same model access and settings, so the repeats are comparable.
- b) holds in both repeats: each 5.2 app has fewer A and B rules broken than either 5.1 app.
- c) Run 5.3 gave a fully passing app in both repeats; Run 5.2 in one of two.

#### Other evidence

- Runs 3 and 4 also show that the A and B regression rules test real behaviour: an app that passes
  every C, D and E rule can still break more than half of them.
