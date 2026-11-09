"""Rule code for Component E (message edits, the IRC gateway and the web UI). Each function tests one rule group.

Run through the check scripts next to this file (check_e_*.sh), not directly. The UI groups (E4 to E6)
drive the headless Chromium that is installed with Playwright in /opt/venv.
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
from checklib import (IRC, output_mode, ISO_UTC, WS, Fail, api, events_text, fetch, healthy, kill_port,  # noqa: E402
                      make_channel, make_workspace, must_join, must_post, post, raw_post, register, run, same,
                      short, uniq, wait_until, PW)

NO_ID = 999999999


def e0(c):
    with c.rule("E0.1", "PATCH and DELETE /api/messages/{id} return 401 with no token and with a bogus token") as r:
        wrong = []
        for method in ("PATCH", "DELETE"):
            for token in (None, "bogus-token-0000000000"):
                s = api(method, f"/api/messages/{NO_ID}", {"body": "x"} if method == "PATCH" else None, token)
                if s.status != 401:
                    wrong.append(f"{method} {'bogus' if token else 'none'}->{s.status}")
        r.check(not wrong, "expected 401 for " + "; ".join(wrong))
    with c.rule("E0.2", "PATCH and DELETE of an unknown message id return 404") as r:
        u = register("ez")
        r.status(api("PATCH", f"/api/messages/{NO_ID}", {"body": "x"}, u.token), 404)
        r.status(api("DELETE", f"/api/messages/{NO_ID}", token=u.token), 404)


def e1(c):
    owner, mem, other = register("eo"), register("em"), register("ex")
    ws = make_workspace(owner)
    for u in (mem, other):
        must_join(u, ws.general)
    ch = make_channel(owner, ws, "ed")
    for u in (mem, other):
        must_join(u, ch)
    with c.rule("E1.1", "PATCH /api/messages/{id} by the author: 200 {message} with the new body and edited_at (UTC Z)") as r:
        m = must_post(mem, ch, "first draft")
        s = api("PATCH", f"/api/messages/{m['id']}", {"body": "final text"}, mem.token, port=8001)
        r.status(s, 200)
        r.check(s.get("message.body") == "final text" and ISO_UTC.match(str(s.get("message.edited_at"))), f"message was {s.text[:110]}")
        listed = api("GET", f"/api/channels/{ch}/messages?limit=1", token=owner.token, port=8002).get("messages") or [{}]
        r.check(listed[0].get("body") == "final text" and listed[0].get("edited_at"), "the edit is not visible in history on 8002")
    with c.rule("E1.2", "edit: non-author 403 (the workspace owner too); empty or non-string body 400") as r:
        m = must_post(mem, ch, "mine")
        r.status(api("PATCH", f"/api/messages/{m['id']}", {"body": "hijack"}, other.token), 403)
        r.status(api("PATCH", f"/api/messages/{m['id']}", {"body": "owner edit"}, owner.token), 403)
        for body in ({"body": ""}, {"body": "   "}, {"body": None}, {"body": 7}, {}):
            r.status(api("PATCH", f"/api/messages/{m['id']}", body, mem.token), 400)
        st = raw_post(f"/api/channels/{ch}/messages", b"{oops", mem.token)
        r.check(st == 400, f"a body that is not valid JSON got {st}, expected 400")
    with c.rule("E1.3", "DELETE by the author or the workspace owner: 200 {deleted: true}; again 200; anyone else 403") as r:
        m1, m2 = must_post(mem, ch, "delete me"), must_post(mem, ch, "owner deletes me")
        r.status(api("DELETE", f"/api/messages/{m1['id']}", token=other.token), 403)
        s = api("DELETE", f"/api/messages/{m1['id']}", token=mem.token)
        r.status(s, 200)
        r.check(s.get("deleted") is True, f"body was {s.text[:60]}")
        r.status(api("DELETE", f"/api/messages/{m1['id']}", token=mem.token), 200)
        r.status(api("DELETE", f"/api/messages/{m2['id']}", token=owner.token), 200)
    with c.rule("E1.4", "editing a deleted message returns 409") as r:
        m = must_post(mem, ch, "short lived")
        r.status(api("DELETE", f"/api/messages/{m['id']}", token=mem.token), 200)
        r.status(api("PATCH", f"/api/messages/{m['id']}", {"body": "too late"}, mem.token), 409)
    with c.rule("E1.5", "a deleted message leaves history and its thread; the parent's reply_count drops") as r:
        parent = must_post(owner, ch, "parent")
        reps = [must_post(mem, ch, f"rep {i}", parent_id=parent["id"]) for i in range(2)]
        gone = must_post(mem, ch, "vanishing")
        api("DELETE", f"/api/messages/{gone['id']}", token=mem.token)
        api("DELETE", f"/api/messages/{reps[0]['id']}", token=mem.token)
        top = api("GET", f"/api/channels/{ch}/messages", token=owner.token).get("messages") or []
        r.check(str(gone["id"]) not in [str(x.get("id")) for x in top], "deleted message still in channel history")
        p = [x for x in top if same(x.get("id"), parent["id"])]
        r.check(p and p[0].get("reply_count") == 1, f"parent reply_count was {p[0].get('reply_count') if p else 'missing'}")
        rs = [x.get("body") for x in api("GET", f"/api/messages/{parent['id']}/replies", token=owner.token).get("replies") or []]
        r.check(rs == ["rep 1"], f"replies were {rs}")
    with c.rule("E1.6", "an edit resolves mentions again from the new body") as r:
        m = must_post(owner, ch, "no mention yet")
        s = api("PATCH", f"/api/messages/{m['id']}", {"body": f"now @{mem.username}"}, owner.token)
        r.check([str(x) for x in s.get("message.mentions") or []] == [str(mem.id)], f"mentions after edit: {s.get('message.mentions')}")
        s = api("PATCH", f"/api/messages/{m['id']}", {"body": "gone again"}, owner.token)
        r.check(s.get("message.mentions") == [], f"mentions after removing it: {s.get('message.mentions')}")
    with c.rule("E1.7", "message.updated {message} and message.deleted {message_id} events; a repeat delete emits nothing") as r:
        w = WS(8002, other.token)
        sub = w.subscribe(ch) if w.status == 101 else None
        r.check(sub is not None, "setup: WebSocket subscribe failed")
        head = sub.get("head_event_id")
        m = must_post(mem, ch, "evented")
        api("PATCH", f"/api/messages/{m['id']}", {"body": "evented v2"}, mem.token)
        api("DELETE", f"/api/messages/{m['id']}", token=mem.token)
        api("DELETE", f"/api/messages/{m['id']}", token=mem.token)
        must_post(mem, ch, "after")
        got = w.durable(ch, 4)
        w.close()
        r.check([f.get("type") for f in got] == ["message.created", "message.updated", "message.deleted", "message.created"],
                f"got {events_text(got)}")
        r.check([f.get("event_id") for f in got] == [head + 1, head + 2, head + 3, head + 4], f"event_ids: {events_text(got)}")
        r.check((got[1].get("message") or {}).get("body") == "evented v2" and same(got[2].get("message_id"), m["id"]),
                f"payloads were {str(got[1])[:80]} / {str(got[2])[:80]}")


def e2(c):
    owner, bob = register("io"), register("ib")
    ws = make_workspace(owner)
    must_join(bob, ws.general)
    chan = f"#{ws.slug}/general"
    with c.rule("E2.1", "IRC on 0.0.0.0:6667: PASS with a bad token gets 464") as r:
        i = IRC()
        i.send("PASS not-a-token-000000", "NICK badnick", "USER badnick 0 * :bad")
        r.check(i.numeric("464"), f"no 464; got {i.lines[:3]}")
        i.close()
    irc = IRC()
    with c.rule("E2.2", "PASS <token>, NICK, USER get numerics 001 to 005 and then 422") as r:
        irc.send(f"PASS {owner.token}", f"NICK {owner.username}", f"USER {owner.username} 0 * :Owner")
        r.check(irc.numeric("422"), f"no 422 ERR_NOMOTD; got {irc.lines[:6]}")
        codes = {line.split()[1] for line in irc.lines if len(line.split()) > 1}
        missing = [n for n in ("001", "002", "003", "004", "005") if n not in codes]
        r.check(not missing, f"missing numerics {missing}")
    with c.rule("E2.3", "JOIN #slug/channel echoes JOIN, then 353 and 366; '#slug-general' or unknown names get 403") as r:
        irc.send(f"JOIN {chan}")
        r.check(irc.wait_for(lambda s: " JOIN " in s and chan in s), f"no JOIN echo; got {irc.lines[-3:]}")
        r.check(irc.numeric("353") and irc.numeric("366"), f"no 353/366; got {irc.lines[-3:]}")
        irc.send(f"JOIN #{ws.slug}-general")
        r.check(irc.numeric("403"), "hyphenated name was not 403")
        irc.send(f"JOIN #{ws.slug}/nosuch")
        r.check(irc.numeric("403"), "unknown channel was not 403")
    with c.rule("E2.4", "PING gets PONG; PONG, NAMES, WHO, LIST, TOPIC and MODE are accepted (never 421); unknown commands get 421") as r:
        irc.send("PING :tok123")
        r.check(irc.wait_for(lambda s: "PONG" in s and "tok123" in s), "no PONG tok123")
        before = len(irc.lines)
        irc.send("PONG :x", f"NAMES {chan}", f"WHO {chan}", "LIST", f"TOPIC {chan}", f"MODE {chan}", "PING :mark")
        irc.wait_for(lambda s: "PONG" in s and "mark" in s, 3)
        r.check(not any(len(x.split()) > 1 and x.split()[1] == "421" for x in irc.lines[before:]),
                f"an accepted command got 421: {[x for x in irc.lines[before:] if ' 421 ' in x][:2]}")
        irc.send("FOOBAR")
        r.check(irc.numeric("421"), "unknown command was not 421")
    with c.rule("E2.5", "a nick collision gets 433 and keeps the connection open; QUIT closes without an error line") as r:
        i2 = IRC()
        i2.send(f"PASS {bob.token}", f"NICK {owner.username}", f"USER {bob.username} 0 * :Bob")
        r.check(i2.numeric("433"), "nick collision was not 433")
        i2.send(f"NICK {bob.username}")
        r.check(i2.numeric("001"), "after 433 the connection did not accept a new NICK")
        i2.close()
        irc.send("QUIT :bye")
        irc.wait_for(lambda s: False, 2)
        r.check(irc.closed, "QUIT did not close the connection")
        r.check(not any(x.startswith("ERROR") for x in irc.lines[-3:]), f"QUIT sent an error line: {irc.lines[-1:]}")
    irc.close()


def e3(c):
    owner, bob = register("jo"), register("jb")
    ws = make_workspace(owner)
    must_join(bob, ws.general)
    chan = f"#{ws.slug}/general"
    irc = IRC()
    irc.send(f"PASS {owner.token}", f"NICK {owner.username}", f"USER {owner.username} 0 * :Owner", f"JOIN {chan}")
    if not irc.numeric("366"):
        raise Fail("setup: IRC registration or JOIN failed (fix E.2 first)")
    w = WS(8001, bob.token)
    sub = w.subscribe(ws.general) if w.status == 101 else None
    if sub is None:
        raise Fail("setup: WebSocket subscribe failed")
    head = sub.get("head_event_id")
    stamp = uniq("x")
    with c.rule("E3.1", "an IRC PRIVMSG becomes a web message: WS message.created with the next event_id, listed by REST") as r:
        irc.send(f"PRIVMSG {chan} :from irc {stamp}")
        got = w.durable(ws.general, 1, 3)
        r.check(got and got[0].get("type") == "message.created" and got[0].get("event_id") == head + 1,
                f"WS got {events_text(got)}, expected message.created#{head + 1}")
        r.check((got[0].get("message") or {}).get("body") == f"from irc {stamp}", "frame body differs from the PRIVMSG")
        top = api("GET", f"/api/channels/{ws.general}/messages?limit=1", token=bob.token, port=8002).get("messages") or [{}]
        r.check(top[0].get("body") == f"from irc {stamp}" and same(top[0].get("author_id"), owner.id), f"newest REST message was {str(top[0])[:90]}")
    with c.rule("E3.2", "a web post reaches IRC as ':<nick>!<user>@<host> PRIVMSG #slug/channel :<body>'; the sender gets no echo") as r:
        must_post(bob, ws.general, f"from web {stamp}")
        line = irc.wait_for(lambda s: "PRIVMSG" in s and f"from web {stamp}" in s, 3)
        r.check(line and line.startswith(f":{bob.username}!") and f"PRIVMSG {chan} :from web {stamp}" in line, f"IRC line was {line!r}")
        r.check(not any(f"from irc {stamp}" in x for x in irc.lines if "PRIVMSG" in x), "the IRC sender got its own message echoed")
        w.durable(ws.general, 1, 3)
    with c.rule("E3.3", "alternating IRC and web writes share one dense event_id sequence") as r:
        base = head + 2
        for i in range(3):
            irc.send(f"PRIVMSG {chan} :mix irc {i} {stamp}")
            time.sleep(0.05)
            must_post(bob, ws.general, f"mix web {i} {stamp}", port=8000 + i)
        got = w.durable(ws.general, 6, 5)
        r.check([f.get("event_id") for f in got] == list(range(base + 1, base + 7)), f"got {events_text(got)}")
    with c.rule("E3.4", "with node 8001 killed, IRC still bridges both ways through the surviving nodes") as r:
        w.close()
        r.check(kill_port(8001), "nothing was listening on 8001")
        must_post(bob, ws.general, f"after kill web {stamp}", port=8002)
        r.check(irc.wait_for(lambda s: "PRIVMSG" in s and f"after kill web {stamp}" in s, 3), "a web post via 8002 did not reach IRC")
        irc.send(f"PRIVMSG {chan} :after kill irc {stamp}")
        r.check(wait_until(lambda: any(m.get("body") == f"after kill irc {stamp}" for m in
                                       api("GET", f"/api/channels/{ws.general}/messages?limit=3", token=bob.token, port=8000).get("messages") or []), 3),
                "an IRC PRIVMSG was not stored while 8001 was down")
        r.check(wait_until(lambda: healthy(8001, 1), 15), "8001 did not come back within 15s")
    irc.close()


# ---------------------------------------------------------------- web UI (E4 to E6)

DYNAMIC = {"join-workspace-error", "channel-entry", "channel-member-row", "message", "message-body", "open-thread-btn",
           "reaction-button", "reaction-chip", "emoji-picker", "emoji-option", "member-row", "role-badge", "role-select",
           "invitation-code"}
PINNED = """auth-form auth-submit auth-toggle workspace-create-form empty-state-create-workspace join-workspace-form
join-workspace-submit join-workspace-error channel-list channel-entry dms-list new-channel-btn current-user logout-btn
workspace-header workspace-settings-btn workspace-settings-close workspace-name-input workspace-join-mode
workspace-general-submit channel-title channel-topic channel-settings-btn channel-settings-title channel-settings-close
channel-members-list channel-member-row channel-add-member-form channel-add-member-input channel-add-member-submit
channel-topic-input channel-topic-submit archive-channel-btn unarchive-channel-btn create-channel-form
create-channel-private create-channel-submit create-channel-cancel message-list message message-body message-input
send-btn open-thread-btn thread-panel thread-input thread-send close-thread reaction-button reaction-chip emoji-picker
emoji-option settings-tab-members settings-tab-invitations settings-tab-general settings-pane-members
settings-pane-invitations settings-pane-general member-row role-badge role-select create-invitation-btn
create-invitation-form create-invitation-submit invitations-list invitation-code""".split()
DOM_IDS = ["auth-modal", "create-channel-modal", "channel-settings-modal", "workspace-settings-modal", "create-invitation-form"]
ROLES = {
    "primary": ("rgb(0, 122, 90)", "auth-submit send-btn thread-send create-channel-submit join-workspace-submit "
                "workspace-general-submit create-invitation-submit channel-topic-submit channel-add-member-submit"),
    "danger": ("rgb(224, 30, 90)", "archive-channel-btn logout-btn"),
    "secondary": ("rgb(18, 100, 163)", "new-channel-btn create-invitation-btn unarchive-channel-btn create-channel-cancel "
                  "close-thread channel-settings-close workspace-settings-close channel-settings-btn workspace-settings-btn"),
}
UI_EXTRA = ["auth-error", "create-channel-error", "channel-empty-state", "sidebar"]


def tid(name):
    return f'[data-testid="{name}"]'


class Browser:
    """One headless Chromium page at 1280x900 on node 8000, failing the rule on any browser dialog."""

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise Fail("setup: Playwright is not importable with this python3 (it ships in /opt/venv)")
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch()
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        self.page.set_default_timeout(4000)
        self.dialogs = []
        self.page.on("dialog", lambda d: (self.dialogs.append(d.type), d.dismiss()))
        # Uncaught JavaScript errors, shown in verbose mode: a script that throws looks like a missing element.
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e).splitlines()[0][:160]))
        self.page.goto("http://127.0.0.1:8000/", wait_until="domcontentloaded")
        return self

    def __exit__(self, *exc):
        if self.errors and output_mode() == "verbose":
            seen = list(dict.fromkeys(self.errors))
            print(f"NOTE uncaught JavaScript errors in the page: {' | '.join(seen[:3])}", flush=True)
        try:
            self.browser.close()
        finally:
            self.pw.stop()
        return False

    def visible(self, name):
        try:
            return self.page.locator(tid(name)).first.is_visible()
        except Exception:  # noqa: BLE001 - a missing element is simply not visible
            return False

    def text(self, name):
        try:
            return self.page.locator(tid(name)).first.inner_text().strip()
        except Exception:  # noqa: BLE001
            return ""

    def token(self):
        return self.page.evaluate("""() => localStorage.getItem('huddle.token') || localStorage.getItem('token')
            || localStorage.getItem('auth_token') || window.__huddle_token || null""")


def ui_signup(b, username, mode_toggle=True):
    """Fills the auth form in sign-up mode and submits it."""
    form = b.page.locator(tid("auth-form"))
    if form.get_attribute("data-mode") != "signup" and mode_toggle:
        b.page.locator(tid("auth-toggle")).click()
    form.locator('input[name="username"]').fill(username)
    form.locator('input[name="password"]').fill(PW)
    b.page.locator(tid("auth-submit")).click()


def e4(c):
    with c.rule("E4.1", "GET / on 8000, 8001 and 8002 returns 200 HTML") as r:
        for port in (8000, 8001, 8002):
            st, hdr, body = fetch("/", port=port)
            r.check(st == 200 and "text/html" in hdr.get("content-type", "") and b"<html" in body.lower(),
                    f"port {port}: status {st}, Content-Type {hdr.get('content-type')!r}")
    st, _, raw = fetch("/")
    html = raw.decode(errors="replace")
    with c.rule("E4.2", "the initial HTML carries every pinned non-dynamic data-testid and the extra hooks this spec adds") as r:
        need = [t for t in PINNED if t not in DYNAMIC] + UI_EXTRA
        missing = [t for t in need if f'data-testid="{t}"' not in html]
        r.check(not missing, f"missing {len(missing)}: {' '.join(missing[:8])}")
    with c.rule("E4.3", "the initial HTML carries the DOM ids auth-modal, create-channel-modal, channel-settings-modal, workspace-settings-modal, create-invitation-form") as r:
        missing = [i for i in DOM_IDS if not re.search(rf'id="{i}"', html)]
        r.check(not missing, f"missing ids {missing}")
    with c.rule("E4.4", "each listed button carries data-button-role (primary, danger or secondary) in the initial HTML") as r:
        wrong = []
        for role, (_, names) in ROLES.items():
            for n in names.split():
                tag = re.search(rf'<[^>]*data-testid="{n}"[^>]*>', html)
                if not tag or f'data-button-role="{role}"' not in tag.group(0):
                    wrong.append(f"{n} (want {role})")
        r.check(not wrong, f"wrong or missing data-button-role: {', '.join(wrong[:6])}")
    with c.rule("E4.5", "at 1280x900 each listed button's resting background-color and color are exactly its role's colours") as r:
        with Browser() as b:
            wrong = []
            for role, (bg, names) in ROLES.items():
                for n in names.split():
                    got = b.page.evaluate("""(sel) => { const e = document.querySelector(sel); if (!e) return null;
                        const s = getComputedStyle(e); return [s.backgroundColor, s.color, s.backgroundImage]; }""", tid(n))
                    if got is None or got[0] != bg or got[1] != "rgb(255, 255, 255)" or got[2] not in ("none", ""):
                        wrong.append(f"{n}={got[:2] if got else 'missing'}")
            r.check(not wrong, f"{len(wrong)} buttons wrong: {'; '.join(wrong[:3])}")


def e5(c):
    user = uniq("ui")
    with Browser() as b:
        page = b.page
        with c.rule("E5.1", "sign-up with an invalid username shows a visible non-empty auth-error and logs nobody in") as r:
            r.check(b.visible("auth-form"), "the auth form is not visible on first load")
            ui_signup(b, "bad name!")
            page.wait_for_timeout(600)
            r.check(b.visible("auth-error") and b.text("auth-error"), "no visible text in [data-testid=auth-error]")
            r.check(not b.token(), "a token was stored after a failed sign-up")
        with c.rule("E5.2", "a valid sign-up logs in: token stored under huddle.token, auth-form hidden, current-user shows the name") as r:
            ui_signup(b, user, mode_toggle=False)
            r.check(wait_until(lambda: b.token() is not None, 4), "no token in localStorage after sign-up")
            r.check(api("GET", "/api/auth/me", token=b.token()).get("user.username") == user, "the stored token is not this user's")
            r.check(wait_until(lambda: not b.visible("auth-form"), 3), "the auth form is still visible after sign-up")
            r.check(user in b.text("current-user"), f"current-user shows {b.text('current-user')!r}")
        slug = uniq("uw")
        with c.rule("E5.3", "workspace-create-form creates a workspace: workspace-header shows it and #general is listed") as r:
            form = page.locator(tid("workspace-create-form"))
            if not form.is_visible():
                page.locator(tid("empty-state-create-workspace")).click()
            form.locator('input[name="name"]').fill("UI Works")
            form.locator('input[name="slug"]').fill(slug)
            form.locator('[type="submit"]').first.click()
            r.check(wait_until(lambda: "UI Works" in b.text("workspace-header"), 4), f"workspace-header shows {b.text('workspace-header')!r}")
            g = page.locator(f'{tid("channel-entry")}[data-channel-name="general"]')
            r.check(g.count() == 1 and g.first.get_attribute("data-channel-id"), "no channel-entry with data-channel-name=general and a data-channel-id")
            r.check("#general" in g.first.inner_text(), f"the entry reads {g.first.inner_text()!r}, expected a '#general' label")
        name = uniq("uc")
        with c.rule("E5.4", "new-channel-btn and create-channel-form create a channel and open it; a duplicate name shows create-channel-error") as r:
            page.locator(tid("new-channel-btn")).click()
            cf = page.locator(tid("create-channel-form"))
            cf.locator('input[name="name"]').fill(name)
            page.locator(tid("create-channel-submit")).click()
            entry = page.locator(f'{tid("channel-entry")}[data-channel-name="{name}"]')
            r.check(wait_until(lambda: entry.count() == 1, 4), "the new channel did not appear in the sidebar")
            r.check(wait_until(lambda: name in b.text("channel-title"), 3), f"channel-title shows {b.text('channel-title')!r}")
            page.locator(tid("new-channel-btn")).click()
            cf.locator('input[name="name"]').fill(name)
            page.locator(tid("create-channel-submit")).click()
            r.check(wait_until(lambda: b.visible("create-channel-error") and b.text("create-channel-error"), 3), "no visible create-channel-error for a duplicate name")
            r.check(entry.count() == 1, "the duplicate created a second sidebar entry")
            if b.visible("create-channel-cancel"):
                page.locator(tid("create-channel-cancel")).click()
        with c.rule("E5.5", "switching channels updates channel-title and channel-topic; an empty channel shows non-blank channel-empty-state") as r:
            cid = page.locator(f'{tid("channel-entry")}[data-channel-name="{name}"]').first.get_attribute("data-channel-id")
            api("PATCH", f"/api/channels/{cid}", {"topic": "UI topic"}, b.token())
            page.locator(f'{tid("channel-entry")}[data-channel-name="general"]').click()
            r.check(wait_until(lambda: "general" in b.text("channel-title"), 3), f"title {b.text('channel-title')!r} after clicking #general")
            page.locator(f'{tid("channel-entry")}[data-channel-name="{name}"]').click()
            r.check(wait_until(lambda: name in b.text("channel-title") and "UI topic" in b.text("channel-topic"), 3),
                    f"title {b.text('channel-title')!r}, topic {b.text('channel-topic')!r}")
            r.check(b.visible("channel-empty-state") and b.text("channel-empty-state"), "no visible non-blank channel-empty-state")
        with c.rule("E5.6", "logout-btn logs out (token cleared, auth-form shown); logging back in restores the same workspace") as r:
            page.locator(tid("logout-btn")).click()
            r.check(wait_until(lambda: b.visible("auth-form") and not b.token(), 3), "auth-form not shown or token not cleared after logout")
            form = page.locator(tid("auth-form"))
            if form.get_attribute("data-mode") != "login":
                page.locator(tid("auth-toggle")).click()
            form.locator('input[name="username"]').fill(user)
            form.locator('input[name="password"]').fill(PW)
            page.locator(tid("auth-submit")).click()
            r.check(wait_until(lambda: "UI Works" in b.text("workspace-header"), 4), f"after login workspace-header shows {b.text('workspace-header')!r}")
            r.check(page.locator(f'{tid("channel-entry")}[data-channel-name="{name}"]').count() == 1, "the channel list was not restored")
        with c.rule("E5.7", "no browser dialog (alert, confirm or prompt) opened during these flows") as r:
            r.check(not b.dialogs, f"dialogs opened: {b.dialogs}")


def e6(c):
    owner = register("uo")
    ws = make_workspace(owner)
    ch = make_channel(owner, ws, "um")
    with Browser() as b:
        page = b.page
        page.evaluate("t => localStorage.setItem('huddle.token', t)", owner.token)
        page.goto(f"http://127.0.0.1:8000/?token={owner.token}", wait_until="domcontentloaded")
        entry = page.locator(f'{tid("channel-entry")}[data-channel-id="{ch}"]')
        if not wait_until(lambda: entry.count() == 1, 5):
            raise Fail("setup: the channel is not in the sidebar after loading with a stored token (fix E.5 first)")
        entry.click()
        rows = page.locator(f'{tid("message-list")} {tid("message")}')

        def api_bodies():
            return [m.get("body") for m in api("GET", f"/api/channels/{ch}/messages", token=owner.token).get("messages") or []]

        with c.rule("E6.1", "message-input and send-btn post a message: a row shows avatar, author, readable time and body") as r:
            page.locator(tid("message-input")).fill("hello from the ui")
            page.locator(tid("send-btn")).click()
            r.check(wait_until(lambda: rows.count() == 1, 4), "no message row appeared")
            row = rows.first
            r.check(row.locator(tid("message-body")).inner_text().strip() == "hello from the ui", "message-body text differs")
            r.check(owner.username in row.inner_text(), "the row does not show the author's name")
            for part in ("message-avatar", "message-author", "message-time"):
                r.check(row.locator(tid(part)).count() == 1 and (part == "message-avatar" or row.locator(tid(part)).inner_text().strip()),
                        f"missing or blank [data-testid={part}] in the row")
            r.check(api_bodies() == ["hello from the ui"], f"API history is {api_bodies()}")
        with c.rule("E6.2", "an empty or blank message-input posts nothing") as r:
            page.locator(tid("message-input")).fill("   ")
            page.locator(tid("send-btn")).click()
            page.wait_for_timeout(500)
            r.check(rows.count() == 1 and len(api_bodies()) == 1, "a blank message was posted")
        with c.rule("E6.3", "the hover toolbar edits a message: edited indicator shown, and both survive a hard reload") as r:
            rows.first.hover()
            rows.first.locator(tid("edit-message-btn")).click()
            box = rows.first.locator(tid("edit-message-input"))
            box.fill("edited in the ui")
            box.press("Enter")
            r.check(wait_until(lambda: rows.first.locator(tid("message-body")).inner_text().strip() == "edited in the ui", 3), "body not updated after edit")
            r.check(rows.first.locator(tid("message-edited")).is_visible(), "no visible [data-testid=message-edited] indicator")
            page.reload(wait_until="domcontentloaded")
            page.locator(f'{tid("channel-entry")}[data-channel-id="{ch}"]').click()
            r.check(wait_until(lambda: rows.count() == 1 and rows.first.locator(tid("message-edited")).count() == 1, 4),
                    "after a reload the edited message or its indicator is gone")
        with c.rule("E6.4", "the thread opens in a right-side thread-panel; a reply there bumps the parent's reply-count to '1 reply'") as r:
            rows.first.hover()
            rows.first.locator(tid("open-thread-btn")).click()
            r.check(wait_until(lambda: b.visible("thread-panel"), 3), "thread-panel did not open")
            tp, ml = page.locator(tid("thread-panel")).bounding_box(), page.locator(tid("message-list")).bounding_box()
            r.check(tp and ml and tp["x"] >= ml["x"] + ml["width"] - 5, f"thread-panel is not to the right of the message list ({tp}, {ml})")
            page.locator(tid("thread-input")).fill("a threaded reply")
            page.locator(tid("thread-send")).click()
            r.check(wait_until(lambda: "a threaded reply" in b.text("thread-panel"), 3), "the reply is not shown in the thread panel")
            r.check(wait_until(lambda: re.search(r"\b1 reply\b", rows.first.locator(tid("reply-count")).inner_text() if rows.first.locator(tid("reply-count")).count() else ""), 3),
                    "the parent row has no reply-count reading '1 reply'")
            page.locator(tid("close-thread")).click()
            r.check(wait_until(lambda: not b.visible("thread-panel"), 3), "close-thread did not hide the panel")
        with c.rule("E6.5", "reaction-button opens an emoji-picker grid; choosing an emoji-option shows a reaction-chip with count 1") as r:
            rows.first.hover()
            rows.first.locator(tid("reaction-button")).click()
            r.check(wait_until(lambda: b.visible("emoji-picker"), 3), "emoji-picker did not open")
            opts = page.locator(f'{tid("emoji-picker")} {tid("emoji-option")}')
            r.check(opts.count() >= 6, f"the picker has {opts.count()} emoji-option buttons, expected a grid of at least 6")
            opts.first.click()
            chip = rows.first.locator(tid("reaction-chip"))
            r.check(wait_until(lambda: chip.count() == 1 and "1" in chip.first.inner_text(), 3), "no reaction-chip showing count 1")
            got = api("GET", f"/api/channels/{ch}/messages", token=owner.token).get("messages") or [{}]
            r.check(got[0].get("reactions") and got[0]["reactions"][0].get("count") == 1, "the reaction was not saved through the API")
        with c.rule("E6.6", "the hover toolbar deletes a message without any dialog; it leaves the list and the API history") as r:
            rows.first.hover()
            rows.first.locator(tid("delete-message-btn")).click()
            r.check(wait_until(lambda: rows.count() == 0, 3), "the row is still shown after delete")
            r.check(api_bodies() == [], f"API history is {api_bodies()}")
            r.check(not b.dialogs, f"dialogs opened: {b.dialogs}")
        with c.rule("E6.7", "three-pane layout at 1280x900: a coloured sidebar left of the message list, which is left of the thread panel") as r:
            sb, ml = page.locator(tid("sidebar")).bounding_box(), page.locator(tid("message-list")).bounding_box()
            r.check(sb and ml and sb["x"] + sb["width"] <= ml["x"] + 5, f"sidebar {sb} is not left of message-list {ml}")
            bg = page.evaluate("s => getComputedStyle(document.querySelector(s)).backgroundColor", tid("sidebar"))
            r.check(bg not in ("rgba(0, 0, 0, 0)", "transparent", "rgb(255, 255, 255)"), f"sidebar background is {bg}, expected a colour")


GROUPS = [
    ("E0", e0, ["E0.1", "E0.2"], None),
    ("E1", e1, [f"E1.{i}" for i in range(1, 8)], None),
    ("E2", e2, [f"E2.{i}" for i in range(1, 6)], None),
    ("E3", e3, [f"E3.{i}" for i in range(1, 5)], None),
    ("E4", e4, [f"E4.{i}" for i in range(1, 6)], None),
    ("E5", e5, [f"E5.{i}" for i in range(1, 8)], None),
    ("E6", e6, [f"E6.{i}" for i in range(1, 8)], None),
]
STEPS = {"1": ("E0", "E1"), "2": ("E2",), "3": ("E3",), "4": ("E4",), "5": ("E5",), "6": ("E6",),
         "all": tuple(g[0] for g in GROUPS)}

if __name__ == "__main__":
    step, rest = sys.argv[1], sys.argv[2:]
    run([g for g in GROUPS if g[0] in STEPS[step]], rest, f"bash /spec/e/checks/check_e_{step}.sh [{' '.join(STEPS[step])}]")
