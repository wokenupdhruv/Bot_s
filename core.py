"""The brain. Same logic for Telegram and WhatsApp.

msg = {platform:'tg'|'wa', chat_id, chat_type:'private'|'group', sender_id, text, is_admin}
The bot only says what is written in replies.json. Empty text = silent.
"""
import collections
import hashlib
import hmac
import json
import os
import logging
import re
import secrets
import time

import bus
import config
import db
import messages as M
import panel as panel_mod

log = logging.getLogger("core")
panel = panel_mod.get_panel()

CMD_RE = re.compile(r"^\s*#?(\d{3,12})\s+(mark\s*complete[d]?\s*(?:with\s*)?done|re-?\s*partial|speed\s*up|speedup|speed|cancel|refill|partial|fake\s*comp(?:lete)?|fail)\b(.*)$",
                    re.I | re.S)
BULK_MAX = 100
BULK_RE = re.compile(r"^\s*#?(\d{3,12}(?:[\s,;]+#?\d{3,12})+)\s+(mark\s*complete[d]?\s*(?:with\s*)?done|re-?\s*partial|speed\s*up|speedup|speed|cancel|refill|partial|fake\s*comp(?:lete)?)\b(.*)$",
                     re.I | re.S)
PROV_RE = re.compile(r"^\s*#?(\d+)\s+(.+)$", re.S)

ACTIVE = {"pending", "in progress", "processing"}
DONE = {"completed"}
PARTIAL = {"partial"}
FAILED = {"fail", "failed", "error"}
DONE_WORDS = {"done", "ok", "completed", "complete", "fixed", "resolved"}
LOGIN_WORDS = {"login", "/login", "/start", "start", "verify"}
RESET_WORDS = {"reset", "/reset", "unlink", "/unlink", "logout", "/logout"}
PROVIDER_WORDS = {"speed": "speed", "cancel": "cancel", "refill": "refill", "partial": "partial", "repartial": "repartial", "fakecomp": "fake complete"}
RESTART_WORDS = {"restart", "/restart"}
GREETINGS = {"hi", "hii", "hello", "hey", "hlo", "start", "/start", "login", "/login", "verify", "menu"}

_hits = collections.defaultdict(collections.deque)


# ---------- helpers ----------
def providers():
    try:
        with open(config.PROVIDERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log.error("providers file problem: %s", e)
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}


def provider_by_chat(p, c):
    for name, v in providers().items():
        if v.get("platform") == p and str(v.get("chat_id")) == str(c):
            return name
    return None


def provider_cfg(name):
    for k, v in providers().items():
        if k.lower() == (name or "").lower():
            return k, v
    return None, None


def is_staff(msg):
    return f"{msg['platform']}:{msg['sender_id']}" in config.STAFF_TARGETS


def say(msg, key, **kw):
    if msg.get("_collect") is not None:
        msg["_collect"].append((key, kw))
        return
    text = M.get(key, **kw)
    if text:
        bus.send(msg["platform"], msg["chat_id"], text)


def say_limited(msg, key, secs=30, **kw):
    """Same message at most once per `secs` in a chat (so the bot never spams)."""
    if not M.get(key, **kw):
        return
    k = f"n:{msg['platform']}:{msg['chat_id']}:{key}"
    if time.time() - float(db.kv_get(k, 0)) < secs:
        return
    db.kv_set(k, time.time())
    say(msg, key, **kw)


def unhandled(msg, order, reason):
    """Not a failed/manual order: no staff alert. Tell the customer to contact support and log it here."""
    log.warning("NOT PROCESSED order %s (%s): %s", order.get("id"), order.get("provider") or "no provider", reason)
    say(msg, "support_contact", order=order.get("id"))


def userbot_send(chat_id, ext, word, rid):
    """Sends through the Telegram helper account (for providers whose support is a bot)."""
    try:
        import tg_user
        return tg_user.send_request(chat_id, ext, word, rid)
    except Exception:
        log.exception("telegram helper unavailable")
        return False


def hash_otp(username, otp):
    return hmac.new(config.SECRET.encode(), f"{username.lower()}:{otp}".encode(), hashlib.sha256).hexdigest()


def locked(p, c):
    v = str(db.kv_get(f"lock:{p}:{c}", "0"))
    if v == "perm":
        return True
    try:
        return float(v) > time.time()
    except ValueError:
        return False


def staff_alert(order, reason, customer):
    if not config.STAFF_TARGETS:
        log.warning("no STAFF_TARGETS set, alert not sent: %s", order["id"])
        return False
    i = int(db.kv_get("rr", 0))
    target = config.STAFF_TARGETS[i % len(config.STAFF_TARGETS)]
    db.kv_set("rr", i + 1)
    plat, _, chat = target.partition(":")
    text = M.get("staff_alert", order=order["id"], provider=order.get("provider") or "-", reason=reason,
                 amount=f"{order.get('charge')} {order.get('currency', '')}".strip(), customer=customer)
    return bus.send(plat, chat, text)


# ---------- entry ----------
OPEN_MODE = os.getenv("OPEN_MODE", "0") == "1"


def cust_name(link, o):
    return link["username"] or str(o.get("user", "") or "customer")


def get_link(p, c):
    """Open mode: nobody has to sign in, every chat counts as signed in."""
    if OPEN_MODE:
        return {"username": "", "open": True}
    return db.link_get(p, c)


def handle(msg):
    log.info("core got: %s %s group=%s text=%r", msg["platform"], msg["chat_id"], msg["chat_type"] == "group", (msg.get("text") or "")[:40])
    try:
        _handle(msg)
    except Exception:
        log.exception("handle failed")


def _handle(msg):
    p, c = msg["platform"], str(msg["chat_id"])
    text = (msg.get("text") or "").strip()
    if not text:
        return
    group = msg["chat_type"] == "group"
    low = text.lower()
    words = low.split()

    # 1. provider chats: only "#id message" replies, never anything else
    prov = provider_by_chat(p, c)
    if prov:
        log.info('core why: %s is a PROVIDER group (%s) - the bot only reads #id replies there and stays silent', c, prov)
        return provider_reply(prov, text)

    # 2. group controls (/mute /unmute /slow N /free user)
    if low.startswith("/") and words[0].lstrip("/") in ("mute", "unmute", "slow", "free"):
        return control(msg, words)
    if group and db.group_get(p, c)["muted"]:
        log.info("core why: this group is muted")
        return

    # 3. reset (frees the username so someone else can use it)
    if not OPEN_MODE and len(words) == 1 and words[0] in RESET_WORDS:
        return do_reset(msg)

    if OPEN_MODE and len(words) == 1 and words[0] in RESTART_WORDS | RESET_WORDS:
        return
    link = get_link(p, c)
    is_order = CMD_RE.match(text)
    greeting = len(words) == 1 and words[0] in GREETINGS

    # 4. restart: only for chats that are not linked (starts the sign-up again, clears any block)
    if len(words) == 1 and words[0] in RESTART_WORDS:
        if link:
            log.info('core why: restart ignored, this chat is already signed in as %s', link['username'])
            return
        db.kv_set(f"lock:{p}:{c}", "0")
        db.state_del(p, c)
        return welcome(msg)

    # 5. help
    if words[0] in ("help", "/help"):
        return say(msg, "help")

    # 6. chat not linked yet: sign-up flow
    if not link:
        st = db.state_get(p, c)
        trigger = (not group) or bool(is_order) or greeting
        if locked(p, c):
            log.info('core why: this chat is locked after wrong codes')
            if group and not trigger:
                return
            return say_limited(msg, "max_attempts", 60) if group else say(msg, "max_attempts")
        if st:
            if group and str(msg["sender_id"]) != st["starter"]:
                log.info('core why: a sign-up in this group was started by another person (%s); ignoring %s. Send restart to clear it', st['starter'], msg['sender_id'])
                return
            if is_order or greeting:
                if st["step"] == "username":
                    say(msg, "ask_username")
                return
            if words[0] in ("login", "/login") and len(words) > 1:
                text = text.split(None, 1)[1]
            return login_step(msg, st, text)
        if group and not trigger:
            return
        if words[0] in ("login", "/login") and len(words) > 1:
            start_state(msg)
            return login_step(msg, db.state_get(p, c), text.split(None, 1)[1])
        return welcome(msg)

    # 7. linked chat
    bulk = None if is_order else BULK_RE.match(text)
    if bulk:
        return bulk_cmd(msg, bulk)
    if is_order:
        return order_cmd(msg, is_order)
    if group:
        if OPEN_MODE:
            log.info("core why: open mode - not a command in a group, staying silent")
            return
        log.info("core why: group is already signed in as %s and this message is not a command - staying silent", link["username"])
        return say_limited(msg, "invalid_group", 60)
    if greeting:
        return say(msg, "help")
    say_limited(msg, "invalid", 20)


# ---------- group controls ----------
def control(msg, words):
    p, c = msg["platform"], str(msg["chat_id"])
    cmd = words[0].lstrip("/")
    if cmd == "free":                                  # staff only: free a username from anywhere
        if is_staff(msg) and len(words) > 1:
            db.link_del_username(words[1])
            say(msg, "freed", username=words[1])
        return
    if msg["chat_type"] != "group":
        return
    if not (msg.get("is_admin") or is_staff(msg)):
        return
    if cmd == "mute":
        db.group_set(p, c, muted=1)
        say(msg, "muted")
    elif cmd == "unmute":
        db.group_set(p, c, muted=0)
        say(msg, "unmuted")
    elif cmd == "slow" and len(words) > 1 and words[1].isdigit():
        db.group_set(p, c, cooldown=min(int(words[1]), 3600))
        say(msg, "slow_set", seconds=words[1])


# ---------- reset ----------
def do_reset(msg):
    p, c = msg["platform"], str(msg["chat_id"])
    if msg["chat_type"] == "group":
        owner = db.kv_get(f"owner:{p}:{c}")
        if not (msg.get("is_admin") or is_staff(msg) or owner == str(msg["sender_id"])):
            return
    db.state_del(p, c)
    db.link_del(p, c)                                  # username becomes free for anyone else
    say(msg, "reset")


# ---------- login ----------
def start_state(msg):
    p, c = msg["platform"], str(msg["chat_id"])
    db.state_set(p, c, "username", "", "", time.time() + config.OTP_TTL, 0, str(msg["sender_id"]))


def welcome(msg):
    start_state(msg)
    say(msg, "ask_username")


def bad_attempt(msg, st, key):
    p, c = msg["platform"], str(msg["chat_id"])
    n = st["attempts"] + 1
    left = config.MAX_TRIES - n
    if left <= 0:
        db.state_del(p, c)
        db.kv_set(f"lock:{p}:{c}", "perm")             # stays blocked until the customer sends "restart"
        return say(msg, "max_attempts")
    db.state_set(p, c, st["step"], st["username"], st["otp_hash"], st["expires"], n, st["starter"])
    say(msg, key, left_text=f"{left} chance" + ("" if left == 1 else "s"))


def login_step(msg, st, text):
    p, c = msg["platform"], str(msg["chat_id"])
    if time.time() > st["expires"]:
        db.state_del(p, c)
        return welcome(msg)
    if st["step"] == "username":
        uname = text.strip().lstrip("@")
        if not re.fullmatch(r"[\w.\-@]{2,64}", uname) or not panel.user_exists(uname):
            return bad_attempt(msg, st, "wrong_user")
        other = db.link_by_username(uname)
        if other and (other["platform"], other["chat_id"]) != (p, c):
            return say(msg, "username_taken")          # 1 username = 1 person / 1 group (not counted as a wrong try)
        otp = f"{secrets.randbelow(10**6):06d}"
        say(msg, "creating_ticket")
        ok = panel.send_ticket(uname, M.get("ticket_text", code=otp, minutes=config.OTP_TTL // 60))
        if not ok:
            db.state_del(p, c)
            return say(msg, "ticket_failed")
        db.state_set(p, c, "otp", uname, hash_otp(uname, otp), time.time() + config.OTP_TTL, 0, st["starter"])
        return say(msg, "otp_sent", username=uname, ticket_id=("" if ok is True else ok))
    # step == otp
    code = re.sub(r"\D", "", text)
    if len(code) == 6 and hmac.compare_digest(hash_otp(st["username"], code), st["otp_hash"]):
        other = db.link_by_username(st["username"])
        if other and (other["platform"], other["chat_id"]) != (p, c):
            db.state_del(p, c)
            return say(msg, "username_taken")
        db.link_set(p, c, st["username"], msg["chat_type"])
        db.kv_set(f"owner:{p}:{c}", st["starter"])
        db.state_del(p, c)
        say(msg, "linked", username=st["username"], platform_name="WhatsApp" if p == "wa" else "Telegram")
        return say(msg, "help")
    bad_attempt(msg, st, "wrong_otp")


# ---------- order commands ----------
def allowed(msg):
    if msg.get("_collect") is not None:
        return True
    p, c = msg["platform"], str(msg["chat_id"])
    now = time.time()
    if msg["chat_type"] == "group":
        cd = db.group_get(p, c)["cooldown"]
        if now - float(db.kv_get(f"cd:{p}:{c}", 0)) < cd:
            return False
        db.kv_set(f"cd:{p}:{c}", now)
    q = _hits[(p, str(msg["sender_id"]))]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= config.USER_MAX_PER_MIN:
        say_limited(msg, "slow_down", 60)
        return False
    q.append(now)
    return True


REFILL_DAYS_RE = re.compile(r"(\d+)\s*days?\s*refill", re.I)


def service_rules():
    try:
        with open("service_rules.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def service_block(o, act):
    """Returns the reply key if this service does not accept this request, else ''."""
    name = str(o.get("service") or "").lower()
    for rule in service_rules():
        match = str(rule.get("match", "")).lower()
        if match and match in name and act in rule.get("deny", {}):
            return rule["deny"][act]
    if act == "refill":
        if "no refill" in name:
            return "no_refill_service"
        m = REFILL_DAYS_RE.search(name)
        if m:
            start = o.get("updated_ts") or o.get("created_ts") or 0
            if start and time.time() - start > int(m.group(1)) * 86400:
                return "out_of_refill"
    return ""


def bulk_cmd(msg, m):
    p, c = msg["platform"], str(msg["chat_id"])
    if not get_link(p, c):
        return say_limited(msg, "need_login", 30)
    if not allowed(msg):
        return
    ids = []
    for x in re.findall(r"\d{3,12}", m.group(1)):
        if x not in ids:
            ids.append(x)
    if len(ids) > BULK_MAX:
        return say(msg, "bulk_too_many")
    act_word = m.group(2)
    results = collections.OrderedDict()
    for oid in ids:
        sub = dict(msg)
        sub["_collect"] = []
        try:
            order_cmd(sub, re.match(CMD_RE, f"{oid} {act_word}{m.group(3)}"))
        except Exception:
            log.exception("bulk item failed %s", oid)
            sub["_collect"] = [("support_contact", {})]
        for key, kw in sub["_collect"]:
            if key in ("sent", "cancel_requested"):
                k = ("sent", kw.get("action") or "cancel")
            elif key == "not_allowed":
                st = str(kw.get("status", "")).lower()
                k = ("completed", "") if st == "completed" else ("partial", "") if st == "partial" else ("other", kw.get("status", ""))
            elif key == "order_not_found":
                k = ("not_found", "")
            elif key == "duplicate":
                k = ("duplicate", "")
            elif key == "forwarded_staff":
                k = ("staff", "")
            elif key == "partial_staff":
                k = ("partial_staff", "")
            elif key == "support_contact":
                k = ("support", "")
            else:
                k = ("rule", key)
            results.setdefault(k, []).append(oid)
    label = {"speed": "speed up", "speedup": "speed up", "cancel": "cancel", "refill": "refill",
             "partial": "partial", "repartial": "repartial", "re-partial": "repartial"}.get(re.sub(r"\s+", "", act_word.lower()), "fake comp")
    order_of = ["completed", "partial", "sent", "other", "duplicate", "staff", "not_found", "rule", "partial_staff", "support"]
    for kind in order_of:
        for (k, extra), oids in results.items():
            if k != kind:
                continue
            orders = ", ".join(oids)
            if kind == "rule":
                if extra in ("persian_ticket_speed", "persian_ticket_update"):
                    text = M.get(extra, order=orders)
                else:
                    text = M.get("bulk_rule", orders=orders, message=M.get(extra, order=orders))
            elif kind == "partial_staff":
                text = M.get("partial_staff", order=orders)
            else:
                text = M.get("bulk_" + kind, orders=orders, action=extra if kind == "sent" else label, status=extra)
            if text:
                bus.send(p, c, text)


def order_cmd(msg, m):
    p, c = msg["platform"], str(msg["chat_id"])
    link = get_link(p, c)
    if not link:
        return say_limited(msg, "need_login", 30)
    if not allowed(msg):
        return
    oid = m.group(1)
    act = re.sub(r"\s+", "", m.group(2).lower())
    act = {"speedup": "speed", "fakecomp": "fakecomp", "fakecomplete": "fakecomp", "re-partial": "repartial"}.get(act, act)
    if act.startswith("markcomplete"):
        act = "markdone"
    extra = m.group(3).strip()
    o = panel.get_order(oid)
    if not o or (not link.get("open") and str(o["user"]).lower() != link["username"].lower()):
        return say(msg, "order_not_found", order=oid)
    status = str(o["status"]).lower()
    label = {"speed": "speed up", "cancel": "cancel", "refill": "refill", "fakecomp": "fake comp", "fail": "fail", "partial": "partial",
             "repartial": "repartial", "markdone": "mark complete"}[act]

    name, cfg = provider_cfg(o.get("provider"))
    persian = bool(cfg and cfg.get("via") == "userbot")
    if act in ("markdone", "repartial") and not persian:
        return say(msg, "invalid")          # these two commands exist only for the Persian provider
    if persian and act == "speed":
        # the Persian bot does not do speed: the customer is told to use a ticket, nothing is sent to it
        return say(msg, "persian_ticket_speed", order=oid)
    if persian and act in ("fakecomp", "markdone"):
        act = "partial"          # the Persian bot only knows partial, so these go to it as a partial request
    api_cancel = bool(act == "cancel" and cfg and cfg.get("cancel_mode") == "api"
                      and status in ACTIVE | {"error"})

    if act == "fail" or (status in FAILED and not api_cancel):
        reason = extra or o.get("error") or o["status"]
        staff_alert(o, reason, cust_name(link, o))
        if act == "fail":
            return say(msg, "forwarded_staff", order=oid)
        return say(msg, "sent", order=oid, action=label)

    block = service_block(o, "partial" if act == "repartial" else act)
    if block:
        return say(msg, block, order=oid)

    ok = (api_cancel
          or (act in ("speed", "cancel") and status in ACTIVE)
          or (act in ("partial", "repartial") and (status in ACTIVE
                                    or (cfg and cfg.get("via") == "userbot" and status in DONE | PARTIAL)))
          or (act == "refill" and status in DONE | PARTIAL)
          or (act == "fakecomp" and status in DONE))
    if not ok:
        return say(msg, "not_allowed", order=oid, status=("Pending" if status in FAILED else o["status"]), action=label)

    if not (act in ("partial", "repartial") and cfg and cfg.get("via") == "userbot") and db.request_recent(oid, act, config.DUP_WINDOW):
        return say(msg, "duplicate", order=oid, action=label)

    if str(o.get("mode", "")).lower() == "manual":
        # manual-mode orders are handled by our team, not by a provider
        db.request_add(oid, act, p, c, cust_name(link, o), "staff")
        staff_alert(o, f"Manual order - {label}", cust_name(link, o))
        return say(msg, "sent", order=oid, action=label)

    name, cfg = provider_cfg(o.get("provider"))
    if not cfg:
        return unhandled(msg, o, f"provider not in list ({o.get('provider') or 'unknown'}) - {label}")

    rid = db.request_add(oid, act, p, c, cust_name(link, o), name)
    if cfg.get("via") == "userbot":
        ext = str(o.get("provider_order_id") or "").strip()
        if not ext:
            return unhandled(msg, o, f"provider order ID missing - {label}")
        busy = db._q("SELECT id FROM requests WHERE order_id=? AND action=? AND provider=? AND id<? "
                     "AND status IN ('sent','waiting') AND created>? LIMIT 1",
                     (str(oid), act, name, rid, time.time() - 600), one=True)
        if busy:
            db.request_set_status(rid, "duplicate")
            return                      # same request is still waiting for the provider's answer: stay silent
        if userbot_send(cfg.get("chat_id"), ext, PROVIDER_WORDS[act], rid):
            return                      # silent: the customer gets the provider's answer (persian_replies.json)
    if cfg.get("via") == "userbot_post":
        # Telegram provider group, reached through the helper account; no answer is relayed to the customer
        ext = str(o.get("provider_order_id") or "").strip()
        if not ext:
            db.request_set_status(rid, "no_ext")
            return unhandled(msg, o, f"provider order ID missing - {label}")
        try:
            import tg_user
            posted = tg_user.send_plain(cfg.get("chat_id"), f"{ext} {PROVIDER_WORDS[act]}")
        except Exception:
            log.exception("telegram helper unavailable")
            posted = False
        if posted:
            return say(msg, "sent", rid=rid, order=oid, action=label)
        db.request_set_status(rid, "no_chat")
        return unhandled(msg, o, f"telegram helper could not post to {name} - {label}")
    if act == "partial" and cfg.get("partial_mode") == "staff":
        # provider can't be reached automatically: our team updates the partial by hand
        db.request_set_status(rid, "staff")
        return say(msg, "partial_staff", order=oid)
    if act == "cancel" and cfg.get("cancel_mode") == "api":
        # this provider: press the panel's own "Request Cancel" button instead of messaging them
        if panel.request_cancel(oid):
            db.request_set_status(rid, "api_cancel")
            return say(msg, "cancel_requested", order=oid, rid=rid)
        db.request_set_status(rid, "api_failed")
        return unhandled(msg, o, "Request Cancel button failed (API)")
    if not cfg.get("chat_id"):
        db.request_set_status(rid, "no_chat")
        return unhandled(msg, o, f"no provider chat set for {name} - {label}")
    ext = str(o.get("provider_order_id") or "").strip()
    if not ext:
        db.request_set_status(rid, "no_ext")
        return unhandled(msg, o, f"provider order ID missing - {label}")
    db.kv_set(f"ext:{name.lower()}:{ext}", rid)        # lets us match the provider's reply to this request
    text = M.get("provider_request", ext=ext, action=PROVIDER_WORDS[act], order=oid, rid=rid,
                 link=o.get("link", ""), quantity=o.get("quantity", ""), start=o.get("start_count", ""),
                 remains=o.get("remains", ""))
    bus.send(cfg["platform"], cfg["chat_id"], text)
    say(msg, "sent", rid=rid, order=oid, action=label)


# ---------- provider replies ----------
BAD_WORDS = re.compile(r"\b(fail(ed|ure|s)?|error)\b", re.I)
URL_RE = re.compile(r"(https?://\S+|www\.\S+|\b[\w-]+\.(com|net|org|io|ir|co|me|ru|in|info|xyz|site|shop|store|app)\b\S*)", re.I)


def scrub_for_customer(text):
    """Returns text that is safe to show a customer, or '' if it mentions a provider or a failure."""
    t = text or ""
    low = re.sub(r"[^a-z0-9]", "", t.lower())
    for name in providers():
        base = re.sub(r"^(www\.|my\.)", "", name.lower())
        for tok in {base, base.split(".")[0]}:
            tok = re.sub(r"[^a-z0-9]", "", tok)
            if len(tok) >= 4 and tok in low:
                return ""
    if URL_RE.search(t) or BAD_WORDS.search(t):
        return ""
    return t


def provider_reply(prov, text):
    """A provider answers in their chat. We match it to a request by the provider's order ID
    (or by '#request number'), then pass the answer to the customer. Anything else is ignored."""
    req = None
    stripped = text.strip()
    if stripped.startswith("#"):
        m = PROV_RE.match(stripped)
        if m:
            req = db.request_get(int(m.group(1)))
    if req is None:
        for num in re.findall(r"\d{5,}", stripped):
            rid = db.kv_get(f"ext:{prov.lower()}:{num}")
            if rid:
                req = db.request_get(int(rid))
                break
    if not req or req["provider"].lower() != prov.lower():
        return                                         # never answer chatter in provider chats
    body = re.sub(r"^\s*#?\d+\s*", "", stripped).strip() or stripped
    if M.get("provider_reply", order="x", message="x") and body.lower() not in DONE_WORDS and not scrub_for_customer(body):
        log.warning("provider reply hidden from customer (order %s): %s", req["order_id"], body[:200])
        db.request_set_status(req["id"], "replied")
        out = M.get("support_contact", order=req["order_id"])
        if out:
            bus.send(req["platform"], req["chat_id"], out)
        return
    if body.lower() in DONE_WORDS:
        db.request_set_status(req["id"], "done")
        out = M.get("provider_done", order=req["order_id"], action=req["action"])
    else:
        db.request_set_status(req["id"], "replied")
        out = M.get("provider_reply", order=req["order_id"], action=req["action"], message=body)
    if out:
        bus.send(req["platform"], req["chat_id"], out)


# ---------------- PROVIDER_GROUP_FOREIGN_IDS ----------------
# In a provider's group, ids that are not orders on OUR panel (the provider's own ids that staff paste)
# never get a "not found" reply - the bot stays silent.
_orig_handle_pg = _handle


def _handle(msg):
    try:
        text = (msg.get("text") or "").strip()
        p, c = msg["platform"], str(msg["chat_id"])
        if text and provider_by_chat(p, c) and (CMD_RE.match(text) or BULK_RE.match(text)):
            ids = []
            for x in re.findall(r"\d{3,12}", text):
                if x not in ids:
                    ids.append(x)
            if ids and not any(panel.get_order(x) for x in ids[:3]):
                log.info("core why: provider group %s - ids %s are not orders on our panel, staying silent", c, ids[:3])
                return
    except Exception:
        log.exception("provider group check failed")
    return _orig_handle_pg(msg)
