"""Shared helpers for the checks. Imported by the rule modules; never run directly.

Standard library only, so the checks run in any container without installing anything.
"""
import base64
import hashlib
import http.client
import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
from types import SimpleNamespace

PORTS = (8000, 8001, 8002)
RESTART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "restart.sh")
# Leaves headroom under the agent's 30 s per-command cap for Python start-up and output.
TIME_LIMIT_S = 26
PW = "correct-horse-1"
# Each complete check run records its result here, with a fingerprint of /app, for ready.sh.
RESULTS = "/tmp/spec_check_results.json"
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
USER_FIELDS = ("id", "username", "display_name", "timezone", "avatar_url", "status_text", "status_emoji")
WORKSPACE_FIELDS = ("id", "slug", "name", "owner_id", "join_mode")
CHANNEL_FIELDS = ("id", "workspace_id", "name", "is_private", "is_dm", "topic", "is_archived")
MESSAGE_FIELDS = ("id", "channel_id", "author_id", "author", "body", "parent_id", "reply_count",
                  "created_at", "edited_at", "files", "reactions", "mentions")


class OutOfTime(BaseException):
    """Raised by SIGALRM; a BaseException so a rule's own error handling cannot swallow it."""


class Fail(Exception):
    pass


def uniq(prefix):
    """A fresh lowercase name that is a valid username, workspace slug and channel name."""
    return f"{prefix}{int(time.time() * 1000) % 10**8}{os.urandom(2).hex()}"


def same(a, b):
    """Ids may come back as ints or strings; compare them by value."""
    return a is not None and str(a) == str(b)


def short(text, n=170):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "..."


# ---------------------------------------------------------------- HTTP

class Resp:
    def __init__(self, status, data, text, ctype):
        self.status, self.json, self.text, self.ctype = status, data, text, ctype

    def get(self, dotted, default=None):
        v = self.json
        for k in dotted.split("."):
            if not isinstance(v, dict) or k not in v:
                return default
            v = v[k]
        return v

    def brief(self):
        return f"got {self.status} {short(self.text, 90)}" if self.status else f"no answer ({self.text})"


def api(method, path, body=None, token=None, port=8000, timeout=5):
    """Sends one request and never raises; status 0 means the node did not answer."""
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        raw = r.read().decode(errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return Resp(r.status, parsed, raw, r.getheader("Content-Type", ""))
    except (OSError, http.client.HTTPException) as e:
        return Resp(0, None, e.__class__.__name__, "")
    finally:
        conn.close()


def raw_post(path, raw_body, token, port=8000):
    """POSTs bytes that are not valid JSON; returns the status (0 if no answer)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, body=raw_body,
                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        return conn.getresponse().status
    except (OSError, http.client.HTTPException):
        return 0
    finally:
        conn.close()


def upload(token, filename, data, content_type="application/octet-stream", port=8000, timeout=15):
    """POSTs /api/files as multipart/form-data with one 'file' part. Never raises."""
    boundary = "----check" + os.urandom(8).hex()
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n").encode()
    body = head + data + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Authorization": f"Bearer {token}"}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("POST", "/api/files", body=body, headers=headers)
        r = conn.getresponse()
        raw = r.read().decode(errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return Resp(r.status, parsed, raw, r.getheader("Content-Type", ""))
    except (OSError, http.client.HTTPException) as e:
        return Resp(0, None, e.__class__.__name__, "")
    finally:
        conn.close()


def fetch(path, token=None, port=8000, timeout=5):
    """GET returning (status, headers as a lower-cased dict, raw bytes); status 0 means no answer."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Authorization": f"Bearer {token}"} if token else {})
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    except (OSError, http.client.HTTPException):
        return 0, {}, b""
    finally:
        conn.close()


def q(**params):
    return "?" + urllib.parse.urlencode(params)


def healthy(port, timeout=2):
    return api("GET", "/api/health", port=port, timeout=timeout).status == 200


def cluster_up():
    return all(healthy(p) for p in PORTS)


def wait_until(pred, timeout, step=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(step)
    return pred()


def listener_pids(port):
    """PIDs listening on a TCP port, found by port (never by process name)."""
    try:
        out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True, timeout=3).stdout
        return sorted({int(p) for p in re.findall(r"pid=(\d+)", out)})
    except FileNotFoundError:
        out = subprocess.run(["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=3).stdout
        return sorted({int(p) for p in out.split()})


def kill_port(port):
    pids = listener_pids(port)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return pids


# ---------------------------------------------------------------- setup helpers (Component A routes)

def register(prefix="u", port=8000):
    name = uniq(prefix)
    r = api("POST", "/api/auth/register", {"username": name, "password": PW}, port=port)
    if r.status != 201 or not r.get("token") or r.get("user.id") is None:
        raise Fail(f"setup: POST /api/auth/register {r.brief()}")
    return SimpleNamespace(id=r.get("user.id"), username=name, token=r.get("token"))


def make_workspace(owner, prefix="ws"):
    slug = uniq(prefix)
    r = api("POST", "/api/workspaces", {"slug": slug, "name": "Check " + slug}, owner.token)
    if r.status != 201 or r.get("workspace.id") is None or r.get("general_channel.id") is None:
        raise Fail(f"setup: POST /api/workspaces {r.brief()}")
    return SimpleNamespace(id=r.get("workspace.id"), slug=slug, general=r.get("general_channel.id"))


def make_channel(user, ws, prefix="ch", private=False, port=8000):
    name = uniq(prefix)
    r = api("POST", f"/api/workspaces/{ws.slug}/channels", {"name": name, "is_private": private}, user.token, port)
    if r.status != 201 or r.get("channel.id") is None:
        raise Fail(f"setup: create channel {name} {r.brief()}")
    return r.get("channel.id")


def join(user, channel_id, port=8000):
    return api("POST", f"/api/channels/{channel_id}/join", {}, user.token, port)


def must_join(user, channel_id):
    r = join(user, channel_id)
    if r.status != 200:
        raise Fail(f"setup: join channel {channel_id} {r.brief()}")


def set_role(actor, ws, user, role):
    return api("PATCH", f"/api/workspaces/{ws.slug}/members/{user.id}", {"role": role}, actor.token)


def post(user, channel_id, body, port=8000, **extra):
    return api("POST", f"/api/channels/{channel_id}/messages", {"body": body, **extra}, user.token, port)


def must_post(user, channel_id, body, port=8000, **extra):
    r = post(user, channel_id, body, port, **extra)
    if r.status != 201 or r.get("message.id") is None:
        raise Fail(f"setup: post message {r.brief()}")
    return r.get("message")


def roles_of(owner, ws):
    """{user_id as str: role} from GET /api/workspaces/{slug}/members."""
    r = api("GET", f"/api/workspaces/{ws.slug}/members", token=owner.token)
    return {str(m.get("user_id")): m.get("role") for m in (r.get("members") or []) if isinstance(m, dict)}


# ---------------------------------------------------------------- WebSocket client

class WS:
    """Minimal RFC 6455 client. .status is the handshake HTTP status (101 on success)."""

    def __init__(self, port, token, timeout=3.0):
        self.frames, self.buf, self.closed = [], b"", False
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET /api/ws?token={urllib.parse.quote(token)} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        try:
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                head += chunk
        except OSError:
            pass
        header, _, self.buf = head.partition(b"\r\n\r\n")
        parts = header.split(b" ", 2)
        self.status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if self.status != 101:
            self.close()

    def close(self):
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass

    def send_json(self, obj):
        data, mask = json.dumps(obj).encode(), os.urandom(4)
        n = len(data)
        if n < 126:
            hdr = bytes([0x81, 0x80 | n])
        elif n < 65536:
            hdr = bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _parse(self):
        """Pops one complete frame off the buffer as (fin, opcode, payload), or None if incomplete."""
        b = self.buf
        if len(b) < 2:
            return None
        n, i = b[1] & 0x7F, 2
        if n == 126:
            if len(b) < 4:
                return None
            n, i = struct.unpack(">H", b[2:4])[0], 4
        elif n == 127:
            if len(b) < 10:
                return None
            n, i = struct.unpack(">Q", b[2:10])[0], 10
        mask = b""
        if b[1] & 0x80:
            mask, i = b[i:i + 4], i + 4
        if len(b) < i + n:
            return None
        payload = b[i:i + n]
        if mask:
            payload = bytes(x ^ mask[k % 4] for k, x in enumerate(payload))
        self.buf = b[i + n:]
        return b[0] & 0x80, b[0] & 0x0F, payload

    def recv(self, timeout):
        """Next JSON frame as a dict, or None on timeout or close."""
        if self.closed and not self.buf:
            return None
        deadline, message = time.monotonic() + timeout, b""
        while True:
            frame = self._parse()
            if frame is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self.closed:
                    return None
                try:
                    self.sock.settimeout(remaining)
                    chunk = self.sock.recv(65536)
                except socket.timeout:
                    return None
                except OSError:
                    chunk = b""
                if not chunk:
                    self.closed = True
                    return None
                self.buf += chunk
                continue
            fin, op, payload = frame
            if op == 8:
                self.closed = True
                return None
            if op == 9:
                self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + b"\0\0\0\0" + payload)
                continue
            if op in (0, 1, 2):
                message += payload
                if not fin:
                    continue
                try:
                    obj = json.loads(message.decode())
                except ValueError:
                    obj = {"_raw": message.decode(errors="replace")}
                obj = obj if isinstance(obj, dict) else {"_raw": obj}
                self.frames.append(obj)
                return obj

    def wait_for(self, pred, timeout=3.0):
        deadline = time.monotonic() + timeout
        while True:
            f = self.recv(max(0.0, deadline - time.monotonic()))
            if f is None:
                return None
            if pred(f):
                return f

    def subscribe(self, channel_id, timeout=3.0):
        self.send_json({"type": "subscribe", "channel_id": channel_id})
        return self.wait_for(lambda f: f.get("type") == "subscribed" and same(f.get("channel_id"), channel_id), timeout)

    def durable(self, channel_id, count, timeout=3.0):
        """Collects up to `count` frames that carry an event_id for this channel."""
        got, deadline = [], time.monotonic() + timeout
        while len(got) < count:
            f = self.wait_for(lambda f: "event_id" in f and same(f.get("channel_id"), channel_id),
                              max(0.0, deadline - time.monotonic()))
            if f is None:
                break
            got.append(f)
        return got


def events_text(frames):
    return ", ".join(f"{f.get('type')}#{f.get('event_id')}" for f in frames) or "nothing"


# ---------------------------------------------------------------- IRC client

class IRC:
    def __init__(self, port=6667, timeout=3.0):
        self.buf, self.lines, self.closed = b"", [], False
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)

    def send(self, *lines):
        self.sock.sendall("".join(line + "\r\n" for line in lines).encode())

    def wait_for(self, pred, timeout=3.0):
        deadline = time.monotonic() + timeout
        while True:
            while b"\n" in self.buf:
                raw, _, self.buf = self.buf.partition(b"\n")
                line = raw.decode(errors="replace").rstrip("\r")
                self.lines.append(line)
                if pred(line):
                    return line
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.closed:
                return None
            try:
                self.sock.settimeout(remaining)
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None
            except OSError:
                chunk = b""
            if not chunk:
                self.closed = True
                return None
            self.buf += chunk

    def numeric(self, code, timeout=3.0):
        return self.wait_for(lambda s: len(s.split()) > 1 and s.split()[1] == code, timeout)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------- cluster baseline (rules X0.1 to X0.6)

def baseline_ids(p):
    return [f"{p}.{i}" for i in range(1, 7)]


def baseline_rules(c, p):
    """Rules p.1 to p.5. p is "A0" or "B0": both specs carry the same baseline."""
    with c.rule(f"{p}.1", "GET /api/health on 8000 returns 200 {status: ok, node_id: 0}") as r:
        h = api("GET", "/api/health")
        r.status(h, 200)
        r.check(h.get("status") == "ok" and h.get("node_id") == 0, f"body was {short(h.text, 80)}")
    with c.rule(f"{p}.2", "8000, 8001, 8002 report node_id 0, 1, 2") as r:
        ids = [api("GET", "/api/health", port=port).get("node_id") for port in PORTS]
        r.check(ids == [0, 1, 2], f"node_ids were {ids}")
    with c.rule(f"{p}.3", "WebSocket /api/ws with a bogus token is rejected before the upgrade") as r:
        ws = WS(8000, "bogus-token-0000000000")
        ws.close()
        r.check(ws.status != 101, "handshake returned 101: reject a bad token with 401 instead of upgrading")
    with c.rule(f"{p}.4", "Redis answers PING on 127.0.0.1:6379") as r:
        try:
            s = socket.create_connection(("127.0.0.1", 6379), timeout=2)
            s.sendall(b"PING\r\n")
            reply = s.recv(64)
            s.close()
        except OSError as e:
            reply = str(e).encode()
        r.check(reply.startswith(b"+PONG"), f"got {reply[:40]!r}; start.sh must keep redis-server running")
    with c.rule(f"{p}.5", "/app/start.sh is executable and /app/requirements.txt exists") as r:
        r.check(os.access("/app/start.sh", os.X_OK), "/app/start.sh is not executable")
        r.check(os.path.isfile("/app/requirements.txt"), "/app/requirements.txt is missing")


def crash_rule(c, p):
    """Rule p.6. Runs last because it kills node 8001."""
    with c.rule(f"{p}.6", "after SIGKILL of node 8001, nodes 8000 and 8002 still answer and 8001 is back within 15s") as r:
        pids = kill_port(8001)
        r.check(pids, "no process is listening on 8001")
        time.sleep(0.3)
        r.check(healthy(8000) and healthy(8002), "8000 or 8002 stopped answering after 8001 was killed")
        r.check(wait_until(lambda: healthy(8001, 1), 15), "8001 did not come back within 15s: start.sh must respawn a dead node")


# ---------------------------------------------------------------- rules and the runner

class Rule:
    def __init__(self, checker, rid, desc):
        self.c, self.rid, self.desc = checker, rid, desc

    def __enter__(self):
        return self

    def check(self, cond, why):
        if not cond:
            raise Fail(why)

    def status(self, resp, *expected):
        if resp.status not in expected:
            raise Fail(f"expected {'/'.join(map(str, expected))}, {resp.brief()}")

    def __exit__(self, etype, exc, tb):
        if etype is None:
            self.c.record(self.rid, True, self.desc)
        elif issubclass(etype, OutOfTime):
            return False
        elif issubclass(etype, Fail):
            self.c.record(self.rid, False, f"{self.desc}: {exc}")
        else:
            msg = str(exc)
            # Browser timeouts bury the element they waited for deep in a long message; surface it first.
            loc = re.search(r"waiting for (locator\([^\n]*?\))", msg)
            if loc:
                why = f"timed out waiting for {loc.group(1)} (it is missing, hidden or disabled)"
            else:
                why = f"check crashed on the response ({etype.__name__}: {msg.splitlines()[0] if msg else ''})"
            self.c.record(self.rid, False, f"{self.desc}: {why}")
        return True


class Checker:
    """Collects rule results and prints them according to the output mode.

    verbose: a PASS or FAIL line per rule. score_only: nothing per rule. regression: only failures,
    as REGRESSION FAIL lines tagged with the component they belong to.
    """

    def __init__(self, mode, labels):
        self.passed, self.failed, self.done = 0, 0, set()
        self.mode, self.labels = mode, labels

    def rule(self, rid, desc):
        return Rule(self, rid, desc)

    def say(self, line):
        if self.mode == "verbose":
            print(line, flush=True)

    def record(self, rid, ok, text):
        self.done.add(rid)
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        if self.mode == "verbose":
            print(f"{'PASS' if ok else 'FAIL'} {rid} {short(text, 160)}", flush=True)
        elif self.mode == "regression" and not ok:
            print(f"REGRESSION FAIL {self.labels.get(rid[0], rid[0])} {rid} {short(text, 160)}", flush=True)


def output_mode():
    if os.environ.get("CHECK_MODE") == "regression":
        return "regression"
    # The mode is set once in the lib.sh beside the check; reading it here means running the rule file
    # directly gives the same output as the wrapper.
    mode = os.environ.get("FEEDBACK")
    try:
        lib = open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "lib.sh")).read()
        found = re.search(r"^FEEDBACK=(\w+)", lib, re.M)
        mode = found.group(1) if found else mode
    except OSError:
        pass
    return "score_only" if mode == "score_only" else "verbose"


def app_fingerprint():
    """Hash of the code in /app (not its data, logs or caches), so results can be tied to a version of it."""
    h = hashlib.sha256()
    for root, dirs, files in os.walk("/app"):
        dirs[:] = sorted(d for d in dirs if d not in ("data", "__pycache__", ".git", "venv", ".venv", "node_modules"))
        for f in sorted(files):
            if f.endswith((".pyc", ".log", ".pid", ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3")) or f == ".timer_start":
                continue
            path = os.path.join(root, f)
            try:
                h.update(path.encode() + b"\0" + open(path, "rb").read())
            except OSError:
                pass
    return h.hexdigest()[:16]


def save_result(name, passed, failed):
    try:
        results = json.load(open(RESULTS))
    except (OSError, ValueError):
        results = {}
    results[name] = {"passed": passed, "failed": failed, "app": app_fingerprint(), "at": int(time.time())}
    try:
        json.dump(results, open(RESULTS, "w"))
    except OSError:
        pass


def ready(spec="/spec"):
    """Prints, for every check in /spec, whether its last complete run passed on the current /app."""
    import glob
    checks = sorted(glob.glob(os.path.join(spec, "*", "checks", "check_*.sh")),
                    key=lambda p: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p)])
    if os.path.exists(os.path.join(spec, "checks", "regression.sh")):
        checks.insert(0, os.path.join(spec, "checks", "regression.sh"))
    try:
        results = json.load(open(RESULTS))
    except (OSError, ValueError):
        results = {}
    now, todo = app_fingerprint(), []
    for chk in checks:
        r = results.get(chk)
        if r is None:
            todo.append(f"{chk}: not run yet")
        elif r["app"] != now:
            todo.append(f"{chk}: last run was before your latest change to /app; run it again")
        elif r["failed"]:
            todo.append(f"{chk}: " + (f"{r['failed']} broken" if chk.endswith("regression.sh")
                                      else f"{r['passed']} passed, {r['failed']} failed"))
    down = [p for p in (8000, 8001, 8002) if not healthy(p)]
    if down:
        todo.append(f"GET /api/health does not answer on {', '.join(map(str, down))}; run bash {RESTART}")
    for line in todo:
        print("NOT DONE " + line)
    print("READY TO SUBMIT" if not todo else f"NOT READY: {len(todo)} item(s) above; fix or rerun them, then run this again")
    return not todo


def run(groups, argv, usage, labels=None):
    """Runs rule groups, prints results in the current output mode, exits 0 only if all pass.

    groups: ordered list of (group_id, setup_and_rules_fn, [rule ids], final_fn or None).
    final_fn runs after every selected group, so destructive rules (killing a node) go last.
    argv: group ids to run; empty means all. labels: {rule-id first letter: component name}.
    """
    known = [g[0] for g in groups]
    wanted = [a.upper() for a in argv]
    if any(a not in known for a in wanted):
        print(f"unknown group in {argv}; choose from {' '.join(known)}\nusage: {usage}")
        sys.exit(2)
    todo = [g for g in groups if not wanted or g[0] in wanted]
    c, notes = Checker(output_mode(), labels or {}), {}

    def on_alarm(*_):
        raise OutOfTime()

    signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(TIME_LIMIT_S)
    try:
        if not cluster_up():
            c.say("NOTE cluster was not answering on 8000/8001/8002; running restart.sh")
            subprocess.run(["bash", RESTART], capture_output=True, timeout=22)
        if not cluster_up():
            notes = {g[0]: "cluster is not answering /api/health on 8000, 8001, 8002; run "
                           f"bash {RESTART} and read /tmp/start.log" for g in todo}
        else:
            for stage in (1, 3):
                for g in todo:
                    fn = g[stage]
                    if fn is None or g[0] in notes:
                        continue
                    try:
                        fn(c)
                    except Fail as e:
                        notes[g[0]] = str(e)
                    except Exception as e:  # noqa: BLE001 - any crash in setup must still produce FAIL lines
                        notes[g[0]] = f"setup crashed ({e.__class__.__name__}: {e})"
    except OutOfTime:
        late = [g[0] for g in todo if g[0] not in notes and any(rid not in c.done for rid in g[2])]
        cmd = usage.split(" [")[0]
        for gid in late:
            notes[gid] = (f"check hit its {TIME_LIMIT_S}s limit before finishing {' '.join(late)}; "
                          f"run them in a separate command: {cmd} {' '.join(late)}")
    signal.alarm(0)
    printed = set()
    for g in todo:
        missing = [rid for rid in g[2] if rid not in c.done]
        note = notes.get(g[0], "the group stopped before reaching these rules")
        if missing and c.mode == "regression":
            # One line per group, so a mostly unbuilt app cannot push the output past the transcript cut-off.
            c.failed += len(missing)
            c.done.update(missing)
            rng = missing[0] if len(missing) == 1 else f"{missing[0]} to {missing[-1]}"
            print(f"REGRESSION FAIL {c.labels.get(g[0][0], g[0][0])} {rng} not run ({len(missing)} rules): {short(note, 120)}", flush=True)
            continue
        if missing and note not in printed:
            c.say(f"NOTE {g[0]}: {short(note, 220)}")
            printed.add(note)
        for rid in missing:
            c.record(rid, False, f"not run (see NOTE above for {g[0]})")
    if not wanted:  # only a complete run of a check counts for ready.sh, not a run of some of its groups
        save_result(usage.split(" [")[0].split()[-1], c.passed, c.failed)
    if c.mode == "regression":
        print(f"REGRESSION: {c.failed} broken", flush=True)
    else:
        print(f"RESULT: {c.passed} passed, {c.failed} failed", flush=True)
    sys.exit(0 if c.failed == 0 else 1)
