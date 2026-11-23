"""The server's bot registry: the single source of truth for what the API can build.

Every bot the live-play server accepts is described here once -- type name, label,
parameter schemas with defaults and ranges, and the factory that builds it. The UI in
M8 part 2 renders its forms from this, and the API rejects anything that is not here,
so adding a bot means adding one ``BotType`` entry.

The factory delegates to ``bots.base.build_bot``; the limits on this page are *input*
limits (what a client may send), while the per-call time clamp lives in
``server.games`` because it applies to every search regardless of the request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bots.base import Bot, build_bot
from eval.evaluator import available_evaluators

# Evaluator names are a closed enum, matching the evaluator factory.
EVALUATOR_CHOICES: tuple[str, ...] = available_evaluators()

# The player type that means "a person at the keyboard".
HUMAN = "human"


@dataclass(frozen=True)
class ParamSpec:
    """One parameter a client may send for a bot.

    ``kind`` is one of ``"int"``, ``"float"``, ``"bool"`` or ``"enum"`` and decides
    how the value is validated and how the UI should render it. ``nullable`` allows
    an explicit ``null`` (used to mean "no limit" for depth and time).
    """

    name: str
    kind: str
    default: Any
    help: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] | None = None
    nullable: bool = False

    def to_json(self) -> dict[str, Any]:
        """Describe the parameter for ``GET /api/bots``."""
        return {
            "name": self.name,
            "type": self.kind,
            "default": self.default,
            "min": self.minimum,
            "max": self.maximum,
            "choices": list(self.choices) if self.choices else None,
            "nullable": self.nullable,
            "help": self.help,
        }


@dataclass(frozen=True)
class BotType:
    """A bot the API can create, with its parameters."""

    type_name: str
    label: str
    description: str
    params: tuple[ParamSpec, ...] = ()

    def to_json(self) -> dict[str, Any]:
        """Describe the bot type for ``GET /api/bots``."""
        return {
            "type": self.type_name,
            "label": self.label,
            "description": self.description,
            "params": [param.to_json() for param in self.params],
            "defaults": {param.name: param.default for param in self.params},
        }


BOT_TYPES: tuple[BotType, ...] = (
    BotType(
        type_name="random",
        label="Random",
        description="Uniform random legal move. The weakest baseline.",
    ),
    BotType(
        type_name="greedy",
        label="Greedy",
        description="One-ply material with mate-in-one detection.",
    ),
    BotType(
        type_name="alphabeta",
        label="Alpha-beta",
        description=(
            "Iterative-deepening alpha-beta with move ordering and a transposition "
            "table. Depth is the maximum; the time limit stops it earlier."
        ),
        params=(
            ParamSpec(
                name="depth",
                kind="int",
                default=None,
                minimum=1,
                maximum=6,
                nullable=True,
                help="Maximum search depth; null lets the time limit choose the depth.",
            ),
            ParamSpec(
                name="time_limit_ms",
                kind="float",
                default=2000.0,
                minimum=50,
                maximum=10_000,
                nullable=True,
                help="Soft deadline per move; the server also caps it at 10 s.",
            ),
            ParamSpec(
                name="evaluator",
                kind="enum",
                default="material",
                choices=EVALUATOR_CHOICES,
                help="Evaluation function used at the leaves.",
            ),
            ParamSpec(
                name="quiescence",
                kind="bool",
                default=False,
                help="Extend the search on captures at the horizon.",
            ),
        ),
    ),
    BotType(
        type_name="mcts",
        label="MCTS",
        description="Plain UCT MCTS with random rollouts and an evaluation cutoff.",
        params=(
            ParamSpec(
                name="iterations",
                kind="int",
                default=1000,
                minimum=1,
                maximum=100_000,
                help="Iteration budget; the time limit stops it earlier if set.",
            ),
            ParamSpec(
                name="time_limit_ms",
                kind="float",
                default=None,
                minimum=50,
                maximum=10_000,
                nullable=True,
                help="Soft deadline per move; the server also caps it at 10 s.",
            ),
            ParamSpec(
                name="exploration",
                kind="float",
                default=1.4,
                minimum=0.1,
                maximum=5.0,
                help="UCT exploration constant.",
            ),
            ParamSpec(
                name="rollout_depth",
                kind="int",
                default=30,
                minimum=1,
                maximum=100,
                help="Rollout cap in plies.",
            ),
            ParamSpec(
                name="evaluator",
                kind="enum",
                default="full",
                choices=EVALUATOR_CHOICES,
                help="Evaluation used at the rollout cutoff.",
            ),
            ParamSpec(
                name="capture_bias",
                kind="float",
                default=0.0,
                minimum=0.0,
                maximum=1.0,
                help="Probability of preferring a capture during a rollout (0 = off).",
            ),
        ),
    ),
)

BOT_TYPES_BY_NAME: dict[str, BotType] = {bot.type_name: bot for bot in BOT_TYPES}
BOT_NAMES: tuple[str, ...] = tuple(BOT_TYPES_BY_NAME)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def is_human(spec: dict[str, Any]) -> bool:
    """True if a normalised spec is a human player."""
    return spec.get("type") == HUMAN


def _coerce(param: ParamSpec, value: Any) -> Any:
    """Validate one parameter value, raising ``ValueError`` naming the field."""
    if value is None:
        if param.nullable:
            return None
        raise ValueError(f"{param.name}: null is not allowed (expected {param.kind})")

    if param.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{param.name}: expected true or false")
        return value

    if param.kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{param.name}: expected a number, got {value!r}")
        if param.kind == "int" and int(value) != value:
            raise ValueError(f"{param.name}: expected a whole number, got {value!r}")
        number = int(value) if param.kind == "int" else float(value)
        if param.minimum is not None and number < param.minimum:
            raise ValueError(f"{param.name}: {number} is below the minimum {param.minimum:g}")
        if param.maximum is not None and number > param.maximum:
            raise ValueError(f"{param.name}: {number} is above the maximum {param.maximum:g}")
        return number

    if param.kind == "enum":
        if value not in (param.choices or ()):
            choices = ", ".join(param.choices or ())
            raise ValueError(f"{param.name}: {value!r} is not one of {choices}")
        return value

    raise ValueError(f"{param.name}: unknown parameter kind {param.kind!r}")


def validate_spec(spec: dict[str, Any], side: str | None = None) -> dict[str, Any]:
    """Validate a bot player spec and fill in defaults, or raise ``ValueError``.

    The error message names the side (when given) and the offending field, for
    example ``red: depth: 9 is above the maximum 6``.
    """
    prefix = f"{side}: " if side else ""
    if not isinstance(spec, dict):
        raise ValueError(f"{prefix}a player must be an object with a 'type'")
    bot_type = spec.get("type")
    if bot_type not in BOT_TYPES_BY_NAME:
        known = ", ".join(BOT_NAMES)
        raise ValueError(f"{prefix}unknown bot type {bot_type!r}; known types: {known}")

    definition = BOT_TYPES_BY_NAME[bot_type]
    params = spec.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError(f"{prefix}params must be an object")

    known_params = {param.name: param for param in definition.params}
    for name in params:
        if name not in known_params:
            raise ValueError(f"{prefix}unknown parameter {name!r} for {bot_type}")

    normalised: dict[str, Any] = {}
    for param in definition.params:
        value = params[param.name] if param.name in params else param.default
        try:
            normalised[param.name] = _coerce(param, value)
        except ValueError as error:
            raise ValueError(prefix + str(error)) from None
    return {"type": bot_type, "params": normalised}


def validate_player_spec(spec: dict[str, Any], side: str | None = None) -> dict[str, Any]:
    """Validate a player spec, which is a bot or ``{"type": "human"}``."""
    if isinstance(spec, dict) and spec.get("type") == HUMAN:
        if spec.get("params"):
            raise ValueError(f"{side + ': ' if side else ''}human players take no params")
        return {"type": HUMAN, "params": {}}
    return validate_spec(spec, side)


def build_bot_for_spec(spec: dict[str, Any], seed: int) -> Bot:
    """Build the bot described by a validated spec (``seed`` makes randomness reproducible)."""
    return build_bot(spec, seed=seed)


def bot_schema() -> list[dict[str, Any]]:
    """The registry as JSON for ``GET /api/bots``."""
    return [bot.to_json() for bot in BOT_TYPES]
