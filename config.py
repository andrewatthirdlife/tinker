import json
from dataclasses import dataclass
from pathlib import Path

from permissions import Mode


@dataclass
class Config:
    host: str
    model: str
    num_ctx: int
    temperature: float
    max_iterations: int
    max_tool_output_chars: int
    sessions_dir: Path
    lint_args: list[str]
    default_mode: str
    modes: dict[str, Mode]

    def __post_init__(self):
        self.sessions_dir = Path(self.sessions_dir).expanduser()

        # Convert mode dictionaries to Mode objects
        mode_objects = {}
        for name, settings in self.modes.items():
            try:
                mode_objects[name] = Mode(name=name, **settings)
            except TypeError as e:
                raise ValueError(f"Mode {name!r} in config: {e}")

        self.modes = mode_objects

        # Validate default_mode
        if self.default_mode not in self.modes:
            raise ValueError(f"default_mode {self.default_mode!r} is not a defined mode")


def load_config(path: Path) -> Config:
    return Config(**json.loads(path.read_text()))
