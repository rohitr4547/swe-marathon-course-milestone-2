"""Rule code for Component D (cluster robustness). Each function tests one rule group of the spec.

Run through the check scripts next to this file (check_d_*.sh), not directly.
"""
import os
import socket
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "common"))
from checklib import (PORTS, WS, Fail, api, events_text, healthy, kill_port, make_channel, make_workspace,  # noqa: E402
                      must_join, must_post, post, register, run, short, wait_until)


def redis_ok():
    try:
        s = socket.create_connection(("127.0.0.1", 6379), timeout=1)
        s.sendall(b"PING\r\n")
        ok = s.recv(16).startswith(b"+PONG")
        s.close()
        return ok
    except OSError:
        return False


def setup_channel(prefix):
    owner, mem = register(prefix + "o"), register(prefix + "m")
    ws = make_workspace(owner)
    must_join(mem, ws.general)
    ch = make_channel(owner, ws, prefix)
    must_join(mem, ch)
    return owner, mem, ch


def d0(c):
    with c.rule("D0.1", "GET /api/health returns 200 {status: ok, node_id} with node_id 0, 1, 2 on 8000, 8001, 8002") as r:
        got = [(h.status, h.get("status"), h.get("node_id")) for h in (api("GET", "/api/health", port=p) for p in PORTS)]
        r.check(got == [(200, "ok", 0), (200, "ok", 1), (200, "ok", 2)], f"got {got}")
    with c.rule("D0.2", "a WebSocket with a bogus token is rejected before the upgrade (no 101)") as r:
        w = WS(8000, "bogus-token-0000000000")
        w.close()
        r.check(w.status != 101, "handshake returned 101: reject a bad token with 401 instead of upgrading")
    with c.rule("D0.3", "Redis answers PING on 127.0.0.1:6379; /app/start.sh is executable; /app/requirements.txt exists") as r:
        r.check(redis_ok(), "no +PONG from 127.0.0.1:6379: start.sh must keep redis-server running")
        r.check(os.access("/app/start.sh", os.X_OK) and os.path.isfile("/app/requirements.txt"), "start.sh not executable or requirements.txt missing")


def d1(c):
    owner, mem, ch = setup_channel("nk")
    must_post(owner, ch, "before the kill")
    w2 = WS(8002, mem.token)
    w1 = WS(8001, owner.token)
    s2 = w2.subscribe(ch) if w2.status == 101 else None
    s1 = w1.subscribe(ch) if w1.status == 101 else None
    if not (s1 and s2):
        raise Fail("setup: WebSocket subscribe failed on 8001 or 8002")
    head = s2.get("head_event_id")
    with c.rule("D1.1", "with node 8001 SIGKILLed, 8000 and 8002 still answer health, reads and writes") as r:
        r.check(kill_port(8001), "nothing was listening on 8001")
        time.sleep(0.3)
        r.check(healthy(8000) and healthy(8002), "8000 or 8002 stopped answering /api/health")
        for port in (8000, 8002):
            r.status(api("GET", f"/api/channels/{ch}/messages", token=mem.token, port=port), 200)
        for port in (8000, 8002, 8000):
            r.status(post(owner, ch, f"during outage via {port}", port=port), 201)
    with c.rule("D1.2", "a subscriber on 8002 keeps receiving those posts live, with consecutive event_ids") as r:
        got = w2.durable(ch, 3)
        r.check([f.get("event_id") for f in got] == [head + 1, head + 2, head + 3], f"8002 got {events_text(got)}")
    with c.rule("D1.3", "a client whose node died reconnects to 8000, resumes, and gets every missed event, then 'resumed'") as r:
        w1.recv(1)
        r.check(w1.closed, "the socket on the killed node 8001 did not close")
        w = WS(8000, owner.token)
        w.send_json({"type": "resume", "channel_id": ch, "since_event_id": head})
        frames = []
        end = w.wait_for(lambda f: frames.append(f) or f.get("type") == "resumed", 4)
        w.close()
        replay = [f.get("event_id") for f in frames if "event_id" in f]
        r.check(replay == [head + 1, head + 2, head + 3] and end is not None, f"replayed {replay}, end {end}")
    with c.rule("D1.4", "8001 comes back within 15s with node_id 1, the same history, and the same event head") as r:
        r.check(wait_until(lambda: healthy(8001, 1), 15), "8001 did not answer /api/health within 15s (start.sh must respawn it)")
        r.check(api("GET", "/api/health", port=8001).get("node_id") == 1, "8001 came back with the wrong node_id")
        lists = [[m.get("id") for m in api("GET", f"/api/channels/{ch}/messages", token=mem.token, port=p).get("messages") or []]
                 for p in PORTS]
        r.check(lists[0] == lists[1] == lists[2] and len(lists[0]) == 4, f"histories differ or are incomplete: {[len(x) for x in lists]}")
        w = WS(8001, mem.token)
        sub = w.subscribe(ch) if w.status == 101 else None
        w.close()
        r.check(sub is not None and sub.get("head_event_id") == head + 3, f"8001 subscribe gave {sub}")
    with c.rule("D1.5", "after it returns, a post through 8001 reaches the 8002 subscriber with the next event_id") as r:
        r.status(post(owner, ch, "after return", port=8001), 201)
        got = w2.durable(ch, 1)
        r.check(got and got[0].get("event_id") == head + 4, f"8002 got {events_text(got)}")
    w1.close()
    w2.close()


def d2(c):
    owner, mem, ch = setup_channel("rk")
    subs = {p: WS(p, mem.token) for p in (8000, 8002)}
    heads = [w.subscribe(ch).get("head_event_id") if w.status == 101 else None for w in subs.values()]
    if None in heads or len(set(heads)) != 1:
        raise Fail(f"setup: subscribe on 8000/8002 gave heads {heads}")
    head = heads[0]
    with c.rule("D2.1", "with redis-server SIGKILLed, posts through each of 8000, 8001, 8002 return 201 within 5s") as r:
        r.check(kill_port(6379), "nothing was listening on 6379")
        t0 = time.monotonic()
        for port in (8000, 8001, 8002):
            s = post(owner, ch, f"no redis via {port}", port=port)
            r.check(s.status == 201, f"post via {port}: {s.brief()}")
        r.check(time.monotonic() - t0 < 5, f"the three posts took {time.monotonic() - t0:.1f}s")
    with c.rule("D2.2", "subscribers on 8000 and 8002 receive all three during the outage, event_ids dense from the head") as r:
        for port, w in subs.items():
            got = w.durable(ch, 3, timeout=5)
            r.check([f.get("event_id") for f in got] == [head + 1, head + 2, head + 3], f"{port} got {events_text(got)}")
    with c.rule("D2.3", "start.sh restarts Redis within 10s, and fan-out still works afterwards") as r:
        r.check(wait_until(redis_ok, 10), "redis-server did not answer PING within 10s")
        r.status(post(owner, ch, "redis is back", port=8001), 201)
        for port, w in subs.items():
            got = w.durable(ch, 1)
            r.check(got and got[0].get("event_id") == head + 4, f"{port} got {events_text(got)}")
    for w in subs.values():
        w.close()


def d3(c):
    owner, mem, ch = setup_channel("pf")
    stats = {}
    with c.rule("D3.1", "6 writers on 3 nodes post 60 messages; 3 subscribers (one per node) miss at most 1% of them") as r:
        socks = {p: WS(p, mem.token) for p in PORTS}
        for p, w in socks.items():
            r.check(w.status == 101 and w.subscribe(ch) is not None, f"setup: subscribe on {p} failed")
        sent, arrivals, statuses, done = {}, {p: {} for p in PORTS}, [], threading.Event()

        def listen(port, w):
            while not done.is_set():
                f = w.recv(0.2)
                if f and f.get("type") == "message.created":
                    body = (f.get("message") or {}).get("body", "")
                    arrivals[port].setdefault(body, time.monotonic())

        def write(port, k):
            for i in range(10):
                body = f"perf {port}-{k}-{i}"
                sent[body] = time.monotonic()
                statuses.append(post(owner, ch, body, port=port).status)

        listeners = [threading.Thread(target=listen, args=(p, w), daemon=True) for p, w in socks.items()]
        writers = [threading.Thread(target=write, args=(p, k)) for p in PORTS for k in range(2)]
        for t in listeners + writers:
            t.start()
        for t in writers:
            t.join()
        wait_until(lambda: all(len(a) >= len(sent) for a in arrivals.values()), 5, step=0.1)
        done.set()
        for t in listeners:
            t.join(1)
        for w in socks.values():
            w.close()
        bad = [s for s in statuses if s != 201]
        r.check(not bad, f"{len(bad)} posts did not return 201 ({sorted(set(bad))})")
        expected = len(sent) * len(PORTS)
        lat = [arrivals[p][b] - t for p in PORTS for b, t in sent.items() if b in arrivals[p]]
        missing = expected - len(lat)
        stats.update(lat=lat)
        r.check(missing <= expected * 0.01, f"{missing} of {expected} expected deliveries missing "
                                            f"(per node: {[len(arrivals[p]) for p in PORTS]} of {len(sent)})")
    with c.rule("D3.2", "median delivery latency (send to receipt) is at most 300 ms") as r:
        lat = stats.get("lat") or []
        r.check(lat, "no deliveries measured (see D3.1)")
        med = statistics.median(lat) * 1000
        r.check(med <= 300, f"median was {med:.0f} ms")
    with c.rule("D3.3", "p95 delivery latency is at most 800 ms") as r:
        lat = sorted(stats.get("lat") or [])
        r.check(lat, "no deliveries measured (see D3.1)")
        p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))] * 1000
        r.check(p95 <= 800, f"p95 was {p95:.0f} ms; {short(sorted(round(x * 1000) for x in lat)[-5:], 60)} are the slowest")


GROUPS = [
    ("D0", d0, ["D0.1", "D0.2", "D0.3"], None),
    ("D1", d1, [f"D1.{i}" for i in range(1, 6)], None),
    ("D2", d2, ["D2.1", "D2.2", "D2.3"], None),
    ("D3", d3, ["D3.1", "D3.2", "D3.3"], None),
]
STEPS = {"1": ("D0", "D1"), "2": ("D2",), "3": ("D3",), "all": tuple(g[0] for g in GROUPS)}

if __name__ == "__main__":
    step, rest = sys.argv[1], sys.argv[2:]
    run([g for g in GROUPS if g[0] in STEPS[step]], rest, f"bash /spec/d/checks/check_d_{step}.sh [{' '.join(STEPS[step])}]")
