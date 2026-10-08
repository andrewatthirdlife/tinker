# Evaluation results

How five local models do as the model behind Tinker, measured with the evaluation set in [`evals/`](evals/).
Each model ran all 13 tasks twice, with everything else identical within each run.

- **Date:** 8 October 2026
- **Run 1:** Tinker commit `79dd97e`. **Run 2:** commit `fd9b81f`, which streams replies (so a slow reply is no
  longer cut off) and asks again after an empty final answer; see [What the fixes changed](#what-the-fixes-changed).
- **Ollama:** 0.35.1, on a separate machine with an estimated 16 GB of GPU memory (models larger than that run
  partly on the CPU), every model at its default `q4_K_M` quantization
- **Settings:** `config.json` as committed: 64K context window, temperature 0.2, at most 40 model calls per task

## Summary

| Model | Run 2 | Run 1 | Both runs | Failing-test tasks | Bug-fix tasks | Run 2 time | Median model calls (run 2) |
|---|---|---|---|---|---|---|---|
| `laguna-xs-2.1` | 12/13 | 13/13 | **25/26** | 16/16 | 9/10 | 4.4 min | 9 |
| `ornith` | 11/13 | 13/13 | **24/26** | 15/16 | 9/10 | 6.1 min | 8 |
| `glm-4.7-flash` | 12/13 | 10/13 | **22/26** | 14/16 | 8/10 | 16.0 min | 8 |
| `qwen3-coder` | 11/13 | 11/13 | **22/26** | 14/16 | 8/10 | 15.8 min | 17 |
| `nemotron-3.5-lightning` | 10/13 | 11/13 | **21/26** | 16/16 | 5/10 | 13.2 min | 11 |

| Model | Parameters | Download | Notes |
|---|---|---|---|
| `laguna-xs-2.1` (Poolside) | 33B, 3B active per token | 20.3 GB | built for agentic coding; a thinking model |
| `ornith` (`ornith:latest`, the 9B version) | 9B | 5.6 GB | built for agentic coding; a thinking model; fits on the GPU entirely; **Tinker's default** |
| `glm-4.7-flash` (Z.ai) | 30B | 19.0 GB | a thinking model |
| `qwen3-coder` (Alibaba) | 30B, 3B active per token | 18.6 GB | Tinker's default before these results |
| `nemotron-3.5-lightning` (NVIDIA) | 33B, 3B active per token | 25.4 GB | built for agents; a thinking model |

**In short:** over two runs, `laguna-xs-2.1` (25/26) and `ornith` (24/26) were the most reliable and by far the
fastest. `ornith` is Tinker's default because it is nearly as good, and as a 9B model it leaves the most room for
other work on the machine; `laguna-xs-2.1` is the choice if every task counts. `glm-4.7-flash` and `qwen3-coder`
tied at 22/26 but were two to four times slower. `nemotron-3.5-lightning` solved every failing-test task, but
only half the bug-fix tasks.

**Scores move between runs.** `ornith` went from 13 to 11 and `glm-4.7-flash` from 10 to 12. One run per task
can't separate models whose totals are a point or two apart; see [Caveats](#caveats).

## The tasks

There are two kinds of task, all small Python projects:

- **Failing-test tasks (8):** the project has failing tests, and Tinker must make them pass without changing them.
  They run in `feature` mode, which can't write to test files.
- **Bug-fix tasks (5):** the project's tests all pass, and the bug is only described in the request, like a bug
  report. They run in the `bugfix` mode, whose instructions say to prove the bug with a failing test before
  changing any code.

Every task also has **hidden tests**, added only for grading, so a fix that only special-cases the visible tests
fails. Grading runs all the tests in the sandbox, because the code under test was written by the model. The final
answer doesn't affect grading: only the code and tests left behind do.

A bug-fix task passes only if all three of these hold:

- **Fixed:** all tests pass afterwards, hidden ones included.
- **Reproduced:** the agent's new tests fail when run against the original code, so they really catch the bug.
- **Valid:** the agent's new tests pass against a reference fix, so they test the right behaviour rather than the
  agent's own fix.

The runner also records whether the agent **tested first**: whether it ran a failing test before its first change
to the code. This is reported, but doesn't count towards passing.

## Results by task, both runs

How many of the two runs passed each task.

| Task | `laguna-xs-2.1` | `ornith` | `glm-4.7-flash` | `qwen3-coder` | `nemotron-3.5-lightning` | Passed |
|---|---|---|---|---|---|---|
| `add_function` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 10/10 |
| `deep_merge` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 10/10 |
| `fizzbuzz` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 10/10 |
| `keep_compatible` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 10/10 |
| `off_by_one` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 10/10 |
| `rename` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 10/10 |
| `slugify` | 2/2 | **1/2** | **1/2** | **0/2** | 2/2 | 6/10 |
| `stack_error` | 2/2 | 2/2 | **1/2** | 2/2 | 2/2 | 9/10 |
| `bug_cart_total` | 2/2 | 2/2 | 2/2 | 2/2 | **1/2** | 9/10 |
| `bug_date_range` | 2/2 | 2/2 | 2/2 | 2/2 | **1/2** | 9/10 |
| `bug_duration` | **1/2** | **1/2** | **0/2** | **1/2** | **0/2** | 3/10 |
| `bug_median` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 10/10 |
| `bug_word_case` | 2/2 | 2/2 | 2/2 | **1/2** | **1/2** | 8/10 |

Seven tasks were solved by every model in every run, so they no longer tell models apart. The useful ones are
`slugify`, `bug_duration`, and to a lesser extent `bug_word_case`.

## Run 2 in detail

Each cell gives the result, the number of model calls and the time taken.

| Task | `laguna-xs-2.1` | `ornith` | `glm-4.7-flash` | `qwen3-coder` | `nemotron-3.5-lightning` |
|---|---|---|---|---|---|
| `add_function` | pass (7, 27s) | pass (5, 6s) | pass (7, 40s) | pass (6, 36s) | pass (7, 48s) |
| `deep_merge` | pass (7, 14s) | pass (5, 13s) | pass (6, 24s) | pass (7, 20s) | pass (5, 18s) |
| `fizzbuzz` | pass (6, 14s) | pass (5, 7s) | pass (5, 21s) | pass (9, 29s) | pass (7, 31s) |
| `keep_compatible` | pass (7, 13s) | pass (5, 6s) | pass (6, 23s) | pass (11, 30s) | pass (7, 26s) |
| `off_by_one` | pass (6, 12s) | pass (5, 8s) | pass (6, 27s) | pass (7, 21s) | pass (5, 20s) |
| `rename` | pass (9, 14s) | pass (9, 14s) | pass (11, 32s) | pass (17, 54s) | pass (11, 30s) |
| `slugify` | pass (18, 33s) | **FAIL** (7, 20s) | pass (14, 59s) | **FAIL** (24, 104s) | pass (15, 58s) |
| `stack_error` | pass (8, 17s) | pass (10, 17s) | pass (8, 24s) | pass (9, 25s) | pass (10, 27s) |
| `bug_cart_total` | pass (10, 23s) | pass (8, 14s) | pass (10, 42s) | pass (17, 68s) | **FAIL** (29, 96s) |
| `bug_date_range` | pass (10, 23s) | pass (9, 17s) | pass (8, 36s) | pass (40, 254s) | **FAIL** (40, 146s) |
| `bug_duration` | **FAIL** (14, 34s) | **FAIL** (15, 68s) | **FAIL** (18, 560s) | pass (30, 135s) | **FAIL** (28, 176s) |
| `bug_median` | pass (9, 18s) | pass (9, 15s) | pass (10, 40s) | pass (21, 99s) | pass (16, 57s) |
| `bug_word_case` | pass (10, 23s) | pass (28, 162s) | pass (9, 34s) | **FAIL** (19, 76s) | pass (15, 60s) |

### Bug-fix checks, run 2

"All" means the task was fixed, reproduced, valid and tested first. Otherwise the cell lists the checks that were
missed.

| Task | `laguna-xs-2.1` | `ornith` | `glm-4.7-flash` | `qwen3-coder` | `nemotron-3.5-lightning` |
|---|---|---|---|---|---|
| `bug_cart_total` | all | all | all | all | not fixed, reproduced, valid or tested first |
| `bug_date_range` | all | all | all | all | not fixed, reproduced, valid or tested first |
| `bug_duration` | not fixed | not fixed | not fixed | all | not fixed |
| `bug_median` | all | all | all | all | all |
| `bug_word_case` | all | all | all | not fixed or valid | all |

### Why each run 2 failure happened

- **`bug_duration` (`laguna-xs-2.1`, `ornith`, `nemotron-3.5-lightning`):** each reproduced the bug with a valid
  test and fixed `"2h"`, but the fix also makes the parser accept an empty string, or `"h"`, as zero minutes, which
  the original code rejected. See [Observations](#observations).
- **`bug_duration` (`glm-4.7-flash`):** its fix didn't work (its own new test still fails), and it ended with an
  empty answer even after being asked for one.
- **`slugify` (`ornith`, `qwen3-coder`):** both handle the visible tests but not the hidden case
  `"Python 3.12 -- released!"` (digits and repeated dashes). `ornith` finished in 20 seconds; both answers said all
  tests pass, which was true of the tests they could see.
- **`bug_word_case` (`qwen3-coder`):** it fixed the code, but also wrote a second "comprehensive" test file with a
  wrong expectation, so its own test fails. The "valid" check exists to catch this.
- **`bug_cart_total` (`nemotron-3.5-lightning`):** no test and no working fix (the hidden test gets 48 instead of
  60), then an empty answer, twice.
- **`bug_date_range` (`nemotron-3.5-lightning`):** no test and no fix; it used all 40 model calls.

## What the fixes changed

Run 2 added two fixes to Tinker after run 1 showed the problems:

- **Streamed replies, with a 5-minute limit on silence instead of 10 minutes per reply.** In run 1,
  `glm-4.7-flash` lost `stack_error` to the old limit even though its code was already correct. In run 2 no task
  ended with an error, although `glm-4.7-flash` spent 560 seconds on one task.
- **Asking again after an empty final answer.** This happened 4 times in run 2. Twice the model then gave a proper
  answer (`ornith` on `bug_word_case`, `nemotron-3.5-lightning` on `slugify`, both passed), and twice it answered
  with nothing again. Because grading looks at the code, not the answer, this fix improves what the user is told
  more than it changes scores.

## Observations

- **`bug_duration` is the hardest task, and catches a common mistake.** The bug is that `"2h"` is rejected. The
  obvious fix, making both parts of the pattern optional, also makes the parser accept an empty string as zero
  minutes. Only 3 of 10 attempts passed, and 4 of the 7 failures were exactly this regression. The bug report
  doesn't mention those inputs, but the original code rejected them: it's a regression a careful developer would
  test for.
- **Proving the bug first mostly works.** In 23 of the 25 bug-fix attempts in run 2, the model ran a failing test
  before changing the code. The exceptions were both `nemotron-3.5-lightning`, which in two tasks wrote no test at
  all.
- **Effort varies a lot between models.** `qwen3-coder` needed a median of 17 model calls per task, against 8 or 9
  for `laguna-xs-2.1`, `ornith` and `glm-4.7-flash`.
- **Final answers overclaim.** In run 2, both failing `slugify` answers said all tests pass. The grading, which
  runs hidden tests, is what shows the code is wrong.

## Caveats

- **Two runs per task.** Even so, a difference of one or two tasks between models is within run-to-run variation.
  Use `evals/run.py --repeat 3` or more before choosing between models with close totals.
- **The two runs used slightly different versions of Tinker** (see the top of this page). The fixes mainly affect
  timeouts and final answers, but they could change behaviour in other small ways.
- **Small tasks.** Each project has one to three short files. Larger codebases, where finding the right place to
  change is harder, aren't tested yet. Seven of the thirteen tasks are now too easy to separate models.
- **One machine and one quantization.** Speed depends on how much of each model fits on the GPU. On a GPU that
  holds the whole model, the 30B models would be much faster, and their quality might change at a different
  quantization.
- **An earlier run was discarded.** Before run 1, a bug in Tinker cut some tasks short (a shortcut meant to end
  repetitive loops also stopped models that were still working). It was fixed in `a1dc663`.

## Things to fix in Tinker

- **An empty answer after the reminder.** Twice, the model answered with nothing even after being asked. Tinker
  could fall back to the change report, so the user at least sees what happened.
- **Reaching the 40-call limit.** Two tasks ended at the limit without a final answer, one of them with working
  code (`qwen3-coder`, `bug_date_range`). Tinker could ask for a summary a few calls before the limit.

## Reproducing

```bash
.venv/bin/python evals/run.py --check-tasks                         # check that the tasks are fair
.venv/bin/python evals/run.py --model laguna-xs-2.1:latest          # all tasks, one model
.venv/bin/python evals/run.py --model ornith:latest --repeat 3      # repeat each task
```

Results are printed as a table, with full details (including each final answer) in `evals/results/`.
