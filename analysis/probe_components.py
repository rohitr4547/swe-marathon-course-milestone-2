"""Probes a running app for COMPONENTS.md requirements that the staged checks do not test directly.

A rough stand-in for the hidden tests: each probe prints PASS or FAIL with what it saw. It runs inside
the check container (analysis/probe_app.sh) and reuses the request helpers from /spec/common/checklib.py.
"""
import re, sys, time
sys.path.insert(0, "/spec/common")
from checklib import api, upload, fetch, register, make_workspace, make_channel, must_join, must_post, post, IRC, uniq, PW

RFC3339_UTC = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|\+00:00)$")
results = []


def probe(name, fn):
    try:
        ok, seen = fn()
    except Exception as e:  # a probe that crashes is a failure of the app, not of the probe run
        ok, seen = False, f"{e.__class__.__name__}: {str(e)[:160]}"
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name}" + ("" if ok else f": {seen}"), flush=True)


owner, member, outsider = register("po"), register("pm"), register("px")
ws = make_workspace(owner)
must_join(member, ws.general)  # joining #general makes a user a workspace member (open join)
ch = make_channel(owner, ws)
must_join(member, ch)


def timestamps():
    m = must_post(owner, ch, "stamp")
    f = upload(owner.token, "t.txt", b"hello", "text/plain")
    e = api("PATCH", f"/api/messages/{m['id']}", {"body": "stamp2"}, owner.token)
    vals = {"message.created_at": m.get("created_at"), "file.created_at": f.get("file.created_at"),
            "message.edited_at": e.get("message.edited_at")}
    bad = {k: v for k, v in vals.items() if not (isinstance(v, str) and RFC3339_UTC.match(v))}
    return not bad, f"not RFC 3339 UTC: {bad}"


def ids_across_nodes():
    m = must_post(owner, ch, "cross", port=8000)
    lst = api("GET", f"/api/channels/{ch}/messages", token=owner.token, port=8002)
    ids = [x.get("id") for x in (lst.get("messages") or [])]
    f = upload(owner.token, "x.bin", b"abc", port=8001)
    g = api("GET", f"/api/files/{f.get('file.id')}", token=owner.token, port=8002)
    return m["id"] in ids and g.status == 200 and g.get("file.id") == f.get("file.id"), \
        f"message on 8000 listed on 8002: {m['id'] in ids}; file from 8001 on 8002: {g.status}"


def reactions_shape():
    m = must_post(owner, ch, "react me")
    api("POST", f"/api/messages/{m['id']}/reactions", {"emoji": "tada"}, member.token, 8001)
    lst = api("GET", f"/api/channels/{ch}/messages", token=owner.token, port=8002).get("messages") or []
    r = next((x.get("reactions") for x in lst if x.get("id") == m["id"]), None)
    ok = isinstance(r, list) and len(r) == 1 and r[0].get("emoji") == "tada" and r[0].get("count") == 1 \
        and r[0].get("user_ids") == [member.id]
    return ok, f"reactions on 8002: {r}"


def slash_edge_cases():
    out = {}
    a = post(owner, ch, "//hello"); out["//hello"] = (a.status, a.get("message.body"))
    b = post(owner, ch, "/shrug"); out["/shrug"] = (b.status, b.get("message.body"))
    c = post(owner, ch, "/me"); out["/me"] = c.status
    d = post(owner, ch, "/frobnicate x"); out["/frobnicate"] = d.status
    t = post(owner, ch, "/topic probed topic"); out["/topic"] = (t.status, t.text[:60])
    ok = out["//hello"] == (201, "/hello") and out["/shrug"] == (201, "¯\\_(ツ)_/¯") and out["/me"] == 400 \
        and out["/frobnicate"] == 400 and t.status == 201 and t.json and t.json.get("message") is None \
        and t.get("channel.topic") == "probed topic"
    return ok, str(out)


def file_attach_rules():
    mine = upload(owner.token, "a.txt", b"a", "text/plain").get("file.id")
    theirs = upload(member.token, "b.txt", b"b", "text/plain").get("file.id")
    first = post(owner, ch, "with file", file_ids=[mine])
    again = post(owner, ch, "same file", file_ids=[mine])
    other = post(owner, ch, "their file", file_ids=[theirs])
    st, hd, body = fetch(f"/api/files/{mine}/download", owner.token)
    ok = first.status == 201 and again.status == 400 and other.status == 400 and st == 200 \
        and hd.get("content-type", "").startswith("text/plain") and "a.txt" in hd.get("content-disposition", "")
    return ok, f"attach own {first.status}, re-attach {again.status}, other's {other.status}, download {st} " \
               f"type={hd.get('content-type')} disp={hd.get('content-disposition')}"


def group_rules():
    h = uniq("g")[:30].lower().replace("_", "-")
    c = api("POST", f"/api/workspaces/{ws.slug}/groups", {"handle": h, "name": "G", "user_ids": [owner.id]}, owner.token)
    g = c.get("group") or {}
    shape = "member_user_ids" in g and "members" not in g
    patch = api("PATCH", f"/api/workspaces/{ws.slug}/groups/{h}", {"name": "nope"}, member.token)
    d1 = api("DELETE", f"/api/workspaces/{ws.slug}/groups/{h}", token=owner.token)
    d2 = api("DELETE", f"/api/workspaces/{ws.slug}/groups/{h}", token=owner.token)
    ok = c.status == 201 and shape and patch.status == 403 and d1.status == 200 and d1.json == {"deleted": True} \
        and d2.status == 200 and d2.json == {"deleted": True}
    return ok, f"create {c.status} keys={sorted(g)}; PATCH by plain member {patch.status}; delete {d1.status} {d1.json}, again {d2.status} {d2.json}"


def profile_rules():
    p = api("PATCH", "/api/users/me", {"status_text": "probing"}, member.token, 8001)
    g = api("GET", f"/api/users/{member.id}", token=owner.token, port=8002)
    tz = api("PATCH", "/api/users/me", {"timezone": "Mars/Olympus"}, member.token)
    ok_tz = api("PATCH", "/api/users/me", {"timezone": "America/New_York"}, member.token)
    long = api("PATCH", "/api/users/me", {"display_name": "x" * 500}, member.token)
    unk = api("GET", "/api/users/99999999", token=owner.token)
    ok = p.status == 200 and g.get("user.status_text") == "probing" and tz.status == 400 and ok_tz.status == 200 \
        and long.status == 400 and unk.status == 404
    return ok, f"patch {p.status}; read on other node status_text={g.get('user.status_text')}; bad tz {tz.status}; " \
               f"good tz {ok_tz.status}; long name {long.status}; unknown user {unk.status}"


def delete_permissions():
    m1 = must_post(member, ch, "owner deletes me")
    m2 = must_post(owner, ch, "member cannot delete me")
    by_owner = api("DELETE", f"/api/messages/{m1['id']}", token=owner.token)
    by_member = api("DELETE", f"/api/messages/{m2['id']}", token=member.token)
    again = api("DELETE", f"/api/messages/{m1['id']}", token=owner.token)
    ok = by_owner.status == 200 and by_member.status == 403 and again.status == 200 and again.json == {"deleted": True}
    return ok, f"owner deletes member's message {by_owner.status}; member deletes owner's {by_member.status}; again {again.status} {again.json}"


def irc_extras():
    general = f"#{ws.slug}/general"
    c = IRC()
    nick = uniq("n")[:15]
    c.send(f"PASS {owner.token}", f"NICK {nick}", f"USER {nick} 0 * :{nick}")
    if not c.numeric("001"):
        return False, f"no 001 after PASS/NICK/USER: {c.lines[-3:]}"
    c.numeric("422")
    c.send(f"JOIN {general}")
    joined = c.numeric("366")
    c.send(f"JOIN #{ws.slug}-general")
    hyphen = c.numeric("403")
    c.send("PING :probe")
    pong = c.wait_for(lambda s: "PONG" in s and "probe" in s)
    c.send("FROBNICATE")
    unknown = c.numeric("421")
    for cmd in ("WHO " + general, "LIST", "TOPIC " + general, "MODE " + general, "NAMES " + general):
        c.send(cmd)
    time.sleep(1.0)
    c.wait_for(lambda s: False, 0.5)
    bad_accepted = [l for l in c.lines if " 421 " in l and any(k in l for k in ("WHO", "LIST", "TOPIC", "MODE", "NAMES"))]
    body = uniq("webmsg")
    must_post(member, ws.general, body, port=8001)  # another user: the IRC sender itself gets no echo
    line = c.wait_for(lambda s: body in s, 4.0)
    shape = bool(line and re.match(r"^:[^\s!]+![^\s@]+@\S+ PRIVMSG " + re.escape(general) + r" :" + re.escape(body) + r"$", line))
    c.close()
    ok = bool(joined and hyphen and pong and unknown and not bad_accepted and shape)
    return ok, f"join {bool(joined)}; #slug-name 403 {bool(hyphen)}; PONG {bool(pong)}; 421 {bool(unknown)}; " \
               f"421 for accepted commands {bad_accepted[:2]}; web->IRC line {line!r}"


def ui_details():
    from playwright.sync_api import sync_playwright
    seen = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1280, "height": 900})
        page.on("dialog", lambda d: d.dismiss())
        page.goto("http://127.0.0.1:8000/", wait_until="domcontentloaded")
        form = page.locator("[data-testid=auth-form]")
        if form.get_attribute("data-mode") != "signup":
            page.click("[data-testid=auth-toggle]")
        name = uniq("ui")
        page.fill("[data-testid=auth-form] [name=username]", name)
        page.fill("[data-testid=auth-form] [name=password]", PW)
        page.click("[data-testid=auth-submit]")
        # Logged in = token stored and the auth form closed; a new user with no workspace may see the
        # create-workspace screen rather than the sidebar that holds current-user.
        page.wait_for_function("() => !!(localStorage.getItem('huddle.token') || localStorage.getItem('token') || "
                               "localStorage.getItem('auth_token') || window.__huddle_token)", timeout=8000)
        page.wait_for_selector("[data-testid=auth-form]", state="hidden", timeout=8000)
        token = page.evaluate("() => localStorage.getItem('huddle.token') || localStorage.getItem('token') || "
                              "localStorage.getItem('auth_token') || window.__huddle_token || null")
        seen["token stored"] = bool(token)
        slug = uniq("uiws")[:30].lower().replace("_", "-")
        w = api("POST", "/api/workspaces", {"slug": slug, "name": "Probe WS"}, token)
        gen = w.get("general_channel.id")
        api("PATCH", f"/api/channels/{gen}", {"topic": "probe topic"}, token)
        must_post(type("U", (), {"token": token})(), gen, "hello from api")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("[data-channel-id][data-channel-name]", timeout=8000)
        item = page.locator("[data-channel-id][data-channel-name]").first
        page.locator(f"[data-channel-id='{gen}']").first.click()
        page.wait_for_selector("[data-testid=message]", timeout=8000)
        seen["sidebar attrs"] = item.get_attribute("data-channel-name") == "general"
        seen["hashtag name"] = "#" in (page.locator("[data-testid=channel-list]").inner_text() or "")
        seen["title has name"] = "general" in page.locator("[data-testid=channel-title]").inner_text()
        seen["header has topic"] = "probe topic" in page.locator("[data-testid=channel-topic]").inner_text()
        t = page.locator("[data-testid=message-time]").first.inner_text().strip()
        seen["human time"] = bool(t) and not re.search(r"\d{4}-\d\d-\d\dT", t)
        seen["workspace identity"] = "Probe WS" in page.locator("[data-testid=workspace-header]").inner_text()
        b.close()
    return all(seen.values()), str(seen)


for name, fn in [("timestamps are RFC 3339 UTC", timestamps),
                 ("ids stable across nodes", ids_across_nodes),
                 ("reactions shape read from another node", reactions_shape),
                 ("slash edge cases (//literal, bare /shrug, bare /me, unknown, /topic response)", slash_edge_cases),
                 ("file attach rules and download headers", file_attach_rules),
                 ("group response shape, permissions, idempotent delete", group_rules),
                 ("profile persists across nodes; validation; unknown user", profile_rules),
                 ("delete by workspace owner; not by other members; idempotent", delete_permissions),
                 ("IRC: PING, unknown command, #slug-name, accepted commands, web->IRC line", irc_extras),
                 ("UI: token key, sidebar attributes, # names, header, human time, workspace name", ui_details)]:
    probe(name, fn)
print(f"PROBES: {sum(results)} passed, {len(results) - sum(results)} failed")
