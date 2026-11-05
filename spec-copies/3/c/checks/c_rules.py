"""Rule code for Component C (features built on messages). Each function tests one rule group of the spec.

Run through the check scripts next to this file (check_c_*.sh), not directly.
"""
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
from checklib import (ISO_UTC, USER_FIELDS, WS, Fail, api, events_text, fetch, make_channel,  # noqa: E402
                      make_workspace, must_join, must_post, post, register, run, same, uniq, upload)

NO_ID = 999999999
SHRUG = "¯\\_(ツ)_/¯"
TEN_MIB = 10 * 1024 * 1024


def mentions_of(resp):
    return sorted(str(x) for x in resp.get("message.mentions") or [])


def c0(c):
    with c.rule("C0.1", "Component C routes return 401 with no token and with a bogus token") as r:
        routes = [("POST", f"/api/messages/{NO_ID}/reactions"), ("DELETE", f"/api/messages/{NO_ID}/reactions/x"),
                  ("POST", "/api/dms"), ("POST", "/api/files"), ("GET", f"/api/files/{NO_ID}"),
                  ("GET", f"/api/files/{NO_ID}/download"), ("GET", "/api/workspaces/nope/groups"),
                  ("POST", "/api/workspaces/nope/groups"), ("PATCH", "/api/workspaces/nope/groups/x"),
                  ("DELETE", "/api/workspaces/nope/groups/x"), ("GET", "/api/users/1"), ("PATCH", "/api/users/me")]
        wrong = []
        for method, path in routes:
            for token in (None, "bogus-token-0000000000"):
                s = api(method, path, {} if method in ("POST", "PATCH") else None, token)
                if s.status != 401:
                    wrong.append(f"{method} {path} {'bogus' if token else 'none'}->{s.status}")
        r.check(not wrong, "expected 401 for " + "; ".join(wrong[:4]))
    with c.rule("C0.2", "unknown message, file, user or workspace ids return 404 with a valid token") as r:
        u = register("cz")
        wrong = []
        for method, path, body in [("POST", f"/api/messages/{NO_ID}/reactions", {"emoji": "x"}),
                                   ("GET", f"/api/files/{NO_ID}", None), ("GET", f"/api/files/{NO_ID}/download", None),
                                   ("GET", f"/api/users/{NO_ID}", None), ("POST", "/api/dms", {"user_id": NO_ID}),
                                   ("GET", f"/api/workspaces/{uniq('zz')}/groups", None)]:
            s = api(method, path, body, u.token)
            if s.status != 404:
                wrong.append(f"{method} {path}->{s.status}")
        r.check(not wrong, "expected 404 for " + "; ".join(wrong[:4]))


def c1(c):
    owner, mem = register("ro"), register("rm")
    ws = make_workspace(owner)
    must_join(mem, ws.general)
    ch = make_channel(owner, ws, "rx")
    must_join(mem, ch)
    m = must_post(owner, ch, "react to me")
    path = f"/api/messages/{m['id']}/reactions"
    party = urllib.parse.quote("🎉")
    with c.rule("C1.1", "first reaction: POST returns 200 (not 201) {reactions: [{emoji, count, user_ids}]}") as r:
        s = api("POST", path, {"emoji": "thumbsup"}, owner.token)
        r.status(s, 200)
        r.check(s.get("reactions") == [{"emoji": "thumbsup", "count": 1, "user_ids": [owner.id]}], f"reactions were {s.text[:100]}")
    with c.rule("C1.2", "the same user and emoji again changes nothing; a second user makes count 2 in reaction order") as r:
        s = api("POST", path, {"emoji": "thumbsup"}, owner.token)
        r.status(s, 200)
        r.check(s.get("reactions") == [{"emoji": "thumbsup", "count": 1, "user_ids": [owner.id]}], f"repeat changed it: {s.text[:100]}")
        s = api("POST", path, {"emoji": "thumbsup"}, mem.token)
        r.check(s.get("reactions") == [{"emoji": "thumbsup", "count": 2, "user_ids": [owner.id, mem.id]}],
                f"after a second user: {s.text[:100]}")
    with c.rule("C1.3", "a unicode emoji works in POST and as the URL-encoded {emoji} in DELETE; emojis keep first-added order") as r:
        s = api("POST", path, {"emoji": "🎉"}, mem.token)
        r.status(s, 200)
        r.check([x.get("emoji") for x in s.get("reactions") or []] == ["thumbsup", "🎉"], f"order was {s.text[:120]}")
        s = api("DELETE", f"{path}/{party}", token=mem.token)
        r.status(s, 200)
        r.check([x.get("emoji") for x in s.get("reactions") or []] == ["thumbsup"], f"🎉 not removed: {s.text[:120]}")
    with c.rule("C1.4", "DELETE removes only the caller's reaction; an emoji with no users left is dropped") as r:
        s = api("DELETE", f"{path}/thumbsup", token=owner.token)
        r.status(s, 200)
        r.check(s.get("reactions") == [{"emoji": "thumbsup", "count": 1, "user_ids": [mem.id]}], f"after delete: {s.text[:100]}")
        s = api("DELETE", f"{path}/{party}", token=owner.token)
        r.status(s, 200)
        r.check(s.get("reactions") == [{"emoji": "thumbsup", "count": 1, "user_ids": [mem.id]}], "removing an absent reaction changed the list")
        s = api("DELETE", f"{path}/thumbsup", token=mem.token)
        r.check(s.get("reactions") == [], f"emoji with no users left was kept: {s.text[:100]}")
    with c.rule("C1.5", "message objects in GET /api/channels/{id}/messages carry the same reactions list") as r:
        api("POST", path, {"emoji": "eyes"}, mem.token)
        msgs = api("GET", f"/api/channels/{ch}/messages", token=owner.token, port=8001).get("messages") or []
        got = [x.get("reactions") for x in msgs if same(x.get("id"), m["id"])]
        r.check(got == [[{"emoji": "eyes", "count": 1, "user_ids": [mem.id]}]], f"listed reactions were {got}")
    with c.rule("C1.6", "reaction.added / reaction.removed events: one each, consecutive event_ids; repeats emit nothing") as r:
        w = WS(8000, owner.token)
        sub = w.subscribe(ch) if w.status == 101 else None
        r.check(sub is not None, "setup: WebSocket subscribe failed")
        head = sub.get("head_event_id")
        api("POST", path, {"emoji": "rocket"}, owner.token)
        api("POST", path, {"emoji": "rocket"}, owner.token)
        api("DELETE", f"{path}/rocket", token=owner.token)
        api("DELETE", f"{path}/rocket", token=owner.token)
        must_post(owner, ch, "after reactions")
        got = w.durable(ch, 3)
        w.close()
        r.check([f.get("type") for f in got] == ["reaction.added", "reaction.removed", "message.created"], f"got {events_text(got)}")
        r.check([f.get("event_id") for f in got] == [head + 1, head + 2, head + 3], f"event_ids not consecutive: {events_text(got)}")
        r.check(same(got[0].get("message_id"), m["id"]) and got[0].get("emoji") == "rocket" and same(got[0].get("user_id"), owner.id),
                f"reaction.added payload was {got[0]}")
    with c.rule("C1.7", "empty, non-string or missing emoji returns 400; a non-member of a private channel gets 403") as r:
        for body in ({"emoji": ""}, {"emoji": 5}, {}):
            r.status(api("POST", path, body, owner.token), 400)
        priv = make_channel(owner, ws, "rp", private=True)
        pm = must_post(owner, priv, "private")
        r.status(api("POST", f"/api/messages/{pm['id']}/reactions", {"emoji": "x"}, mem.token), 403)


def c2(c):
    alice, bob, eve = register("da"), register("db"), register("de")
    dm = None
    with c.rule("C2.1", "POST /api/dms returns 200 {channel_id}; the same id both directions and on repeat") as r:
        s = api("POST", "/api/dms", {"user_id": bob.id}, alice.token)
        r.status(s, 200)
        dm = s.get("channel_id")
        r.check(dm is not None, f"no channel_id in {s.text[:80]}")
        again = api("POST", "/api/dms", {"user_id": bob.id}, alice.token, port=8001).get("channel_id")
        back = api("POST", "/api/dms", {"user_id": alice.id}, bob.token, port=8002).get("channel_id")
        r.check(same(again, dm) and same(back, dm), f"ids were {dm}, {again} (repeat), {back} (reverse)")
    with c.rule("C2.2", "both people can post and read in the DM; anyone else gets 403") as r:
        r.check(dm is not None, "no DM from C2.1")
        r.status(post(alice, dm, "hi bob"), 201)
        r.status(post(bob, dm, "hi alice"), 201)
        got = [m.get("body") for m in api("GET", f"/api/channels/{dm}/messages", token=bob.token).get("messages") or []]
        r.check(got == ["hi alice", "hi bob"], f"DM history was {got}")
        r.status(api("GET", f"/api/channels/{dm}/messages", token=eve.token), 403)
        r.status(post(eve, dm, "intruder"), 403)
    with c.rule("C2.3", "POST /api/dms with an unknown user returns 404; a missing or non-integer user_id returns 400") as r:
        r.status(api("POST", "/api/dms", {"user_id": NO_ID}, alice.token), 404)
        r.status(api("POST", "/api/dms", {}, alice.token), 400)
        r.status(api("POST", "/api/dms", {"user_id": "bob"}, alice.token), 400)
    data = os.urandom(2048) + b"end"
    fid = None
    with c.rule("C2.4", "POST /api/files (multipart 'file') returns 201 {file: {id, uploader_id, filename, content_type, size, created_at}}") as r:
        s = upload(alice.token, "notes.bin", data, "application/octet-stream")
        r.status(s, 201)
        f = s.get("file") or {}
        fid = f.get("id")
        r.check(same(f.get("uploader_id"), alice.id) and f.get("filename") == "notes.bin", f"file was {s.text[:120]}")
        r.check(f.get("content_type") == "application/octet-stream" and f.get("size") == len(data), f"content_type/size were {f.get('content_type')}/{f.get('size')}")
        r.check(ISO_UTC.match(str(f.get("created_at"))), f"created_at {f.get('created_at')!r} is not UTC ISO 8601")
    with c.rule("C2.5", "GET /api/files/{id} returns 200 {file} on every node") as r:
        r.check(fid is not None, "no file from C2.4")
        for port in (8000, 8001, 8002):
            s = api("GET", f"/api/files/{fid}", token=bob.token, port=port)
            r.status(s, 200)
            r.check(same(s.get("file.id"), fid) and s.get("file.size") == len(data), f"port {port}: {s.text[:90]}")
    with c.rule("C2.6", "GET /api/files/{id}/download returns the exact bytes, the Content-Type, and the filename in Content-Disposition") as r:
        r.check(fid is not None, "no file from C2.4")
        txt = b"hello, plain text\n"
        tid = upload(alice.token, "hello.txt", txt, "text/plain").get("file.id")
        for file_id, want, ctype, name in ((fid, data, "application/octet-stream", "notes.bin"), (tid, txt, "text/plain", "hello.txt")):
            st, hdr, body = fetch(f"/api/files/{file_id}/download", alice.token, port=8002)
            r.check(st == 200 and body == want, f"{name}: status {st}, {len(body)} bytes (expected {len(want)})")
            r.check(hdr.get("content-type", "").startswith(ctype), f"{name}: Content-Type was {hdr.get('content-type')!r}")
            r.check(name in hdr.get("content-disposition", ""), f"{name}: Content-Disposition was {hdr.get('content-disposition')!r}")
    with c.rule("C2.7", "an upload over 10 MiB returns 413") as r:
        s = upload(alice.token, "big.bin", b"\0" * (TEN_MIB + 1))
        r.status(s, 413)
    ws = make_workspace(alice)
    must_join(bob, ws.general)
    with c.rule("C2.8", "file_ids on a post attach the caller's own unattached files; reuse, someone else's or unknown ids return 400") as r:
        f1 = upload(alice.token, "a.txt", b"one", "text/plain").get("file.id")
        f2 = upload(bob.token, "b.txt", b"two", "text/plain").get("file.id")
        r.check(f1 is not None and f2 is not None, "setup: upload failed")
        s = post(alice, ws.general, "with file", file_ids=[f1])
        r.status(s, 201)
        files = s.get("message.files") or []
        r.check([str(x.get("id")) for x in files] == [str(f1)] and files[0].get("filename") == "a.txt", f"message.files was {files}")
        r.status(post(alice, ws.general, "again", file_ids=[f1]), 400)
        r.status(post(alice, ws.general, "not mine", file_ids=[f2]), 400)
        r.status(post(alice, ws.general, "unknown", file_ids=[NO_ID]), 400)
        listed = api("GET", f"/api/channels/{ws.general}/messages?limit=5", token=bob.token).get("messages") or []
        r.check(any([str(x.get("id")) for x in m.get("files") or []] == [str(f1)] for m in listed), "attached file missing from history")


def c3(c):
    owner, mem, carol = register("so"), register("sm"), register("sc")
    ws = make_workspace(owner)
    for u in (mem, carol):
        must_join(u, ws.general)
    ch = make_channel(owner, ws, "sl")
    must_join(mem, ch)

    def say(user, body):
        return post(user, ch, body)

    with c.rule("C3.1", "'/me <text>' posts a normal message whose body stays '/me <text>'; '/me' with no text returns 400") as r:
        s = say(mem, "/me waves hello")
        r.status(s, 201)
        r.check(s.get("message.body") == "/me waves hello", f"body was {s.get('message.body')!r}")
        r.status(say(mem, "/me"), 400)
        r.status(say(mem, "/me   "), 400)
    with c.rule("C3.2", "'/shrug <text>' posts '<text> " + SHRUG + "'; '/shrug' alone posts just the emoticon") as r:
        s = say(mem, "/shrug ok then")
        r.status(s, 201)
        r.check(s.get("message.body") == f"ok then {SHRUG}", f"body was {s.get('message.body')!r}")
        s = say(mem, "/shrug")
        r.status(s, 201)
        r.check(s.get("message.body") == SHRUG, f"body was {s.get('message.body')!r}")
    with c.rule("C3.3", "'//text' posts a literal message whose body is '/text'; an unknown command returns 400") as r:
        s = say(mem, "//etc/hosts is a file")
        r.status(s, 201)
        r.check(s.get("message.body") == "/etc/hosts is a file", f"body was {s.get('message.body')!r}")
        r.status(say(mem, "/frobnicate now"), 400)
    with c.rule("C3.4", "'/topic <text>' returns 201 {message: null, channel} with the new topic") as r:
        s = say(mem, "/topic Release day")
        r.status(s, 201)
        r.check((s.json or {}).get("message", "missing") is None and s.get("channel.topic") == "Release day", f"response was {s.text[:120]}")
        chans = api("GET", f"/api/workspaces/{ws.slug}", token=owner.token).get("channels") or []
        r.check(any(same(x.get("id"), ch) and x.get("topic") == "Release day" for x in chans), "topic not saved")
    with c.rule("C3.5", "'/invite @user' returns 201 {message: null, invited_user} and adds them to the channel") as r:
        s = say(mem, f"/invite @{carol.username}")
        r.status(s, 201)
        r.check((s.json or {}).get("message", "missing") is None and same(s.get("invited_user.id"), carol.id), f"response was {s.text[:120]}")
        members = [str(x.get("id")) for x in api("GET", f"/api/channels/{ch}/members", token=owner.token).get("members") or []]
        r.check(str(carol.id) in members, "invited user is not a channel member")
    with c.rule("C3.6", "'/archive' and '/unarchive' by the workspace owner return 201 {message: null, channel}; others get 403") as r:
        r.status(say(mem, "/archive"), 403)
        s = say(owner, "/archive")
        r.status(s, 201)
        r.check((s.json or {}).get("message", "missing") is None and s.get("channel.is_archived") is True, f"response was {s.text[:120]}")
        r.status(say(mem, "plain message while archived"), 423)
        s = say(owner, "/unarchive")
        r.status(s, 201)
        r.check(s.get("channel.is_archived") is False, f"response was {s.text[:120]}")
        r.status(say(mem, "after unarchive"), 201)


def c4(c):
    owner, ann, ben, cat, outsider = (register(p) for p in ("go", "ga", "gb", "gc", "gx"))
    ws = make_workspace(owner)
    for u in (ann, ben, cat):
        must_join(u, ws.general)
    gpath = f"/api/workspaces/{ws.slug}/groups"
    handle = uniq("des")
    with c.rule("C4.1", "any member creates a group: 201 {group: {id, handle, name, member_user_ids}}; GET lists it") as r:
        s = api("POST", gpath, {"handle": handle, "name": "Design", "user_ids": [ann.id, ben.id]}, cat.token)
        r.status(s, 201)
        g = s.get("group") or {}
        r.check(g.get("handle") == handle and g.get("name") == "Design" and "id" in g, f"group was {s.text[:120]}")
        r.check(sorted(map(str, g.get("member_user_ids") or [])) == sorted([str(ann.id), str(ben.id)]), f"member_user_ids was {g.get('member_user_ids')}")
        r.check("members" not in g, "responses use member_user_ids, not members")
        s = api("GET", gpath, token=ann.token, port=8002)
        r.status(s, 200)
        r.check(handle in [x.get("handle") for x in s.get("groups") or []], "group missing from GET")
    with c.rule("C4.2", "duplicate handle 409; bad handle, missing handle or a non-member in user_ids 400; outsiders 403") as r:
        r.status(api("POST", gpath, {"handle": handle, "name": "Again", "user_ids": []}, ann.token), 409)
        r.status(api("POST", gpath, {"handle": "Bad Handle", "name": "x", "user_ids": []}, ann.token), 400)
        r.status(api("POST", gpath, {"name": "x", "user_ids": []}, ann.token), 400)
        r.status(api("POST", gpath, {"handle": uniq("g"), "name": "x", "user_ids": [outsider.id]}, ann.token), 400)
        r.status(api("POST", gpath, {"handle": uniq("g"), "name": "x", "user_ids": []}, outsider.token), 403)
        r.status(api("GET", gpath, token=outsider.token), 403)
    with c.rule("C4.3", "PATCH by the creator or workspace owner 200 {group}; other members 403; unknown handle 404") as r:
        s = api("PATCH", f"{gpath}/{handle}", {"user_ids": [ann.id, ben.id, cat.id]}, cat.token)
        r.status(s, 200)
        r.check(len(s.get("group.member_user_ids") or []) == 3, f"PATCH result was {s.text[:120]}")
        s = api("PATCH", f"{gpath}/{handle}", {"name": "Design Team"}, owner.token)
        r.status(s, 200)
        r.check(s.get("group.name") == "Design Team" and len(s.get("group.member_user_ids") or []) == 3, "owner PATCH lost data")
        r.status(api("PATCH", f"{gpath}/{handle}", {"name": "nope"}, ann.token), 403)
        r.status(api("PATCH", f"{gpath}/{uniq('zz')}", {"name": "nope"}, owner.token), 404)
    ch = make_channel(owner, ws, "gm")
    for u in (ann, cat):
        must_join(u, ch)
    with c.rule("C4.4", "@group-handle mentions group members in the channel, minus the author and anyone not in the channel") as r:
        s = post(ann, ch, f"ping @{handle} please")
        r.status(s, 201)
        r.check(mentions_of(s) == [str(cat.id)], f"mentions were {mentions_of(s)}; expected only cat (ben is not in the channel, ann is the author)")
    with c.rule("C4.5", "@here mentions non-author channel members with an open WebSocket on any node, and stops after it closes") as r:
        w = WS(8002, cat.token)
        r.check(w.status == 101, f"handshake returned {w.status}")
        time.sleep(0.3)
        s = post(ann, ch, "@here standup")
        r.check(mentions_of(s) == [str(cat.id)], f"with cat connected: {mentions_of(s)}")
        w.close()
        gone = False
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and not gone:
            time.sleep(0.4)
            gone = mentions_of(post(ann, ch, "@here again")) == []
        r.check(gone, "cat was still mentioned by @here 4s after closing the WebSocket")
    with c.rule("C4.6", "ids from @user, @group, @here and @channel are deduped; the author is never included") as r:
        w = WS(8001, cat.token)
        time.sleep(0.3)
        s = post(cat, ch, f"@here @{handle} @channel @{ann.username} @{cat.username}")
        w.close()
        want = sorted([str(ann.id), str(owner.id)])
        r.check(mentions_of(s) == want, f"mentions were {mentions_of(s)}; expected ann and owner once each, never cat")
    with c.rule("C4.7", "DELETE by others 403; by the creator 200 {deleted: true}, again 200; the group is gone") as r:
        r.status(api("DELETE", f"{gpath}/{handle}", token=ben.token), 403)
        s = api("DELETE", f"{gpath}/{handle}", token=cat.token)
        r.status(s, 200)
        r.check(s.get("deleted") is True, f"body was {s.text[:60]}")
        r.status(api("DELETE", f"{gpath}/{handle}", token=cat.token), 200)
        r.check(handle not in [x.get("handle") for x in api("GET", gpath, token=ann.token).get("groups") or []], "deleted group still listed")


def c5(c):
    amy, ted = register("pa"), register("pt")
    with c.rule("C5.1", "GET /api/users/{id} returns 200 {user} with every UserObj field") as r:
        s = api("GET", f"/api/users/{amy.id}", token=ted.token, port=8001)
        r.status(s, 200)
        missing = [f for f in USER_FIELDS if f not in (s.get("user") or {})]
        r.check(not missing and s.get("user.username") == amy.username, f"user missing {missing}: {s.text[:90]}")
        r.check(not any("password" in k for k in s.get("user") or {}), "user object exposes a password field")
    w = WS(8002, ted.token)
    with c.rule("C5.2", "PATCH /api/users/me saves any subset of profile fields and returns 200 {user}; other nodes see it") as r:
        patch = {"display_name": "Amy P", "timezone": "America/New_York", "status_text": "in a meeting",
                 "status_emoji": "📅", "avatar_url": "https://example.com/amy.png"}
        s = api("PATCH", "/api/users/me", patch, amy.token)
        r.status(s, 200)
        r.check(all(s.get(f"user.{k}") == v for k, v in patch.items()), f"user was {s.text[:140]}")
        got = api("GET", f"/api/users/{amy.id}", token=ted.token, port=8002).get("user") or {}
        r.check(all(got.get(k) == v for k, v in patch.items()), f"8002 sees {got}")
        s = api("PATCH", "/api/users/me", {"status_text": "free"}, amy.token)
        r.check(s.get("user.status_text") == "free" and s.get("user.display_name") == "Amy P", "a partial update changed other fields")
    with c.rule("C5.3", "an unknown timezone, an out-of-range length or a wrong type returns 400 and saves nothing") as r:
        for bad in ({"timezone": "Mars/Olympus_Mons"}, {"display_name": "x" * 81}, {"display_name": ""},
                    {"status_text": "x" * 141}, {"status_emoji": "x" * 33}, {"avatar_url": "x" * 513}, {"display_name": 5}):
            s = api("PATCH", "/api/users/me", bad, amy.token)
            r.check(s.status == 400, f"{str(bad)[:50]} -> {s.brief()}")
        got = api("GET", f"/api/users/{amy.id}", token=ted.token).get("user") or {}
        r.check(got.get("timezone") == "America/New_York" and got.get("display_name") == "Amy P", f"a rejected PATCH changed the profile: {got}")
    with c.rule("C5.4", "a profile update reaches every connected client as {type: user.updated, user} with no channel_id or event_id") as r:
        r.check(w.status == 101, f"handshake returned {w.status}")
        api("PATCH", "/api/users/me", {"status_text": "back"}, amy.token, port=8000)
        f = w.wait_for(lambda f: f.get("type") == "user.updated" and same((f.get("user") or {}).get("id"), amy.id)
                       and (f.get("user") or {}).get("status_text") == "back", 3)
        r.check(f is not None, f"ted (no shared channel, on 8002) got no user.updated with the new status; frames: {w.frames[-3:]}")
        r.check("channel_id" not in f and "event_id" not in f, f"frame must not carry channel_id or event_id: {f}")
    w.close()


GROUPS = [
    ("C0", c0, ["C0.1", "C0.2"], None),
    ("C1", c1, [f"C1.{i}" for i in range(1, 8)], None),
    ("C2", c2, [f"C2.{i}" for i in range(1, 9)], None),
    ("C3", c3, [f"C3.{i}" for i in range(1, 7)], None),
    ("C4", c4, [f"C4.{i}" for i in range(1, 8)], None),
    ("C5", c5, [f"C5.{i}" for i in range(1, 5)], None),
]
STEPS = {"1": ("C0", "C1"), "2": ("C2",), "3": ("C3",), "4": ("C4",), "5": ("C5",), "all": tuple(g[0] for g in GROUPS)}

if __name__ == "__main__":
    step, rest = sys.argv[1], sys.argv[2:]
    run([g for g in GROUPS if g[0] in STEPS[step]], rest, f"bash /spec/c/checks/check_c_{step}.sh [{' '.join(STEPS[step])}]")
