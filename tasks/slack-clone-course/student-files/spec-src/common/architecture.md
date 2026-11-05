# Architecture: build it this way

Every rule and check in `/spec` assumes this design. It is chosen so that ids, cursors and
`event_id`s are automatically the same on all three nodes, and so that a dead node or a dead
Redis cannot break anything. Do not invent a different design: there is no time to debug one.

## Files in /app

| File | What it holds |
|---|---|
| `start.sh` | The supervisor below. Stays in the foreground. |
| `requirements.txt` | Exactly one line: `aiohttp==3.10.10`. Run `pip install -r /app/requirements.txt` once. |
| `core.py` | SQLite schema and every domain function (no HTTP code). `python3 /app/core.py --init-db` creates the tables. |
| `server.py` | aiohttp app: HTTP routes and the WebSocket on the same port. `python3 /app/server.py --node N` listens on `127.0.0.1:800N`. |
| `irc.py` | IRC gateway on `0.0.0.0:6667` (rule group B6 only). Imports `core`. |

## start.sh (use exactly this)

```bash
#!/bin/bash
cd /app
mkdir -p /app/data
python3 /app/core.py --init-db
(while true; do redis-server --port 6379 --bind 127.0.0.1 --save '' --appendonly no; sleep 1; done) &
for n in 0 1 2; do (while true; do python3 /app/server.py --node $n; sleep 1; done) & done
(while true; do [ -f /app/irc.py ] && python3 /app/irc.py; sleep 1; done) &
wait
```

The `while true` loops respawn a node or Redis within about a second after it is killed.

## Design rules

1. **One database for all nodes.** Every node opens the same file `/app/data/huddle.db` with
   `sqlite3.connect(path, timeout=10, isolation_level=None)`, then `PRAGMA busy_timeout=10000`.
   `init_db()` sets `PRAGMA journal_mode=WAL` and runs `CREATE TABLE IF NOT EXISTS ...`. All ids
   are `INTEGER PRIMARY KEY`, so they are the same on every node.
2. **Every write is one transaction.** `with tx() as c:` runs `BEGIN IMMEDIATE` ... `COMMIT`
   (`ROLLBACK` on error). SQLite then serializes writers across the three processes.
3. **Durable events live in the database.** The `channels` table has `last_event_id INTEGER
   DEFAULT 0`. `emit_event(c, channel_id, kind, payload)` runs *inside the same transaction as the
   write*: `UPDATE channels SET last_event_id = last_event_id + 1 WHERE id = ? RETURNING
   last_event_id`, then inserts the full frame JSON
   `{"type": kind, "event_id": n, "channel_id": id, ...payload}` into
   `events(channel_id, event_id, payload, PRIMARY KEY(channel_id, event_id))`. This makes
   `event_id` dense per channel across all nodes and the IRC gateway.
4. **WebSocket fan-out polls the database.** Each WebSocket connection keeps
   `{channel_id: last_sent_event_id}` and a background task that, every 0.05 s, sends every row of
   `events` with a larger `event_id`, in order. This works across nodes and survives a Redis kill.
   Redis only has to be running; do not make any request depend on it.
5. **Cursors** are `base64url(json.dumps({"k": kind, "v": last_key}))` with the `=` padding
   stripped, where `kind` names the list (`"msg"`, `"rep"`, `"pin"`, `"srch"`). A cursor that
   fails to decode, is not a JSON object, has another `kind`, or has a non-integer `v` → `400`.
   Paging uses `id < v` (newest-first) or `id > v` (oldest-first), never offsets, so new rows
   never shift a page.
6. **Errors** are JSON: `{"error": "<message>"}` with the status code. Every response, including
   errors, has `Content-Type: application/json`.
7. **Check order in every handler:** `401` (no or bad token) → `404` (unknown id or slug) →
   `403` (forbidden) → `423` (archived channel, writes only) → `400` (bad body or query) →
   `409` (conflict).
8. **Timestamps:** `now_iso()` returns UTC with milliseconds and a `Z`, for example
   `2026-05-16T08:07:34.123Z`.
9. **Restarting:** `bash /spec/common/restart.sh` (add `--fresh` after changing the schema).
   Never kill processes by name.

## Function names

Use these names; the staged spec files say which step adds each one and which steps reuse it.
Never write a second copy of a function that already exists: extend it.

| Function (in `core.py` unless noted) | What it does |
|---|---|
| `connect()`, `tx()`, `init_db()`, `now_iso()` | Database access and timestamps (rules 1, 2, 8) |
| `hash_password(pw)`, `verify_password(pw, stored)` | Salted SHA-256 (`hashlib`), stored as `salt$hexdigest` |
| `issue_token(c, user_id)`, `user_for_token(token)` | `secrets.token_urlsafe(24)` tokens in a `tokens` table |
| `user_to_json(row)` | The `UserObj` |
| `server.py: json_error(status, msg)`, `require_user(request)` | Error response; bearer-token check that raises `401` |
| `workspace_to_json(row)`, `get_workspace(slug)`, `member_role(workspace_id, user_id)` | Workspace object, lookup (`404`), role or `None` |
| `add_workspace_member(c, workspace_id, user_id, role)` | Adds the membership and also joins the user to `#general` |
| `create_channel_row(c, workspace_id, name, is_private, topic, creator_id)`, `channel_to_json(row)`, `get_channel(id)` | Channel insert (creator joins it), `ChannelObj`, lookup (`404`) |
| `add_channel_member(c, channel_id, user_id)` | Inserts if missing; returns True only when it added a row |
| `join_channel(user, channel_id)` | All join rules: private, guest, open and invite-only workspaces |
| `set_member_role(...)`, `transfer_ownership(...)` | Role rules |
| `create_invitation(...)`, `accept_invitation(user, code)`, `revoke_invitation(...)`, `invitation_to_json(row)`, `update_workspace(...)` | Invitations and workspace settings |
| `can_read_channel(user_id, channel)`, `require_writable(channel)` | Access check (`403`); archived check (`423`) |
| `validate_topic(topic)`, `remove_channel_member(c, channel_id, user_id)`, `set_archived(...)`, `workspace_detail(...)` | Channel management |
| `emit_event(c, channel_id, kind, payload)` | Rule 3; returns the new `event_id` |
| `parse_paging(query, kind)`, `encode_cursor(kind, v)`, `decode_cursor(kind, s)` | `limit`, `cursor`, offset rejection (rule 5) |
| `create_message(...)`, `message_to_json(row)`, `list_messages(...)`, `list_replies(...)`, `edit_message(...)`, `delete_message(...)`, `react(...)`, `reactions_for(message_id)` | Messages, threads, reactions |
| `pin(...)`, `unpin(...)`, `list_pins(...)`, `search(...)` | Pins and search |
| `head_event_id(channel_id)`, `events_after(channel_id, since)`, `server.py: ws_handler(request)` | WebSocket subscribe, resume and live delivery |
| `resolve_mentions(c, body, channel, author_id)`, `read_state(user_id, channel_id)`, `mark_read(...)` | Mentions and read state |
| `irc.py: handle(reader, writer)`, `irc.py: deliver(conn)` | IRC gateway |

## Writing large files

A reply that is cut off is rejected and wasted. Write `core.py` and `server.py` in several
commands of at most about 250 lines each (`cat > file <<'EOF'` for the first part, then
`cat >> file <<'EOF'`), and run `python3 -m py_compile /app/core.py /app/server.py` after each.
