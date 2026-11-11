# Milestone 2 report

- Name: Rohit Ramesh
- UNI: rr3713

All results are from my own checks. The check results below come from rerunning every step check
(and, for Runs 3, 4 and 5.x, the A and B regression suite) on each run's final `artifacts/app/` in a
fresh container built like the task container, starting from an empty database
(`analysis/verify_app.sh`, `analysis/regression_app.sh`; outputs in `analysis/verify/`). Transcript
counts come from `analysis/transcript_stats.py`, `analysis/transcript_evidence.py` and
`analysis/ab_breaks.py` (outputs in `analysis/*.json` and `analysis/ab_breaks.txt`).

**Setup common to all 22 runs.**
- **Spec.** One frozen spec is used throughout: Spec C and its checks are identical in Runs 1.1, 2.1,
  3, 4 and 5.x, and likewise for D and E, and the shared files in `common/` are identical wherever they
  appear. Every run's `/spec` includes `common/ready.sh`, a submit gate: each complete check run
  records its result together with a fingerprint of the code in `/app`, and the gate prints
  `READY TO SUBMIT` only when every check has passed on the current code.
- **Model.** Every run used GPT 5.6 Luna through OpenRouter, pinned to the OpenAI provider, with
  reasoning effort `minimal`, under the harness's 50-minute agent limit and $3 per-run cost limit.
- **Cost.** "Cost" is mini-swe-agent's estimate with cached prompt tokens priced at the cache-read
  rate, the figure the harness's $3 per-run limit uses.
- **Runs reported.** Each row is one complete run: the agent worked from an empty `/app` until it
  submitted. A launch that did not produce a complete run, because the environment interrupted it or
  the agent submitted an empty `/app` on its first model call, was relaunched.

## Runs

One row per repeat of each run (22 in total). "Run" is the run in the run plan in README Setup.
"Check results" covers the components the run builds. "Ran checks" counts every invocation of a step
check or of the regression suite; for Runs 5.1 and 5.2 the number of regression-suite runs is given
in brackets. "Submitted with failures?" uses the last result the agent itself saw for each check.

| Run | Component | Repeat | Job folder | Check results | Cost | Ran checks (times) | Submitted with failures? | Problems |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1.1 | C | 1 | `jobs/2026-11-10__00-06-03` | C: 34 passed, 0 failed | $0.14 | 34 | No | Ran the rule file directly with `--verbose` to get per-rule output (11 times) |
| 1.1 | C | 2 | `jobs/2026-11-10__00-23-28` | C: 34 passed, 0 failed | $0.14 | 38 | No | Copied `lib.sh` to `/tmp`, switched the copy to `verbose` and got per-rule output (10 times) |
| 1.2 | D | 1 | `jobs/2026-11-10__00-37-50` | D: 14 passed, 0 failed | $0.02 | 4 | No | None |
| 1.2 | D | 2 | `jobs/2026-11-10__00-45-11` | D: 14 passed, 0 failed | $0.03 | 10 | No | None |
| 1.3 | E | 1 | `jobs/2026-11-09__23-06-24` | E: 37 passed, 0 failed | $0.16 | 50 | No | Tried twice to get `verbose` output by overriding `FEEDBACK`, without success |
| 1.3 | E | 2 | `jobs/2026-11-09__23-27-46` | E: 37 passed, 0 failed | $0.30 | 93 | No | Tried four times to get `verbose` output by overriding `FEEDBACK`, without success |
| 2.1 | C | 1 | `jobs/2026-11-10__00-53-33` | C: 34 passed, 0 failed | $0.08 | 31 | No | None |
| 2.1 | C | 2 | `jobs/2026-11-10__01-04-10` | C: 34 passed, 0 failed | $0.09 | 30 | No | None |
| 2.2 | D | 1 | `jobs/2026-11-10__01-15-32` | D: 14 passed, 0 failed | $0.04 | 9 | No | None |
| 2.2 | D | 2 | `jobs/2026-11-10__01-24-53` | D: 14 passed, 0 failed | $0.02 | 5 | No | None |
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

No run crashed, timed out or hit the cost limit, and no agent wrote to `/spec`. Sixteen of the 22
agents reached `READY TO SUBMIT` before submitting; the six that did not are exactly the six marked
"Yes" under "Submitted with failures?".

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
| C | 1 | 1.1 | score_only | C: 34 passed, 0 failed | $0.14 | 89 | 7.0 | 33 (24) | 4 | 11 times |
| C | 1 | 2.1 | verbose | C: 34 passed, 0 failed | $0.08 | 50 | 5.0 | 31 (21) | 4 | n/a |
| C | 2 | 1.1 | score_only | C: 34 passed, 0 failed | $0.14 | 86 | 7.9 | 37 (24) | 14 | 10 times |
| C | 2 | 2.1 | verbose | C: 34 passed, 0 failed | $0.09 | 55 | 5.3 | 28 (19) | 3 | n/a |
| D | 1 | 1.2 | score_only | D: 14 passed, 0 failed | $0.02 | 18 | 1.3 | 4 (1) | 2 | 0 |
| D | 1 | 2.2 | verbose | D: 14 passed, 0 failed | $0.04 | 36 | 3.2 | 8 (5) | 1 | n/a |
| D | 2 | 1.2 | score_only | D: 14 passed, 0 failed | $0.03 | 24 | 2.2 | 10 (1) | 1 | 0 |
| D | 2 | 2.2 | verbose | D: 14 passed, 0 failed | $0.02 | 17 | 1.3 | 5 (2) | 0 | n/a |
| E | 1 | 1.3 | score_only | E: 37 passed, 0 failed | $0.16 | 97 | 15.0 | 48 (30) | 10 | 0 (2 attempts) |
| E | 1 | 2.3 | verbose | E: 35 passed, 2 failed | $0.44 | 175 | 28.2 | 121 (91) | 7 | n/a |
| E | 2 | 1.3 | score_only | E: 37 passed, 0 failed | $0.30 | 159 | 24.5 | 86 (61) | 10 | 0 (4 attempts) |
| E | 2 | 2.3 | verbose | E: 37 passed, 0 failed | $0.13 | 76 | 10.5 | 57 (37) | 3 | n/a |

#### Answer

Yes, mainly by saving the agent work. The clearest evidence is what the agents did when the detail
was withheld: they went out of their way to get it back.
- Four of the six `score_only` agents tried to obtain per-rule output: both C agents and both E
  agents. The D agents needed only a few check runs and did not try.
- Both C agents succeeded, about ten times each: repeat 1 by running the rule file directly with
  `--verbose`, repeat 2 by copying `lib.sh` to `/tmp` and switching the copy to `verbose`.
- Both E agents tried overriding `FEEDBACK` (two and four times). The checks read the mode from
  their own `lib.sh`, so this had no effect.
- The `score_only` agents read the rule code more often in five of six pairs (14 against 3, 2
  against 1, 1 against 0, 10 against 7, 10 against 3; one pair tied at 4).

An agent that did not need the failure details would not try to rebuild them by hand.

With `verbose` output the run was cheaper in four of the six pairs. Both C pairs were cheaper and
faster with `verbose` ($0.08 against $0.14 and $0.09 against $0.14). The largest gap is on E repeat
2, where each failure names a missing UI element: 24.5 agent minutes and $0.30 with `score_only`
against 10.5 minutes and $0.13 with `verbose`. E repeat 1 goes the other way: the `verbose` agent
spent 121 check runs on the E.5 UI step and submitted with two E rules failing.

In the final results the mode made little difference: all six `score_only` apps and five of the six
`verbose` apps pass all of their checks. Detailed output therefore helps by saving the agent work,
not by deciding whether a component gets built.

#### Consistency across repeats

All 22 runs used the same spec, model access and settings, so the repeats are directly comparable.
- C is consistent: in both repeats the `score_only` agent recovered per-rule output about ten times
  and cost more than the `verbose` agent.
- The extra reading of the rule code under `score_only` holds in both E repeats.
- D runs are short (1 to 3 minutes) and need few check runs; their differences reverse between
  repeats and are within noise.
- E favours `verbose` in repeat 2 but not in repeat 1.

The direction of the effect (more effort to recover detail without `verbose`) is consistent; its
size is not.

#### Other evidence

- The workaround shows a limit of a `score_only` setup. The checks must ship in `/spec` as runnable
  code, so a determined agent can always run the rule file directly or copy the checks and change
  the mode. The spec reads the mode from the check's own `lib.sh`, which stops a plain
  `FEEDBACK=verbose` override, but not an edited copy. A truly blind setting would need the checks
  to run outside the agent's reach.
- The `score_only` E agents, unable to get per-rule output, read the rule code instead (10 reads
  each) and still reached a fully passing app, at higher cost in repeat 2.

### 2) Tests as the spec

> Can the checks alone replace the written rules?

#### Runs compared and why

Run 3 (Specs C, D and E plus their `verbose` checks) against Run 4 (the same `verbose` checks and a
README, with no spec files and no `architecture.md`). Both build all three components together with
the same checks and feedback mode, so the only difference is whether the written rules are present.

#### Supporting results

| Repeat | Run | What the agent had | Check results | C + D + E passed | Cost | Model calls | Agent minutes | Failing check runs C / D / E |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 3 | specs + checks | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | 85 of 85 | $0.26 | 119 | 15.5 | 11 / 0 / 42 |
| 1 | 4 | checks only | C: 4 passed, 30 failed; D: 14 passed, 0 failed; E: 19 passed, 18 failed | 37 of 85 | $0.32 | 134 | 17.2 | 39 / 8 / 17 |
| 2 | 3 | specs + checks | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | 85 of 85 | $0.22 | 111 | 14.0 | 25 / 1 / 19 |
| 2 | 4 | checks only | C: 34 passed, 0 failed; D: 11 passed, 3 failed; E: 27 passed, 10 failed | 72 of 85 | $0.81 | 259 | 37.1 | 53 / 31 / 43 |

Behaviour outside the C, D and E checks (the A and B regression suite on the same apps; neither run
was asked to build all of A and B):

| Repeat | Run 3 | Run 4 |
| --- | --- | --- |
| 1 | 62 broken | not measurable: the app's cluster does not start under the suite |
| 2 | 60 broken | 66 broken |

#### Answer

No. With the written rules, both Run 3 apps pass all 85 C, D and E rules. With only the checks, Run 4
finished at 37 and 72 of 85, at higher cost, and both Run 4 agents submitted with checks still failing
and without reaching `READY TO SUBMIT`.

The difference is largest where the rules describe a design rather than a single response:
- **Cluster supervision.** In repeat 1 the cluster does not restart cleanly, so most later checks find
  no cluster. `architecture.md` prescribes the supervisor and the single `start.sh` that prevent this.
- **The browser UI (E.5, E.6).** Without the spec, repeat 2 spent most of its run discovering the
  expected UI structure one failing element at a time: 259 model calls and 37 minutes, with 43 of its
  48 E check runs failing, and it still finished with 10 E rules failing.

Behaviour outside the checks is also worse without the spec. The checks can confirm that the rules
are met, but they cannot replace the rules as the description of what to build.

#### Consistency across repeats

The result is the same in both repeats. Run 3 passed everything both times; Run 4 fell short both
times. The size of the shortfall varies (48 and 13 rules missing), but its direction does not.

#### Other evidence

- Failing check runs show where the written rules mattered. On D, the cluster-robustness component,
  Run 3 had 0 and 1 failing runs against 8 and 31 for Run 4: without `architecture.md`, the agent
  had to infer process supervision and restart behaviour from the check failures alone.
- Run 4 also read the rule code (9 and 17 reads) about as often as Run 3 (10 and 7), so reading the
  checks' source did not make up for the missing spec.

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
| C passed (of 34) | 34 / 34 | 34 / 34 | 34 / 34, 34 / 1, 34 / 34 |
| D passed (of 14) | 14 / 14 | 14 / 14 | 14 / 14, 14 / 13, 14 / 14 |
| E passed (of 37) | 35 / 37 | 37 / 37 | 35 / 37, 37 / 3, 37 / 37 |

| Repeat | Alone: 2.1 + 2.2 + 2.3 | Together: Run 3 |
| --- | --- | --- |
| 1 | $0.57 | $0.26 |
| 2 | $0.24 | $0.22 |

#### Answer

Not when building C, D and E together. Run 3 passed every C, D and E rule in both repeats, the same
as building each component alone, and at lower cost in both repeats. The only shortfall in either
column is in the "alone" column (two E.5 rules in 2.3 repeat 1), so building C, D and E together did
no worse than building them separately.

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

Final state of each whole-app run, measured in a fresh container, with what the agent saw during the
run (from its transcript):

| Run | Repeat | Final A and B (fresh container) | C, D, E passed (of 34, 14, 37) | Regression-suite results seen by the agent, in order | A or B checks that broke after passing | First `ready.sh` result | Cost |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5.1 | 1 | 44 broken | 34, 14, 35 | 68, 56, 52, 44, 44 | none | 15 items not ready | $0.27 |
| 5.1 | 2 | 34 broken | 34, 14, 37 | 74, 53, 51, 41, 40, 38, 38, 37, 37, 37, 36, 35, 34, 34, 34, 3, 3, 3, 3, 3 | none | 14 items not ready | $0.29 |
| 5.2 | 1 | 0 broken | 34, 14, 37 | 34 runs, ending 6, 11, 5, 3, 3, 0, 0, 0 | none | 14 items not ready | $0.39 |
| 5.2 | 2 | 10 broken | 1, 13, 3 | 13 runs, ending 29, 22, 16, 13, 12, 10 | none | 15 items not ready | $0.12 |
| 5.3 | 1 | 0 broken (A 48/48, B 52/52) | 34, 14, 37 | n/a (staged checks) | none | 22 items not ready | $0.37 |
| 5.3 | 2 | 0 broken (A 48/48, B 52/52) | 34, 14, 37 | n/a (staged checks) | none | 23 items not ready | $0.42 |

For comparison, Runs 3 and 4, which were told to build only the basics of A and B and were given no A
and B checks, finish with 60 to 66 A and B regression rules broken.

#### Answer

a) Not in these runs. In no whole-app run did an A or B check that had passed later fail while the
agent was building C, D or E. The A and B failures that did appear late were features the agent had
not finished yet, not regressions. In both Run 5.3 repeats the agent moved on to C to E with parts of
A and B incomplete (B.4 and B.5 in repeat 1, A.4 and B.1 in repeat 2); the submit gate then listed 22 and 23 unfinished items, and the agent
went back, finished them while on E.5 and E.6, and submitted with all 100 A and B rules passing. In
5.2 repeat 1 the suite read `0 broken` before C to E and stayed at `0 broken` in both reruns after.
By contrast, Runs 3 and 4, which had no A and B checks, end with 60 to 66 A and B rules broken: that
is how much of A and B goes missing when nothing checks it.

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
- a) No A or B break after passing appeared in any of the six runs.

#### Other evidence

- Runs 3 and 4 also show that the A and B regression rules test real behaviour: an app that passes
  every C, D and E rule can still break more than half of them.
- The submit gate mattered most in Run 5.3. Both agents believed they were done while 22 and 23
  items were still failing or unrerun; `ready.sh` listed them, and both agents fixed every one before
  submitting.
- 5.1 repeat 2 saw only 3 broken at its last suite run, but its app has 34 broken in a fresh
  container. Its own database had been built up over the run, which hid failures that appear when
  the app starts empty, as it does for the grader.
