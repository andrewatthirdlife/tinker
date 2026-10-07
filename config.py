import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    host: str
    model: str
    num_ctx: int
    temperature: float
    max_iterations: int
    max_tool_output_chars: int


def load_config(path: Path) -> Config:
    return Config(**json.loads(path.read_text()))
