# Evaluation results

How six local models do as the model behind Tinker, measured with the evaluation set in [`evals/`](evals/). Each
model ran all 13 tasks twice, with everything else identical. These are the final results of the project: the
experiment ends here.

## Environment

| | 12 October 2026 (these results) | 8 October 2026 (previous results) |
|---|---|---|
| Tinker | commit `11140b1` (`a253927` plus the three fixes in [Before the fixes](#before-the-fixes)) | `79dd97e` (run 1) and `fd9b81f` (run 2) |
| Ollama server | **0.40.3** (updated automatically between the two dates) | 0.35.1 |
| ollama-python | 0.6.3 | 0.6.3 |
| Python | 3.12.3 | 3.12.3 |
| ruff / pytest | 0.16.10 / 9.1.1 | 0.16.10 / 9.1.1 |
| Sandbox kernel | 6.6.87.2-microsoft-standard-WSL2 (Landlock ABI 3) | the same |
| Hardware | Ollama on a separate machine with an estimated 16 GB of GPU memory; models larger than that run partly on the CPU | the same |
| Settings | `config.json` as committed: 64K context window, temperature 0.2, at most 40 model calls per task, 120 s per command | the same |

The Python packages were installed on 7 October and haven't changed since, so both sets of results used the same
versions.

**Models**, identified by digest, since a tag such as `:latest` can change:

| Model | Maker | Parameters | Quantization | Size | Digest | Downloaded |
|---|---|---|---|---|---|---|
| `laguna-xs-2.1:latest` | Poolside | 33B, 3B active per token | Q4_K_M | 20.3 GB | `0175be1e57f4` | 8 Oct |
| `ornith:latest` (**Tinker's default**) | — | 9B | Q4_K_M | 5.6 GB | `a75697c14589` | 8 Oct |
| `glm-4.7-flash:latest` | Z.ai | 30B | Q4_K_M | 19.0 GB | `4475827791a2` | 6 Oct |
| `gpt-oss:20b` (**new**) | OpenAI | 21B, about 3.6B active per token | MXFP4 | 13.8 GB | `f38aa0c53da5` | 12 Oct |
| `qwen3-coder:latest` | Alibaba | 30B, 3B active per token | Q4_K_M | 18.6 GB | `06c1097efce0` | 30 Sep |
| `nemotron-3.5-lightning:latest` | NVIDIA | 33B, 3B active per token | Q4_K_M | 25.4 GB | `e7a64ff15fb1` | 8 Oct |

All are thinking models except `qwen3-coder`. The five models from 8 October are the same files, downloaded on or
before that date, so **between the two sets of results only Ollama and Tinker changed**. `gpt-oss:20b` was added to
see how general Tinker is: it comes from a different maker and uses a different chat format ("harmony") from the
other five.

## Summary

| Model | Run 1 | Run 2 | **Total** | 8 Oct total | Failing-test tasks | Bug-fix tasks | Time per run | Median model calls |
|---|---|---|---|---|---|---|---|---|
| `laguna-xs-2.1` | 13/13 | 13/13 | **26/26** | 25/26 | 16/16 | 10/10 | 7.4, 6.1 min | 12, 12 |
| `ornith` | 12/13 | 12/13 | **24/26** | 24/26 | 16/16 | 8/10 | 3.7, 3.2 min | 9, 8 |
| `glm-4.7-flash` | 12/13 | 11/13 | **23/26** | 22/26 | 15/16 | 8/10 | 7.6, 7.0 min | 8, 7 |
| `gpt-oss:20b` | 11/13 | 12/13 | **23/26** | — | 13/16 | 10/10 | 4.1, 3.5 min | 12, 10 |
| `qwen3-coder` | 11/13 | 11/13 | **22/26** | 22/26 | 16/16 | 6/10 | 13.7, 9.8 min | 12, 10 |
| `nemotron-3.5-lightning` | 9/13 | 12/13 | **21/26** | 21/26 | 16/16 | 5/10 | 20.4, 16.9 min | 13, 10 |

**In short:**

- **`laguna-xs-2.1` is the most reliable model**, and the only one to solve every task in every run, including
  `bug_duration`, the task that catches a careless fix.
- **`ornith` remains a good default:** 24/26, the fastest model tested, and at 9B it fits entirely in the GPU's
  memory.
- **`gpt-oss:20b` worked with Tinker without any changes**, scored 23/26, and was nearly as fast as `ornith`. It was
  the only model besides `laguna` to solve every bug-fix task, but it lost three failing-test tasks.
- **The scores of the five original models are within a point of 8 October.** Tinker gained a lot of features in
  between (see [What changed since 8 October](#what-changed-since-8-october)), but on these small tasks they
  neither helped nor hurt much once three bugs were fixed. The same tasks defeat the same models, for the same
  reasons.
- **`glm-4.7-flash`, `ornith` and `qwen3-coder` are now much faster** than on 8 October: about 7 minutes per run
  rather than 16 for `glm`. Ollama is the only other thing that changed, but these results can't prove the update
  is the reason.

## The tasks

There are two kinds of task, all small Python projects:

- **Failing-test tasks (8):** the project has failing tests, and Tinker must make them pass without changing them.
  They run in `feature` mode, which can't write to test files.
- **Bug-fix tasks (5):** the project's tests all pass, and the bug is only described in the request, like a bug
  report. They run in `bugfix` mode, whose instructions say to prove the bug with a failing test before changing
  any code, and which can't delete test files.

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

| Task | `laguna-xs-2.1` | `ornith` | `glm-4.7-flash` | `gpt-oss:20b` | `qwen3-coder` | `nemotron-3.5-lightning` | Passed |
|---|---|---|---|---|---|---|---|
| `add_function` | 2/2 | 2/2 | 2/2 | **1/2** | 2/2 | 2/2 | 11/12 |
| `deep_merge` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 12/12 |
| `fizzbuzz` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 12/12 |
| `keep_compatible` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 12/12 |
| `off_by_one` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 12/12 |
| `rename` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 12/12 |
| `slugify` | 2/2 | 2/2 | **1/2** | **0/2** | 2/2 | 2/2 | 9/12 |
| `stack_error` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 12/12 |
| `bug_cart_total` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | **1/2** | 11/12 |
| `bug_date_range` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | 12/12 |
| `bug_duration` | 2/2 | **0/2** | **0/2** | 2/2 | **0/2** | **0/2** | 4/12 |
| `bug_median` | 2/2 | 2/2 | 2/2 | 2/2 | **0/2** | **1/2** | 9/12 |
| `bug_word_case` | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 | **1/2** | 11/12 |

Seven tasks were solved by every model in every run. `bug_duration` is by far the hardest (4/12), followed by
`slugify` and `bug_median` (9/12).

## Each run in detail

Each cell gives the result, the number of model calls and the time taken.

**Run 1**

| Task | `laguna-xs-2.1` | `ornith` | `glm-4.7-flash` | `gpt-oss:20b` | `qwen3-coder` | `nemotron-3.5-lightning` |
|---|---|---|---|---|---|---|
| `add_function` | pass (11, 38s) | pass (8, 17s) | pass (8, 42s) | **FAIL** (12, 24s) | pass (6, 33s) | pass (6, 42s) |
| `deep_merge` | pass (7, 17s) | pass (5, 10s) | pass (5, 18s) | pass (6, 5s) | pass (16, 90s) | pass (5, 20s) |
| `fizzbuzz` | pass (9, 19s) | pass (5, 6s) | pass (5, 21s) | pass (6, 4s) | pass (6, 20s) | pass (5, 24s) |
| `keep_compatible` | pass (10, 22s) | pass (6, 9s) | pass (6, 24s) | pass (7, 6s) | pass (8, 19s) | pass (7, 28s) |
| `off_by_one` | pass (10, 23s) | pass (5, 8s) | pass (7, 26s) | pass (5, 4s) | pass (7, 21s) | pass (6, 30s) |
| `rename` | pass (14, 24s) | pass (9, 13s) | pass (8, 25s) | pass (12, 8s) | pass (11, 26s) | pass (13, 46s) |
| `slugify` | pass (24, 95s) | pass (19, 49s) | pass (15, 69s) | **FAIL** (37, 103s) | pass (21, 122s) | pass (26, 102s) |
| `stack_error` | pass (12, 25s) | pass (10, 14s) | pass (7, 23s) | pass (7, 8s) | pass (9, 28s) | pass (12, 39s) |
| `bug_cart_total` | pass (10, 29s) | pass (9, 15s) | pass (8, 39s) | pass (13, 15s) | pass (12, 50s) | **FAIL** (40, 300s) |
| `bug_date_range` | pass (13, 36s) | pass (10, 21s) | pass (9, 39s) | pass (10, 9s) | pass (16, 63s) | pass (18, 95s) |
| `bug_duration` | pass (17, 50s) | **FAIL** (16, 29s) | **FAIL** (12, 62s) | pass (20, 34s) | **FAIL** (21, 89s) | **FAIL** (40, 179s) |
| `bug_median` | pass (15, 36s) | pass (8, 13s) | pass (7, 32s) | pass (12, 12s) | **FAIL** (36, 198s) | **FAIL** (40, 171s) |
| `bug_word_case` | pass (13, 32s) | pass (11, 17s) | pass (9, 38s) | pass (13, 15s) | pass (15, 62s) | **FAIL** (40, 148s) |

**Run 2**

| Task | `laguna-xs-2.1` | `ornith` | `glm-4.7-flash` | `gpt-oss:20b` | `qwen3-coder` | `nemotron-3.5-lightning` |
|---|---|---|---|---|---|---|
| `add_function` | pass (8, 34s) | pass (7, 15s) | pass (6, 38s) | pass (11, 85s) | pass (7, 37s) | pass (10, 54s) |
| `deep_merge` | pass (11, 25s) | pass (6, 11s) | pass (5, 21s) | pass (6, 5s) | pass (7, 21s) | pass (5, 20s) |
| `fizzbuzz` | pass (9, 19s) | pass (5, 8s) | pass (6, 21s) | pass (6, 5s) | pass (6, 20s) | pass (5, 24s) |
| `keep_compatible` | pass (9, 18s) | pass (5, 7s) | pass (6, 19s) | pass (7, 5s) | pass (10, 29s) | pass (8, 31s) |
| `off_by_one` | pass (10, 22s) | pass (5, 8s) | pass (8, 36s) | pass (5, 4s) | pass (7, 21s) | pass (5, 24s) |
| `rename` | pass (14, 25s) | pass (9, 13s) | pass (10, 31s) | pass (13, 9s) | pass (10, 27s) | pass (8, 29s) |
| `slugify` | pass (12, 27s) | pass (5, 8s) | **FAIL** (5, 19s) | **FAIL** (15, 25s) | pass (18, 74s) | pass (10, 40s) |
| `stack_error` | pass (8, 18s) | pass (8, 11s) | pass (7, 24s) | pass (9, 12s) | pass (8, 25s) | pass (9, 38s) |
| `bug_cart_total` | pass (13, 33s) | pass (8, 14s) | pass (8, 34s) | pass (11, 11s) | pass (12, 45s) | pass (34, 177s) |
| `bug_date_range` | pass (12, 35s) | pass (10, 20s) | pass (10, 50s) | pass (11, 11s) | pass (16, 66s) | pass (24, 104s) |
| `bug_duration` | pass (17, 53s) | **FAIL** (21, 48s) | **FAIL** (12, 58s) | pass (8, 13s) | **FAIL** (19, 68s) | **FAIL** (40, 215s) |
| `bug_median` | pass (13, 30s) | pass (8, 13s) | pass (7, 35s) | pass (10, 9s) | **FAIL** (19, 69s) | pass (28, 103s) |
| `bug_word_case` | pass (12, 26s) | pass (11, 18s) | pass (8, 35s) | pass (15, 17s) | pass (21, 84s) | pass (40, 154s) |

## Why each failure happened

There were 17 failures in 156 attempts.

- **`bug_duration`: the fix makes the parser accept an empty string (`ornith` ×2, `glm-4.7-flash` ×2,
  `qwen3-coder` ×2, `nemotron-3.5-lightning` run 2).** The bug is that `"2h"` is rejected. The obvious fix makes
  both parts of the pattern optional, so an empty string, or `"h"`, now parses as zero minutes, which the original
  code rejected (hidden test `test_still_rejects`). Each of these attempts reproduced the bug with a valid test,
  then made exactly this regression. Only `laguna-xs-2.1` and `gpt-oss:20b` kept the rejection, in all four of
  their attempts. This was also the commonest failure on 8 October.
- **`slugify`: digits and repeated dashes (`gpt-oss:20b` ×2, `glm-4.7-flash` run 2).** All three handle the visible
  tests but not the hidden case `"Python 3.12 -- released!"`. `glm` finished in 5 calls and 19 seconds.
- **`bug_median` (`qwen3-coder` ×2): a wrong extra test.** Both times it fixed the bug and reproduced it, but also
  wrote a second "comprehensive" test file with expectations of its own, which fail against the reference fix (in
  run 1, about the median of an empty list). The "valid" check exists to catch this. In run 1 it then re-ran the
  same tests until Tinker stopped the request after 7 identical runs. `qwen3-coder` wrote extra test files like
  this in most bug-fix tasks.
- **`nemotron-3.5-lightning`: the 40-call limit (`bug_cart_total`, `bug_duration`, `bug_median` and
  `bug_word_case` in run 1, `bug_duration` in run 2).** All five failures used every call without giving an
  answer. In two of them the code was already fixed (`bug_cart_total`, `bug_word_case`), but the tests it wrote
  didn't reproduce the bug, or it wrote none. It tested first in only 6 of its 10 bug-fix attempts; every other
  model did in all 10.
- **`add_function` (`gpt-oss:20b`, run 1): stuck re-reading.** It read `calc.py` 7 times in a row with nothing
  changing, and Tinker stopped the request with the function unwritten.

**Final answers still overclaim.** Of the 17 failures, 10 ended with an answer claiming that the tests pass or the
work is done; the other 7 ended at the call limit (5) or were stopped for repeating a call (2). Grading the code,
not the answer, is what shows them up.

## Before the fixes

The first run on 12 October used Tinker `a253927`, before three bugs it exposed were fixed. All six models, twice
each:

| Model | Before the fixes | After | What the fix changed |
|---|---|---|---|
| `laguna-xs-2.1` | 26/26 | 26/26 | |
| `ornith` | 25/26 | 24/26 | (run-to-run variation) |
| `glm-4.7-flash` | 24/26 | 23/26 | (run-to-run variation) |
| `gpt-oss:20b` | 23/26 | 23/26 | a task ended by an Ollama error is now retried |
| `qwen3-coder` | **17/26** | 22/26 | it can no longer delete its reproduction tests |
| `nemotron-3.5-lightning` | **17/26** | 21/26 | whitespace is no longer taken as a final answer |

The three bugs:

1. **Whitespace taken as a final answer.** Tinker has a shortcut for a model stuck in a loop: if it sends the same
   text with the same read-only call twice, the text is taken as its final answer. It checked that the text wasn't
   empty, but not that it wasn't whitespace, and `nemotron-3.5-lightning` sends `"\n\n"` with every tool call. So
   running the tests twice in a row, which is normal, ended the task with a blank answer. The shortcut fired 24
   times in nemotron's two runs, and was followed by a blank answer 14 times. Fixed: the shortcut needs real text.
2. **Reproduction tests deleted.** The `delete_file` tool, added for the larger-task experiments after 8 October,
   let `qwen3-coder` delete the test that proved the bug once the bug was fixed. It did so in every bug-fix task it
   failed, despite instructions to keep the test. Fixed with a new mode setting, `deny_delete`: bugfix mode can
   write test files but not delete them. In the final run `qwen3-coder` still tried to delete its tests 10 times,
   was refused each time, and passed those tasks.
3. **A tool call Ollama couldn't parse, mid-stream.** Ollama reports this as HTTP 500 at the start of a reply, but
   as an error with status -1 partway through a streamed one, and Tinker only retried the first. `gpt-oss:20b` lost
   a task to it. Fixed: both are retried. In the final run the retry fired once for `gpt-oss:20b`, and that task
   passed.

**Run-to-run variation is about one task in 26.** `ornith` and `glm-4.7-flash` each scored one lower after fixes
that don't affect anything they did, and `nemotron-3.5-lightning` scored 9 and then 12 in its two final runs.

A seventh model, `qwen2.5-coder:32b`, was also in the first run and is **left out of the results**, because it
can't use Tinker as it is. It writes its tool calls as JSON in the text of its reply, instead of in Ollama's
`tool_calls` field. Tinker recovers text tool calls only in qwen3-coder's XML style, so it took the first reply as
the final answer, and every task ended after one model call (0/13 twice). That reflects how the model formats tool
calls through Ollama, not its coding, so it isn't a fair comparison.

## What changed since 8 October

Tinker gained these between the two sets of results, most of them while it was used on larger tasks (see
[DESIGN.md](DESIGN.md#15-the-larger-task-experiments)):

- project **checks** run before a final answer, and a warning above the answer when they still fail;
- thinking is saved and sent back to the model within a request, and a reply **cut off** at the length limit is
  sent back with thinking turned off;
- a **breaker for repeated calls**, which stops running a call repeated with the same result and ends the request
  after 6 repeats;
- the **`create_directory`** and **`delete_file`** tools, and `deny_delete`;
- standard **input** for commands, and refusals that name the tool to use instead.

Most of these target failures that only appeared on larger tasks, so the small evaluation tasks barely exercise
them: in the final run no reply was cut off, and the repeat breaker stopped two requests.

**Speed.** Several models were much faster than on 8 October with the same files: `glm-4.7-flash` took about 7
minutes per run against 16, and `ornith` 3–4 minutes against 6. `nemotron-3.5-lightning` was slower (17–20 minutes
against 13). The Ollama update is the most likely cause, but these results can't separate it from Tinker's
changes.

## Observations

- **The hardest task tests care, not knowledge.** `bug_duration`'s obvious fix accepts an empty string. Four of
  the six models failed it in every attempt in this run. A test of the inputs the original code rejected would
  have caught the regression, and none of those attempts included one.
- **Proving the bug first works for five of the six models.** All except `nemotron-3.5-lightning` ran a failing
  test before changing the code in all 10 of their bug-fix attempts.
- **Instructions aren't enough; enforcement is.** `qwen3-coder` was told to keep its reproduction tests and
  deleted them anyway. Only refusing the deletion stopped it.
- **Tinker works across models that use Ollama's tool calling.** `gpt-oss:20b` uses a different chat format and a
  different kind of thinking (it can't turn thinking off), and worked unchanged. The one model that failed
  completely, `qwen2.5-coder:32b`, did so because of how it formats tool calls.
- **Effort varies.** The median number of model calls per task ranged from 7 to 13, and the time per run from 3.2
  to 20.4 minutes. `ornith` and `gpt-oss:20b` were the quickest. `nemotron-3.5-lightning` was the slowest, partly
  because it ran out of calls on five tasks.

## Caveats

- **Two runs per task.** Run-to-run variation is about one task in 26, so totals a point or two apart don't
  separate models. Use `evals/run.py --repeat 3` or more before choosing between close models.
- **Small tasks.** Each project has one to three short files, and seven of the thirteen tasks are now solved by
  every model. Larger codebases aren't tested; the larger-task experiments in [DESIGN.md](DESIGN.md) suggest much
  bigger differences between models there.
- **One machine, and one quantization per model.** Speed depends on how much of each model fits in GPU memory.
- **Ollama changed between the two dates**, so comparisons with 8 October mix Tinker's changes with Ollama's.

## Reproducing

```bash
.venv/bin/python evals/run.py --check-tasks                         # check that the tasks are fair
.venv/bin/python evals/run.py --model laguna-xs-2.1:latest          # all tasks, one model
.venv/bin/python evals/run.py --model ornith:latest --repeat 3      # repeat each task
```

Results are printed as a table, with full details (including each final answer) in `evals/results/`.
