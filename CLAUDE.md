- This project is a coding agent written in python and using ollama for the LLM.  
- This project is being used locally only so there is no need to ever worry about backwards compatibility.
- There is an existing ollama installation running on a separate machine on the network, if you cannot contact ollama then it is most likely that the other machine has gone to sleep, not that the code is wrong.  Stop and remind the user to wake the other machine up.

## Development

- Tests: `.venv/bin/python -m pytest tests`. Tests that start a sandbox skip themselves when `TINKER_SANDBOXED` is set (a sandbox can't be started inside one), so after changing `sandbox.py`, `permissions.py` or command handling, run the tests directly, not through Tinker.
- Lint with the same rules Tinker uses (`lint_args` in config.json): `.venv/bin/python -m ruff check --select F,E9,PLE,W291,W293,Q000 .`. Ruff's own defaults are far broader and mostly style.
- Tinker (this agent) builds many of its own features. Drive it with `.venv/bin/python main.py . --mode edit -p - < prompt.txt`, one small, exact instruction per request (1–3 edits), and check every step with tests: its final answers overclaim, so trust the diff and the change report. Changes to constructors or imports can stop Tinker starting; `tests/test_startup.py` catches that.
- Commits that contain Tinker's code say so in the body and add `Co-Authored-By: Tinker (qwen3-coder via Ollama) <tinker@localhost>`.
- Try real-model behaviour in a scratch git repository, not this one: with an explicit `--mode`, one-shot runs approve changes and commands automatically.
- The sandbox needs Linux x86_64 with Landlock and unprivileged user namespaces. This WSL2 kernel has Landlock ABI 3, which doesn't cover network, Unix sockets or signals; namespaces and a seccomp filter cover those.

