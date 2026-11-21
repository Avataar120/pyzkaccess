"""Back up a device to a dated JSON file and keep the last 30 (run it from cron).

    python nightly_backup.py 192.168.1.201 /var/backups/zkaccess
"""
import sys
from datetime import datetime
from pathlib import Path

from pyzkaccess_native import ZKAccess, backup

host, folder = sys.argv[1], Path(sys.argv[2])
folder.mkdir(parents=True, exist_ok=True)
with ZKAccess(host) as dev:
    dev.set_time()  # also fix the clock drift
    snap = backup.backup(dev)
path = folder / f"zkaccess_{datetime.now():%Y%m%d_%H%M%S}.json"
backup.save(snap, path)
path.chmod(0o600)  # biometric data
for old in sorted(folder.glob("zkaccess_*.json"))[:-30]:
    old.unlink()
print(path, {k: len(v) for k, v in snap["tables"].items()})
