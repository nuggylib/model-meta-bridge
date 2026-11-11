import socket
from typing import ClassVar

import bpy

from ..addon import manifest
from ..scene import armatures, blend_file, changes, meshes
from . import pages, protocol

HOST = "127.0.0.1"
PORT = 7417
TICK_SECONDS = 0.1

_SUBSCRIPTIONS = {
    "subscribe_armatures": lambda: {"armatures": armatures.armature_names()},
    "subscribe_meshes": lambda: {"meshes_revision": changes.revision()},
}


def _meshes_page(_request):
    objects = meshes.mesh_objects()
    return "meshes", len(objects), lambda i: meshes.summary(objects[i])


def _weights_page(request):
    targets = request["meshes"]
    return "mesh_weights", len(targets), lambda i: meshes.weight_report(targets[i])


def _textures_page(request):
    targets = request["meshes"]
    return "mesh_textures", len(targets), lambda i: meshes.texture_report(targets[i])


def _uvs_page(request):
    targets = request["meshes"]
    return "mesh_uvs", len(targets), lambda i: meshes.uv_report(targets[i])


def _modifiers_page(request):
    targets = request["meshes"]
    return (
        "mesh_modifiers",
        len(targets),
        lambda i: meshes.modifier_report(targets[i]),
    )


def _duplicates_page(_request):
    groups = meshes.duplicate_groups()
    return (
        "duplicate_meshes",
        len(groups),
        lambda i: {"duplicates": groups[i]},
    )


_PAGED = {
    "get_meshes": (None, _meshes_page),
    "get_mesh_weights": (protocol.weight_targets_problem, _weights_page),
    "get_mesh_textures": (protocol.mesh_targets_problem, _textures_page),
    "get_mesh_uvs": (protocol.mesh_targets_problem, _uvs_page),
    "get_mesh_modifiers": (protocol.mesh_targets_problem, _modifiers_page),
    "get_duplicate_meshes": (None, _duplicates_page),
}


class _Subscription:
    def __init__(self, filepath):
        self.filepath = filepath
        self.pending = True
        self.busy_sent = False
        self.last_sent = None


class _Queued:
    def __init__(self, request):
        self.request = request
        self.busy_sent = False


class _Client:
    def __init__(self, sock):
        self.sock = sock
        self.inbox = b""
        self.outbox = b""
        self.subscriptions: dict[str, _Subscription] = {}
        self.queued: list[_Queued] = []


class _State:
    server: ClassVar[socket.socket | None] = None
    clients: ClassVar[list[_Client]] = []


def start():
    if _State.server is not None:
        return
    try:
        server = socket.create_server((HOST, PORT))
    except OSError as exc:
        print(f"Model Meta Bridge: not listening on {HOST}:{PORT}: {exc}")
        return
    server.setblocking(False)
    _State.server = server
    bpy.app.timers.register(_tick, persistent=True)


def stop():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for client in _State.clients:
        client.sock.close()
    _State.clients.clear()
    if _State.server is not None:
        _State.server.close()
        _State.server = None


def _tick():
    _accept()
    for client in list(_State.clients):
        _receive(client)
    if changes.take_dirty():
        _file_changed()
    _answer_pending()
    for client in list(_State.clients):
        _flush(client)
    return TICK_SECONDS


def _accept():
    while True:
        try:
            sock, _ = _State.server.accept()
        except BlockingIOError:
            return
        sock.setblocking(False)
        _State.clients.append(_Client(sock))


def _receive(client):
    try:
        data = client.sock.recv(4096)
    except BlockingIOError:
        return
    except OSError:
        _drop(client)
        return
    if not data:
        _drop(client)
        return
    *lines, client.inbox = (client.inbox + data).split(b"\n")
    for line in lines:
        request, problem = protocol.parse_request(line, _HANDLERS)
        if problem is not None:
            client.outbox += protocol.error(problem)
        else:
            _HANDLERS[request["type"]](client, request)


def _get_filepath(client, _request):
    client.outbox += protocol.success({"filepath": blend_file.blend_filepath()})


def _get_version(client, _request):
    client.outbox += protocol.success({"version": manifest.VERSION})


def _subscribe(client, request):
    client.subscriptions[request["type"]] = _Subscription(blend_file.blend_filepath())


def _queue(client, request):
    targets_problem, _ = _PAGED[request["type"]]
    problem = protocol.paging_problem(request)
    if problem is None and targets_problem is not None:
        problem = targets_problem(request)
    if problem is not None:
        client.outbox += protocol.error(problem)
    else:
        client.queued.append(_Queued(request))


_HANDLERS = {
    "get_filepath": _get_filepath,
    "get_version": _get_version,
    **{kind: _subscribe for kind in _SUBSCRIPTIONS},
    **{kind: _queue for kind in _PAGED},
}


def _file_changed():
    path = blend_file.blend_filepath()
    for client in _State.clients:
        ended = [
            kind for kind, sub in client.subscriptions.items() if sub.filepath != path
        ]
        for kind in ended:
            del client.subscriptions[kind]
        for sub in client.subscriptions.values():
            sub.pending = True
        if ended:
            client.outbox += protocol.success({"filepath": path})


def _answer_pending():
    waiting = [
        (client, kind, sub)
        for client in _State.clients
        for kind, sub in client.subscriptions.items()
        if sub.pending
    ]
    queued = [(client, item) for client in _State.clients for item in client.queued]
    if not waiting and not queued:
        return
    if bpy.context.window_manager.is_interface_locked:
        _send_busy([*((client, sub) for client, _kind, sub in waiting), *queued])
        return
    answers = {}
    for client, kind, sub in waiting:
        if kind not in answers:
            answers[kind] = _query(kind)
        result, problem = answers[kind]
        if problem is not None:
            client.outbox += protocol.error(problem)
        elif result != sub.last_sent or sub.busy_sent:
            client.outbox += protocol.success(result)
            sub.last_sent = result
        sub.pending = sub.busy_sent = False
    for client, item in queued:
        client.outbox += _answer(item.request)
    for client in _State.clients:
        client.queued.clear()


def _send_busy(waiting):
    # "busy" names no request, so a client hears it once however many are waiting.
    told = set()
    for client, item in waiting:
        if not item.busy_sent and client not in told:
            client.outbox += protocol.error("busy")
            told.add(client)
        item.busy_sent = True


def _query(kind):
    try:
        return _SUBSCRIPTIONS[kind](), None
    except Exception as exc:  # noqa: BLE001 - sent to the client as "query failed"
        return None, f"query failed: {exc}"


def _answer(request):
    revision = request["revision"]
    if revision != changes.revision():
        return protocol.error("stale")
    _, page_of = _PAGED[request["type"]]
    try:
        key, count, build = page_of(request)
        items, next_cursor = pages.page(count, build, request["cursor"])
    except Exception as exc:  # noqa: BLE001 - sent to the client as "query failed"
        return protocol.error(f"query failed: {exc}")
    return protocol.success(
        {
            "revision": revision,
            key: items,
            "has_more": next_cursor is not None,
            "next_cursor": next_cursor,
        }
    )


def _flush(client):
    if not client.outbox:
        return
    try:
        sent = client.sock.send(client.outbox)
    except BlockingIOError:
        return
    except OSError:
        _drop(client)
        return
    client.outbox = client.outbox[sent:]


def _drop(client):
    client.sock.close()
    _State.clients.remove(client)
