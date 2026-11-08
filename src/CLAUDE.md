# `src/`

The Blender extension package. Blender loads this directory as the add-on; build it with
`./build.sh` from the repo root, which writes the zip to `build/`. It finds Blender via `$BLENDER`,
else Spotlight (newest copy), else `blender` on `PATH`, and checks `blender_version_min`.

- `blender_manifest.toml`: extension metadata. Its `[build]` table keeps every `CLAUDE.md` out of
  the built zip.
- `__init__.py`: `register()` / `unregister()`, called when the add-on is enabled / disabled.
- `addon/`: facts about the add-on itself, such as its version.
- `scene/`: the read-only armature, mesh and file path queries, and the save/load dirty flag and
  revision.
- `server/`: the TCP listener and protocol messages. Not started in background mode.

## Conventions

- Relative imports only (`from . import x`); Blender extensions require it.
- Requires Blender 4.2.0 or later.
- Never modify the scene; only read it.
- No threads. All `bpy` access runs on the main thread (timers and handlers).

## Protocol

- TCP `127.0.0.1:7417`. Not listening in background mode or if the port is taken.
- One UTF-8 JSON object per line, `\n`-terminated. Every message has `"protocol": 1`.
- `{"protocol":1,"type":"get_filepath"}`: answered once with
  `{"protocol":1,"status":"success","result":{"filepath":"..."}}`, the open `.blend` file's absolute
  path, or `null` if it was never saved. Send it before subscribing to confirm the right file is open.
- `{"protocol":1,"type":"get_version"}`: answered once with
  `{"protocol":1,"status":"success","result":{"version":"0.3.0"}}`, the installed add-on's `version`
  from `blender_manifest.toml`.
- `{"protocol":1,"type":"subscribe_armatures"}`: answered with
  `{"protocol":1,"status":"success","result":{"armatures":[...]}}` on subscribe and after a save/load
  that changes the list from the last one sent on that connection.
- `{"protocol":1,"type":"subscribe_meshes"}`: answered with
  `{"protocol":1,"status":"success","result":{"meshes_revision":<n>}}` on subscribe and after every
  save/load. Pass `<n>` as `revision` to the paged requests below.
- If a save/load leaves a subscriber on a different file than the one it subscribed on, it gets the
  `get_filepath` reply for the new file instead of results, and every subscription it made on the
  old file ends. Subscribe again once the file is confirmed.
- Error: `{"protocol":1,"status":"error","message":"..."}`. Connection stays open. Messages: `busy`
  (interface locked; sent once, results follow when it unlocks), `invalid JSON`,
  `unsupported protocol: <v>`, `unknown type: <t>`, `invalid request: <detail>`, `stale`,
  `query failed: <detail>`.

### Paged requests

Every paged request carries `"revision"` (from `meshes_revision`) and `"cursor"` (`0` for the first
page), both whole numbers. The reply is
`{"protocol":1,"status":"success","result":{"revision":<n>,"<key>":[...],"has_more":<bool>,"next_cursor":<int or null>}}`.
While `has_more` is true, send the same request again with `cursor` set to `next_cursor`. A page holds
as many items as fit in about 32 KB, and at least one. If a save/load happened since `revision`, the
reply is the `stale` error: wait for the next `meshes_revision` and start again from cursor `0`.

All mesh data is the authored mesh (`obj.data`), never the modifier-evaluated one. Meshes are
`MESH` objects in the active scene, looked up by object name.

- `get_meshes` → key `meshes`: every scene mesh, sorted by name, as
  `{"name":"...","vertex_groups":[...],"material_slots":[...]}`. A slot with no material is `""`.
- `get_mesh_weights` with `"meshes":[{"mesh":"...","vertex_group":"...","weight":<number>}]` →
  key `mesh_weights`, one item per entry, in order:
  `{"mesh","vertex_group","vertices","assigned","at_weight"}`. `assigned` counts vertices in the
  group, `at_weight` those whose weight equals `weight` exactly (compared as float32, Blender's storage).
  A mesh passes when both equal `vertices`. Other groups a vertex belongs to are ignored. A missing mesh or group is
  `{"mesh","vertex_group","error":"no mesh"|"no vertex group"}`.
- `get_mesh_textures` with `"meshes":[{"mesh":"...","maps":["albedo","orm",...]}]` → key
  `mesh_textures`, one item per entry, in order: `{"mesh","maps":{"albedo":<bool>,...}}`. A map is
  `true` when an Image Texture node in any of the mesh's slot materials holds an image saved to
  disk whose filename ends with `<mesh>_<map>.png`, e.g. `wheel_hub.front.L_albedo.png`. Wiring is
  not checked. A missing mesh is `{"mesh","error":"no mesh"}`.
- `get_mesh_uvs` with `"meshes":[{"mesh":"..."}]` → key `mesh_uvs`, one item per entry, in order:
  `{"mesh","faces","has_default_uv_map","uv_maps":[{"name","zero_area"},...]}`, one `uv_maps` entry
  per UV map in Blender's order (`[]` if the mesh has none).
  - `has_default_uv_map` is `true` when any UV map still holds more than half of the UV
    coordinates an Add-menu primitive generates at its default settings: Cube, Plane, Grid, UV
    Sphere, Ico Sphere, Cylinder, Cone, Torus. Monkey's hand-made UVs and Circle don't count.
    Loop cuts, extrudes, insets, scaling and deleting a few faces keep a default; an unwrap or
    projection replaces it. Add UV Map on a mesh with no UVs reads as default. Misses: a bevel
    replaces the corner UVs, and a primitive added with non-default segments or subdivisions has a
    different layout. Cube Projection of a flat plane or grid at the default size reproduces their
    default exactly, so it reads as default. A joined mesh is default if any part is.
  - `zero_area` counts faces whose UVs enclose exactly no area (extruding copies the source UVs, so
    the new faces get these). A face with no area in 3D counts too.

  A missing mesh is `{"mesh","error":"no mesh"}`. A mesh in edit mode (e.g. the UV Editing workspace) is
  `{"mesh","error":"in edit mode"}`: Blender hides its UVs until it leaves edit mode.
- `get_mesh_modifiers` with `"meshes":[{"mesh":"..."}]` → key `mesh_modifiers`, one item per entry,
  in order: `{"mesh","modifiers":["ARMATURE","MIRROR",...]}`, each modifier's Blender type in stack
  order, repeats included (`[]` if it has none). A missing mesh is `{"mesh","error":"no mesh"}`.
- `get_duplicate_meshes` → key `duplicate_meshes`: one item per set of two or more scene meshes
  with the same shape, as `{"duplicates":["arm_top.FL","arm_top.FR",...]}`, names sorted, items
  sorted by their first name. No member is the original. Meshes with no faces are left out.
  - Same shape: vertex positions equal exactly after any move, axis swap or axis flip, joined by
    the same faces (either winding). Vertex order, object transforms, UVs and materials are
    ignored.
