# `addon/`

Facts about the add-on itself, not the scene.

- `manifest.py`: `VERSION` is the `version` in `src/blender_manifest.toml`, read once at import so a
  missing or broken manifest fails when the add-on is enabled, not inside the listener's timer. The
  manifest is the only place the version is written; never copy it into code.
