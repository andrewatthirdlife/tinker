import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from permissions import Mode


@dataclass
class CommandSettings:
    """Settings for running commands in the sandbox."""
    timeout_seconds: int = 120
    max_output_chars: int = 20000
    allow_project_auto_approve: bool = False
    extra_read: list[str] = field(default_factory=list)  # outside the checkout, readable by commands in every project
    network: bool = False
    projects: dict[str, dict] = field(default_factory=dict)  # project path -> {"extra_read": [...], "network": bool}

    def __post_init__(self):
        for path, grants in self.projects.items():
            if not isinstance(grants, dict):
                raise ValueError(f"commands.projects[{path!r}]: settings must be an object")
            valid_keys = {"extra_read", "network"}
            if not set(grants.keys()).issubset(valid_keys):
                raise ValueError(f"commands.projects[{path!r}]: settings may only be extra_read (a list of paths) and network (true or false)")
            if "extra_read" in grants and not isinstance(grants["extra_read"], list):
                raise ValueError(f"commands.projects[{path!r}]: extra_read must be a list of paths")
            if "network" in grants and not isinstance(grants["network"], bool):
                raise ValueError(f"commands.projects[{path!r}]: network must be true or false")

    def grants_for(self, workspace: Path) -> tuple[list[str], bool]:
        """Paths outside the checkout that commands may read, and whether they may use the network, in workspace."""

        extra = list(self.extra_read)
        network = self.network
        for path, grants in self.projects.items():
            if Path(path).expanduser().resolve() == workspace.resolve():
                extra.extend(grants.get("extra_read", []))
                network = network or grants.get("network", False)
        return ([str(Path(p).expanduser()) for p in extra], network)


PROJECT_CONFIG = Path(".tinker") / "config.json"
PATH_KEYS = ("read", "deny_read", "write", "deny_write")


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
    commands: CommandSettings = field(default_factory=CommandSettings)

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

        # Convert commands dict to CommandSettings object
        if isinstance(self.commands, dict):
            try:
                self.commands = CommandSettings(**self.commands)
            except TypeError as e:
                raise ValueError(f"commands in config: {e}")


def apply_project_config(config: Config, workspace: Path) -> list[str]:
    """Merge the workspace's .tinker/config.json into config.modes and return warnings for anything ignored.

    A project may add modes or change existing ones, but its path patterns must stay inside the checkout,
    and it may only turn on automatic approval if the global config allows it.
    """

    warnings = []

    project_config_path = workspace / PROJECT_CONFIG
    if not project_config_path.exists():
        return []

    try:
        project_config = json.loads(project_config_path.read_text())
    except Exception:
        return [f"{PROJECT_CONFIG}: ignored, it must contain a JSON object"]

    if not isinstance(project_config, dict):
        return [f"{PROJECT_CONFIG}: ignored, it must contain a JSON object"]

    for key in project_config:
        if key not in ("modes", "default_mode"):
            warnings.append(f"{PROJECT_CONFIG}: ignored {key!r}; a project config may only set modes and default_mode")

    # Handle invalid "modes" value
    modes_value = project_config.get("modes", {})
    if not isinstance(modes_value, dict):
        warnings.append(f"{PROJECT_CONFIG}: ignored 'modes'; modes must be an object")
        modes = {}
    else:
        modes = modes_value

    for name, settings in modes.items():
        # Handle invalid mode settings
        if not isinstance(settings, dict):
            warnings.append(f"{PROJECT_CONFIG}: mode {name!r}: ignored, its settings must be an object")
            continue

        for key in PATH_KEYS:
            if key in settings and not isinstance(settings[key], list):
                warnings.append(f"{PROJECT_CONFIG}: mode {name!r}: ignored, {key} must be a list of patterns")
                break
        else:
            # Validate that all items in PATH_KEYS lists are strings
            for key in PATH_KEYS:
                if key in settings and isinstance(settings[key], list):
                    for item in settings[key]:
                        if not isinstance(item, str):
                            warnings.append(f"{PROJECT_CONFIG}: mode {name!r}: ignored, {key} must be a list of patterns")
                            break
                    else:
                        continue
                    break
            else:
                if name in config.modes:
                    merged = asdict(config.modes[name])
                    merged.pop("name", None)
                else:
                    merged = {}

                merged.update(settings)

                for key in PATH_KEYS:
                    if key in merged:
                        patterns = merged[key]
                        if isinstance(patterns, list):
                            filtered_patterns = []
                            for pattern in patterns:
                                if (isinstance(pattern, str) and
                                    (pattern.startswith("/") or
                                     pattern.startswith("~") or
                                     ".." in pattern.split("/"))):
                                    warnings.append(f"{PROJECT_CONFIG}: mode {name!r}: ignored {key} pattern {pattern!r}; it reaches outside the checkout")
                                else:
                                    filtered_patterns.append(pattern)
                            merged[key] = filtered_patterns

                project_approve = settings.get("approve", None)
                project_approve_commands = settings.get("approve_commands", None)

                if project_approve == "auto" or project_approve_commands == "auto":
                    if not config.commands.allow_project_auto_approve:
                        if name in config.modes:
                            merged["approve"] = getattr(config.modes[name], "approve", "ask")
                            merged["approve_commands"] = getattr(config.modes[name], "approve_commands", "ask")
                        else:
                            merged["approve"] = "ask"
                            merged["approve_commands"] = "ask"
                        if project_approve == "auto":
                            warnings.append(f"{PROJECT_CONFIG}: mode {name!r}: ignored approve: auto; the global config doesn't allow projects to turn on automatic approval")
                        if project_approve_commands == "auto":
                            warnings.append(f"{PROJECT_CONFIG}: mode {name!r}: ignored approve_commands: auto; the global config doesn't allow projects to turn on automatic approval")

                try:
                    new_mode = Mode(name=name, **merged)
                    config.modes[name] = new_mode
                except (TypeError, ValueError) as e:
                    warnings.append(f"{PROJECT_CONFIG}: mode {name!r}: ignored, {e}")

    if "default_mode" in project_config:
        value = project_config["default_mode"]
        if value in config.modes:
            config.default_mode = value
        else:
            warnings.append(f"{PROJECT_CONFIG}: ignored default_mode {value!r}; it is not a defined mode")

    return warnings


def load_config(path: Path) -> Config:
    return Config(**json.loads(path.read_text()))
