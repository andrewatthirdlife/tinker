"""Run a command in a sandbox: Landlock file rules, a seccomp filter, and new user, network, PID and mount namespaces.

The command can only use the paths in its policy, has no network (unless allowed), sees only its own processes,
can't create Unix sockets (Landlock ABI 3 doesn't stop connecting to sockets such as Docker's or WSL's interop
socket), and runs under resource limits. Linux x86_64 only.

This module only uses the standard library: run() starts it as a separate launcher with `python -I`, which sets
up the namespaces, forks the command as PID 1 of the new PID namespace, and applies the rest before exec.
"""

import ctypes
import fcntl
import json
import os
import resource
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field

SYSTEM_READ_ONLY = ["/usr", "/lib", "/lib64", "/lib32", "/bin", "/sbin", "/etc", "/sys"]
DEVICES = ["/dev/null", "/dev/zero", "/dev/full", "/dev/random", "/dev/urandom", "/dev/tty"]
UNAVAILABLE_EXIT = 125  # the launcher couldn't set up the sandbox

# Landlock (include/uapi/linux/landlock.h)
SYS_LANDLOCK_CREATE_RULESET, SYS_LANDLOCK_ADD_RULE, SYS_LANDLOCK_RESTRICT_SELF = 444, 445, 446
LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1
FS_RIGHTS = [
    "EXECUTE", "WRITE_FILE", "READ_FILE", "READ_DIR", "REMOVE_DIR", "REMOVE_FILE", "MAKE_CHAR", "MAKE_DIR",
    "MAKE_REG", "MAKE_SOCK", "MAKE_FIFO", "MAKE_BLOCK", "MAKE_SYM", "REFER", "TRUNCATE", "IOCTL_DEV",
]
FS = {name: 1 << i for i, name in enumerate(FS_RIGHTS)}
RIGHTS_IN_ABI = {1: 13, 2: 14, 3: 15, 4: 15, 5: 16}  # number of filesystem rights each ABI version knows
FILE_ONLY_RIGHTS = FS["EXECUTE"] | FS["WRITE_FILE"] | FS["READ_FILE"] | FS["TRUNCATE"] | FS["IOCTL_DEV"]
ACCESS = {
    "list": FS["READ_DIR"],
    "ro": FS["EXECUTE"] | FS["READ_FILE"] | FS["READ_DIR"],
    "dev": FS["READ_FILE"] | FS["WRITE_FILE"] | FS["IOCTL_DEV"],
    "rw": sum(FS.values()),
}

# seccomp (linux/seccomp.h, linux/filter.h, linux/audit.h)
PR_SET_NO_NEW_PRIVS, PR_SET_SECCOMP, PR_SET_PDEATHSIG = 38, 22, 1
SECCOMP_MODE_FILTER = 2
SECCOMP_RET_ALLOW, SECCOMP_RET_ERRNO = 0x7FFF0000, 0x00050000
AUDIT_ARCH_X86_64 = 0xC000003E
X32_SYSCALL_BIT = 0x40000000
SYS_SOCKET, SYS_IO_URING = 41, (425, 426, 427)
BPF_LD_W_ABS, BPF_JEQ_K, BPF_JGE_K, BPF_RET_K = 0x20, 0x15, 0x35, 0x06
SECCOMP_DATA_NR, SECCOMP_DATA_ARCH, SECCOMP_DATA_ARG0 = 0, 4, 16

# Namespaces and mounts
MS_REC, MS_PRIVATE, MS_NOSUID, MS_NODEV, MS_NOEXEC = 0x4000, 1 << 18, 2, 4, 8
SIOCSIFFLAGS, IFF_UP, IFF_LOOPBACK, IFF_RUNNING = 0x8914, 0x1, 0x8, 0x40

libc = ctypes.CDLL(None, use_errno=True)


class SandboxError(Exception):
    pass


@dataclass
class Limits:
    cpu_seconds: int = 300
    memory_bytes: int = 4 * 1024**3
    processes: int = 256
    file_bytes: int = 200 * 1024**2


@dataclass
class Policy:
    """What the command may access. rules maps absolute paths to "ro", "rw", "list" (directory listing only)."""

    rules: dict[str, str]
    cwd: str
    env: dict[str, str] = field(default_factory=dict)
    network: bool = False
    limits: Limits = field(default_factory=Limits)


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False


def run(argv: list[str], policy: Policy, timeout: float) -> Result:
    """Run argv in the sandbox. A private temporary directory is added and used as TMPDIR and HOME."""
    tmp = tempfile.mkdtemp(prefix="tinker-")
    try:
        system = {path: "ro" for path in [*SYSTEM_READ_ONLY, "/proc"]} | {path: "dev" for path in DEVICES}
        policy = Policy(
            rules={**system, "/dev/shm": "rw", **policy.rules, tmp: "rw"},
            cwd=policy.cwd,
            env={**policy.env, "TMPDIR": tmp, "HOME": tmp},
            network=policy.network,
            limits=policy.limits,
        )
        start = time.monotonic()
        proc = subprocess.Popen(
            [sys.executable, "-I", os.path.abspath(__file__), json.dumps(asdict(policy)), "--", *argv],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            errors="replace", start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
            timed_out = True
        if proc.returncode == UNAVAILABLE_EXIT and stderr.startswith("sandbox unavailable"):
            raise SandboxError(stderr.strip())
        return Result(proc.returncode, stdout, stderr, time.monotonic() - start, timed_out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Launcher: everything below runs in the separate `python -I sandbox.py` process.


def _check(result: int, what: str) -> None:
    if result < 0:
        raise OSError(ctypes.get_errno(), f"{what}: {os.strerror(ctypes.get_errno())}")


def _write(path: str, text: str) -> None:
    with open(path, "w") as f:
        f.write(text)


def _enter_namespaces(network: bool) -> None:
    uid, gid = os.getuid(), os.getgid()
    flags = os.CLONE_NEWUSER | os.CLONE_NEWPID | os.CLONE_NEWNS | (0 if network else os.CLONE_NEWNET)
    os.unshare(flags)
    # Keep our own uid and gid inside, so files we create are ours and tools that look up the user still work.
    _write("/proc/self/setgroups", "deny")
    _write("/proc/self/uid_map", f"{uid} {uid} 1")
    _write("/proc/self/gid_map", f"{gid} {gid} 1")
    _check(libc.mount(None, b"/", None, MS_REC | MS_PRIVATE, None), "make mounts private")
    if not network:  # the new network namespace only has a loopback interface, and it starts down
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            fcntl.ioctl(s, SIOCSIFFLAGS, struct.pack("16sH14x", b"lo", IFF_UP | IFF_LOOPBACK | IFF_RUNNING))


def _mount_private_filesystems() -> None:
    # A /proc for the new PID namespace: the command can't see, or read the environment of, any other process.
    _check(libc.mount(b"proc", b"/proc", b"proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, None), "mount /proc")
    # An empty /dev/shm (used by multiprocessing), instead of the one shared with every other process.
    _check(libc.mount(b"tmpfs", b"/dev/shm", b"tmpfs", MS_NOSUID | MS_NODEV, None), "mount /dev/shm")


def _set_limits(limits: dict) -> None:
    for name, value in (
        (resource.RLIMIT_CPU, limits["cpu_seconds"]),
        (resource.RLIMIT_AS, limits["memory_bytes"]),
        (resource.RLIMIT_NPROC, limits["processes"]),
        (resource.RLIMIT_FSIZE, limits["file_bytes"]),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(name, (value, value))


def _landlock(rules: dict[str, str]) -> None:
    abi = libc.syscall(SYS_LANDLOCK_CREATE_RULESET, None, 0, LANDLOCK_CREATE_RULESET_VERSION)
    if abi < 1:
        raise OSError(ctypes.get_errno(), "Landlock is not available")
    handled = sum(FS[name] for name in FS_RIGHTS[: RIGHTS_IN_ABI.get(abi, 16)])
    attr = struct.pack("Q", handled)  # landlock_ruleset_attr.handled_access_fs; network rules aren't used
    ruleset = libc.syscall(SYS_LANDLOCK_CREATE_RULESET, ctypes.c_char_p(attr), len(attr), 0)
    _check(ruleset, "landlock_create_ruleset")
    for path, kind in rules.items():
        try:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)  # follows links: /bin and /lib often point into /usr
        except FileNotFoundError:
            continue
        access = ACCESS[kind] & handled
        if not os.path.isdir(f"/proc/self/fd/{fd}"):
            access &= FILE_ONLY_RIGHTS
        rule = struct.pack("=Qi", access, fd)  # landlock_path_beneath_attr is packed
        _check(libc.syscall(SYS_LANDLOCK_ADD_RULE, ruleset, LANDLOCK_RULE_PATH_BENEATH, ctypes.c_char_p(rule), 0),
               f"landlock_add_rule {path}")
        os.close(fd)
    _check(libc.syscall(SYS_LANDLOCK_RESTRICT_SELF, ruleset, 0), "landlock_restrict_self")
    os.close(ruleset)


def _seccomp() -> None:
    """Refuse socket(AF_UNIX) and io_uring with EACCES; allow everything else."""

    def stmt(code, k, jt=0, jf=0):
        return struct.pack("HBBI", code, jt, jf, k)

    deny = SECCOMP_RET_ERRNO | 13  # EACCES
    program = [
        stmt(BPF_LD_W_ABS, SECCOMP_DATA_ARCH),
        stmt(BPF_JEQ_K, AUDIT_ARCH_X86_64, 1, 0),
        stmt(BPF_RET_K, deny),  # 2: any other architecture
        stmt(BPF_LD_W_ABS, SECCOMP_DATA_NR),
        stmt(BPF_JGE_K, X32_SYSCALL_BIT, 6, 0),  # x32 calls -> deny
        stmt(BPF_JEQ_K, SYS_IO_URING[0], 5, 0),
        stmt(BPF_JEQ_K, SYS_IO_URING[1], 4, 0),
        stmt(BPF_JEQ_K, SYS_IO_URING[2], 3, 0),
        stmt(BPF_JEQ_K, SYS_SOCKET, 0, 3),  # 8: not socket() -> allow
        stmt(BPF_LD_W_ABS, SECCOMP_DATA_ARG0),
        stmt(BPF_JEQ_K, socket.AF_UNIX, 0, 1),
        stmt(BPF_RET_K, deny),  # 11
        stmt(BPF_RET_K, SECCOMP_RET_ALLOW),  # 12
    ]
    code = b"".join(program)
    buffer = ctypes.create_string_buffer(code, len(code))
    fprog = struct.pack("HxxxxxxQ", len(program), ctypes.addressof(buffer))
    _check(libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "no_new_privs")
    _check(libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.c_char_p(fprog), 0, 0), "seccomp")


def _launch(policy: dict, argv: list[str]) -> int:
    try:
        _enter_namespaces(policy["network"])
    except OSError as e:
        print(f"sandbox unavailable: {e}", file=sys.stderr)
        return UNAVAILABLE_EXIT
    init = os.fork()
    if init:  # the launcher: wait for PID 1 of the new namespace
        _, status = os.waitpid(init, 0)
        return os.waitstatus_to_exitcode(status)

    # PID 1 of the new namespace. When it exits, the kernel kills everything else in the namespace.
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    try:
        _mount_private_filesystems()
    except OSError as e:
        print(f"sandbox unavailable: {e}", file=sys.stderr)
        os._exit(UNAVAILABLE_EXIT)
    command = os.fork()
    if command == 0:
        try:
            os.chdir(policy["cwd"])
            _set_limits(policy["limits"])
            _landlock(policy["rules"])
            _seccomp()
        except OSError as e:
            print(f"sandbox unavailable: {e}", file=sys.stderr)
            os._exit(UNAVAILABLE_EXIT)
        try:
            os.execvpe(argv[0], argv, policy["env"])
        except OSError as e:
            print(f"{argv[0]}: {e.strerror}", file=sys.stderr)
            os._exit(127)
    while True:  # reap everything, as PID 1 must, until the command itself exits
        pid, status = os.wait()
        if pid == command:
            os._exit(os.waitstatus_to_exitcode(status))


if __name__ == "__main__":
    separator = sys.argv.index("--")
    sys.exit(_launch(json.loads(sys.argv[1]), sys.argv[separator + 1 :]))
