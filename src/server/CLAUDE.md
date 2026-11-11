# `server/`

Serves the protocol in `src/CLAUDE.md` over TCP, without threads.

- `protocol.py`: builds outgoing lines (`success(result)`, `error(message)`) and checks incoming
  ones (`parse_request(line, known_types)`, which returns the whole request dict so handlers can read
  its parameters). `paging_problem`, `weight_targets_problem` and `mesh_targets_problem`
  (entries that need only `mesh`) return a request's `invalid request: ...` message, or `None`. Pure JSON; no `bpy`, no sockets.
- `pages.py`: `page(count, build, cursor)` fills one page from `build(i)` up to `PAGE_BYTES`. Pure.
- `listener.py`: `start()` opens a non-blocking listening socket and registers `_tick` as a
  persistent `bpy.app.timers` callback every 0.1 s; `stop()` unregisters it and closes every socket.
  `_HANDLERS` maps each request type to its handler and is the list `parse_request` accepts.
  `_SUBSCRIPTIONS` maps each subscribe type to the query that builds its `result`. A new
  subscription is one entry there; it gets a handler automatically. `_PAGED` maps each paged
  request type to its targets check and a function returning `(result key, item count, build)`.

## How a tick works

Accept new clients, read complete lines, handle a save/load if `scene.changes` saw one, answer
pending clients, write what each socket will take, drop closed ones.

- `get_filepath` and `get_version` are answered as soon as their line is read, so they never wait
  behind a subscription.
- Each client holds one `_Subscription` per subscribe type: the file it subscribed on, `pending`,
  `busy_sent` and `last_sent`. Subscribing again replaces it, so the next answer is always sent.
- On a save/load, a subscription still on the file it subscribed on is marked pending. Ones on a
  different file are removed, and the client gets the new path once, never their results.
- Each query runs at most once per tick, shared by every client waiting on it. A result is sent only
  if it differs from that subscription's `last_sent`.
- A paged request's parameters are checked as soon as its line is read; a bad one is answered with
  `invalid request: ...` at once. A valid one is queued and answered after the subscriptions in the
  same tick, so a save/load seen that tick makes it `stale`. Nothing is kept between pages: the
  client resends the request with the next cursor.
- While `is_interface_locked`, pending subscriptions and queued requests stay put and a client gets
  one `busy` error however many are waiting. When it unlocks they are all answered, subscriptions
  even if unchanged.
- Never block: every socket call is non-blocking and `BlockingIOError` means "try next tick".
