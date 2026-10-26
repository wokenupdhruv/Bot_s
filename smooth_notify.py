import json, os, re, sqlite3, threading, time, random, logging
import bus

_log = logging.getLogger("smooth_notify")
_DIR = os.path.dirname(os.path.abspath(__file__))
_DB = os.path.join(_DIR, "notify.db")
_CFG = os.path.join(_DIR, "notify.json")
_LOCK = threading.Lock()

_DEF = {
    "notify": {"welcome": True, "ticket": True, "funds": True},
    "poll_seconds": 60, "gap_seconds": 25, "max_per_hour": 40, "max_age_minutes": 180,
    "default_cc": "", "skip_methods": ["Bonus", "Fee"], "skip_subjects": ["Support bot verification"],
    "welcome_text": "Hi {name}! 🎉\n\nThanks for joining SmoothSmm. Your account ({username}) is created and ready to use.\n\nIf you ever need help with an order, just message this number with your order ID and what you need, like:\n123456 Refill\n\nHappy growing! 🚀",
    "ticket_text": "📩 We have replied to your ticket #{id} - \"{subject}\".\nLog in to smoothsmm.com and open Tickets to read the answer.",
    "funds_text": "{amount} Funds Added !!!\n\nFrom: {method}\n\n{time}\n\nSmooth Smm",
}
_cc = [0, dict(_DEF)]


def _r2(amount):
    """Round the number in an amount like '$21.06448' to 2 decimals -> '$21.06'."""
    try:
        return re.sub(r"\d[\d,]*(?:\.\d+)?", lambda m: "{:,.2f}".format(float(m.group(0).replace(",", ""))), str(amount), count=1)
    except Exception:
        return amount


def cfg():
    try:
        m = os.path.getmtime(_CFG)
    except OSError:
        m = -1
    if m != _cc[0]:
        d = dict(_DEF)
        try:
            if m >= 0:
                d.update(json.load(open(_CFG, encoding="utf-8")))
        except Exception:
            _log.exception("notify.json is not valid, using defaults")
        _cc[0], _cc[1] = m, d
    return _cc[1]


def _q(sql, args=(), write=False):
    with _LOCK:
        c = sqlite3.connect(_DB, timeout=20)
        c.row_factory = sqlite3.Row
        try:
            cur = c.execute(sql, args)
            rows = cur.fetchall()
            if write:
                c.commit()
            return rows
        finally:
            c.close()


def _tables():
    _q("create table if not exists notified(kind text, key text, ts real, primary key(kind, key))", write=True)
    _q("create table if not exists meta(k text primary key, v text)", write=True)


def meta_get(k, default=0):
    r = _q("select v from meta where k=?", (k,))
    try:
        return float(r[0]["v"]) if r else default
    except Exception:
        return default


def meta_set(k, v):
    _q("insert or replace into meta values(?,?)", (k, str(v)), write=True)


def _notified(kind, key):
    return bool(_q("select 1 from notified where kind=? and key=?", (kind, str(key))))


def _mark(kind, key):
    _q("insert or ignore into notified values(?,?,?)", (kind, str(key), time.time()), write=True)


class _Safe(dict):
    def __missing__(self, k):
        return ""


def _fill(text, **kw):
    return str(text).format_map(_Safe(kw))


def _norm(raw):
    d = re.sub(r"\D", "", str(raw or ""))
    if d.startswith("00"):
        d = d[2:]
    cc = re.sub(r"\D", "", str(cfg().get("default_cc") or ""))
    if len(d) == 10 and cc:
        d = cc + d
    return d if 11 <= len(d) <= 15 else ""


def _find_number(details):
    d = {str(k).lower(): v for k, v in (details or {}).items()}
    for k in ("whatsapp", "whats_app", "whatsapp_number", "phone", "mobile", "number"):
        n = _norm(d.get(k))
        if n:
            return n
    for k, v in d.items():
        if any(w in k for w in ("whatsapp", "phone", "mobile")):
            n = _norm(v)
            if n:
                return n
    return ""


def _list(pn, path, params):
    code, body = pn._call("GET", path, params=params)
    if code != 200:
        raise RuntimeError("%s -> HTTP %s" % (path, code))
    return (body.get("data") or {}).get("list") or []


_numc = {}


def _number_of(pn, username):
    k = str(username or "").lower()
    if not k:
        return ""
    hit = _numc.get(k)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    num = ""
    for u in _list(pn, "/users", {"username": username}):
        if str(u.get("username", "")).lower() == k:
            num = _find_number(u.get("details"))
            break
    _numc[k] = (time.time(), num)
    return num


_outq, _sent = [], []
_qlock = threading.Lock()


def enqueue(number, text, kind):
    with _qlock:
        if len(_outq) >= 300:
            _log.warning("notify queue is full, dropped a %s message", kind)
            return
        _outq.append((number, text, kind))
    _log.info("notify queued: %s -> %s...%s", kind, number[:3], number[-2:])


def _sender():
    nxt = 0.0
    while True:
        try:
            now = time.time()
            _sent[:] = [t for t in _sent if now - t < 3600]
            c = cfg()
            if _outq and now >= nxt and len(_sent) < int(c["max_per_hour"]):
                with _qlock:
                    number, text, kind = _outq.pop(0)
                ok = bus.send("wa", number, text)
                _log.info("notify sent (%s) to %s...%s ok=%s", kind, number[:3], number[-2:], ok)
                _sent.append(now)
                nxt = now + int(c["gap_seconds"]) + random.uniform(0, 15)
        except Exception:
            _log.exception("notify sender")
        time.sleep(2)


def seed_if_needed(pn):
    _tables()
    if meta_get("seeded", 0):
        return
    off, maxid = 0, 0
    while True:
        items = _list(pn, "/users", {"limit": 100, "offset": off})
        if not items:
            break
        off += len(items)
        maxid = max([maxid] + [int(i.get("id") or 0) for i in items])
        if len(items) < 100:
            break
    meta_set("users_off", off)
    meta_set("users_maxid", maxid)
    for x in _list(pn, "/payments", {"limit": 100}):
        if str(x.get("status")).lower() == "completed":
            _mark("pay", x.get("payment_id"))
    for t in _list(pn, "/tickets", {"limit": 100}):
        if str(t.get("status")).lower() == "answered":
            _mark("ticket", "%s:%s" % (t.get("id"), t.get("last_update_timestamp")))
    meta_set("seeded", 1)
    _log.info("notify: first run, %d existing users noted (none will be messaged)", off)


def poll_once(pn):
    c = cfg()
    on = c.get("notify") or {}
    now = time.time()
    max_age = int(c["max_age_minutes"]) * 60
    if on.get("welcome"):
        off, maxid = int(meta_get("users_off", 0)), int(meta_get("users_maxid", 0))
        while True:
            items = _list(pn, "/users", {"limit": 100, "offset": off})
            if not items:
                break
            for u in items:
                uid = int(u.get("id") or 0)
                maxid = max(maxid, uid)
                if uid <= int(meta_get("users_maxid", 0)) or _notified("welcome", uid):
                    continue
                _mark("welcome", uid)
                if now - float(u.get("created_timestamp") or now) > max_age:
                    continue
                d = u.get("details") or {}
                num = _find_number(d)
                if not num:
                    _log.info("notify: new user %s has no usable WhatsApp number, skipped", uid)
                    continue
                enqueue(num, _fill(c["welcome_text"], name=d.get("first_name") or u.get("username"),
                                   username=u.get("username")), "welcome")
            off += len(items)
            if len(items) < 100:
                break
        meta_set("users_off", off)
        meta_set("users_maxid", maxid)
    if on.get("ticket"):
        for t in _list(pn, "/tickets", {"limit": 100}):
            if str(t.get("status")).lower() != "answered":
                continue
            key = "%s:%s" % (t.get("id"), t.get("last_update_timestamp"))
            if _notified("ticket", key):
                continue
            _mark("ticket", key)
            subj = t.get("subject") or ""
            if any(x.lower() in subj.lower() for x in c["skip_subjects"]):
                continue
            if now - float(t.get("last_update_timestamp") or now) > max_age:
                continue
            num = _number_of(pn, (t.get("user") or {}).get("username"))
            if num:
                enqueue(num, _fill(c["ticket_text"], id=t.get("id"), subject=subj[:80]), "ticket")
    if on.get("funds"):
        for x in _list(pn, "/payments", {"limit": 100}):
            if str(x.get("status")).lower() != "completed" or str(x.get("mode")).lower() != "auto":
                continue
            pid = x.get("payment_id")
            if _notified("pay", pid):
                continue
            _mark("pay", pid)
            method = x.get("method") or ""
            if method.lower() in [m.lower() for m in c["skip_methods"]]:
                continue
            if now - float(x.get("created_timestamp") or now) > max_age:
                continue
            num = _number_of(pn, (x.get("user") or {}).get("username"))
            if num:
                a = x.get("amount") or {}
                amount = a.get("formatted") or ("%s %s" % (a.get("value", ""), a.get("currency_code", ""))).strip()
                amount = _r2(amount)
                enqueue(num, _fill(c["funds_text"], method=method, amount=amount, time=x.get("created") or ""), "funds")


def install(g):
    def panel():
        pn = g.get("panel")
        if pn is not None and hasattr(pn, "_call"):
            return pn
        import panel as panel_mod
        return panel_mod.get_panel()

    def run():
        time.sleep(45)
        pn = None
        while True:
            try:
                if any((cfg().get("notify") or {}).values()):
                    pn = pn or panel()
                    seed_if_needed(pn)
                    poll_once(pn)
            except Exception:
                _log.exception("notify poll")
            time.sleep(max(20, int(cfg().get("poll_seconds", 60))))
    threading.Thread(target=run, daemon=True, name="smooth-notify").start()
    threading.Thread(target=_sender, daemon=True, name="smooth-notify-send").start()
    _log.info("customer notifications active (welcome / ticket answered / funds added)")


# ---- v2: each part works on its own (a part the API key cannot read is skipped) ----
_warned = {}


def _warn(part, e):
    if time.time() - _warned.get(part, 0) > 1800:
        _warned[part] = time.time()
        _log.warning("notify: %s skipped - %s (the panel API key may not be allowed to read it)", part, e)


def seed_if_needed(pn):
    _tables()
    if meta_get("seeded", 0):
        for k in ("seed_users", "seed_pay", "seed_ticket"):
            meta_set(k, 1)
    if not meta_get("seed_users", 0):
        off, maxid = 0, 0
        while True:
            items = _list(pn, "/users", {"limit": 100, "offset": off})
            if not items:
                break
            off += len(items)
            maxid = max([maxid] + [int(i.get("id") or 0) for i in items])
            if len(items) < 100:
                break
        meta_set("users_off", off)
        meta_set("users_maxid", maxid)
        meta_set("seed_users", 1)
        _log.info("notify: first run, %d existing users noted (none will be messaged)", off)
    if not meta_get("seed_pay", 0):
        try:
            for x in _list(pn, "/payments", {"limit": 100}):
                if str(x.get("status")).lower() == "completed":
                    _mark("pay", x.get("payment_id"))
            meta_set("seed_pay", 1)
        except Exception as e:
            _warn("funds-added messages", e)
    if not meta_get("seed_ticket", 0):
        try:
            for t in _list(pn, "/tickets", {"limit": 100}):
                if str(t.get("status")).lower() == "answered":
                    _mark("ticket", "%s:%s" % (t.get("id"), t.get("last_update_timestamp")))
            meta_set("seed_ticket", 1)
        except Exception as e:
            _warn("ticket-reply messages", e)


def _poll_welcome(pn, c, now, max_age):
    off, maxid = int(meta_get("users_off", 0)), int(meta_get("users_maxid", 0))
    base = maxid
    while True:
        items = _list(pn, "/users", {"limit": 100, "offset": off})
        if not items:
            break
        for u in items:
            uid = int(u.get("id") or 0)
            maxid = max(maxid, uid)
            if uid <= base or _notified("welcome", uid):
                continue
            _mark("welcome", uid)
            if now - float(u.get("created_timestamp") or now) > max_age:
                continue
            d = u.get("details") or {}
            num = _find_number(d)
            if not num:
                _log.info("notify: new user %s has no usable WhatsApp number, skipped", uid)
                continue
            enqueue(num, _fill(c["welcome_text"], name=d.get("first_name") or u.get("username"),
                               username=u.get("username")), "welcome")
        off += len(items)
        if len(items) < 100:
            break
    meta_set("users_off", off)
    meta_set("users_maxid", maxid)


def _poll_tickets(pn, c, now, max_age):
    for t in _list(pn, "/tickets", {"limit": 100}):
        if str(t.get("status")).lower() != "answered":
            continue
        key = "%s:%s" % (t.get("id"), t.get("last_update_timestamp"))
        if _notified("ticket", key):
            continue
        _mark("ticket", key)
        subj = t.get("subject") or ""
        if any(x.lower() in subj.lower() for x in c["skip_subjects"]):
            continue
        if now - float(t.get("last_update_timestamp") or now) > max_age:
            continue
        num = _number_of(pn, (t.get("user") or {}).get("username"))
        if num:
            enqueue(num, _fill(c["ticket_text"], id=t.get("id"), subject=subj[:80]), "ticket")


def _poll_funds(pn, c, now, max_age):
    for x in _list(pn, "/payments", {"limit": 100}):
        if str(x.get("status")).lower() != "completed" or str(x.get("mode")).lower() != "auto":
            continue
        pid = x.get("payment_id")
        if _notified("pay", pid):
            continue
        _mark("pay", pid)
        method = x.get("method") or ""
        if method.lower() in [m.lower() for m in c["skip_methods"]]:
            continue
        if now - float(x.get("created_timestamp") or now) > max_age:
            continue
        num = _number_of(pn, (x.get("user") or {}).get("username"))
        if num:
            a = x.get("amount") or {}
            amount = a.get("formatted") or ("%s %s" % (a.get("value", ""), a.get("currency_code", ""))).strip()
            amount = _r2(amount)
            enqueue(num, _fill(c["funds_text"], method=method, amount=amount, time=x.get("created") or ""), "funds")


def poll_once(pn):
    c = cfg()
    on = c.get("notify") or {}
    now = time.time()
    max_age = int(c["max_age_minutes"]) * 60
    for flag, seed, name, fn in (("welcome", "seed_users", "welcome messages", _poll_welcome),
                                 ("ticket", "seed_ticket", "ticket-reply messages", _poll_tickets),
                                 ("funds", "seed_pay", "funds-added messages", _poll_funds)):
        if not on.get(flag) or not meta_get(seed, 0):
            continue
        try:
            fn(pn, c, now, max_age)
        except Exception as e:
            _warn(name, e)


# ---- order-completed message (one per order, combined when many at once) ----
_DONE = {
    "done_on": True, "done_single_max": 3, "done_list_max": 5, "done_per_day": 8, "done_pages": 10,
    "done_users_per_cycle": 25, "done_queue_limit": 25,
    "done_text": "✅ Order completed!\n\nOrder ID: {id}\nStatus: Completed\n\nThank you for choosing SmoothSmm. Place your next order any time at smoothsmm.com 💛",
    "done_text_many": "✅ {n} orders completed!\n\nOrder IDs: {ids}\n\nThank you for choosing SmoothSmm. Place your next order any time at smoothsmm.com 💛",
}


def _dc():
    c = dict(_DONE)
    c.update({k: v for k, v in cfg().items() if k in _DONE})
    return c


def _its(o):
    for k in ("created_timestamp", "created_ts"):
        try:
            v = float(o.get(k) or 0)
            if v:
                return v
        except Exception:
            pass
    return 0.0


def _opage(pn, off):
    code, body = pn._call("GET", "/orders", params={"limit": 100, "offset": off})
    if code != 200:
        raise RuntimeError("/orders -> HTTP %s" % code)
    d = (body or {}).get("data") or {}
    try:
        cnt = int(d.get("count") or 0)
    except Exception:
        cnt = 0
    return (d.get("list") or []), cnt


def _recent_orders(pn, pages):
    items, count = _opage(pn, 0)
    if not items:
        return []
    ts = [_its(i) for i in items if _its(i)]
    asc = len(ts) >= 2 and ts[0] < ts[-1]
    out = []
    if not asc:
        out += items
        off = 100
        for _ in range(pages - 1):
            if len(items) < 100:
                break
            items, _n = _opage(pn, off)
            off += 100
            if not items:
                break
            out += items
    else:
        off = max(0, count - 100)
        for _ in range(pages):
            lst, _n = _opage(pn, off)
            out += lst
            if off == 0:
                break
            off = max(0, off - 100)
    return out


def _uname(it):
    u = it.get("user")
    if isinstance(u, dict):
        return str(u.get("username") or "").strip()
    return str(u or it.get("username") or "").strip()


def _poll_done(pn):
    c = _dc()
    _q("create table if not exists done_seen(order_id text primary key, ts real)", write=True)
    _q("create table if not exists done_log(username text, ts real)", write=True)
    seeded = bool(meta_get("done_seeded", 0))
    by_user = {}
    orders = _recent_orders(pn, int(c["done_pages"]))
    for it in orders:
        oid = str(it.get("id") or it.get("order_id") or "")
        if not oid or str(it.get("status") or "").replace("_", " ").strip().lower() != "completed":
            continue
        if _q("select 1 from done_seen where order_id=?", (oid,)):
            continue
        if not seeded:
            _q("insert or ignore into done_seen values(?,?)", (oid, time.time()), write=True)
        elif _uname(it):
            by_user.setdefault(_uname(it), []).append(oid)
    if not seeded:
        meta_set("done_seeded", 1)
        _log.info("notify: order-completed first run, %d recent orders noted (none will be messaged)", len(orders))
        return
    _q("delete from done_seen where ts < ?", (time.time() - 30 * 86400,), write=True)
    n_users = 0
    for un, ids in by_user.items():
        if not c["done_on"]:
            for o in ids:
                _q("insert or ignore into done_seen values(?,?)", (o, time.time()), write=True)
            continue
        if n_users >= int(c["done_users_per_cycle"]) or len(_outq) >= int(c["done_queue_limit"]):
            break
        n_users += 1
        for o in ids:
            _q("insert or ignore into done_seen values(?,?)", (o, time.time()), write=True)
        today = _q("select count(*) n from done_log where username=? and ts>?", (un.lower(), time.time() - 86400))[0]["n"]
        num = _number_of(pn, un)
        if not num:
            continue
        if len(ids) <= int(c["done_single_max"]):
            texts = [_fill(c["done_text"], id=o) for o in ids]
        else:
            mx = int(c["done_list_max"])
            shown = ", ".join(ids[:mx]) + (" and %d more" % (len(ids) - mx) if len(ids) > mx else "")
            texts = [_fill(c["done_text_many"], n=len(ids), ids=shown)]
        for t in texts:
            if today >= int(c["done_per_day"]):
                break
            enqueue(num, t, "done")
            _q("insert into done_log values(?,?)", (un.lower(), time.time()), write=True)
            today += 1


_poll_once_v2 = poll_once


def poll_once(pn):
    _poll_once_v2(pn)
    try:
        if _dc()["done_on"] or not meta_get("done_seeded", 0):
            _poll_done(pn)
    except Exception as e:
        _warn("order-completed messages", e)


# ---- order-status v3: Completed / Canceled / Partial messages in the new wording ----
_DONE = {
    "done_on": True, "done_single_max": 3, "done_list_max": 5, "done_per_day": 8, "done_pages": 10,
    "done_users_per_cycle": 25, "done_queue_limit": 25,
    "done_text": "Order {action}\n\nID: {id}",
    "done_text_many": "{n} Orders {action}\n\nIDs: {ids}",
}
_ACTIONS = {"completed": "Completed", "canceled": "Canceled", "cancelled": "Canceled", "partial": "Partial"}


def _poll_done(pn):
    c = _dc()
    _q("create table if not exists done_seen(order_id text primary key, ts real)", write=True)
    _q("create table if not exists done_log(username text, ts real)", write=True)
    seeded = bool(meta_get("done_seeded3", 0))
    by = {}
    orders = _recent_orders(pn, int(c["done_pages"]))
    for it in orders:
        oid = str(it.get("id") or it.get("order_id") or "")
        act = _ACTIONS.get(str(it.get("status") or "").replace("_", " ").strip().lower())
        if not oid or not act:
            continue
        if _q("select 1 from done_seen where order_id=?", (oid,)):
            continue
        if not seeded:
            _q("insert or ignore into done_seen values(?,?)", (oid, time.time()), write=True)
        elif _uname(it):
            by.setdefault((_uname(it), act), []).append(oid)
    if not seeded:
        meta_set("done_seeded3", 1)
        _log.info("notify: order-status first run, %d recent orders noted (none will be messaged)", len(orders))
        return
    _q("delete from done_seen where ts < ?", (time.time() - 30 * 86400,), write=True)
    n_users = 0
    for (un, act), ids in by.items():
        if not c["done_on"]:
            for o in ids:
                _q("insert or ignore into done_seen values(?,?)", (o, time.time()), write=True)
            continue
        if n_users >= int(c["done_users_per_cycle"]) or len(_outq) >= int(c["done_queue_limit"]):
            break
        n_users += 1
        for o in ids:
            _q("insert or ignore into done_seen values(?,?)", (o, time.time()), write=True)
        today = _q("select count(*) n from done_log where username=? and ts>?", (un.lower(), time.time() - 86400))[0]["n"]
        num = _number_of(pn, un)
        if not num:
            continue
        if len(ids) <= int(c["done_single_max"]):
            texts = [_fill(c["done_text"], id=o, action=act) for o in ids]
        else:
            mx = int(c["done_list_max"])
            shown = ", ".join(ids[:mx]) + (" and %d more" % (len(ids) - mx) if len(ids) > mx else "")
            texts = [_fill(c["done_text_many"], n=len(ids), ids=shown, action=act)]
        for t in texts:
            if today >= int(c["done_per_day"]):
                break
            enqueue(num, t, "done")
            _q("insert into done_log values(?,?)", (un.lower(), time.time()), write=True)
            today += 1
