"""Lists every WhatsApp group this number is in, with its ID. Run: python list_wa_groups.py
Stop the main bot first (only one program can use the WhatsApp login at a time)."""
import os
import threading

from neonize.client import NewClient
from neonize.events import ConnectedEv

ready = threading.Event()
client = NewClient("wa_session")


@client.event(ConnectedEv)
def on_connected(_, __):
    ready.set()


threading.Thread(target=client.connect, daemon=True).start()
print("Connecting to WhatsApp... if a QR code appears, scan it: WhatsApp > Linked devices > Link a device")
if not ready.wait(180):
    print("Could not connect within 3 minutes.")
    os._exit(1)

lines = []
for g in client.get_joined_groups():
    try:
        name = g.GroupName.Name
        gid = f"{g.JID.User}@g.us"
    except Exception:
        name, gid = "?", repr(g)[:80]
    lines.append(f"{name}  ->  {gid}")

lines.sort(key=str.lower)
text = "\n".join(lines) if lines else "No groups found."
print("\n===== YOUR WHATSAPP GROUPS =====")
print(text)
open("wa_groups.txt", "w", encoding="utf-8").write(text)
print("\nSaved in wa_groups.txt")
os._exit(0)
