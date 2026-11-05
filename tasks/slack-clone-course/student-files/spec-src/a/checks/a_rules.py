"""Rule code for Component A (roles and invitations). Each function tests one rule group of the spec.

Imported by the check scripts next to this file; run those, not this module.
"""
import http.client
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
from checklib import (PW, USER_FIELDS, WORKSPACE_FIELDS, Fail, api, baseline_ids, baseline_rules,  # noqa: E402
                      crash_rule, join, make_channel, make_workspace, must_join, raw_post, register, roles_of,
                      same, set_role, uniq)

NO_ID = 999999999


def a0(c):
    baseline_rules(c, "A0")
    with c.rule("A0.7", "Component A routes return 401 with no token and with a bogus token") as r:
        routes = [("GET", "/api/auth/me"), ("GET", "/api/workspaces"), ("POST", "/api/workspaces"),
                  ("GET", "/api/workspaces/nope/members"), ("PATCH", "/api/workspaces/nope"),
                  ("PATCH", "/api/workspaces/nope/members/1"), ("POST", "/api/workspaces/nope/transfer_ownership"),
                  ("POST", "/api/workspaces/nope/invitations"), ("GET", "/api/workspaces/nope/invitations"),
                  ("POST", "/api/invitations/nope/accept"), ("POST", f"/api/channels/{NO_ID}/join")]
        wrong = []
        for method, path in routes:
            for token in (None, "bogus-token-0000000000"):
                s = api(method, path, {} if method != "GET" else None, token)
                if s.status != 401:
                    wrong.append(f"{method} {path} {'bogus' if token else 'none'}->{s.status}")
        r.check(not wrong, "expected 401 for " + "; ".join(wrong[:4]))
    with c.rule("A0.8", "errors and successes are JSON objects with Content-Type application/json") as r:
        s = api("GET", "/api/auth/me")
        r.check(isinstance(s.json, dict) and "application/json" in s.ctype,
                f"401 body/Content-Type was {s.ctype!r} {s.text[:60]!r}")
        h = api("GET", "/api/health")
        r.check("application/json" in h.ctype, f"/api/health Content-Type was {h.ctype!r}")


def a0_final(c):
    crash_rule(c, "A0")


def a1(c):
    name, token, uid = uniq("au"), None, None
    with c.rule("A1.1", "register returns 201 {user, token}; token is 16+ chars; user has every UserObj field") as r:
        s = api("POST", "/api/auth/register", {"username": name, "password": PW, "display_name": "Check User"})
        r.status(s, 201)
        token, user = s.get("token"), s.get("user") or {}
        uid = user.get("id")
        r.check(isinstance(token, str) and len(token) >= 16, f"token {token!r} is not a string of 16+ chars")
        r.check(user.get("username") == name, f"user.username was {user.get('username')!r}, expected {name!r}")
        missing = [f for f in USER_FIELDS if f not in user]
        r.check(not missing, f"user is missing fields {missing}")
        r.check(user.get("display_name") == "Check User", f"display_name was {user.get('display_name')!r}")
        r.check(not any("password" in k for k in user), "user object exposes a password field")
    with c.rule("A1.2", "registering the same username again returns 409") as r:
        r.status(api("POST", "/api/auth/register", {"username": name, "password": PW}), 409)
    with c.rule("A1.3", "invalid usernames (space, hyphen, '!', empty, missing) return 400") as r:
        wrong = []
        for bad in ("bad name", "bad-name", "bad!name", ""):
            s = api("POST", "/api/auth/register", {"username": bad, "password": PW})
            if s.status != 400:
                wrong.append(f"{bad!r}->{s.status}")
        s = api("POST", "/api/auth/register", {"password": PW})
        if s.status != 400:
            wrong.append(f"missing->{s.status}")
        r.check(not wrong, "expected 400 for " + ", ".join(wrong))
    with c.rule("A1.4", "7-char or missing password returns 400; an 8-char password is accepted") as r:
        r.status(api("POST", "/api/auth/register", {"username": uniq("ap"), "password": "short77"}), 400)
        r.status(api("POST", "/api/auth/register", {"username": uniq("ap")}), 400)
        r.status(api("POST", "/api/auth/register", {"username": uniq("ap"), "password": "exactly8"}), 201)
    with c.rule("A1.5", "login with the right password returns 200 {user, token}") as r:
        s = api("POST", "/api/auth/login", {"username": name, "password": PW})
        r.status(s, 200)
        r.check(isinstance(s.get("token"), str) and len(s.get("token")) >= 16, "login token missing or < 16 chars")
        r.check(s.get("user.username") == name, f"user.username was {s.get('user.username')!r}")
    with c.rule("A1.6", "login with a wrong password or an unknown username returns 401") as r:
        r.status(api("POST", "/api/auth/login", {"username": name, "password": "WRONG-pass-1"}), 401)
        r.status(api("POST", "/api/auth/login", {"username": uniq("nobody"), "password": PW}), 401)
    with c.rule("A1.7", "GET /api/auth/me returns 200 {user} for the token holder") as r:
        s = api("GET", "/api/auth/me", token=token)
        r.status(s, 200)
        r.check(s.get("user.username") == name and same(s.get("user.id"), uid), f"user was {s.text[:80]}")
    with c.rule("A1.8", "GET /api/auth/me with no header, a bogus token, or a non-Bearer scheme returns 401") as r:
        r.status(api("GET", "/api/auth/me"), 401)
        r.status(api("GET", "/api/auth/me", token="not-a-real-token-000000"), 401)
        r.check(token is not None, "no token from A1.1")
        raw = http.client.HTTPConnection("127.0.0.1", 8000, timeout=5)
        raw.request("GET", "/api/auth/me", headers={"Authorization": f"Token {token}"})
        st = raw.getresponse().status
        raw.close()
        r.check(st == 401, f"'Authorization: Token <token>' returned {st}, expected 401")
    with c.rule("A1.9", "a user registered on 8000 logs in on 8002; tokens work on every node") as r:
        s = api("POST", "/api/auth/login", {"username": name, "password": PW}, port=8002)
        r.status(s, 200)
        t2 = s.get("token")
        for port in (8000, 8001, 8002):
            for t in (token, t2):
                r.status(api("GET", "/api/auth/me", token=t, port=port), 200)


def a2(c):
    owner, other = register("ao"), register("ax")
    slug, ws = uniq("ws"), None
    with c.rule("A2.1", "POST /api/workspaces returns 201 {workspace, general_channel}; join_mode open; caller is owner") as r:
        s = api("POST", "/api/workspaces", {"slug": slug, "name": "Acme Check"}, owner.token)
        r.status(s, 201)
        w, g = s.get("workspace") or {}, s.get("general_channel") or {}
        missing = [f for f in WORKSPACE_FIELDS if f not in w]
        r.check(not missing, f"workspace is missing {missing}")
        r.check(w.get("slug") == slug and w.get("name") == "Acme Check", f"workspace was {s.text[:90]}")
        r.check(same(w.get("owner_id"), owner.id), f"owner_id {w.get('owner_id')!r} is not the creator {owner.id!r}")
        r.check(w.get("join_mode") == "open", f"join_mode was {w.get('join_mode')!r}, expected 'open'")
        r.check(g.get("name") == "general" and same(g.get("workspace_id"), w.get("id")) and g.get("is_private") is False,
                f"general_channel was {str(g)[:90]}")
        ws = SimpleNamespace(id=w.get("id"), slug=slug, general=g.get("id"))
    if ws is None or ws.id is None:
        raise Fail("setup: workspace creation failed (A2.1)")
    with c.rule("A2.2", "a duplicate slug returns 409, even from another user") as r:
        r.status(api("POST", "/api/workspaces", {"slug": slug, "name": "Again"}, other.token), 409)
    with c.rule("A2.3", "bad slugs (uppercase, 1 char, 33 chars, space, underscore) or a missing name return 400") as r:
        wrong = []
        for bad in ("Bad-Slug", "a", "x" * 33, "has space", "under_score"):
            s = api("POST", "/api/workspaces", {"slug": bad, "name": "Bad"}, owner.token)
            if s.status != 400:
                wrong.append(f"{bad[:12]!r}->{s.status}")
        s = api("POST", "/api/workspaces", {"slug": uniq("ws")}, owner.token)
        if s.status != 400:
            wrong.append(f"no name->{s.status}")
        r.check(not wrong, "expected 400 for " + ", ".join(wrong))
    with c.rule("A2.4", "GET /api/workspaces lists only the caller's workspaces") as r:
        s = api("GET", "/api/workspaces", token=owner.token)
        r.status(s, 200)
        r.check(slug in [w.get("slug") for w in s.get("workspaces") or []], "owner's workspace is not listed")
        s = api("GET", "/api/workspaces", token=other.token)
        r.status(s, 200)
        r.check(isinstance(s.get("workspaces"), list), "missing workspaces list")
        r.check(slug not in [w.get("slug") for w in s.get("workspaces")], "a non-member sees the workspace")
    with c.rule("A2.5", "GET /api/workspaces/{slug}/members lists the owner with role owner; non-member 403; unknown 404") as r:
        s = api("GET", f"/api/workspaces/{slug}/members", token=owner.token)
        r.status(s, 200)
        r.check(roles_of(owner, ws).get(str(owner.id)) == "owner", f"members were {s.text[:90]}")
        r.status(api("GET", f"/api/workspaces/{slug}/members", token=other.token), 403)
        r.status(api("GET", f"/api/workspaces/{uniq('zz')}/members", token=owner.token), 404)
    pub = None
    with c.rule("A2.6", "the owner creates a public channel: 201 {channel}") as r:
        s = api("POST", f"/api/workspaces/{slug}/channels", {"name": "proj"}, owner.token)
        r.status(s, 201)
        pub = s.get("channel.id")
        r.check(s.get("channel.name") == "proj" and s.get("channel.is_private") is False, f"channel was {s.text[:90]}")
    with c.rule("A2.7", "open workspace: a non-member joining a public channel gets 200 and becomes a 'member'") as r:
        r.check(pub is not None, "no channel from A2.6")
        s = join(other, pub)
        r.status(s, 200)
        r.check(same(s.get("channel.id"), pub), f"join should return {{channel}}, got {s.text[:80]}")
        r.check(roles_of(owner, ws).get(str(other.id)) == "member", "joiner is not listed with role member")
        s = api("GET", "/api/workspaces", token=other.token)
        r.check(slug in [w.get("slug") for w in s.get("workspaces") or []], "joined workspace missing from GET /api/workspaces")
    with c.rule("A2.8", "joining again returns 200 and does not duplicate or change the membership") as r:
        r.status(join(other, pub), 200)
        s = api("GET", f"/api/workspaces/{slug}/members", token=owner.token)
        ids = [str(m.get("user_id")) for m in s.get("members") or []]
        r.check(ids.count(str(other.id)) == 1, f"user listed {ids.count(str(other.id))} times")
        r.check(roles_of(owner, ws).get(str(other.id)) == "member", "role changed after a second join")
    with c.rule("A2.9", "joining an unknown channel returns 404; a private channel without an invite returns 403") as r:
        r.status(join(other, NO_ID), 404)
        s = api("POST", f"/api/workspaces/{slug}/channels", {"name": "secret", "is_private": True}, owner.token)
        r.status(s, 201)
        r.status(join(other, s.get("channel.id")), 403)


def a3(c):
    owner, adm, adm2, mem, gst = (register(p) for p in ("ro", "ra", "rb", "rm", "rg"))
    ws = make_workspace(owner)
    for u in (adm, adm2, mem, gst):
        must_join(u, ws.general)
    with c.rule("A3.1", "the owner promotes members to admin: 200 {member: {user_id, role}}") as r:
        s = set_role(owner, ws, adm, "admin")
        r.status(s, 200)
        r.check(same(s.get("member.user_id"), adm.id) and s.get("member.role") == "admin", f"body was {s.text[:90]}")
        r.status(set_role(owner, ws, adm2, "admin"), 200)
        r.check(roles_of(owner, ws).get(str(adm.id)) == "admin", "members list does not show the new admin")
    with c.rule("A3.2", "an admin can move a non-admin between member and guest") as r:
        r.status(set_role(adm, ws, gst, "guest"), 200)
        r.status(set_role(adm, ws, mem, "guest"), 200)
        r.status(set_role(adm, ws, mem, "member"), 200)
        roles = roles_of(owner, ws)
        r.check(roles.get(str(gst.id)) == "guest" and roles.get(str(mem.id)) == "member", f"roles were {roles}")
    with c.rule("A3.3", "an admin changing another admin or the owner gets 403") as r:
        r.status(set_role(adm, ws, adm2, "member"), 403)
        r.status(set_role(adm, ws, owner, "member"), 403)
        r.check(roles_of(owner, ws).get(str(adm2.id)) == "admin", "the other admin's role changed anyway")
    with c.rule("A3.4", "role 'owner', an unknown role, or a missing role returns 400") as r:
        r.status(set_role(owner, ws, mem, "owner"), 400)
        r.status(set_role(owner, ws, mem, "superuser"), 400)
        r.status(api("PATCH", f"/api/workspaces/{ws.slug}/members/{mem.id}", {}, owner.token), 400)
    with c.rule("A3.5", "the owner cannot change their own role via PATCH (403)") as r:
        r.status(set_role(owner, ws, owner, "admin"), 403)
        r.check(roles_of(owner, ws).get(str(owner.id)) == "owner", "the owner's role changed")
    with c.rule("A3.6", "a member or guest changing roles gets 403") as r:
        r.status(set_role(mem, ws, gst, "member"), 403)
        r.status(set_role(gst, ws, mem, "guest"), 403)
    with c.rule("A3.7", "PATCH for a user who is not in the workspace returns 404") as r:
        r.status(api("PATCH", f"/api/workspaces/{ws.slug}/members/{NO_ID}", {"role": "member"}, owner.token), 404)
    with c.rule("A3.8", "a guest cannot join a public channel or create a channel (403)") as r:
        pub = make_channel(owner, ws, "pub")
        r.status(join(gst, pub), 403)
        r.status(api("POST", f"/api/workspaces/{ws.slug}/channels", {"name": uniq("g")}, gst.token), 403)
    with c.rule("A3.9", "a member creates public channels but not private ones (403); an admin creates private ones") as r:
        r.status(api("POST", f"/api/workspaces/{ws.slug}/channels", {"name": uniq("m")}, mem.token), 201)
        r.status(api("POST", f"/api/workspaces/{ws.slug}/channels", {"name": uniq("m"), "is_private": True}, mem.token), 403)
        r.status(api("POST", f"/api/workspaces/{ws.slug}/channels", {"name": uniq("a"), "is_private": True}, adm.token), 201)
    with c.rule("A3.10", "transfer_ownership: an admin caller gets 403; a non-admin target gets 400") as r:
        r.status(api("POST", f"/api/workspaces/{ws.slug}/transfer_ownership", {"new_owner_id": adm2.id}, adm.token), 403)
        r.status(api("POST", f"/api/workspaces/{ws.slug}/transfer_ownership", {"new_owner_id": mem.id}, owner.token), 400)
        r.check(roles_of(owner, ws).get(str(owner.id)) == "owner", "ownership moved anyway")
    with c.rule("A3.11", "transfer to an admin: 200; target becomes owner (owner_id too), previous owner becomes admin") as r:
        r.status(api("POST", f"/api/workspaces/{ws.slug}/transfer_ownership", {"new_owner_id": adm.id}, owner.token), 200)
        roles = roles_of(adm, ws)
        r.check(roles.get(str(adm.id)) == "owner" and roles.get(str(owner.id)) == "admin", f"roles were {roles}")
        s = api("GET", "/api/workspaces", token=adm.token)
        w = [x for x in s.get("workspaces") or [] if x.get("slug") == ws.slug]
        r.check(w and same(w[0].get("owner_id"), adm.id), "workspace owner_id was not updated")
        r.status(api("POST", f"/api/workspaces/{ws.slug}/transfer_ownership", {"new_owner_id": adm2.id}, owner.token), 403)


def a4(c):
    owner, adm, mem = register("io"), register("ia"), register("im")
    outs = [register(f"i{k}") for k in range(8)]
    ws = make_workspace(owner)
    must_join(adm, ws.general)
    must_join(mem, ws.general)
    if set_role(owner, ws, adm, "admin").status != 200:
        raise Fail("setup: could not make an admin (A3.1)")
    inv_path = f"/api/workspaces/{ws.slug}/invitations"
    expiring = api("POST", inv_path, {"expires_in_seconds": 1}, owner.token)
    t_expiring = time.monotonic()

    def listed(code, who=owner):
        return code in [i.get("code") for i in api("GET", inv_path, token=who.token).get("invitations") or []]

    def accept(user, code):
        return api("POST", f"/api/invitations/{code}/accept", {}, user.token)

    code1 = None
    with c.rule("A4.1", "the owner creates an invitation: 201 {invitation} with a code and max_uses 1") as r:
        s = api("POST", inv_path, {}, owner.token)
        r.status(s, 201)
        code1 = s.get("invitation.code")
        r.check(isinstance(code1, str) and code1, f"no code in {s.text[:90]}")
        r.check(s.get("invitation.max_uses") == 1, f"max_uses was {s.get('invitation.max_uses')!r}, expected 1")
    with c.rule("A4.2", "a plain member cannot create, list or revoke invitations (403)") as r:
        r.status(api("POST", inv_path, {}, mem.token), 403)
        r.status(api("GET", inv_path, token=mem.token), 403)
        r.status(api("DELETE", f"{inv_path}/{code1}", token=mem.token), 403)
    with c.rule("A4.3", "owners and admins list active invitations: 200 {invitations}; admins can create them") as r:
        s = api("GET", inv_path, token=owner.token)
        r.status(s, 200)
        r.check(listed(code1), "the new code is not listed for the owner")
        r.check(listed(code1, adm), "the new code is not listed for an admin")
        r.status(api("POST", inv_path, {}, adm.token), 201)
    with c.rule("A4.4", "accepting adds the user as 'member'; a used-up code then returns 404 and is not listed") as r:
        r.status(accept(outs[0], code1), 200)
        r.check(roles_of(owner, ws).get(str(outs[0].id)) == "member", "accepter is not a member")
        r.status(accept(outs[1], code1), 404)
        r.check(not listed(code1), "an exhausted invitation is still listed")
    with c.rule("A4.5", "max_uses 2 lets exactly two users in; the third gets 404") as r:
        s = api("POST", inv_path, {"max_uses": 2}, owner.token)
        r.status(s, 201)
        code = s.get("invitation.code")
        r.status(accept(outs[1], code), 200)
        r.status(accept(outs[2], code), 200)
        r.status(accept(outs[3], code), 404)
    with c.rule("A4.6", "targeted invites are single-use; another user gets 403, the target gets 200") as r:
        s = api("POST", inv_path, {"invited_user_id": outs[4].id, "max_uses": 5}, owner.token)
        r.status(s, 201)
        r.check(s.get("invitation.max_uses") == 1, f"targeted max_uses was {s.get('invitation.max_uses')!r}, expected 1")
        r.status(accept(outs[3], s.get("invitation.code")), 403)
        r.status(accept(outs[4], s.get("invitation.code")), 200)
        s = api("POST", inv_path, {"invited_username": outs[5].username}, owner.token)
        r.status(s, 201)
        r.status(accept(outs[3], s.get("invitation.code")), 403)
        r.status(accept(outs[5], s.get("invitation.code")), 200)
    with c.rule("A4.7", "an expired invitation (expires_in_seconds 1) returns 404 and is not listed") as r:
        r.status(expiring, 201)
        time.sleep(max(0.0, 2.1 - (time.monotonic() - t_expiring)))
        code = expiring.get("invitation.code")
        r.status(accept(outs[3], code), 404)
        r.check(not listed(code), "an expired invitation is still listed")
    with c.rule("A4.8", "DELETE revokes: 200 {deleted: true}; revoked and unknown codes return 404 on accept") as r:
        code = api("POST", inv_path, {}, owner.token).get("invitation.code")
        s = api("DELETE", f"{inv_path}/{code}", token=owner.token)
        r.status(s, 200)
        r.check(s.get("deleted") is True, f"body was {s.text[:60]}")
        r.status(accept(outs[3], code), 404)
        r.check(not listed(code), "a revoked invitation is still listed")
        r.status(accept(outs[3], "no-such-code-123"), 404)
    with c.rule("A4.9", "PATCH /api/workspaces/{slug}: owner/admin 200 {workspace}; bad join_mode 400; member 403") as r:
        s = api("PATCH", f"/api/workspaces/{ws.slug}", {"join_mode": "invite_only"}, owner.token)
        r.status(s, 200)
        r.check(s.get("workspace.join_mode") == "invite_only", f"join_mode was {s.get('workspace.join_mode')!r}")
        s = api("PATCH", f"/api/workspaces/{ws.slug}", {"name": "Renamed Check"}, adm.token)
        r.status(s, 200)
        r.check(s.get("workspace.name") == "Renamed Check" and s.get("workspace.join_mode") == "invite_only",
                f"workspace was {s.text[:90]}")
        r.status(api("PATCH", f"/api/workspaces/{ws.slug}", {"join_mode": "closed"}, owner.token), 400)
        r.status(api("PATCH", f"/api/workspaces/{ws.slug}", {"name": "Nope"}, mem.token), 403)
    with c.rule("A4.10", "invite_only: a non-member gets 403 joining a public channel, and 200 after accepting an invite") as r:
        pub = make_channel(owner, ws, "pub")
        r.status(join(outs[6], pub), 403)
        r.check(str(outs[6].id) not in roles_of(owner, ws), "the rejected user became a member")
        code = api("POST", inv_path, {}, owner.token).get("invitation.code")
        r.status(accept(outs[6], code), 200)
        r.status(join(outs[6], pub), 200)
    with c.rule("A4.11", "wrong JSON types or a malformed JSON body on Spec A routes return 400, never 500") as r:
        cases = [("POST", "/api/auth/register", {"username": 123, "password": 12345678}),
                 ("POST", "/api/auth/register", {"username": uniq("ty"), "password": None}),
                 ("POST", "/api/workspaces", {"slug": 5, "name": ["x"]}),
                 ("POST", "/api/workspaces", {"slug": uniq("ty"), "name": 7}),
                 ("POST", f"/api/workspaces/{ws.slug}/channels", {"name": None}),
                 ("PATCH", f"/api/workspaces/{ws.slug}/members/{mem.id}", {"role": 3}),
                 ("POST", f"/api/workspaces/{ws.slug}/transfer_ownership", {"new_owner_id": "abc"}),
                 ("POST", inv_path, {"max_uses": "two"}),
                 ("POST", inv_path, {"expires_in_seconds": "1"}),
                 ("PATCH", f"/api/workspaces/{ws.slug}", {"name": 5}),
                 ("PATCH", f"/api/workspaces/{ws.slug}", {"join_mode": None})]
        wrong = [f"{m} {p.split('/api/')[1][:30]} {b}->{s}" for m, p, b in cases
                 if (s := api(m, p, b, owner.token).status) != 400]
        s = api("POST", "/api/auth/login", {"username": None, "password": None})
        if s.status not in (400, 401):
            wrong.append(f"login with null fields->{s.status}")
        s = raw_post("/api/workspaces", b"{not json", owner.token)
        if s != 400:
            wrong.append(f"malformed JSON on POST /api/workspaces->{s}")
        r.check(not wrong, "expected 400 for " + "; ".join(wrong)[:200])


GROUPS = [
    ("A0", a0, baseline_ids("A0") + ["A0.7", "A0.8"], a0_final),
    ("A1", a1, [f"A1.{i}" for i in range(1, 10)], None),
    ("A2", a2, [f"A2.{i}" for i in range(1, 10)], None),
    ("A3", a3, [f"A3.{i}" for i in range(1, 12)], None),
    ("A4", a4, [f"A4.{i}" for i in range(1, 12)], None),
]
