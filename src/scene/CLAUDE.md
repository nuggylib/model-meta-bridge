# `scene/`

Reads Blender's scene and tracks when it may have changed. Read-only: nothing here modifies `bpy` data.

- `armatures.py`: `armature_names()` lists `ARMATURE` objects linked to the active scene
  (`bpy.context.scene.objects`, not `bpy.data.objects`, so objects outside the scene are left out).
- `blend_file.py`: `blend_filepath()` returns the open `.blend` file's absolute path
  (`bpy.data.filepath`), or `None` if it has never been saved.
- `changes.py`: `save_post` / `load_post` handlers that set a dirty flag and bump a revision counter.
  `take_dirty()` returns and clears the flag; `revision()` returns the counter, which never resets.
  Handlers are `@persistent` so they survive file loads.
- `meshes.py`: the mesh role queries behind the paged requests in `src/CLAUDE.md`.
  `mesh_objects()` lists scene `MESH` objects sorted by name; `summary(obj)`,
  `weight_report(target)`, `texture_report(target)`, `uv_report(target)` and
  `modifier_report(target)` build one item each. Take a target that has already been validated by
  `server.protocol`.
  - Always reads the authored mesh (`obj.data`), never an evaluated one: the spec's `modifiers`
    section is what governs modifiers.
  - `weight_report` loops over every vertex in Python (`foreach_get` can't read deform weights), so
    it is the slow one. It only looks at the requested group.
  - `uv_report` reads UVs with `foreach_get` but loops over every face corner in Python. Area is
    compared exactly, with no tolerance: forgotten-unwrap UVs are copied, not computed.
    It never reads the edit-mode BMesh; a mesh in edit mode gets the `in edit mode` error instead.
  - The default layouts `has_default_uv_map` matches against are built once per session from
    Blender's own `bmesh.ops.create_*` (in a free-standing BMesh) so they track the running
    version. The torus has no `bmesh` builder, so `_torus_layout` mirrors `add_uvs()` in
    `bl_operators/add_mesh_torus.py`. Coordinates are matched exactly, as float32.
  - `texture_report` checks a map's presence, not how it's wired: baked images usually sit in
    unlinked nodes. "Saved" means `source == "FILE"` and the resolved path is a file on disk, so an
    unsaved bake (a `GENERATED` image) fails. It reads each material's top-level node tree only,
    not node groups.
