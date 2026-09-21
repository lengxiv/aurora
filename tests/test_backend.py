import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import main
import providers


class AuthTests(unittest.TestCase):
    def test_session_can_be_revoked(self):
        sid = main._new_sid("admin")
        self.assertEqual(main._check_sid(sid), "admin")
        with main._SESSION_LOCK:
            main._SESSIONS.pop(sid, None)
        self.assertIsNone(main._check_sid(sid))

    def test_expired_session_is_rejected(self):
        sid = "expired"
        with main._SESSION_LOCK:
            main._SESSIONS[sid] = {
                "id": "expired-id", "user": "admin", "created": 0,
                "exp": int(time.time()) - 1, "ip": "?", "user_agent": "",
            }
        self.assertIsNone(main._check_sid(sid))

    def test_session_metadata_does_not_expose_token(self):
        sid = main._new_sid("admin", "192.0.2.1", "test browser")
        session = main._SESSIONS[sid]
        self.assertNotIn(sid, session.values())
        self.assertEqual(session["ip"], "192.0.2.1")
        with main._SESSION_LOCK:
            main._SESSIONS.pop(sid, None)


class MediaPathTests(unittest.TestCase):
    def test_path_traversal_is_rejected(self):
        old = os.environ.get("AURORA_LOCAL_MOUNT")
        with tempfile.TemporaryDirectory() as td:
            os.environ["AURORA_LOCAL_MOUNT"] = td
            with self.assertRaises(main.HTTPException):
                main._media_safe(Path("../outside"))
        if old is None:
            os.environ.pop("AURORA_LOCAL_MOUNT", None)
        else:
            os.environ["AURORA_LOCAL_MOUNT"] = old

    def test_trash_and_restore_round_trip(self):
        old_mount = os.environ.get("AURORA_LOCAL_MOUNT")
        old_trash = providers._TRASH_FILE
        with tempfile.TemporaryDirectory() as td:
            os.environ["AURORA_LOCAL_MOUNT"] = td
            providers._TRASH_FILE = str(Path(td) / "trash-meta.json")
            source = Path(td) / "sample.txt"
            source.write_text("test")
            result = main.media_delete(main.media_path_body(path="sample.txt"), "admin")
            self.assertFalse(source.exists())
            self.assertEqual(len(providers.trash_list()), 1)
            main.media_trash_restore(main.media_path_body(path=result["trash_id"]), "admin")
            self.assertEqual(source.read_text(), "test")
            self.assertEqual(providers.trash_list(), [])
        providers._TRASH_FILE = old_trash
        if old_mount is None:
            os.environ.pop("AURORA_LOCAL_MOUNT", None)
        else:
            os.environ["AURORA_LOCAL_MOUNT"] = old_mount


class SettingsTests(unittest.TestCase):
    def test_settings_are_bounded(self):
        old = providers._SETTINGS_FILE
        with tempfile.TemporaryDirectory() as td:
            providers._SETTINGS_FILE = str(Path(td) / "settings.json")
            result = providers.save_settings({
                "alerts": {"diskWarn": 1000},
                "daily": {"time": "99:99"},
                "tg": {"tokens": [{"name": "x" * 100, "token": "t", "chat_id": "c"}] * 5},
            })
            self.assertEqual(result["alerts"]["diskWarn"], 99.0)
            self.assertEqual(result["daily"]["time"], "21:00")
            self.assertEqual(len(result["tg"]["tokens"]), 2)
            self.assertEqual(len(result["tg"]["tokens"][0]["name"]), 20)
        providers._SETTINGS_FILE = old


if __name__ == "__main__":
    unittest.main()
