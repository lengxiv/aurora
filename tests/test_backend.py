import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

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


class TorrentUploadTests(unittest.TestCase):
    def test_qbit_add_file_posts_torrent_multipart(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        session.post.return_value.status_code = 200
        qbit._sess = session

        self.assertTrue(qbit.add_file("sample.torrent", b"torrent-data"))
        call = session.post.call_args
        self.assertEqual(call.kwargs["files"]["torrents"][0], "sample.torrent")
        self.assertEqual(call.kwargs["files"]["torrents"][1], b"torrent-data")

    def test_qbit_add_file_rejects_non_torrent(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        qbit._sess = session

        self.assertFalse(qbit.add_file("sample.txt", b"data"))
        session.post.assert_not_called()


class RcloneTests(unittest.TestCase):
    def test_remote_test_reads_root(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"list": []})

        ok, detail, latency = rclone.test_remote("media")

        self.assertTrue(ok)
        self.assertEqual(detail, "根目录读取成功")
        self.assertGreaterEqual(latency, 0)
        call = rclone._req.call_args
        self.assertEqual(call.args[0], "/operations/list")
        self.assertEqual(json.loads(call.kwargs["data"]), {"fs": "media:", "remote": ""})

    def test_remote_test_rejects_invalid_name(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock()

        ok, detail, latency = rclone.test_remote("../media")

        self.assertFalse(ok)
        self.assertEqual(detail, "网盘名称无效")
        self.assertEqual(latency, 0)
        rclone._req.assert_not_called()

    def test_remote_config_does_not_expose_secrets(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={
            "type": "webdav",
            "url": "https://dav.example.com",
            "vendor": "nextcloud",
            "user": "alice",
            "pass": "super-secret",
            "token": "opaque-token",
            "untrusted": "should be ignored",
        })

        ok, config, detail = rclone.get_remote("media")

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(config["params"], {
            "url": "https://dav.example.com",
            "vendor": "nextcloud",
            "user": "alice",
        })
        self.assertEqual(set(config["secretFields"]), {"pass", "token"})
        self.assertNotIn("super-secret", json.dumps(config))
        self.assertNotIn("opaque-token", json.dumps(config))

    def test_remote_update_filters_blank_secrets_and_unknown_fields(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={})

        ok, detail = rclone.update_remote("media", {
            "url": "https://new.example.com",
            "pass": "",
            "token": "new-token",
            "untrusted": "should be ignored",
        })

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        call = rclone._req.call_args
        self.assertEqual(call.args[0], "/config/update")
        payload = json.loads(call.kwargs["data"])
        self.assertEqual(payload["name"], "media")
        self.assertEqual(payload["parameters"], {
            "url": "https://new.example.com",
            "token": "new-token",
        })
        self.assertTrue(payload["opt"]["obscure"])
        self.assertNotIn("pass", payload["parameters"])

    def test_remote_update_rejects_empty_payload(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock()

        ok, detail = rclone.update_remote("media", {"pass": ""})

        self.assertFalse(ok)
        self.assertEqual(detail, "没有需要更新的配置")
        rclone._req.assert_not_called()


if __name__ == "__main__":
    unittest.main()
