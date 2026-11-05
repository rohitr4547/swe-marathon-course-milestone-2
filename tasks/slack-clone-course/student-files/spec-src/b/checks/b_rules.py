"""Rule code for Component B (channels and message history). Each function tests one rule group of the spec.

Imported by the check scripts next to this file; run those, not this module.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
import threading  # noqa: E402

from checklib import (CHANNEL_FIELDS, ISO_UTC, MESSAGE_FIELDS, USER_FIELDS, WS, Fail, api, baseline_ids,  # noqa: E402
                      baseline_rules, crash_rule, events_text, join, make_channel, make_workspace,
                      must_join, must_post, post, q, raw_post, register, same, uniq)

NO_ID = 999999999


def ids_of(items):
    return [str(x.get("id")) for x in items or [] if isinstance(x, dict)]


def bodies(resp, key="messages"):
    return [m.get("body") for m in resp.get(key) or [] if isinstance(m, dict)]


def b0(c):
    baseline_rules(c, "B0")
    with c.rule("B0.7", "Component B routes return 401 with no token and with a bogus token") as r:
        routes = [("GET", "/api/workspaces/nope"), ("POST", "/api/workspaces/nope/channels"),
                  ("PATCH", f"/api/channels/{NO_ID}"), ("POST", f"/api/channels/{NO_ID}/leave"),
                  ("GET", f"/api/channels/{NO_ID}/members"), ("POST", f"/api/channels/{NO_ID}/archive"),
                  ("GET", f"/api/channels/{NO_ID}/messages"), ("POST", f"/api/channels/{NO_ID}/messages"),
                  ("GET", f"/api/messages/{NO_ID}/replies"), ("GET", "/api/search?q=x"),
                  ("GET", f"/api/channels/{NO_ID}/pins"), ("POST", f"/api/messages/{NO_ID}/pin"),
                  ("GET", f"/api/channels/{NO_ID}/read"), ("POST", f"/api/channels/{NO_ID}/read")]
        wrong = []
        for method, path in routes:
            for token in (None, "bogus-token-0000000000"):
                s = api(method, path, {} if method != "GET" else None, token)
                if s.status != 401:
                    wrong.append(f"{method} {path} {'bogus' if token else 'none'}->{s.status}")
        r.check(not wrong, "expected 401 for " + "; ".join(wrong[:4]))
    with c.rule("B0.8", "unknown channel or message ids return 404 with a valid token") as r:
        u = register("bz")
        wrong = []
        for method, path in [("GET", f"/api/channels/{NO_ID}/messages"), ("POST", f"/api/channels/{NO_ID}/messages"),
                             ("GET", f"/api/channels/{NO_ID}/members"), ("PATCH", f"/api/channels/{NO_ID}"),
                             ("GET", f"/api/messages/{NO_ID}/replies"), ("POST", f"/api/messages/{NO_ID}/pin"),
                             ("GET", f"/api/channels/{NO_ID}/read")]:
            s = api(method, path, {"body": "x", "topic": "x"} if method != "GET" else None, u.token)
            if s.status != 404:
                wrong.append(f"{method} {path}->{s.status}")
        r.check(not wrong, "expected 404 for " + "; ".join(wrong[:4]))


def b0_final(c):
    crash_rule(c, "B0")


def b1(c):
    owner, mem, mem2, outsider, late = (register(p) for p in ("co", "cm", "cn", "cx", "cl"))
    ws = make_workspace(owner)
    must_join(mem, ws.general)
    must_join(mem2, ws.general)
    chan = None
    with c.rule("B1.1", "create channel: 201 {channel} with every ChannelObj field; topic '' and 'description' ignored") as r:
        name = uniq("b1")
        s = api("POST", f"/api/workspaces/{ws.slug}/channels", {"name": name, "description": "ignore me"}, owner.token)
        r.status(s, 201)
        ch = s.get("channel") or {}
        chan = ch.get("id")
        missing = [f for f in CHANNEL_FIELDS if f not in ch]
        r.check(not missing, f"channel is missing {missing}")
        r.check(ch.get("name") == name and same(ch.get("workspace_id"), ws.id), f"channel was {s.text[:90]}")
        r.check(ch.get("topic") == "", f"topic was {ch.get('topic')!r}, expected ''")
        r.check((ch.get("is_private"), ch.get("is_dm"), ch.get("is_archived")) == (False, False, False),
                "is_private/is_dm/is_archived must all be false")
    with c.rule("B1.2", "topic of 250 chars is accepted at create; 251 chars returns 400") as r:
        s = api("POST", f"/api/workspaces/{ws.slug}/channels", {"name": uniq("t"), "topic": "x" * 250}, owner.token)
        r.status(s, 201)
        r.check(s.get("channel.topic") == "x" * 250, "topic was not stored as sent")
        r.status(api("POST", f"/api/workspaces/{ws.slug}/channels", {"name": uniq("t"), "topic": "x" * 251}, owner.token), 400)
    with c.rule("B1.3", "a duplicate name returns 409; bad names (space, uppercase, 33 chars, empty) return 400") as r:
        r.check(chan is not None, "no channel from B1.1")
        dup = api("GET", f"/api/workspaces/{ws.slug}", token=owner.token)
        name = [x.get("name") for x in dup.get("channels") or [] if same(x.get("id"), chan)]
        r.check(name, "channel from B1.1 is missing in GET /api/workspaces/{slug}")
        r.status(api("POST", f"/api/workspaces/{ws.slug}/channels", {"name": name[0]}, mem.token), 409)
        wrong = []
        for bad in ("bad name", "UPPER", "x" * 33, ""):
            s = api("POST", f"/api/workspaces/{ws.slug}/channels", {"name": bad}, owner.token)
            if s.status != 400:
                wrong.append(f"{bad[:12]!r}->{s.status}")
        r.check(not wrong, "expected 400 for " + ", ".join(wrong))
    secret = make_channel(owner, ws, "sec", private=True)
    with c.rule("B1.4", "GET /api/workspaces/{slug}: 200 {workspace, channels, read_state}; hides others' private channels") as r:
        s = api("GET", f"/api/workspaces/{ws.slug}", token=mem.token)
        r.status(s, 200)
        r.check(all(k in (s.json or {}) for k in ("workspace", "channels", "read_state")), f"keys were {list(s.json or {})}")
        seen = ids_of(s.get("channels"))
        r.check(str(ws.general) in seen and str(chan) in seen, "#general or the public channel is missing")
        r.check(str(secret) not in seen, "a private channel is shown to a non-member")
        r.check(str(secret) in ids_of(api("GET", f"/api/workspaces/{ws.slug}", token=owner.token).get("channels")),
                "the private channel is missing for its member")
        r.status(api("GET", f"/api/workspaces/{ws.slug}", token=outsider.token), 403)
        r.status(api("GET", f"/api/workspaces/{uniq('zz')}", token=owner.token), 404)
    with c.rule("B1.5", "archived channels are omitted unless ?include_archived=1 or ?include_archived=true") as r:
        arch = make_channel(owner, ws, "arc")
        r.status(api("POST", f"/api/channels/{arch}/archive", {}, owner.token), 200)
        r.check(str(arch) not in ids_of(api("GET", f"/api/workspaces/{ws.slug}", token=owner.token).get("channels")),
                "archived channel listed by default")
        for flag in ("1", "true"):
            got = api("GET", f"/api/workspaces/{ws.slug}?include_archived={flag}", token=owner.token).get("channels")
            r.check(str(arch) in ids_of(got), f"archived channel missing with include_archived={flag}")
    mchan = make_channel(mem, ws, "mc")
    with c.rule("B1.6", "PATCH topic: channel member or workspace owner 200 {channel}; other member 403; 251 chars 400") as r:
        s = api("PATCH", f"/api/channels/{mchan}", {"topic": "hello"}, mem.token)
        r.status(s, 200)
        r.check(s.get("channel.topic") == "hello", f"topic was {s.get('channel.topic')!r}")
        s = api("PATCH", f"/api/channels/{mchan}", {"topic": "by owner"}, owner.token)
        r.status(s, 200)
        r.check(s.get("channel.topic") == "by owner", "owner's topic change not returned")
        r.status(api("PATCH", f"/api/channels/{mchan}", {"topic": "nope"}, mem2.token), 403)
        r.status(api("PATCH", f"/api/channels/{mchan}", {"topic": "x" * 251}, mem.token), 400)
    with c.rule("B1.7", "GET /api/channels/{id}/members: 200 {members: [UserObj]} including everyone who joined") as r:
        must_join(mem2, mchan)
        s = api("GET", f"/api/channels/{mchan}/members", token=mem.token)
        r.status(s, 200)
        members = s.get("members") or []
        r.check({str(mem.id), str(mem2.id)} <= set(ids_of(members)), f"members were {ids_of(members)}")
        missing = [f for f in USER_FIELDS if members and f not in members[0]]
        r.check(not missing, f"member objects are missing {missing}")
    with c.rule("B1.8", "leave returns 200 {channel}, again 200 (idempotent), and removes the member") as r:
        s = api("POST", f"/api/channels/{mchan}/leave", {}, mem2.token)
        r.status(s, 200)
        r.check(same(s.get("channel.id"), mchan), f"leave should return {{channel}}, got {s.text[:80]}")
        r.status(api("POST", f"/api/channels/{mchan}/leave", {}, mem2.token), 200)
        r.check(str(mem2.id) not in ids_of(api("GET", f"/api/channels/{mchan}/members", token=mem.token).get("members")),
                "user still listed after leaving")
    with c.rule("B1.9", "a user who joins the workspace through another channel is also added to #general") as r:
        must_join(late, chan)
        got = ids_of(api("GET", f"/api/channels/{ws.general}/members", token=owner.token).get("members"))
        r.check(str(late.id) in got and str(owner.id) in got, "new workspace member (or the owner) is not in #general")
    with c.rule("B1.10", "archive/unarchive are owner-only; writes to an archived channel return 423 until unarchived") as r:
        r.status(api("POST", f"/api/channels/{mchan}/archive", {}, mem.token), 403)
        s = api("POST", f"/api/channels/{mchan}/archive", {}, owner.token)
        r.status(s, 200)
        r.check(s.get("channel.is_archived") is True, "is_archived is not true after archive")
        r.status(post(mem, mchan, "while archived"), 423)
        r.status(api("PATCH", f"/api/channels/{mchan}", {"topic": "archived"}, mem.token), 423)
        r.status(api("POST", f"/api/channels/{mchan}/unarchive", {}, mem.token), 403)
        s = api("POST", f"/api/channels/{mchan}/unarchive", {}, owner.token)
        r.status(s, 200)
        r.check(s.get("channel.is_archived") is False, "is_archived is not false after unarchive")
        r.status(post(mem, mchan, "after unarchive"), 201)


def b2(c):
    owner, mem, mem2, outsider = (register(p) for p in ("mo", "mm", "mn", "mx"))
    ws = make_workspace(owner)
    must_join(mem, ws.general)
    must_join(mem2, ws.general)
    main = make_channel(owner, ws, "main")
    with c.rule("B2.1", "post: 201 {message} with every MessageObj field and correct defaults") as r:
        s = post(owner, main, "hello world")
        r.status(s, 201)
        m = s.get("message") or {}
        missing = [f for f in MESSAGE_FIELDS if f not in m]
        r.check(not missing, f"message is missing {missing}")
        r.check(m.get("body") == "hello world" and same(m.get("channel_id"), main), "body/channel_id wrong")
        r.check(same(m.get("author_id"), owner.id) and (m.get("author") or {}).get("username") == owner.username,
                "author_id/author.username do not match the poster")
        r.check(m.get("parent_id") is None and m.get("reply_count") == 0 and m.get("edited_at") is None,
                "parent_id and edited_at must be null and reply_count 0")
        r.check(m.get("files") == [] and m.get("reactions") == [] and m.get("mentions") == [],
                "files, reactions and mentions must be []")
        r.check(ISO_UTC.match(str(m.get("created_at"))), f"created_at {m.get('created_at')!r} is not UTC ISO 8601 ending in Z")
    with c.rule("B2.2", "empty, whitespace-only or missing body returns 400") as r:
        for body in ({"body": ""}, {"body": "   "}, {}):
            r.status(api("POST", f"/api/channels/{main}/messages", body, owner.token), 400)
    with c.rule("B2.3", "workspace members may post in public channels; outsiders and private non-members get 403") as r:
        r.status(post(mem, main, "member without joining"), 201)
        r.status(post(outsider, main, "outsider"), 403)
        priv = make_channel(owner, ws, "priv", private=True)
        r.status(post(mem, priv, "not a member"), 403)
        r.status(api("GET", f"/api/channels/{priv}/messages", token=mem.token), 403)
    page = make_channel(owner, ws, "page")
    for i in range(1, 6):
        must_post(owner, page, f"p{i}")
    cur1 = None
    with c.rule("B2.4", "GET messages pages newest-first with next_cursor; null on the last page") as r:
        s = api("GET", f"/api/channels/{page}/messages?limit=2", token=owner.token)
        r.status(s, 200)
        cur1 = s.get("next_cursor")
        r.check(bodies(s) == ["p5", "p4"] and isinstance(cur1, str) and cur1, f"page 1 was {bodies(s)}, next_cursor {cur1!r}")
        s = api("GET", f"/api/channels/{page}/messages" + q(limit=2, cursor=cur1), token=owner.token)
        cur2 = s.get("next_cursor")
        r.check(bodies(s) == ["p3", "p2"] and isinstance(cur2, str) and cur2, f"page 2 was {bodies(s)}")
        s = api("GET", f"/api/channels/{page}/messages" + q(limit=2, cursor=cur2), token=owner.token)
        r.check(bodies(s) == ["p1"] and "next_cursor" in (s.json or {}) and s.get("next_cursor") is None,
                f"last page was {bodies(s)} with next_cursor {s.get('next_cursor')!r}, expected ['p1'] and null")
    with c.rule("B2.5", "a cursor issued by 8000 gives the same page on 8001 and 8002") as r:
        r.check(cur1, "no cursor from B2.4")
        for port in (8001, 8002):
            s = api("GET", f"/api/channels/{page}/messages" + q(limit=2, cursor=cur1), token=owner.token, port=port)
            r.check(bodies(s) == ["p3", "p2"], f"port {port} returned {bodies(s)} ({s.status})")
    with c.rule("B2.6", "a cursor still resumes from the same place after new messages arrive") as r:
        r.check(cur1, "no cursor from B2.4")
        must_post(owner, page, "p6")
        s = api("GET", f"/api/channels/{page}/messages" + q(limit=2, cursor=cur1), token=owner.token)
        r.check(bodies(s) == ["p3", "p2"], f"old cursor returned {bodies(s)}")
        s = api("GET", f"/api/channels/{page}/messages?limit=2", token=owner.token)
        r.check(bodies(s) == ["p6", "p5"], f"first page is now {bodies(s)}")
    with c.rule("B2.7", "limit defaults to 50 when omitted") as r:
        many = make_channel(owner, ws, "many")
        for i in range(52):
            must_post(owner, many, f"n{i}")
        s = api("GET", f"/api/channels/{many}/messages", token=owner.token)
        r.status(s, 200)
        r.check(len(s.get("messages") or []) == 50 and s.get("next_cursor"), f"got {len(s.get('messages') or [])} messages")
        s = api("GET", f"/api/channels/{many}/messages" + q(cursor=s.get("next_cursor")), token=owner.token)
        r.check(bodies(s) == ["n1", "n0"] and s.get("next_cursor") is None, f"second page was {bodies(s)}")
    with c.rule("B2.8", "limit 0, 201, -1 or 'abc' returns 400; limit 200 is accepted") as r:
        for bad in ("0", "201", "-1", "abc"):
            r.status(api("GET", f"/api/channels/{page}/messages?limit={bad}", token=owner.token), 400)
        r.status(api("GET", f"/api/channels/{page}/messages?limit=200", token=owner.token), 200)
    with c.rule("B2.9", "a malformed or made-up cursor returns 400") as r:
        for bad in ("not-a-cursor", "AAAA", "eyJmb28iOiAxfQ", "%%%"):
            r.status(api("GET", f"/api/channels/{page}/messages" + q(cursor=bad), token=owner.token), 400)
    with c.rule("B2.10", "offset pagination is rejected: ?offset= or ?page= returns 400") as r:
        r.status(api("GET", f"/api/channels/{page}/messages?offset=2", token=owner.token), 400)
        r.status(api("GET", f"/api/channels/{page}/messages?page=2", token=owner.token), 400)
    with c.rule("B2.11", "replies: oldest-first and paged; parent reply_count counts them; replies stay out of the channel list") as r:
        parent = must_post(owner, main, "parent")
        reps = []
        for i in range(3):
            s = post(mem, main, f"r{i}", parent_id=parent["id"])
            r.status(s, 201)
            r.check(same(s.get("message.parent_id"), parent["id"]), "reply parent_id does not match")
            reps.append(s.get("message.id"))
        s = api("GET", f"/api/messages/{parent['id']}/replies", token=owner.token)
        r.status(s, 200)
        r.check(bodies(s, "replies") == ["r0", "r1", "r2"] and s.get("next_cursor") is None,
                f"replies were {bodies(s, 'replies')} next_cursor {s.get('next_cursor')!r}")
        s = api("GET", f"/api/messages/{parent['id']}/replies?limit=2", token=owner.token)
        r.check(bodies(s, "replies") == ["r0", "r1"] and s.get("next_cursor"), f"replies page 1 was {bodies(s, 'replies')}")
        s = api("GET", f"/api/messages/{parent['id']}/replies" + q(limit=2, cursor=s.get("next_cursor")), token=owner.token)
        r.check(bodies(s, "replies") == ["r2"] and s.get("next_cursor") is None, f"replies page 2 was {bodies(s, 'replies')}")
        top = api("GET", f"/api/channels/{main}/messages", token=owner.token).get("messages") or []
        p = [m for m in top if same(m.get("id"), parent["id"])]
        r.check(p and p[0].get("reply_count") == 3, f"parent reply_count was {p[0].get('reply_count') if p else 'missing'}")
        r.check(not set(map(str, reps)) & set(ids_of(top)), "replies appear in the channel message list")
        r.status(api("GET", f"/api/messages/{NO_ID}/replies", token=owner.token), 404)


def b3(c):
    owner, mem, outsider = register("po"), register("pm"), register("px")
    ws = make_workspace(owner)
    must_join(mem, ws.general)
    main = make_channel(owner, ws, "pins")
    m1, m2 = must_post(owner, main, "pin one"), must_post(owner, main, "pin two")
    pinned_at = None
    with c.rule("B3.1", "POST /api/messages/{id}/pin: 200 {pin: {message_id, pinned_by, pinned_at}}; repeat is idempotent") as r:
        s = api("POST", f"/api/messages/{m1['id']}/pin", {}, owner.token)
        r.status(s, 200)
        pin = s.get("pin") or {}
        pinned_at = pin.get("pinned_at")
        r.check(same(pin.get("message_id"), m1["id"]) and same(pin.get("pinned_by"), owner.id), f"pin was {s.text[:90]}")
        r.check(ISO_UTC.match(str(pinned_at)), f"pinned_at {pinned_at!r} is not UTC ISO 8601")
        s = api("POST", f"/api/messages/{m1['id']}/pin", {}, owner.token)
        r.status(s, 200)
        r.check(s.get("pin.pinned_at") == pinned_at, "re-pinning changed pinned_at")
    with c.rule("B3.2", "GET pins: newest-first [{message, pinned_by, pinned_at}], paged with next_cursor; bad cursor 400") as r:
        time.sleep(0.01)
        r.status(api("POST", f"/api/messages/{m2['id']}/pin", {}, owner.token), 200)
        s = api("GET", f"/api/channels/{main}/pins", token=owner.token)
        r.status(s, 200)
        pins = s.get("pins") or []
        r.check([str((p.get("message") or {}).get("id")) for p in pins] == [str(m2["id"]), str(m1["id"])],
                f"pins were {s.text[:120]}")
        r.check(all("pinned_by" in p and "pinned_at" in p for p in pins), "pin entries need pinned_by and pinned_at")
        r.check("next_cursor" in (s.json or {}) and s.get("next_cursor") is None, "next_cursor must be null on the last page")
        s = api("GET", f"/api/channels/{main}/pins?limit=1", token=owner.token)
        r.check(len(s.get("pins") or []) == 1 and s.get("next_cursor"), "limit=1 should give one pin and a next_cursor")
        s = api("GET", f"/api/channels/{main}/pins" + q(limit=1, cursor=s.get("next_cursor")), token=owner.token)
        r.check([str((p.get("message") or {}).get("id")) for p in s.get("pins") or []] == [str(m1["id"])], "pins page 2 wrong")
        r.status(api("GET", f"/api/channels/{main}/pins?cursor=junk", token=owner.token), 400)
    with c.rule("B3.3", "DELETE /api/messages/{id}/pin: 200 {unpinned: true}; the pin leaves the list") as r:
        s = api("DELETE", f"/api/messages/{m1['id']}/pin", token=owner.token)
        r.status(s, 200)
        r.check(s.get("unpinned") is True, f"body was {s.text[:60]}")
        pins = api("GET", f"/api/channels/{main}/pins", token=owner.token).get("pins") or []
        r.check([str((p.get("message") or {}).get("id")) for p in pins] == [str(m2["id"])], "unpinned message still listed")
    priv = make_channel(owner, ws, "pp", private=True)
    with c.rule("B3.4", "non-members of a private channel get 403 on its pin list") as r:
        r.status(api("GET", f"/api/channels/{priv}/pins", token=mem.token), 403)
        r.status(api("GET", f"/api/channels/{priv}/pins", token=owner.token), 200)
    token = uniq("zq")
    must_post(owner, main, f"alpha {token} one")
    must_post(owner, ws.general, f"beta {token} two")
    must_post(owner, priv, f"secret {token} three")
    ws2 = make_workspace(owner)
    must_post(owner, ws2.general, f"other {token} four")
    with c.rule("B3.5", "search: 200 {results, next_cursor} newest-first, case-insensitive, accessible channels only") as r:
        s = api("GET", "/api/search" + q(q=token), token=mem.token)
        r.status(s, 200)
        r.check(bodies(s, "results") == [f"beta {token} two", f"alpha {token} one"], f"member got {bodies(s, 'results')}")
        s = api("GET", "/api/search" + q(q=token.upper()), token=owner.token)
        r.check(len(s.get("results") or []) == 4, f"owner (upper-case query) got {len(s.get('results') or [])} results, expected 4")
        r.check(bodies(s, "results")[0] == f"other {token} four", "results are not newest-first")
        r.check(api("GET", "/api/search" + q(q=token), token=outsider.token).get("results") == [], "outsider sees results")
    with c.rule("B3.6", "search workspace_id=<id> limits results to that workspace") as r:
        s = api("GET", "/api/search" + q(q=token, workspace_id=ws.id), token=owner.token)
        r.status(s, 200)
        r.check(len(s.get("results") or []) == 3 and f"other {token} four" not in bodies(s, "results"),
                f"got {bodies(s, 'results')}")
    with c.rule("B3.7", "search pages with limit/cursor; limit=0 or a missing q returns 400") as r:
        s = api("GET", "/api/search" + q(q=token, limit=1), token=owner.token)
        r.check(len(s.get("results") or []) == 1 and s.get("next_cursor"), "limit=1 should give one result and a cursor")
        first = ids_of(s.get("results"))
        s = api("GET", "/api/search" + q(q=token, limit=1, cursor=s.get("next_cursor")), token=owner.token)
        r.check(len(s.get("results") or []) == 1 and ids_of(s.get("results")) != first, "page 2 repeated page 1")
        r.status(api("GET", "/api/search" + q(q=token, limit=0), token=owner.token), 400)
        r.status(api("GET", "/api/search", token=owner.token), 400)


def b4(c):
    owner, mem = register("eo"), register("em")
    ws = make_workspace(owner)
    must_join(mem, ws.general)
    ch = make_channel(owner, ws, "ev")
    must_join(mem, ch)
    socks, head = {}, None
    with c.rule("B4.1", "WebSocket on every node: subscribe returns {type: subscribed, channel_id, head_event_id}") as r:
        heads = []
        for port in (8000, 8001, 8002):
            w = WS(port, owner.token)
            r.check(w.status == 101, f"port {port} handshake returned {w.status}")
            f = w.subscribe(ch)
            r.check(f is not None, f"no 'subscribed' frame from port {port}; got {w.frames[:2]}")
            r.check(isinstance(f.get("head_event_id"), int), f"head_event_id {f.get('head_event_id')!r} is not an int")
            socks[port] = w
            heads.append(f.get("head_event_id"))
        r.check(len(set(heads)) == 1, f"nodes disagree on head_event_id: {heads}")
        head = heads[0]
    if head is None:
        raise Fail("setup: WebSocket subscribe failed (B4.1)")
    with c.rule("B4.2", "a post on 8001 reaches subscribers on 8000 and 8002 as message.created with event_id head+1") as r:
        m = must_post(owner, ch, "fan out", port=8001)
        for port in (8000, 8002):
            got = socks[port].durable(ch, 1)
            r.check(got and got[0].get("type") == "message.created", f"port {port} got {events_text(got)}")
            r.check(got[0].get("event_id") == head + 1, f"event_id was {got[0].get('event_id')}, expected {head + 1}")
            r.check(same((got[0].get("message") or {}).get("id"), m["id"]), "frame.message.id is not the posted message")
        socks[8001].durable(ch, 1)
    with c.rule("B4.3", "posts on 8000, 8001, 8002 get consecutive event_ids with no gaps or duplicates") as r:
        for port in (8000, 8001, 8002):
            must_post(owner, ch, f"dense {port}", port=port)
        for port in (8000, 8001, 8002):
            got = socks[port].durable(ch, 3)
            r.check([f.get("event_id") for f in got] == [head + 2, head + 3, head + 4], f"port {port} got {events_text(got)}")
    root = None
    with c.rule("B4.4", "every Spec B write kind is broadcast once, in order, each advancing event_id by exactly 1") as r:
        root = must_post(owner, ch, "root")
        socks[8000].durable(ch, 1)
        must_post(mem, ch, "reply", parent_id=root["id"])
        steps = [("message.reply", None),
                 ("member.left", ("POST", f"/api/channels/{ch}/leave", {}, mem)),
                 ("member.joined", ("POST", f"/api/channels/{ch}/join", {}, mem)),
                 ("channel.updated", ("PATCH", f"/api/channels/{ch}", {"topic": "new topic"}, owner)),
                 ("channel.archived", ("POST", f"/api/channels/{ch}/archive", {}, owner)),
                 ("channel.unarchived", ("POST", f"/api/channels/{ch}/unarchive", {}, owner))]
        for _, call in steps:
            if call:
                method, path, body, who = call
                s = api(method, path, body, who.token)
                r.check(s.status in (200, 201), f"{method} {path} {s.brief()}")
        got = socks[8000].durable(ch, len(steps))
        want = [k for k, _ in steps]
        r.check([f.get("type") for f in got] == want, f"expected {want}, got {events_text(got)}")
        ids = [f.get("event_id") for f in got]
        r.check(ids == list(range(head + 6, head + 6 + len(steps))), f"event_ids not consecutive: {events_text(got)}")
    with c.rule("B4.5", "no-op writes (join when joined, leave when left) emit no event") as r:
        r.check(root is not None, "setup: B4.4 could not post its message; fix B4.4 first")
        # B4.2-B4.4 committed exactly 11 events, so the head is now head + 11.
        before = head + 11
        join(mem, ch)
        api("POST", f"/api/channels/{ch}/leave", {}, mem.token)
        api("POST", f"/api/channels/{ch}/leave", {}, mem.token)
        join(mem, ch)
        join(mem, ch)
        must_post(owner, ch, "after no-ops")
        got = socks[8000].durable(ch, 3)
        r.check([f.get("type") for f in got] == ["member.left", "member.joined", "message.created"],
                f"got {events_text(got)}")
        r.check([f.get("event_id") for f in got] == [before + 1, before + 2, before + 3],
                f"event_ids not consecutive from {before}: {events_text(got)}")
    with c.rule("B4.6", "resume on another node replays events after since_event_id in order, sends 'resumed', then streams live") as r:
        # B4.2-B4.5 committed exactly 14 events.
        last = head + 14
        w = WS(8002, mem.token)
        r.check(w.status == 101, f"handshake returned {w.status}")
        w.send_json({"type": "resume", "channel_id": ch, "since_event_id": head})
        frames = []
        end = w.wait_for(lambda f: frames.append(f) or f.get("type") in ("resumed", "resume.gap"), 4)
        replay = [f for f in frames if "event_id" in f and same(f.get("channel_id"), ch)]
        r.check(end is not None and end.get("type") == "resumed", f"no 'resumed' frame; got {events_text(frames)}")
        r.check([f.get("event_id") for f in replay] == list(range(head + 1, last + 1)),
                f"replayed {events_text(replay)}, expected event_ids {head + 1}..{last}")
        r.check(end.get("head_event_id") == last, f"resumed.head_event_id {end.get('head_event_id')} != {last}")
        w.send_json({"type": "resume", "channel_id": ch, "since_event_id": last})
        f = w.wait_for(lambda f: True, 3)
        r.check(f is not None and f.get("type") == "resumed", f"resume from head should only send 'resumed', got {f}")
        must_post(owner, ch, "live after resume", port=8000)
        got = w.durable(ch, 1)
        r.check(got and got[0].get("event_id") == last + 1, f"after resume the socket got {events_text(got)} for a new post")
        w.close()
        socks[8000].durable(ch, 1)
    for w in socks.values():
        w.close()
    with c.rule("B4.7", "60 posts sent at once through all three nodes all return 201 with dense, unique event_ids") as r:
        w = WS(8000, mem.token)
        sub = w.subscribe(ch) if w.status == 101 else None
        r.check(sub is not None, "setup: subscribe failed")
        start, statuses = sub.get("head_event_id"), []

        def burst(port, k):
            for i in range(10):
                statuses.append(post(owner, ch, f"burst {port}-{k}-{i}", port=port).status)

        threads = [threading.Thread(target=burst, args=(p, k)) for p in (8000, 8001, 8002) for k in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        bad = [s for s in statuses if s != 201]
        r.check(not bad, f"{len(bad)} of 60 concurrent posts did not return 201 (statuses {sorted(set(bad))})")
        got = [f.get("event_id") for f in w.durable(ch, 60, timeout=8)]
        w.close()
        r.check(got == list(range(start + 1, start + 61)),
                f"got {len(got)} frames, {len(set(got))} unique, in order: {got == sorted(got)}; expected {start + 1}..{start + 60}")


def b5(c):
    alice, bob, carol, dave = register("ma"), register("mb"), register("mc"), register("md")
    ws = make_workspace(alice)
    for u in (bob, carol):
        must_join(u, ws.general)
    ch = make_channel(alice, ws, "men")
    must_join(bob, ch)
    must_join(carol, ch)

    def mentions(body, who=alice):
        s = post(who, ch, body)
        if s.status != 201:
            raise Fail(f"posting {body!r} {s.brief()}")
        return sorted(map(str, s.get("message.mentions") or []))

    with c.rule("B5.1", "'@username' of a workspace member resolves to their id in mentions") as r:
        r.check(mentions(f"hi @{bob.username}") == [str(bob.id)], "mentions was not [bob's id]")
        r.check(mentions(f"(@{bob.username}), see this") == [str(bob.id)], "punctuation around @bob broke the match")
    with c.rule("B5.2", "mentions are deduped and skip self, emails, in-word '@', non-members and unknown names") as r:
        cases = {f"@{bob.username} and @{bob.username}": [str(bob.id)],
                 f"note to @{alice.username}": [],
                 f"mail {bob.username}@example.com or x@{bob.username}": [],
                 f"hey@{bob.username}": [],
                 f"@{dave.username} is not in the workspace": [],
                 "@nosuchuser_zz9 hello": []}
        wrong = [f"{b!r}->{got}" for b, want in cases.items() if (got := mentions(b)) != want]
        r.check(not wrong, "; ".join(wrong)[:180])
    with c.rule("B5.3", "'@channel' expands to every channel member except the author, deduped with explicit mentions") as r:
        want = sorted([str(bob.id), str(carol.id)])
        r.check(mentions("@channel standup") == want, "@channel did not give bob and carol")
        r.check(mentions(f"@channel and @{bob.username}") == want, "@channel + @bob was not deduped")
    rc = make_channel(alice, ws, "rd")
    must_join(bob, rc)
    w = WS(8000, bob.token)
    if w.status != 101 or w.subscribe(rc) is None:
        raise Fail("setup: WebSocket subscribe failed (fix B4 first)")
    for body in ("one", f"two @{bob.username}", "three"):
        must_post(alice, rc, body)
    evs = [f.get("event_id") for f in w.durable(rc, 3)]
    w.close()
    if len(evs) != 3:
        raise Fail("setup: did not receive 3 message.created frames (fix B4 first)")
    with c.rule("B5.4", "GET read: 200 {read_state: {channel_id, last_read_event_id 0, unread_count 3, mention_count 1}}") as r:
        s = api("GET", f"/api/channels/{rc}/read", token=bob.token)
        r.status(s, 200)
        st = s.get("read_state") or {}
        r.check(same(st.get("channel_id"), rc), f"channel_id was {st.get('channel_id')!r}")
        r.check((st.get("last_read_event_id"), st.get("unread_count"), st.get("mention_count")) == (0, 3, 1),
                f"read_state was {st}")
    with c.rule("B5.5", "POST read moves the cursor forward and recounts; an older event_id never moves it back") as r:
        s = api("POST", f"/api/channels/{rc}/read", {"last_read_event_id": evs[1]}, bob.token)
        r.status(s, 200)
        st = s.get("read_state") or {}
        r.check((st.get("last_read_event_id"), st.get("unread_count"), st.get("mention_count")) == (evs[1], 1, 0),
                f"after reading to {evs[1]}: {st}")
        s = api("POST", f"/api/channels/{rc}/read", {"last_read_event_id": evs[0]}, bob.token)
        r.check(s.get("read_state.last_read_event_id") == evs[1], f"cursor moved back: {s.get('read_state')}")
        r.check(api("GET", f"/api/channels/{rc}/read", token=bob.token).get("read_state.last_read_event_id") == evs[1],
                "GET disagrees with POST")
    with c.rule("B5.6", "the reader's own messages, even self-mentions, add no unread or mention counts") as r:
        must_post(bob, rc, f"@{bob.username} reminder")
        st = api("GET", f"/api/channels/{rc}/read", token=bob.token).get("read_state") or {}
        r.check((st.get("unread_count"), st.get("mention_count")) == (1, 0), f"read_state was {st}")
    with c.rule("B5.7", "GET /api/workspaces/{slug} read_state is a list holding the same object for each channel") as r:
        s = api("GET", f"/api/workspaces/{ws.slug}", token=bob.token)
        lst = s.get("read_state")
        r.check(isinstance(lst, list), f"read_state is {type(lst).__name__}, expected a list")
        mine = [x for x in lst if isinstance(x, dict) and same(x.get("channel_id"), rc)]
        r.check(mine and (mine[0].get("last_read_event_id"), mine[0].get("unread_count"), mine[0].get("mention_count"))
                == (evs[1], 1, 0), f"entry for the channel was {mine[:1]}")
    with c.rule("B5.8", "POST read with a non-integer returns 400; private-channel non-members get 403") as r:
        r.status(api("POST", f"/api/channels/{rc}/read", {"last_read_event_id": "abc"}, bob.token), 400)
        priv = make_channel(alice, ws, "rp", private=True)
        r.status(api("GET", f"/api/channels/{priv}/read", token=carol.token), 403)
    with c.rule("B5.9", "wrong JSON types or a malformed JSON body on Spec B routes return 400, never 500") as r:
        m = must_post(alice, rc, "type probe")
        cases = [("POST", f"/api/workspaces/{ws.slug}/channels", {"name": uniq("ty"), "topic": 7}),
                 ("POST", f"/api/workspaces/{ws.slug}/channels", {"name": 12}),
                 ("PATCH", f"/api/channels/{rc}", {"topic": None}),
                 ("POST", f"/api/channels/{rc}/messages", {"body": 42}),
                 ("POST", f"/api/channels/{rc}/messages", {"body": "x", "parent_id": "abc"}),
                 ("POST", f"/api/channels/{rc}/read", {"last_read_event_id": None}),
                 ("POST", f"/api/channels/{rc}/read", {"last_read_event_id": True})]
        wrong = [f"{mt} {p.split('/api/')[1][:28]} {b}->{s}" for mt, p, b in cases
                 if (s := api(mt, p, b, alice.token).status) != 400]
        s = raw_post(f"/api/channels/{rc}/messages", b"{not json", alice.token)
        if s != 400:
            wrong.append(f"malformed JSON on POST messages->{s}")
        r.check(not wrong, "expected 400 for " + "; ".join(wrong)[:200])


GROUPS = [
    ("B0", b0, baseline_ids("B0") + ["B0.7", "B0.8"], b0_final),
    ("B1", b1, [f"B1.{i}" for i in range(1, 11)], None),
    ("B2", b2, [f"B2.{i}" for i in range(1, 12)], None),
    ("B3", b3, [f"B3.{i}" for i in range(1, 8)], None),
    ("B4", b4, [f"B4.{i}" for i in range(1, 8)], None),
    ("B5", b5, [f"B5.{i}" for i in range(1, 10)], None),
]
