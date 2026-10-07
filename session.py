import json
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

ADJECTIVES = [
    "amber", "bold", "brave", "bright", "calm", "clever", "cosmic", "crisp", "dapper", "eager",
    "fancy", "fierce", "gentle", "giddy", "golden", "happy", "hidden", "humble", "jolly", "keen",
    "lively", "lucky", "mellow", "merry", "mighty", "misty", "nimble", "noble", "plucky", "polite",
    "proud", "quick", "quiet", "rapid", "rustic", "shiny", "silent", "silver", "sleepy", "snappy",
    "snowy", "spicy", "steady", "stormy", "sunny", "swift", "tidy", "witty", "zany", "zesty",
]
NOUNS = [
    "badger", "beaver", "bison", "camel", "cobra", "condor", "coyote", "crane", "dingo", "dolphin",
    "eagle", "falcon", "ferret", "gecko", "gibbon", "heron", "hornet", "ibis", "jackal", "jaguar",
    "koala", "lemur", "lynx", "magpie", "marmot", "marten", "moose", "newt", "ocelot", "osprey",
    "otter", "panda", "parrot", "pelican", "puffin", "python", "quokka", "raven", "salmon", "seal",
    "shark", "sloth", "stoat", "tapir", "tiger", "toucan", "walrus", "weasel", "wombat", "yak",
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Session:
    id: str
    workspace: str
    created: str
    updated: str
    dir: Path = field(repr=False)
    messages: list[dict] = field(default_factory=list, repr=False)

    @classmethod
    def create(cls, sessions_dir: Path, workspace: Path) -> "Session":
        while True:
            session_id = f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}"
            if not (sessions_dir / session_id).exists():
                break
        now = _now()
        session = cls(session_id, str(workspace.resolve()), now, now, sessions_dir / session_id)
        session.dir.mkdir(parents=True)
        session._write("session.json", session._metadata())
        return session

    @classmethod
    def load(cls, sessions_dir: Path, session_id: str) -> "Session":
        session_dir = sessions_dir / session_id
        if not session_dir.is_dir():
            raise SystemExit(f"No session '{session_id}' in {sessions_dir}")
        metadata = json.loads((session_dir / "session.json").read_text())
        messages_file = session_dir / "messages.json"
        messages = json.loads(messages_file.read_text()) if messages_file.exists() else []
        return cls(**metadata, dir=session_dir, messages=messages)

    @staticmethod
    def list_all(sessions_dir: Path) -> list["Session"]:
        if not sessions_dir.is_dir():
            return []
        sessions = [Session.load(sessions_dir, d.name) for d in sessions_dir.iterdir() if (d / "session.json").exists()]
        return sorted(sessions, key=lambda s: s.updated, reverse=True)

    @property
    def first_question(self) -> str:
        return next((m["content"] for m in self.messages if m["role"] == "user"), "")

    def save(self, messages: list[dict]) -> None:
        self.messages = messages
        self.updated = _now()
        self._write("messages.json", messages)
        self._write("session.json", self._metadata())

    def _metadata(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k not in ("dir", "messages")}

    def _write(self, name: str, data) -> None:
        tmp = self.dir / f"{name}.tmp"
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.dir / name)
