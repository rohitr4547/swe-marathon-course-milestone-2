# Milestone 2 report

- Name: Rohit Ramesh
- UNI: rr3713

All results are from my own checks. The check results below come from rerunning every step check
(and, for Runs 3, 4 and 5.x, the A and B regression suite) on each run's final `artifacts/app/` in a
fresh container built like the task container (`analysis/verify_app.sh`, `analysis/regression_app.sh`;
outputs in `analysis/verify/`). Transcript counts come from `analysis/transcript_stats.py` and
`analysis/transcript_evidence.py` (outputs in `analysis/*.json`). The spec and checks were frozen
before the first graded run, so Spec C and its checks are identical in Runs 1.1, 2.1, 3, 4 and 5.x,
and likewise for D and E. "Cost" is mini-swe-agent's estimate from the configured per-token prices,
the same measure the harness uses for its $3 per-run limit.

**Model access.** Every run used GPT 5.6 Luna. Repeat 1 of every run, and repeat 2 of Runs 5.2 and
5.3, called it through the OpenAI API. Repeat 2 of the other nine runs called the same model through
OpenRouter, pinned to the OpenAI provider with no fallbacks. On OpenRouter, the default reasoning
setting (and effort `low` or `minimal`) made the agent reason at length on its first call, imagine a
whole session and submit an empty `/app`. The OpenAI runs used a median of 45 reasoning tokens per
call, so the OpenRouter runs used reasoning effort `none`, the one setting that reproduced their
one-command-per-reply behaviour. The two repeats of those nine runs therefore differ in model access
as well as in chance, and I treat that as a confound in every consistency section below.

## Runs

One row per repeat of each run (22 in total). "Run" is the run in the run plan in README Setup.
"Ran checks" counts every invocation of a step check or of the regression suite; for Runs 5.1 and
5.2 the number of regression-suite runs is given in brackets. "Submitted with failures?" uses the
last result the agent itself saw for each check before it submitted.

| Run | Component | Repeat | Job folder | Check results | Cost | Ran checks (times) | Submitted with failures? | Problems |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1.1 | C | 1 | `jobs/2026-11-06__09-13-24` | C: 34 passed, 0 failed | $0.98 | 38 | No | From command 51 of 80, ran the rule code directly (`FEEDBACK=verbose python3 /spec/c/checks/c_rules.py <step>`), which skips `lib.sh` and printed per-rule failures for C.2 to C.4 |
| 1.1 | C | 2 | `jobs/2026-11-07__20-41-01` | C: 34 passed, 0 failed | $0.57 | 17 | Yes (C.2: 6 of 8) | Submitted without rerunning C.2; the same code passes all 8 C.2 rules in a fresh container |
| 1.2 | D | 1 | `jobs/2026-11-06__09-30-58` | D: 14 passed, 0 failed | $0.42 | 18 | No | None |
| 1.2 | D | 2 | `jobs/2026-11-07__20-37-45` | D: 14 passed, 0 failed | $0.15 | 4 | Yes (D.1: 6 of 8) | Submitted without rerunning D.1; the same code passes all 8 D.1 rules in a fresh container |
| 1.3 | E | 1 | `jobs/2026-11-06__09-59-32` | E: 37 passed, 0 failed | $0.92 | 25 | No | None |
| 1.3 | E | 2 | `jobs/2026-11-07__21-59-09` | E: 30 passed, 7 failed | $1.32 | 2 | Yes (E.1: 7 of 9, E.5: 3 of 7) | Ran checks only twice and never ran E.2 to E.4 or E.6; E.5 and E.6 browser rules (workspace, channels, logout, edit, thread, reactions) fail |
| 2.1 | C | 1 | `jobs/2026-11-06__09-06-08` | C: 34 passed, 0 failed | $0.41 | 21 | No | None |
| 2.1 | C | 2 | `jobs/2026-11-07__20-33-29` | C: 34 passed, 0 failed | $0.32 | 23 | No | None |
| 2.2 | D | 1 | `jobs/2026-11-06__09-24-41` | D: 14 passed, 0 failed | $0.19 | 15 | No | None |
| 2.2 | D | 2 | `jobs/2026-11-07__15-35-37` | D: 14 passed, 0 failed | $0.06 | 4 | No | None |
| 2.3 | E | 1 | `jobs/2026-11-06__09-39-14` | E: 37 passed, 0 failed | $1.50 | 48 | No | E.5 (browser UI) needed 17 check runs |
| 2.3 | E | 2 | `jobs/2026-11-07__22-25-48` | E: 26 passed, 11 failed | $0.80 | 31 | Yes (E.5: 3 of 7, E.6: 0 of 7) | Ran E.5 13 times without passing, then E.6 once, and submitted |
| 3 | C, D, E | 1 | `jobs/2026-11-06__08-24-14` | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $1.17 | 64 | No | None |
| 3 | C, D, E | 2 | `jobs/2026-11-07__20-47-18` | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $1.67 | 70 | No | None |
| 4 | C, D, E | 1 | `jobs/2026-11-06__08-39-31` | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $2.15 | 68 | No | E.5 (browser UI) needed 19 check runs |
| 4 | C, D, E | 2 | `jobs/2026-11-07__22-41-10` | C: 33 passed, 1 failed; D: 12 passed, 2 failed; E: 27 passed, 10 failed | $3.01 | 65 | Did not submit | Stopped by the $3 cost limit after 202 model calls, before reaching E.5 and E.6 |
| 5.1 | A to E | 1 | `jobs/2026-11-06__08-02-57` | A and B: 0 broken; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $2.63 | 60 (16) | No | Did not rerun the regression suite after each C/D/E step, only once at the end |
| 5.1 | A to E | 2 | `jobs/2026-11-07__23-05-03` | A and B: 59 broken; C: 5 passed, 29 failed; D: 6 passed, 8 failed; E: 3 passed, 34 failed | $0.72 | 12 (12) | Yes (regression: 59 broken) | Worked only on A and B (71 to 59 broken), never ran a C, D or E check, and submitted after 5.6 minutes; one format error |
| 5.2 | A to E | 1 | `jobs/2026-11-05__11-24-42` | A and B: 0 broken; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $1.54 | 52 (10) | No | Once tried to run a plain A check (`check_a_1.sh`), which does not exist in this run; reran the suite only at the end |
| 5.2 | A to E | 2 | `jobs/2026-11-05__11-39-55` | A and B: 0 broken; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $2.78 | 73 (12) | No | `/spec` also listed four empty folders (no files); reran the suite only at the end |
| 5.3 | A to E | 1 | `jobs/2026-11-05__10-44-14` | A: 48 passed, 0 failed; B: 52 passed, 0 failed; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $2.63 | 94 | No | B1.6 (topic permission) broke while C to E were built; found by a final A/B sweep and fixed before submitting |
| 5.3 | A to E | 2 | `jobs/2026-11-05__11-06-03` | A: 48 passed, 0 failed; B: 52 passed, 0 failed; C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $2.23 | 97 | No | Reran A and B checks only once after they first passed, in a final sweep of every check |

No run crashed (`exception_info: null`), no agent wrote to `/spec`, and only one reply in all 22 runs
was a format error.

The 13 OpenAI runs all submitted only after every check they ran passed, and all pass 100% of their
own checks in a fresh container. Six of the nine OpenRouter runs did not: four submitted with failing
checks, one was stopped by the cost limit, and one (5.1 repeat 2) submitted without building C, D or E.
The runs that did pass everything cannot be separated by their final results, so the analyses below
also compare the effort each run needed: cost, model calls, agent minutes, and how many check runs
still showed failures ("failing runs").

## Experiments

### 1) Feedback

> Do you find that adding detailed output to the checks helps the agent?

#### Runs compared and why

Runs 1.1, 1.2 and 1.3 (`score_only`) against Runs 2.1, 2.2 and 2.3 (`verbose`). Each pair has the
same component, the same spec and the same checks; only the `FEEDBACK` setting in `lib.sh` differs.
So any difference within a pair comes from the feedback mode (plus run-to-run noise). Each repeat is
compared with the same repeat of its pair, so both runs in a comparison used the same model access.

#### Supporting results

| Component | Repeat | Run | Mode | Check results | Cost | Model calls | Agent minutes | Check runs (failing) | Submitted with failures? | Reads of the rule code |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C | 1 | 1.1 | score_only | C: 34 passed, 0 failed | $0.98 | 80 | 8.1 | 36 (21) | No | 5 |
| C | 1 | 2.1 | verbose | C: 34 passed, 0 failed | $0.41 | 44 | 4.5 | 20 (10) | No | 6 |
| C | 2 | 1.1 | score_only | C: 34 passed, 0 failed | $0.57 | 63 | 4.7 | 16 (12) | Yes | 6 |
| C | 2 | 2.1 | verbose | C: 34 passed, 0 failed | $0.32 | 40 | 3.2 | 23 (13) | No | 4 |
| D | 1 | 1.2 | score_only | D: 14 passed, 0 failed | $0.42 | 50 | 6.1 | 18 (3) | No | 1 |
| D | 1 | 2.2 | verbose | D: 14 passed, 0 failed | $0.19 | 27 | 4.0 | 12 (0) | No | 2 |
| D | 2 | 1.2 | score_only | D: 14 passed, 0 failed | $0.15 | 25 | 1.7 | 4 (2) | Yes | 2 |
| D | 2 | 2.2 | verbose | D: 14 passed, 0 failed | $0.06 | 13 | 1.2 | 4 (1) | No | 0 |
| E | 1 | 1.3 | score_only | E: 37 passed, 0 failed | $0.92 | 81 | 13.5 | 25 (13) | No | 24 |
| E | 1 | 2.3 | verbose | E: 37 passed, 0 failed | $1.50 | 106 | 17.5 | 48 (37) | No | 8 |
| E | 2 | 1.3 | score_only | E: 30 passed, 7 failed | $1.32 | 110 | 20.1 | 2 (2) | Yes | 8 |
| E | 2 | 2.3 | verbose | E: 26 passed, 11 failed | $0.80 | 87 | 9.6 | 30 (21) | Yes | 4 |

Failing check runs per step, repeat 1:

| Run | Step 1 | Step 2 | Step 3 | Step 4 | Step 5 | Step 6 |
| --- | --- | --- | --- | --- | --- | --- |
| 1.1 (C) | 4 of 8 | 9 of 11 | 7 of 10 | 1 of 4 | 0 of 3 | |
| 2.1 (C) | 7 of 9 | 1 of 3 | 1 of 3 | 1 of 3 | 0 of 2 | |
| 1.3 (E) | 2 of 3 | 6 of 7 | 0 of 1 | 0 of 1 | 1 of 2 | 4 of 5 |
| 2.3 (E) | 5 of 6 | 9 of 11 | 3 of 5 | 2 of 4 | 15 of 17 | 3 of 5 |

#### Answer

Yes, for C and D, and in a second way that the repeats made visible.

First, effort. With `verbose` output the agent needed less for C and D in both repeats: 1.8 to 2.4
times cheaper, with fewer model calls and less time in all four comparisons. Under `score_only` the
agent only learns that a step is failing, not which rule. In 1.1 repeat 1 it retried C.2 and C.3
eleven and ten times (nine and seven of them failing). In 2.1 each of those steps failed once and was
then fixed.

Second, knowing when the work is done. All three `score_only` runs in repeat 2 submitted with a check
still failing. Only one `verbose` run did (2.3, on the hardest step). A `RESULT: 6 passed, 2 failed`
line does not say what is left, and in repeat 2, where the agent reasoned least, it gave up on it
rather than investigate. 1.3 repeat 2 is the extreme case: it ran checks only twice in 110 commands.

The agents also worked to get the detail back when it was withheld, which is further evidence that
they need it:
- 1.1 repeat 1 first tried `FEEDBACK=verbose bash check_c_1.sh`, which had no effect because `lib.sh`
  sets the mode. From command 51 of 80 it then ran the rule code directly (`FEEDBACK=verbose python3
  /spec/c/checks/c_rules.py 3`), which skips `lib.sh` and printed the failing rule names for C.2 to C.4.
- 1.2 repeat 1 wrote its own probe scripts with the check helpers.
- 1.3 repeat 1 read the rule code (`e_rules.py`) 24 times and built each UI step to match the exact
  selectors and assertions there, so E.3 to E.5 passed almost on the first run.

E gives no clear answer. In repeat 1 the `verbose` run cost more ($1.50 against $0.92): the agent
iterated 17 times on E.5, where each failure names one missing element at a time, while the
`score_only` agent read the assertions from the rule code instead. In repeat 2 both runs left E.5
and E.6 failing.

So detailed output helps by saving iterations and by telling the agent exactly what remains. It does
not make a component passable on its own: when the output is withheld, a careful agent recovers the
same information from the check code in `/spec`.

#### Consistency across repeats

For C and D the direction is the same in both repeats (`verbose` cheaper and faster in all four
comparisons), even though the repeats used different model access, so I treat it as the reliable
finding. The submit-with-failures difference comes from repeat 2 only (three of three `score_only`
runs against one of three `verbose` runs). Repeat 1 had no such submits in either mode, so that part
depends on the less careful agent of repeat 2. For E the two repeats disagree, so I draw no
conclusion. The C comparison in repeat 1 understates the effect, because 1.1 got per-rule output for
part of its run.

#### Other evidence

- Through the wrapper, the `score_only` output really is one line per check (`RESULT: x passed, y failed`).
  Before it bypassed the wrapper, the 1.1 repeat 1 agent narrowed C.2 down with its own diagnostic
  scripts ("I'll run C.2 one rule at a time to isolate the four failures"), work that 2.1 skipped by
  reading its `FAIL` lines.
- Run 1.1's direct run of `c_rules.py` worked because the check code treats a missing `FEEDBACK`
  setting as `verbose`, and only the wrappers set it to `score_only`. This is a weakness in my
  `score_only` setup. Making the check code default to `score_only` would have closed it.
- In 1.1 and 1.2 repeat 2, the failure the agent submitted with does not reproduce in a fresh
  container: the same code passes every rule. Neither agent changed code after its last check. The
  `score_only` line gave it no way to tell a state problem in its own container from a missing feature.

### 2) Tests as the spec

> Can the checks alone replace the written rules?

#### Runs compared and why

Run 3 (Specs C, D and E plus their `verbose` checks) against Run 4 (the same `verbose` checks, the
README and no spec files). Both build all three components together with the same checks and
feedback mode, so the only difference is whether the written rules (and the architecture they
reference) are present. Each repeat of Run 3 is compared with the same repeat of Run 4.

#### Supporting results

| Repeat | Run | What the agent had | Check results | Cost | Model calls | Agent minutes | Failing check runs C / D / E | Reads of the rule code |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 3 | specs + checks | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $1.17 | 88 | 11.8 | 16 / 0 / 14 | 10 |
| 1 | 4 | checks only | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $2.15 | 125 | 24.6 | 13 / 1 / 26 | 10 |
| 2 | 3 | specs + checks | C: 34 passed, 0 failed; D: 14 passed, 0 failed; E: 37 passed, 0 failed | $1.67 | 112 | 13.7 | 24 / 0 / 32 | 9 |
| 2 | 4 | checks only | C: 33 passed, 1 failed; D: 12 passed, 2 failed; E: 27 passed, 10 failed | $3.01 (limit) | 202 | 17.6 | 30 / 4 / 16 | 7 |

| Step (repeat 1) | Run 3 check runs (failing) | Run 4 check runs (failing) |
| --- | --- | --- |
| E.5 browser auth and navigation | 7 (5) | 19 (17) |
| E.2 IRC basics | 2 (0) | 5 (3) |
| D.3 performance | 2 (0) | 3 (1) |

Behaviour outside the C, D and E checks (the A and B regression suite on the same apps; neither run
was asked to build all of A and B):

| Repeat | Run 3 | Run 4 |
| --- | --- | --- |
| 1 | 49 broken | 68 broken |
| 2 | 58 broken | 58 broken |

#### Answer

No. In repeat 1 the checks alone were enough to pass the checks, but at 1.8 times the cost and twice
the time. In repeat 2 they were not enough: with the spec, Run 3 passed all 85 C, D and E rules. With
only the checks, Run 4 used up the $3 limit after 202 model calls and finished at 72 of 85, never
reaching the last two UI steps.

The extra work without the spec falls on the steps whose rules describe a design rather than a single
response: the UI (E.5: 19 check runs against 7 in repeat 1), the IRC gateway (E.2), and, in repeat 2,
the WebSocket resume and delivery-latency rules of D (D.1 and D.3 fail in the final app). Without the
written rules or `architecture.md`, the agent had to discover the expected structure one failure at a
time.

On behaviour the checks do not cover, the evidence is mixed. Repeat 1 favoured the spec (49 against
68 A and B rules broken), but repeat 2 showed no difference (58 against 58). So I do not claim that
the written spec improved behaviour outside the checks. The finding that holds in both repeats is
about cost and completion: the written rules let the agent finish the checked work faster, and in
one of two repeats they were the difference between finishing and not finishing.

#### Consistency across repeats

The direction is the same in both repeats: Run 4 needed more calls and more money than Run 3, by 1.4
times (repeat 1) and 1.8 times (repeat 2) in calls. Repeat 2 also turned that extra effort into an
unfinished app. Repeat 2 used OpenRouter with reasoning `none` for both runs, so the comparison
within each repeat is fair, but the size of the gap is not comparable across repeats. The A and B
difference did not replicate.

#### Other evidence

- In Run 4 the rule code was the only written statement of the rules. Right after the README, the
  repeat 1 agent's next two commands printed `c_rules.py` and `d_rules.py` and listed every rule in
  `e_rules.py`, using the check code as the spec.
- Run 4 also had no `architecture.md`, so it chose its own stack (repeat 1 pinned different package
  versions and wrote a different `start.sh` from the one the architecture gives). In repeat 2 it spent
  35 C check runs (30 failing), 14 of them on C.1 alone, before the cost limit stopped it in E.

### 3) Scale

> Do you find that a component passes fewer of its checks when the agent builds it together with
> other components, rather than on its own?

#### Runs compared and why

Runs 2.1, 2.2 and 2.3 (each component alone, `verbose`) against Run 3 (C, D and E together,
`verbose`), with Runs 5.1 to 5.3 (all five components, `verbose`) as a larger scale. Same checks and
feedback mode in all of them; only the number of components built in one run changes. Each repeat of
2.x is compared with the same repeat of Run 3.

#### Supporting results

| Component | Repeat | Alone (2.x) | Together, C to E (3) | Whole app, A to E |
| --- | --- | --- | --- | --- |
| C passed | 1 | 34 of 34 | 34 of 34 | 34 of 34 in 5.1, 5.2 (both), 5.3 (both) |
| C passed | 2 | 34 of 34 | 34 of 34 | 5 of 34 in 5.1 repeat 2 |
| D passed | 1 | 14 of 14 | 14 of 14 | 14 of 14 in the same five |
| D passed | 2 | 14 of 14 | 14 of 14 | 6 of 14 in 5.1 repeat 2 |
| E passed | 1 | 37 of 37 | 37 of 37 | 37 of 37 in the same five |
| E passed | 2 | 26 of 37 | 37 of 37 | 3 of 37 in 5.1 repeat 2 |
| C failing check runs | 1 / 2 | 10 of 20 / 13 of 23 | 16 of 28 / 24 of 29 | 4, 6, 11, 9, 12 (repeat 1 five) |
| E failing check runs | 1 / 2 | 37 of 48 / 21 of 30 | 14 of 26 / 32 of 38 | 10, 6, 20, 22, 15 (repeat 1 five) |

| Repeat | Alone: 2.1 + 2.2 + 2.3 | Together: Run 3 |
| --- | --- | --- |
| 1 | $2.10, 26.0 minutes | $1.17, 11.8 minutes |
| 2 | $1.18, 14.0 minutes | $1.67, 13.7 minutes |

#### Answer

Not between building alone and building C, D and E together. In both repeats, every component passed
at least as many checks in Run 3 as on its own. In repeat 2, E alone (2.3) finished at 26 of 37 while
E in Run 3 passed all 37. Building together did not consistently make a component harder: C needed
more failing runs together, E needed fewer in repeat 1 and more in repeat 2, and D needed almost none
anywhere.

At the whole-app scale there is one counter-example. 5.1 repeat 2 had to build A and B first from the
regression suite alone. It spent its whole run there and submitted without starting C, D or E, so
those components pass only the few rules the A and B work happened to cover. The five other
whole-app runs passed every C, D and E rule. So a larger task did not make a component that the agent
built pass fewer checks. It did, once, leave whole components unbuilt, when the agent gave up before
reaching them.

#### Consistency across repeats

The "together passes at least as many as alone" result holds in both repeats. The cost comparison
does not: building together was cheaper in repeat 1 ($1.17 against $2.10) and dearer in repeat 2
($1.67 against $1.18), so I draw no conclusion about cost. The whole-app counter-example is a single
run, on the OpenRouter access that also produced the other early submits, so I attribute it to the
agent stopping early rather than to scale as such.

#### Other evidence

- In every setting the most failing check runs come from one step, E.5 (the browser sign-up, login
  and navigation check): 15 of 37 in 2.3 repeat 1 and 5 to 11 in the repeat-1 whole-app runs. E.5 is
  also where both incomplete E apps of repeat 2 (1.3 and 2.3) stopped. How hard a run is depends mostly
  on how quickly the agent gets the UI right, not on how many other components it is building.
- D needed at most one failing check run in every `verbose` setting with the written spec, alone or
  together (2.2, 3 and the whole-app runs that built it).
  The shared architecture (one database, delivery by polling) makes the robustness rules hold once
  the cluster boots, whatever else is built alongside.

### 4) Regression

> a) Do you find that building components C, D, and E breaks features from A and B that were
> already working?

> b) Does it make a difference whether the agent has Specs A and B or only their regression
> tests?

> c) Does it make a difference whether the A and B checks are given as a regression suite or as
> plain checks?

#### Runs compared and why

- a) All whole-app runs that brought A and B to a passing state (5.1 repeat 1, 5.2 twice, 5.3 twice).
  In each, the agent builds A and B first and must keep them passing while it builds C, D and E. I
  located the point in each transcript where A and B first fully passed, then looked for any later A
  or B failure, and checked the final app.
- b) Run 5.1 (regression suite only) against Run 5.2 (regression suite plus Specs A and B). The only
  difference is whether the A and B rules are written out.
- c) Run 5.2 (Specs A and B plus the regression suite) against Run 5.3 (Specs A and B plus the plain
  staged A and B checks). The only difference is the form the A and B checks take.

#### Supporting results

| Run | Repeat | A and B first all passing (agent minute, command) | A/B check runs before / after that point | A/B failures after that point | Final A and B (fresh container) | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| 5.1 | 1 | 7.4 min, command 56 of 135 | 15 / 1 (suite) | 0 | 0 broken | $2.63 |
| 5.1 | 2 | never (71 broken at first, 59 at the end) | 12 / 0 (suite) | n/a | 59 broken | $0.72 |
| 5.2 | 1 | 5.0 min, command 36 of 95 | 9 / 1 (suite) | 0 | 0 broken | $1.54 |
| 5.2 | 2 | 7.5 min, command 54 of 135 | 11 / 1 (suite) | 0 | 0 broken | $2.78 |
| 5.3 | 1 | 5.0 min, command 45 of 142 | 21 / 14 (staged checks) | 1 (B1.6) | 0 broken | $2.63 |
| 5.3 | 2 | 6.0 min, command 52 of 117 | 33 / 9 (staged checks) | 0 | 0 broken | $2.23 |

In every run that reached a passing A and B, the checks after that point were one final re-check just
before submitting: a single suite run in 5.1 and 5.2, and a sweep of all nine A and B step checks
(four commands in 5.3 repeat 1, one loop in repeat 2).

Regression-suite counts:

| Run | Broken at each suite run |
| --- | --- |
| 5.1 repeat 1 | 86, 85, 54, 36, 23, 16, 14, 11, 10, 10, 6, 5, 2, 1, 0 |
| 5.1 repeat 2 | 71, 64, 60, then eight runs limited to A.2 and A.3 (10 or 11 broken each), then 59 |
| 5.2 repeat 1 | 60, 33, 20, 17, 14, 15, 6, 3, 0 |
| 5.2 repeat 2 | 85, 59, 37, 25, 18, 11, 7, 4, 1, 1, 0 |

#### Answer

a) Rarely, and never in a submitted app. All five apps whose A and B reached a passing state still
pass every A and B rule in a fresh container. I observed one real break: in 5.3 repeat 1, the agent
changed channel permissions while building C to E. That left `PATCH /api/channels/{id}` letting a
workspace member who is not in the channel change its topic (B1.6: expected 403, got 200). It was
found only because the agent ran a final sweep of all A and B checks, and it was fixed in one edit.
The bigger finding is behavioural. The 5.1 and 5.2 READMEs told the agent to rerun the regression
suite after every step, but none of the agents did. The 5.3 README asked only for earlier steps of
the same component to be rerun, with all A and B checks as a condition for submitting. In all five
runs the agent brought A and B to clean, built C, D and E checking only their own steps, and
re-checked A and B once just before submitting. So the runs show few breaks partly because there were
few chances to observe one in between. Every final re-check either was clean or, in the one case
above, was fixed before submitting.

b) It made little difference when the agent persisted, and the one failure happened without the
specs. Repeat 1 of both settings and both 5.2 repeats reached `0 broken` within 5 to 8 minutes. With
the specs, one repeat (5.2 repeat 1) started from a better first draft (60 broken at its first suite
run, against 85 and 86) and reached clean faster (9 suite runs against 15). Without the specs, the
agent has to recover the rules from the suite's `REGRESSION FAIL` messages and from
`regression_rules.py`. In 5.1 repeat 1 that worked. In 5.1 repeat 2 it did not: the agent got stuck on
A.2 and A.3, re-ran only those groups eight times at 10 or 11 broken, and submitted with 59 broken.
Because that run is also the only 5.1 or 5.2 run on OpenRouter, I cannot separate the missing specs
from the different model access. The evidence is consistent with the specs giving a better start and
a fallback when the failure messages are not enough, but it is not conclusive.

c) It made no difference to the outcome (every 5.2 and 5.3 app finished with all A and B rules
passing). It did change how the agent used the checks. With plain checks (5.3), the agent ran A and B
as nine separate step checks, 21 and 33 check runs before they were clean, and a full re-check meant
nine checks. With the suite (5.2), one command covered every A and B rule: the agent ran it 9 and 11
times to reach clean, and the re-check at the end was a single command. Time to clean was the same
(5.0 and 6.0 minutes with plain checks, against 5.0 and 7.5 with the suite). The suite is the cheaper
way to re-check A and B, which is why only the suite setting's README asks for it after every step.
In practice, agents with either form re-checked only at the end, so this advantage was not used. The
only regression observed was in the plain-checks setting. That is because the 5.3 agent re-ran B.1
there, not because plain checks cause more breaks: a suite run at the same point would have reported
the same `REGRESSION FAIL channels B1.6` line.

#### Consistency across repeats

Runs 5.2 and 5.3 both used the OpenAI API in both repeats, so c) compares like with like: the final
result (`0 broken`) is identical in all four runs, and effort varies within a setting about as much
as between settings (5.2's repeats took 9 and 11 suite runs and cost $1.54 and $2.78). For b), the two
5.1 repeats disagree completely (0 broken against 59), and the disagreement coincides with the change
of model access, so b) rests on repeat 1 plus a confounded repeat 2. The skipped per-step reruns are
consistent across every whole-app run.

#### Other evidence

- In 5.2 repeat 1, the agent once tried `bash /spec/a/checks/check_a_1.sh`, which does not exist in
  that run. When it has the A and B specs, the agent expects plain staged checks to go with them.
- The transcripts show the agents treating the A and B re-check as part of the submit condition
  rather than the per-step routine. For example, 5.3 repeat 1 described its sweep as "I'll rerun the
  complete Spec A sweep after the final changes", with 30 minutes still left. Every README lists the
  final re-check as a condition for submitting, and that is the instruction the agents followed. The
  "after every step" instruction in 5.1 and 5.2 was ignored.
- In 5.1 repeat 2 the agent used the suite's group filter (`regression.sh A2 A3`) to re-run only the
  failing groups. That made each run faster but hid the roughly 49 other broken rules until its last
  full run.
- Runs 3 and 4, which were told to build only the basics of A and B, finish with 49 to 68 A and B
  rules broken. That shows how much of A and B the regression suite and the A and B checks cause the
  agent to build in the 5.x runs.
