"""Panel adapter. core.py only talks to these 4 functions, so the bot never cares how the panel works.

request_cancel(order_id) -> True/False presses the panel's Request Cancel button.
get_order() must return a dict:
 id, user, status, provider, provider_order_id, charge, currency, link, service,
 quantity, start_count, remains, error      (or None if the order does not exist)
"""
import logging

import requests

import config

log = logging.getLogger("panel")


class MockPanel:
    """Fake panel for testing on your Mac. No internet needed."""

    def __init__(self):
        self.users = {"demo", "alice"}
        self.last_ticket = {}
        base = dict(currency="USD", link="https://instagram.com/demo", service="IG Followers",
                    quantity=1000, start_count=50, remains=0, error="", provider_order_id="")
        self.orders = {
            "1001": dict(base, id="1001", user="demo", status="Completed", provider="ProviderA", charge="2.50", provider_order_id="PA-77"),
            "1002": dict(base, id="1002", user="demo", status="In progress", provider="ProviderB", charge="1.20", remains=600),
            "1003": dict(base, id="1003", user="demo", status="Pending", provider="ProviderC", charge="0.90"),
            "1004": dict(base, id="1004", user="demo", status="Fail", provider="ProviderA", charge="3.75", error="Balance error"),
            "1005": dict(base, id="1005", user="alice", status="Completed", provider="ProviderA", charge="4.00"),
            "1007": dict(base, id="1007", user="demo", status="Pending", provider="ProviderA", charge="1.10"),
            "1006": dict(base, id="1006", user="demo", status="Completed", provider="UnknownProv", charge="1.00"),
        }

    def user_exists(self, username):
        return username.lower() in self.users

    def get_order(self, order_id):
        return self.orders.get(str(order_id))

    def request_cancel(self, order_id):
        print(f"   [MOCK PANEL] Request Cancel pressed for order {order_id}")
        return True

    def send_ticket(self, username, text):
        self.last_ticket[username.lower()] = text
        print(f"   [MOCK TICKET to {username}] {text}")
        return True


class RealPanel:
    """smmpod.com Admin API v2. Header: X-Api-Key. Base: <PANEL_URL>/adminapi/v2"""

    def __init__(self):
        if not config.PANEL_URL or not config.PANEL_ADMIN_KEY:
            raise SystemExit("PANEL_URL and PANEL_ADMIN_KEY must be set in .env when PANEL_MODE=real")
        self.base = config.PANEL_URL + "/adminapi/v2"
        self.s = requests.Session()
        self.s.headers.update({"X-Api-Key": config.PANEL_ADMIN_KEY, "Content-Type": "application/json"})

    def _call(self, method, path, **kw):
        r = self.s.request(method, self.base + path, timeout=20, **kw)
        try:
            body = r.json()
        except ValueError:
            body = {}
        return r.status_code, body

    def user_exists(self, username):
        code, body = self._call("GET", "/users", params={"username": username})
        if code != 200:
            log.error("users lookup failed %s %s", code, body.get("error_message"))
            return False
        return any(str(u.get("username", "")).lower() == username.lower()
                   for u in body.get("data", {}).get("list", []))

    def get_order(self, order_id):
        code, body = self._call("GET", f"/orders/{int(order_id)}")
        if code == 404:
            return None
        if code != 200:
            log.error("order lookup failed %s %s", code, body.get("error_message"))
            return None
        d = body.get("data") or {}
        charge = d.get("charge") or {}
        status = str(d.get("status", "")).replace("_", " ")
        error = ""
        if status in ("fail", "error"):
            error = str(d.get("provider_response") or "")
        return dict(id=str(d.get("id")), user=d.get("user", ""), status=status,
                    provider=d.get("provider") or "", provider_order_id=d.get("external_id") or "",
                    charge=charge.get("value", ""), currency=charge.get("currency_code", ""),
                    link=d.get("link", ""), service=d.get("service_name", ""),
                    quantity=d.get("quantity", ""), start_count=d.get("start_count", ""),
                    remains=d.get("remains", ""), error=error, mode=str(d.get("mode") or ""),
                    created_ts=d.get("created_timestamp") or 0, updated_ts=d.get("last_update_timestamp") or 0)

    def request_cancel(self, order_id):
        code, body = self._call("POST", "/orders/request-cancel", json={"ids": str(int(order_id))})
        if code != 200:
            log.error("request cancel failed %s %s", code, body.get("error_message"))
            return False
        return int(order_id) in _flat((body.get("data") or {}).get("ids"))

    def send_ticket(self, username, text):
        code, body = self._call("POST", "/tickets/add",
                                json={"username": username, "subject": "Support bot verification", "message": text})
        if code != 200:
            log.error("add ticket failed %s %s", code, body.get("error_message"))
            return False
        return (body.get("data") or {}).get("ticket_id") or True


def _flat(x):
    out = []
    for i in x or []:
        out.extend(_flat(i) if isinstance(i, list) else [i])
    return out


def get_panel():
    return MockPanel() if config.PANEL_MODE == "mock" else RealPanel()
