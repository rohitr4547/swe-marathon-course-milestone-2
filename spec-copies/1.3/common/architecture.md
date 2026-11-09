# Architecture: build it this way

Every rule and check in `/spec` assumes this design. It makes ids, cursors and `event_id`s the same
on all three nodes, and it keeps a dead node or a dead Redis from breaking anything. Do not invent
a different design: there is no time to debug one.

## Files in /app

| File | What it holds |
|---|---|
| `start.sh` | The supervisor below. Stays in the foreground. |
| `requirements.txt` | `aiohttp==3.10.10` and `tzdata==2024.2`. Run `pip install -r /app/requirements.txt` once. |
| `core.py` | SQLite schema and every domain function (no HTTP code). `python3 /app/core.py --init-db` creates the tables. |
| `server.py` | aiohttp app: HTTP routes, the WebSocket, `GET /` and `GET /app.js` on the same port. `python3 /app/server.py --node N` listens on `127.0.0.1:800N`. |
| `irc.py` | IRC gateway on `0.0.0.0:6667`. Imports `core`. |
| `static/index.html` | The UI page: markup and inline CSS holding every pinned `data-testid`, and exactly one script element, `<script src="/app.js"></script>`, just before `</body>`. No build step, no CDN. |
| `static/app.js` | All UI behaviour in one plain-JavaScript file, served at `GET /app.js` as `application/javascript`: an `api()` helper, one function per flow, and an `init()` that binds each control exactly once. |

## start.sh (use exactly this)

```bash
#!/bin/bash
cd /app
mkdir -p /app/data/files
python3 /app/core.py --init-db
(while true; do redis-server --port 6379 --bind 127.0.0.1 --save '' --appendonly no; sleep 1; done) &
for n in 0 1 2; do (while true; do python3 /app/server.py --node $n; sleep 1; done) & done
(while true; do [ -f /app/irc.py ] && python3 /app/irc.py; sleep 1; done) &
wait
```

The `while true` loops respawn a node, Redis or the IRC gateway within about a second after it is killed.

## Design rules

1. **One database for all nodes.** Every process opens `/app/data/huddle.db` with
   `sqlite3.connect(path, timeout=10, isolation_level=None)`, then `PRAGMA busy_timeout=10000`.
   `init_db()` sets `PRAGMA journal_mode=WAL` and runs `CREATE TABLE IF NOT EXISTS ...`. All ids are
   `INTEGER PRIMARY KEY`, so they are the same on every node.
2. **Every write is one transaction.** `with tx() as c:` runs `BEGIN IMMEDIATE` ... `COMMIT`
   (`ROLLBACK` on error), so SQLite serializes writers across all processes.
3. **Channel events live in the database.** `channels.last_event_id INTEGER DEFAULT 0`.
   `emit_event(c, channel_id, kind, payload)` runs inside the same transaction as the write:
   `UPDATE channels SET last_event_id = last_event_id + 1 WHERE id = ? RETURNING last_event_id`, then
   inserts the full frame JSON `{"type": kind, "event_id": n, "channel_id": id, ...payload}` into
   `events(channel_id, event_id, payload, PRIMARY KEY(channel_id, event_id))`. Never compute an
   `event_id` as `MAX(...) + 1` outside the transaction.
4. **Global events live in the database too.** Frames that belong to no channel (only
   `user.updated`) go into `global_events(id INTEGER PRIMARY KEY, payload)`.
5. **Delivery polls the database.** Each WebSocket connection keeps `{channel_id: last_sent_event_id}`
   plus the last `global_events.id` it saw (the newest one when it connected), and a background
   task sends everything newer every 0.05 s, in order. The IRC gateway polls the same way. This works
   across nodes, survives a Redis kill and keeps median delivery far under 300 ms. Redis only has to
   be running; no request may depend on it.
6. **Presence for `@here`.** On WebSocket connect insert a row into
   `presence(conn_id TEXT PRIMARY KEY, user_id, last_seen REAL)`; refresh `last_seen` from the same
   background task every second; delete the row when the socket closes. A user is present if any
   row has `last_seen` within the last 3 seconds, so a killed node's rows simply expire.
7. **Files** are stored as `/app/data/files/<id>` with a `files(id, uploader_id, filename,
   content_type, size, created_at, message_id)` row; `message_id` is set when a post attaches it.
8. **DMs** are channels with `is_dm = 1`, `is_private = 1`, no workspace (`workspace_id NULL`) and
   exactly two members; `dms(user_a, user_b, channel_id, PRIMARY KEY(user_a, user_b))` stores the
   pair with the smaller id first.
9. **Cursors** are `base64url(json.dumps({"k": kind, "v": last_key}))` without `=` padding. A cursor
   that fails to decode, is not a JSON object, has another `kind`, or has a non-integer `v` → `400`.
10. **Errors** are JSON `{"error": "<message>"}`; every API response has
    `Content-Type: application/json`. Read the body with a helper that returns `None` for invalid
    JSON, then check each field's type before use, so bad input is a `400`, never a `500`.
11. **Check order in every handler:** `401` → `404` → `403` → `423` (writes to an archived channel)
    → `400` → `409`.
12. **Timestamps:** `now_iso()` returns UTC with milliseconds and a `Z`, e.g. `2026-05-16T08:07:34.123Z`.
13. **Restarting:** `bash /spec/common/restart.sh` (add `--fresh` after a schema change). Never kill
    processes by name.

## Function names

Use these names. Never write a second copy of a function that already exists: extend it.

| Function (in `core.py` unless noted) | What it does |
|---|---|
| `connect()`, `tx()`, `init_db()`, `now_iso()` | Database access and timestamps |
| `hash_password(pw)`, `verify_password(pw, stored)`, `issue_token(c, user_id)`, `user_for_token(token)` | Auth (salted SHA-256, `secrets.token_urlsafe(24)` tokens) |
| `user_to_json(row)` | The `UserObj` |
| `server.py: read_json(request)`, `json_error(status, msg)`, `require_user(request)` | Body parsing (`None` on bad JSON), errors, the bearer check (`401`) |
| `workspace_to_json(row)`, `get_workspace(slug)`, `member_role(workspace_id, user_id)` | Workspaces and roles |
| `add_workspace_member(c, workspace_id, user_id, role)` | Adds the membership and joins `#general` |
| `create_channel_row(...)`, `channel_to_json(row)`, `get_channel(id)`, `add_channel_member(c, ...)`, `remove_channel_member(c, ...)`, `join_channel(user, channel_id)` | Channels and membership (members change emits `member.joined` / `member.left`) |
| `can_read_channel(user_id, channel)`, `require_writable(channel)` | Access (`403`) and archived (`423`) checks |
| `emit_event(c, channel_id, kind, payload)`, `emit_global(c, payload)` | Rules 3 and 4 |
| `head_event_id(channel_id)`, `events_after(channel_id, since)`, `globals_after(since)` | Reading events for delivery |
| `parse_paging(query, kind)`, `encode_cursor(kind, v)`, `decode_cursor(kind, s)` | Paging (rule 9) |
| `create_message(user, channel_id, body)`, `message_to_json(row)`, `list_messages(...)`, `list_replies(...)` | Messages and threads |
| `resolve_mentions(c, body, channel, author_id)` | `@username`, `@channel`, `@here`, `@group-handle`; deduped, never the author |
| `react(user, message_id, emoji, add)`, `reactions_for(message_id)` | Reactions |
| `edit_message(user, message_id, body)`, `delete_message(user, message_id)` | Edits and deletes |
| `run_slash(user, channel, body)` | Slash commands; returns what `create_message` should post, or the side-effect response |
| `open_dm(user, other_id)`, `save_file(user, filename, content_type, data)`, `file_to_json(row)` | DMs and files |
| `create_group(...)`, `update_group(...)`, `delete_group(...)`, `group_to_json(row)` | User groups |
| `update_profile(user, fields)` | Profile edits; emits `user.updated` through `emit_global` |
| `touch_presence(conn_id, user_id)`, `drop_presence(conn_id)`, `present_user_ids()` | Rule 6 |
| `server.py: ws_handler(request)` | WebSocket: token check before upgrade, subscribe, resume, delivery loop |
| `irc.py: handle(reader, writer)`, `irc.py: deliver(conn)` | IRC gateway |

## Writing large files

A reply that is cut off is rejected and wasted. Write `core.py`, `server.py` and
`static/index.html` in several commands of at most about 250 lines each (`cat > file <<'EOF'`
for the first part, then `cat >> file <<'EOF'`), and run `python3 -m py_compile` on the Python
files after each part.

The UI is the one place where appending goes wrong. Write `index.html` once, with every hook, and
never add a second `<script>` to it. In `app.js`, never append another handler, override or guard for
a control that already has one: two click handlers on `send-btn` post twice, and each later patch hides
the earlier bug instead of fixing it. To change a flow, replace its function in place
(`python3 - <<'PY'` with `assert old in s` and `s.replace(old, new)`), or rewrite `app.js` from the top
with every flow it already had. After any UI change, rerun `check_e_4.sh`, `check_e_5.sh` and
`check_e_6.sh`.
