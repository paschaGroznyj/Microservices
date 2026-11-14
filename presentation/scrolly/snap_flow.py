"""Снимок LIST flow:{chat_id} из Redis -> data/flow_<chat>.json (для главы 3, шаг 2).
Без зависимостей: голый RESP. python snap_flow.py 425826 [redis_host]"""
import json, socket, sys, time, pathlib

def redis(host, *a):
    s = socket.create_connection((host, 6379), timeout=3)
    s.sendall((f"*{len(a)}\r\n" + "".join(f"${len(x.encode())}\r\n{x}\r\n" for x in a)).encode())
    buf = b""; s.settimeout(0.8)
    try:
        while True:
            c = s.recv(65536)
            if not c: break
            buf += c
    except OSError:
        pass
    s.close()
    return buf.decode("utf-8", "replace")

def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else "425826"
    host = sys.argv[2] if len(sys.argv) > 2 else "redis"
    key = f"flow:{cid}"
    raw = redis(host, "LRANGE", key, "0", "-1")
    ev = [json.loads(l) for l in raw.split("\r\n") if l.startswith("{")]
    if not ev:
        sys.exit(f"{key}: пусто (TTL истёк?)")
    rid = ev[-1].get("rid")                      # последний запрос в списке
    ev = [e for e in ev if e.get("rid") == rid]
    ttl = int(redis(host, "TTL", key).strip().lstrip(":") or -1)
    out = {"key": key, "rid": rid, "ttl": ttl, "taken_ms": int(time.time() * 1000), "events": ev}
    p = pathlib.Path(__file__).parent / "data" / f"flow_{cid}.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    span = (ev[-1]["ts"] - ev[0]["ts"]) / 1000
    print(f"{p.name}: {len(ev)} событий, rid={rid[:8]}, {span:.1f} с, TTL {ttl}")

if __name__ == "__main__":
    main()
