import tomllib
from pathlib import Path

with (Path(__file__).parent.parent / "blender_manifest.toml").open("rb") as f:
    VERSION = tomllib.load(f)["version"]
