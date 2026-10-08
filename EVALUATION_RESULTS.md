# Evaluation results

How five local models do as the model behind Tinker, measured with the evaluation set in [`evals/`](evals/).
Each model ran all 13 tasks once, with everything else identical.

- **Date:** 8 October 2026
- **Tinker:** commit `79dd97e`
- **Ollama:** 0.35.1, on a separate machine with an estimated 16 GB of GPU memory (models larger than that run
  partly on the CPU), every model at its default `q4_K_M` quantization
- **Settings:** `config.json` as committed: 64K context window, temperature 0.2, at most 40 model calls per task

## Summary

| Model | All 13 | Failing-test tasks (8) | Bug-fix tasks (5) | Total time | Median model calls per task |
|---|---|---|---|---|---|
| `laguna-xs-2.1` | **13/13** | 8/8 | 5/5 | 5.4 min | 10 |
| `ornith` | **13/13** | 8/8 | 5/5 | 4.5 min | 8 |
| `qwen3-coder` | **11/13** | 7/8 | 4/5 | 19.2 min | 17 |
| `nemotron-3.5-lightning` | **11/13** | 8/8 | 3/5 | 9.4 min | 11 |
| `glm-4.7-flash` | **10/13** | 6/8 | 4/5 | 22.6 min | 7 |

| Model | Parameters | Download | Notes |
|---|---|---|---|
| `laguna-xs-2.1` (Poolside) | 33B, 3B active per token | 20.3 GB | built for agentic coding; a thinking model |
| `ornith` (`ornith:latest`, the 9B version) | 9B | 5.6 GB | built for agentic coding; a thinking model; fits on the GPU entirely |
| `qwen3-coder` (Alibaba) | 30B, 3B active per token | 18.6 GB | Tinker's default model so far |
| `nemotron-3.5-lightning` (NVIDIA) | 33B, 3B active per token | 25.4 GB | built for agents; a thinking model |
| `glm-4.7-flash` (Z.ai) | 30B | 19.0 GB | a thinking model |

**In short:** `laguna-xs-2.1` and `ornith` solved every task, and were also the fastest. `ornith` did it with
a 9B model that fits entirely on the GPU. `qwen3-coder`, the model Tinker has used so far, passed 11 of 13 and took
longest per task apart from `glm-4.7-flash`. One run per task can't separate models that are close (see
[Caveats](#caveats)), but these differences are large enough to make `laguna-xs-2.1` and `ornith` worth trying as
the default.

## The tasks

There are two kinds of task, all small Python projects:

- **Failing-test tasks (8):** the project has failing tests, and Tinker must make them pass without changing them.
  They run in `feature` mode, which can't write to test files.
- **Bug-fix tasks (5):** the project's tests all pass, and the bug is only described in the request, like a bug
  report. They run in the `bugfix` mode, whose instructions say to prove the bug with a failing test before
  changing any code.

Every task also has **hidden tests**, added only for grading, so a fix that only special-cases the visible tests
fails. Grading runs all the tests in the sandbox, because the code under test was written by the model.

A bug-fix task passes only if all three of these hold:

- **Fixed:** all tests pass afterwards, hidden ones included.
- **Reproduced:** the agent's new tests fail when run against the original code, so they really catch the bug.
- **Valid:** the agent's new tests pass against a reference fix, so they test the right behaviour rather than the
  agent's own fix.

The runner also records whether the agent **tested first**: whether it ran a failing test before its first change
to the code. This is reported, but doesn't count towards passing.

## Results by task

Each cell gives the result, the number of model calls and the time taken.

| Task | `laguna-xs-2.1` | `ornith` | `qwen3-coder` | `nemotron-3.5-lightning` | `glm-4.7-flash` |
|---|---|---|---|---|---|
| `add_function` | pass (7, 29s) | pass (8, 17s) | pass (8, 41s) | pass (5, 37s) | pass (7, 42s) |
| `deep_merge` | pass (7, 15s) | pass (5, 9s) | pass (14, 53s) | pass (5, 19s) | pass (7, 30s) |
| `fizzbuzz` | pass (6, 13s) | pass (5, 8s) | pass (8, 23s) | pass (5, 23s) | pass (5, 21s) |
| `keep_compatible` | pass (7, 13s) | pass (5, 6s) | pass (32, 122s) | pass (7, 24s) | pass (7, 26s) |
| `off_by_one` | pass (6, 12s) | pass (5, 8s) | pass (9, 34s) | pass (6, 28s) | pass (6, 32s) |
| `rename` | pass (14, 21s) | pass (11, 19s) | pass (9, 26s) | pass (13, 42s) | pass (7, 29s) |
| `slugify` | pass (27, 63s) | pass (5, 8s) | **FAIL** (31, 153s) | pass (7, 27s) | **FAIL** (19, 375s) |
| `stack_error` | pass (10, 18s) | pass (6, 11s) | pass (7, 20s) | pass (15, 38s) | **FAIL** (7, 614s) |
| `bug_cart_total` | pass (11, 24s) | pass (12, 22s) | pass (20, 95s) | pass (19, 68s) | pass (9, 41s) |
| `bug_date_range` | pass (10, 20s) | pass (9, 32s) | pass (37, 229s) | pass (16, 71s) | pass (9, 37s) |
| `bug_duration` | pass (25, 56s) | pass (16, 65s) | **FAIL** (40, 198s) | **FAIL** (11, 36s) | **FAIL** (8, 41s) |
| `bug_median` | pass (10, 20s) | pass (15, 32s) | pass (22, 91s) | pass (22, 76s) | pass (8, 34s) |
| `bug_word_case` | pass (9, 19s) | pass (15, 33s) | pass (17, 65s) | **FAIL** (23, 74s) | pass (8, 34s) |

### Bug-fix checks

"All" means the task was fixed, reproduced, valid and tested first. Otherwise the cell lists the checks that were
missed.

| Task | `laguna-xs-2.1` | `ornith` | `qwen3-coder` | `nemotron-3.5-lightning` | `glm-4.7-flash` |
|---|---|---|---|---|---|
| `bug_cart_total` | all | all | all | all | all |
| `bug_date_range` | all | all | all | all | all |
| `bug_duration` | all | all | not fixed, reproduced or valid | not fixed, reproduced, valid or tested first | not fixed |
| `bug_median` | all | all | all | all | all |
| `bug_word_case` | all | not tested first | all | not fixed or tested first | all |

## Why each failure happened

- **`qwen3-coder`, `slugify`:** its slug function handles the visible tests but not the hidden case
  `"Python 3.12 -- released!"` (digits and repeated dashes). Its answer said all tests pass, which was true of the
  tests it could see.
- **`qwen3-coder`, `bug_duration`:** it created the same test file twice, once in the project root and once in
  `tests/`. pytest refuses to collect two test modules with the same name (the grading run reports a collection
  error), so its later test runs failed. It never
  worked out why, and stopped at the 40-call limit.
- **`nemotron-3.5-lightning`, `bug_duration`:** it gave up after 11 calls with an empty answer, without writing a
  test or changing the code.
- **`nemotron-3.5-lightning`, `bug_word_case`:** it wrote a test that reproduced the bug, but ended with an empty
  answer before fixing the code.
- **`glm-4.7-flash`, `bug_duration`:** it reproduced the bug correctly, but its fix also accepts input the
  original code rejected (see below).
- **`glm-4.7-flash`, `slugify`:** the same hidden case as `qwen3-coder`, and it ended with an empty answer after
  six minutes.
- **`glm-4.7-flash`, `stack_error`:** its code was already correct (all tests pass), but one of its replies took
  longer than Tinker's 10-minute limit per reply, so the task ended in an error. See "Things to fix in Tinker".

## Observations

- **`bug_duration` is the hardest task, and catches a common mistake.** The bug is that `"2h"` is rejected. Fixing
  it by making both parts of the pattern optional also makes the parser accept an empty string, or `"h"`, as zero
  minutes, which the original code rejected. The bug report doesn't mention those inputs. In this run
  `glm-4.7-flash` made that mistake; in the earlier, discarded run, four of the five models did. Only
  `laguna-xs-2.1` and `ornith` fixed the bug without it this time.
- **Proving the bug first mostly works.** In 22 of the 25 bug-fix runs, the model ran a failing test before
  changing the code. Every new test that ran properly was a valid reproduction; the only bad tests were
  `qwen3-coder`'s for `bug_duration`, which couldn't be collected at all. The `bugfix` mode's instructions appear to
  be followed well.
- **Effort varies a lot between models.** `qwen3-coder` needed 17 model calls per task (median), against 8 for
  `ornith`, and was four times slower in total.
- **Empty answers are a failure mode of some thinking models.** `nemotron-3.5-lightning` and `glm-4.7-flash` each
  ended tasks with an empty final answer. Tinker accepts that as finished.

## Caveats

- **One run per task.** Results vary from run to run, so a single difference between two models means little.
  `qwen3-coder` scored 11/13 in two separate runs, but failed a different pair of tasks each time. Use
  `evals/run.py --repeat 3` or more before choosing between models with similar scores.
- **Small tasks.** Each project has one to three short files. Larger codebases, where finding the right place to
  change is harder, aren't tested yet.
- **One machine and one quantization.** Speed depends on how much of each model fits on the GPU. On a GPU that
  holds the whole model, the 30B models would be much faster, and their quality might change at a different
  quantization.
- **An earlier run was discarded.** A bug in Tinker cut some tasks short (a shortcut meant to end repetitive loops
  also stopped models that were still working). The bug was fixed in `a1dc663`, and all five models were run again
  for this report.

## Things to fix in Tinker

- **Replies longer than the time limit.** Replies are capped at 8,192 tokens, and each reply may take at most 10
  minutes. A model generating at fewer than about 14 tokens a second can hit the time limit before the cap, as
  `glm-4.7-flash` did. The two limits should be matched.
- **Empty final answers.** Tinker should treat an empty final answer as unfinished, and ask the model to continue or
  summarise.

## Reproducing

```bash
.venv/bin/python evals/run.py --check-tasks                         # check that the tasks are fair
.venv/bin/python evals/run.py --model laguna-xs-2.1:latest          # all tasks, one model
.venv/bin/python evals/run.py --model ornith:latest --repeat 3      # repeat each task
```

Results are printed as a table, with full details (including each final answer) in `evals/results/`.
