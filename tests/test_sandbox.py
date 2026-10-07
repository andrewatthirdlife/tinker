"""What a sandboxed command can and can't reach. Each test runs a small Python program inside the sandbox."""

import os
import socket
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sandbox  # noqa: E402

PYTHON = "/usr/bin/python3"  # the system interpreter: Tinker's own .venv is outside the test workspace


@pytest.fixture
def dirs(tmp_path):
    workspace, outside = tmp_path / "workspace", tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    return workspace, outside


def run(workspace, code, rules=None, timeout=30, **policy):
    rules = {str(workspace): "rw"} if rules is None else rules
    policy = sandbox.Policy(rules=rules, cwd=str(workspace), env={"PATH": "/usr/bin:/bin"}, **policy)
    return sandbox.run([PYTHON, "-c", textwrap.dedent(code)], policy, timeout)


def test_reads_and_writes_inside_the_workspace(dirs):
    workspace, _ = dirs
    result = run(workspace, "open('new.txt', 'w').write('x'); print(open('new.txt').read())")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "x"


@pytest.mark.parametrize("target", ["outside/secret.txt", "home"])
def test_cannot_read_outside_the_workspace(dirs, target):
    workspace, outside = dirs
    path = Path.home() / ".bashrc" if target == "home" else outside / "secret.txt"
    if not path.exists():
        pytest.skip(f"{path} doesn't exist")
    result = run(workspace, f"open({str(path)!r}).read()")
    assert result.returncode != 0
    assert "PermissionError" in result.stderr


def test_cannot_write_outside_the_workspace(dirs):
    workspace, outside = dirs
    result = run(workspace, f"open({str(outside / 'planted.txt')!r}, 'w')")
    assert "PermissionError" in result.stderr
    assert not (outside / "planted.txt").exists()


def test_private_temporary_directory(dirs):
    workspace, _ = dirs
    result = run(workspace, """
        import os, tempfile
        with tempfile.NamedTemporaryFile() as f:
            f.write(b"ok")
        print(os.environ["TMPDIR"] == os.environ["HOME"] == tempfile.gettempdir())
        try:
            open("/tmp/tinker-sandbox-test", "w")
        except PermissionError:
            print("shared /tmp blocked")
    """)
    assert result.stdout.split("\n")[:2] == ["True", "shared /tmp blocked"], result.stderr


def test_read_only_and_list_only_rules(dirs):
    workspace, _ = dirs
    (workspace / "docs").mkdir()
    (workspace / "docs" / "guide.md").write_text("guide")
    (workspace / "hidden").mkdir()
    (workspace / "hidden" / "key.txt").write_text("key")
    rules = {str(workspace): "list", str(workspace / "docs"): "ro", str(workspace / "hidden"): "list"}
    result = run(workspace, """
        import os
        print(open("docs/guide.md").read())
        for attempt in (lambda: open("docs/guide.md", "w"), lambda: open("hidden/key.txt").read(),
                        lambda: open("new.txt", "w")):
            try:
                attempt()
                print("ALLOWED")
            except PermissionError:
                print("blocked")
        print(sorted(os.listdir("hidden")))
    """, rules=rules)
    assert result.stdout.split("\n")[:5] == ["guide", "blocked", "blocked", "blocked", "['key.txt']"], result.stderr


def test_cannot_connect_to_unix_sockets(dirs):
    workspace, outside = dirs
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(outside / "service.sock"))
    server.listen(1)
    try:
        result = run(workspace, f"""
            import socket
            s = socket.socket(socket.AF_UNIX)
            s.connect({str(outside / "service.sock")!r})
        """)
    finally:
        server.close()
    assert result.returncode != 0
    assert "PermissionError" in result.stderr


def test_socketpair_still_works(dirs):
    workspace, _ = dirs
    result = run(workspace, "import socket; a, b = socket.socketpair(); a.send(b'hi'); print(b.recv(2).decode())")
    assert result.stdout.strip() == "hi", result.stderr


def test_io_uring_is_blocked(dirs):
    workspace, _ = dirs
    result = run(workspace, """
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        print(libc.syscall(425, 1, ctypes.create_string_buffer(120)), ctypes.get_errno())
    """)
    assert result.stdout.split() == ["-1", "13"], result.stderr  # EACCES


def test_no_network_but_loopback_works(dirs):
    workspace, _ = dirs
    result = run(workspace, """
        import socket
        try:
            socket.create_connection(("1.1.1.1", 80), timeout=3)
            print("ALLOWED")
        except OSError as e:
            print("blocked")
        server = socket.create_server(("127.0.0.1", 0))
        client = socket.create_connection(server.getsockname())
        conn, _ = server.accept()
        client.send(b"loopback")
        print(conn.recv(8).decode())
    """)
    assert result.stdout.split() == ["blocked", "loopback"], result.stderr


def test_cannot_see_or_signal_other_processes(dirs):
    workspace, _ = dirs
    result = run(workspace, f"""
        import os
        pids = sorted(int(p) for p in os.listdir("/proc") if p.isdigit())
        print(pids)
        try:
            os.kill({os.getpid()}, 0)
            print("ALLOWED")
        except ProcessLookupError:
            print("no such process")
    """)
    lines = result.stdout.split("\n")
    assert lines[0] == "[1, 2]", result.stderr  # PID 1 is the sandbox's init, PID 2 the command
    assert lines[1] == "no such process"


def test_timeout_kills_the_command(dirs):
    workspace, _ = dirs
    result = run(workspace, "import time; time.sleep(30)", timeout=1)
    assert result.timed_out
    assert result.seconds < 10


def test_process_limit(dirs):
    workspace, _ = dirs
    result = run(workspace, """
        import subprocess
        children = []
        try:
            for _ in range(100):
                children.append(subprocess.Popen(["sleep", "5"]))
            print("ALLOWED")
        except OSError:
            print(f"stopped after {len(children)}")
        for c in children:
            c.kill()
    """, limits=sandbox.Limits(processes=20))
    assert result.stdout.startswith("stopped after"), result.stdout + result.stderr


def test_memory_limit(dirs):
    workspace, _ = dirs
    result = run(workspace, """
        try:
            data = bytearray(512 * 1024 * 1024)
            print("ALLOWED")
        except MemoryError:
            print("MemoryError")
    """, limits=sandbox.Limits(memory_bytes=256 * 1024**2))
    assert result.stdout.strip() == "MemoryError", result.stderr


def test_file_size_limit(dirs):
    workspace, _ = dirs
    result = run(workspace, """
        import signal
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        try:
            open("big.bin", "wb").write(b"x" * 2_000_000)
            print("ALLOWED")
        except OSError as e:
            print("blocked")
    """, limits=sandbox.Limits(file_bytes=1_000_000))
    assert result.stdout.strip() == "blocked", result.stderr


def test_network_can_be_allowed(dirs):
    workspace, _ = dirs
    result = run(workspace, "import socket; print(len(socket.if_nameindex()) > 1)", network=True)
    assert result.stdout.strip() == "True", result.stderr


def test_missing_command(dirs):
    workspace, _ = dirs
    policy = sandbox.Policy(rules={str(workspace): "rw"}, cwd=str(workspace), env={"PATH": "/usr/bin:/bin"})
    result = sandbox.run(["no-such-command"], policy, 30)
    assert result.returncode == 127
    assert "No such file" in result.stderr
