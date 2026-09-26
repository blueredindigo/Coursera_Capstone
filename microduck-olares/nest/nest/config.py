"""The Nest's configuration: one TOML file. See `config.example.toml`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 (JetPack 6's system Python)
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class DuckConfig:
    name: str
    host: str = ""
    transport: str = "webrtc"      # "webrtc" (the real LAN path) or "unix" (ssh-forwarded dev)
    socket: str = ""               # for transport = "unix"
    signalling_port: int = 8443
    http_port: int = 8080
    keep_video: bool = True
    bed: str | None = None         # a landmark name
    walk_speed: float = 0.15       # m/s: the duck's best, never lowered for tiredness
    personality: dict[str, float] = field(default_factory=dict)


@dataclass
class ReachyConfig:
    enabled: bool = True
    url: str = "http://localhost:8000"
    camera: bool = False
    detector_model: str = ""
    detector_threshold: float = 0.6
    position: tuple[float, float] = (0.0, 0.0)
    facing: float = 1.5708
    homography: list[list[float]] | None = None


@dataclass
class CaptionerConfig:
    enabled: bool = False
    url: str = "http://localhost:11434"   # Ollama on the Jetson (or Olares by day)
    model: str = "qwen2.5vl:3b"
    every_s: float = 20.0                  # per duck, and only while it is looking around
    min_confidence: float = 0.5


@dataclass
class Config:
    data_dir: Path = Path("./data")
    web_host: str = "0.0.0.0"
    web_port: int = 8090
    tick_hz: float = 1.0
    ducks: list[DuckConfig] = field(default_factory=list)
    reachy: ReachyConfig = field(default_factory=ReachyConfig)
    tiredness: dict[str, float] = field(default_factory=dict)
    safety: dict[str, Any] = field(default_factory=dict)
    earshot_m: float = 1.5
    landmarks: dict[str, tuple[float, float]] = field(default_factory=dict)
    vocabulary: list[str] = field(default_factory=list)
    captioner: CaptionerConfig = field(default_factory=CaptionerConfig)
    # Quiet hours: no motion or sound from 20:00 to 09:00 unless you tap Good morning.
    quiet_hours: dict[str, Any] = field(default_factory=dict)
    # The goodnight routine starts this long before quiet hours, so the ducks are in bed by then.
    bedtime_lead_min: int = 15


LANDMARK_WORDS = ["sofa", "green chair", "table", "rug", "door", "window", "radiator", "tv stand",
                  "bed", "kitchen", "hallway", "bookshelf", "plant", "cushion"]

# The simulated living room's furniture (`python -m nest --sim` with no config), metres.
SIM_LANDMARKS = {"sofa": (1.0, 3.2), "green chair": (3.4, 2.6), "tv stand": (2.5, 0.0),
                 "ah-ah's bed": (2.2, 0.4), "tee-tee's bed": (2.8, 0.4)}

DEFAULT_VOCABULARY = [
    *LANDMARK_WORDS,
    # things
    "yellow ball", "red ball", "ball", "plush", "block", "sock", "keys", "shoe", "cat",
    "pinecone", "leaf", "stone", "shell", "toy car", "bottle cap", "treasure",
]


def load(path: str | Path | None) -> Config:
    if path is None:
        return Config(ducks=[DuckConfig("ah-ah", "ah-ah.local", bed="ah-ah's bed"),
                             DuckConfig("tee-tee", "tee-tee.local", bed="tee-tee's bed")],
                      vocabulary=list(DEFAULT_VOCABULARY))
    raw = tomllib.loads(Path(path).read_text())
    nest = raw.get("nest", {})
    personalities = raw.get("personality", {})
    ducks = []
    for d in raw.get("duck", []):
        d = dict(d)
        d["personality"] = personalities.get(d["name"], {})
        ducks.append(DuckConfig(**d))
    r = dict(raw.get("reachy", {}))
    if "position" in r:
        r["position"] = tuple(r["position"])
    if r.get("homography") == []:
        r["homography"] = None
    return Config(
        data_dir=Path(nest.get("data_dir", "./data")),
        web_host=nest.get("web_host", "0.0.0.0"),
        web_port=int(nest.get("web_port", 8090)),
        tick_hz=float(nest.get("tick_hz", 1.0)),
        ducks=ducks,
        reachy=ReachyConfig(**r),
        tiredness=raw.get("tiredness", {}),
        safety=raw.get("safety", {}),
        earshot_m=float(raw.get("earshot", {}).get("max_m", 1.5)),
        landmarks={k: tuple(v) for k, v in raw.get("landmarks", {}).items()},
        vocabulary=list(DEFAULT_VOCABULARY) + [w for w in raw.get("vocabulary", {})
                                               .get("words", []) if w not in DEFAULT_VOCABULARY],
        captioner=CaptionerConfig(**raw.get("captioner", {})),
        quiet_hours={k: v for k, v in raw.get("quiet_hours", {}).items()
                     if k != "bedtime_lead_min"},
        bedtime_lead_min=int(raw.get("quiet_hours", {}).get("bedtime_lead_min", 15)),
    )
