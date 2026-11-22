"""Print access events as they happen.

    python print_events.py 192.168.1.201
"""
import sys

from pyzkaccess_native import ZKAccess

with ZKAccess(sys.argv[1]) as dev:
    print("Connected to", dev.info().serial_number, "- waiting for events (Ctrl+C to stop)")
    try:
        for ev in dev.events(interval=0.5):
            status = "GRANTED" if ev.granted else "DENIED" if ev.denied else ""
            print(f"{ev.time}  door {ev.door}  user {ev.user_id:<6} {ev.label:<35} {status}")
    except KeyboardInterrupt:
        pass
