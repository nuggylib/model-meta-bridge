import json

VERSION = 1


def success(result):
    return _encode({"status": "success", "result": result})


def error(message):
    return _encode({"status": "error", "message": message})


def _encode(body):
    line = json.dumps({"protocol": VERSION, **body}, separators=(",", ":"))
    return (line + "\n").encode()


def parse_request(line, known_types):
    """Return (the request dict, None) if valid, else (None, the error message to send)."""
    try:
        request = json.loads(line)
    except ValueError:
        return None, "invalid JSON"
    if not isinstance(request, dict):
        return None, "invalid JSON"
    version = request.get("protocol")
    if isinstance(version, bool) or version != VERSION:  # bool is an int
        return None, f"unsupported protocol: {json.dumps(version)}"
    kind = request.get("type")
    if not isinstance(kind, str) or kind not in known_types:
        return None, f"unknown type: {json.dumps(kind)}"
    return request, None


def paging_problem(request):
    for key in ("revision", "cursor"):
        value = request.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return f"invalid request: `{key}` is not a whole number"
    return None


def weight_targets_problem(request):
    def valid(target):
        weight = target.get("weight")
        return (
            isinstance(target.get("mesh"), str)
            and isinstance(target.get("vertex_group"), str)
            and isinstance(weight, (int, float))
            and not isinstance(weight, bool)
        )

    return _targets_problem(request, valid, "`mesh`, `vertex_group` and `weight`")


def mesh_targets_problem(request):
    return _targets_problem(
        request, lambda target: isinstance(target.get("mesh"), str), "`mesh`"
    )


def _targets_problem(request, valid, fields):
    targets = request.get("meshes")
    if not isinstance(targets, list):
        return "invalid request: `meshes` is not a list"
    if not all(isinstance(target, dict) and valid(target) for target in targets):
        return f"invalid request: every entry in `meshes` needs {fields}"
    return None
