"""Safe connection check (it only READS, it never changes anything).
Run:  python check_panel.py <username> <order id>
Example: python check_panel.py john 255
"""
import sys
import config
import panel

if len(sys.argv) < 3:
    raise SystemExit("Usage: python check_panel.py <username> <order id>")
p = panel.get_panel()
print("Panel mode:", config.PANEL_MODE)
print("User exists?  ", p.user_exists(sys.argv[1]))
print("Order details:", p.get_order(sys.argv[2]))
