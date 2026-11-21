"""Copy users, fingerprint templates and door rights from one device to another.

    python copy_users.py SOURCE_IP TARGET_IP
"""
import sys

from pyzkaccess_native import ZKAccess

src_ip, dst_ip = sys.argv[1:3]
with ZKAccess(src_ip) as src, ZKAccess(dst_ip) as dst:
    templates = src.get_templates()
    for user in src.get_users():
        user.uid = None  # assigned by the target device
        dst.save_user(user, merge=False)
        for tpl in (t for t in templates if t.pin == user.pin):
            tpl.uid = None
            dst.save_template(tpl)
        for auth in src.get_authorizations(user.pin):
            dst.grant(auth.pin, auth.timezone_id, auth.door_mask)
        print("copied", user.pin, user.name)
