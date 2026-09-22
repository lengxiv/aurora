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

    def test_torrent_save_path_is_scoped_to_download_mount(self):
        old_mount = os.environ.get("AURORA_LOCAL_MOUNT")
        with tempfile.TemporaryDirectory() as td:
            os.environ["AURORA_LOCAL_MOUNT"] = td
            Path(td, "movies").mkdir()
            self.assertEqual(main._torrent_save_path("movies"), "/downloads/movies")
            self.assertEqual(main._torrent_save_path(""), "/downloads")
            with self.assertRaises(main.HTTPException):
                main._torrent_save_path("../outside")
            with self.assertRaises(main.HTTPException):
                main._torrent_save_path("missing")
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
    def test_qbit_add_posts_savepath(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        session.post.return_value.status_code = 200
        qbit._sess = session

        self.assertTrue(qbit.add("magnet:?xt=urn:btih:abcdef", "/downloads/movies"))
        self.assertEqual(session.post.call_args.kwargs["data"], {
            "urls": "magnet:?xt=urn:btih:abcdef",
            "savepath": "/downloads/movies",
        })

    def test_qbit_add_posts_remote_destination_tag(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        session.post.return_value.status_code = 200
        qbit._sess = session

        self.assertTrue(qbit.add("magnet:?xt=urn:btih:abcdef", "/downloads", "aurora-remote-test"))
        self.assertEqual(session.post.call_args.kwargs["data"]["tags"], "aurora-remote-test")

    def test_qbit_add_file_posts_torrent_multipart(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        session.post.return_value.status_code = 200
        qbit._sess = session

        self.assertTrue(qbit.add_file("sample.torrent", b"torrent-data", "/downloads/movies", "aurora-remote-test"))
        call = session.post.call_args
        self.assertEqual(call.kwargs["files"]["torrents"][0], "sample.torrent")
        self.assertEqual(call.kwargs["files"]["torrents"][1], b"torrent-data")
        self.assertEqual(call.kwargs["data"], {
            "savepath": "/downloads/movies", "tags": "aurora-remote-test",
        })

    def test_qbit_add_file_rejects_non_torrent(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        qbit._sess = session

        self.assertFalse(qbit.add_file("sample.txt", b"data"))
        session.post.assert_not_called()


class TorrentDestinationTests(unittest.TestCase):
    def test_completed_torrent_starts_and_finishes_remote_upload(self):
        old_dest_file = providers._TORRENT_DEST_FILE
        old_mount = os.environ.get("AURORA_LOCAL_MOUNT")
        old_qbit = providers._qbit
        old_rclone = providers._rclone

        class FakeQbit:
            def available(self):
                return True

            def torrent_details(self):
                return [{
                    "hash": "abc", "name": "release", "tags": "aurora-remote-test",
                    "progress": 1.0, "content_path": "/downloads/release",
                }]

        class FakeRclone:
            def __init__(self):
                self.jobs = []

            def available(self):
                return True

            def transfers(self):
                return self.jobs

            def upload_local(self, name, local_path, destination):
                self.jobs = [{"id": "job-1", "status": "running", "detail": ""}]
                self.upload = (name, local_path, destination)
                return True, {"id": "job-1"}, ""

        with tempfile.TemporaryDirectory() as td:
            try:
                providers._TORRENT_DEST_FILE = str(Path(td) / "destinations.json")
                os.environ["AURORA_LOCAL_MOUNT"] = td
                source = Path(td) / "release"
                source.mkdir()
                (source / "movie.mkv").write_bytes(b"data")
                Path(providers._TORRENT_DEST_FILE).write_text(json.dumps({
                    "aurora-remote-test": {
                        "remote": "media", "path": "movies", "status": "waiting",
                        "detail": "", "hash": "", "name": "", "local_path": "",
                        "transfer_id": "", "created": 1, "finished": 0,
                    },
                }))
                fake_qbit = FakeQbit()
                fake_rclone = FakeRclone()
                providers._qbit = fake_qbit
                providers._rclone = fake_rclone

                providers._process_torrent_destinations()
                state = json.loads(Path(providers._TORRENT_DEST_FILE).read_text())["aurora-remote-test"]
                self.assertEqual(state["status"], "uploading")
                self.assertEqual(fake_rclone.upload[2], "movies/release")

                fake_rclone.jobs = [{"id": "job-1", "status": "done", "detail": ""}]
                providers._process_torrent_destinations()
                state = json.loads(Path(providers._TORRENT_DEST_FILE).read_text())["aurora-remote-test"]
                self.assertEqual(state["status"], "done")
            finally:
                providers._TORRENT_DEST_FILE = old_dest_file
                providers._qbit = old_qbit
                providers._rclone = old_rclone
                if old_mount is None:
                    os.environ.pop("AURORA_LOCAL_MOUNT", None)
                else:
                    os.environ["AURORA_LOCAL_MOUNT"] = old_mount


class TorrentStateTests(unittest.TestCase):
    def test_stalled_download_is_distinguished_from_active_download(self):
        self.assertEqual(providers._QBIT_STATES["stalledDL"], "stalled")


class RcloneTests(unittest.TestCase):
    def test_s3_provider_is_canonicalized_for_rclone(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={})

        old_buckets_file = providers._RCLONE_BUCKETS_FILE
        with tempfile.TemporaryDirectory() as td:
            providers._RCLONE_BUCKETS_FILE = str(Path(td) / "buckets.json")
            try:
                ok, detail = rclone.create_remote("r2", "s3", {
                    "provider": "cloudflare",
                    "endpoint": "https://account.r2.cloudflarestorage.com/",
                })
            finally:
                providers._RCLONE_BUCKETS_FILE = old_buckets_file

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        payload = json.loads(rclone._req.call_args.kwargs["data"])
        self.assertEqual(payload["parameters"]["provider"], "Cloudflare")
        self.assertEqual(payload["parameters"]["endpoint"], "https://account.r2.cloudflarestorage.com")

    def test_cloudflare_endpoint_rejects_bucket_path(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock()

        ok, detail = rclone.create_remote("r2", "s3", {
            "provider": "cloudflare",
            "endpoint": "https://account.r2.cloudflarestorage.com/lengxi",
        })

        self.assertFalse(ok)
        self.assertIn("不能包含 bucket 路径", detail)
        rclone._req.assert_not_called()

    def test_s3_upload_at_root_returns_bucket_hint(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"type": "s3"})
        with tempfile.TemporaryDirectory() as td:
            stage = Path(td) / "sample.bin"
            stage.write_bytes(b"data")
            old_data_dir = providers._DATA_DIR
            providers._DATA_DIR = td
            try:
                ok, job, detail = rclone.upload_file("r2", str(stage), "sample.bin", "", 4)
            finally:
                providers._DATA_DIR = old_data_dir

        self.assertFalse(ok)
        self.assertIsNone(job)
        self.assertEqual(detail, "S3/R2 上传请先进入 bucket 目录后再上传")
        rclone._req.assert_called_once_with(
            "/config/get", timeout=5.0, method="POST", data="name=r2",
        )

    def test_fixed_s3_bucket_is_used_as_remote_root(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"list": [{"Name": "clip.mp4", "IsDir": False, "Size": 12}]})
        old_buckets_file = providers._RCLONE_BUCKETS_FILE
        with tempfile.TemporaryDirectory() as td:
            providers._RCLONE_BUCKETS_FILE = str(Path(td) / "buckets.json")
            providers._set_rclone_bucket("r2", "lengxi")
            try:
                ok, entries, detail = rclone.list_files("r2", "")
            finally:
                providers._RCLONE_BUCKETS_FILE = old_buckets_file

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(entries[0]["path"], "clip.mp4")
        payload = json.loads(rclone._req.call_args.kwargs["data"])
        self.assertEqual(payload["fs"], "r2:lengxi")
        self.assertEqual(payload["remote"], "")

    def test_upload_uses_fixed_s3_bucket_without_manual_path(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"jobid": 45})
        old_buckets_file = providers._RCLONE_BUCKETS_FILE
        with tempfile.TemporaryDirectory() as td:
            providers._RCLONE_BUCKETS_FILE = str(Path(td) / "buckets.json")
            providers._set_rclone_bucket("r2", "lengxi")
            stage = Path(td) / "sample.bin"
            stage.write_bytes(b"data")
            old_data_dir = providers._DATA_DIR
            providers._DATA_DIR = td
            try:
                ok, job, detail = rclone.upload_file("r2", str(stage), "sample.bin", "", 4)
            finally:
                providers._DATA_DIR = old_data_dir
                providers._RCLONE_BUCKETS_FILE = old_buckets_file

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertIsNotNone(job)
        payload = json.loads(rclone._req.call_args.kwargs["data"])
        self.assertEqual(payload["dstFs"], "r2:lengxi")
        self.assertEqual(payload["dstRemote"], "sample.bin")

    def test_s3_bucket_config_is_stored_outside_rclone_parameters(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={})
        old_buckets_file = providers._RCLONE_BUCKETS_FILE
        with tempfile.TemporaryDirectory() as td:
            providers._RCLONE_BUCKETS_FILE = str(Path(td) / "buckets.json")
            try:
                ok, detail = rclone.create_remote("r2", "s3", {
                    "provider": "cloudflare",
                    "endpoint": "https://account.r2.cloudflarestorage.com",
                    "bucket": "lengxi",
                })
                saved_bucket = providers._rclone_bucket("r2")
            finally:
                providers._RCLONE_BUCKETS_FILE = old_buckets_file

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(saved_bucket, "lengxi")
        payload = json.loads(rclone._req.call_args.kwargs["data"])
        self.assertNotIn("bucket", payload["parameters"])

    def test_s3_write_permission_error_is_actionable(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(side_effect=RuntimeError("HTTP Error 500: Internal Server Error"))
        old_buckets_file = providers._RCLONE_BUCKETS_FILE
        with tempfile.TemporaryDirectory() as td:
            providers._RCLONE_BUCKETS_FILE = str(Path(td) / "buckets.json")
            providers._set_rclone_bucket("r2", "lengxi")
            try:
                ok, detail = rclone.mkdir_remote("r2", "folder")
            finally:
                providers._RCLONE_BUCKETS_FILE = old_buckets_file

        self.assertFalse(ok)
        self.assertEqual(detail, "R2 写入被拒绝，请为该 bucket 的 API Token 授予 Object Read & Write 权限")

    def test_remote_file_listing_maps_entries_and_rejects_traversal(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"list": [
            {"Name": "folder", "IsDir": True, "Size": -1},
            {"Path": "clip.mp4", "IsDir": False, "Size": 2048, "ModTime": "2026-09-23T01:02:03Z"},
        ]})

        ok, entries, detail = rclone.list_files("media", "movies")

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(entries[0]["path"], "movies/folder")
        self.assertTrue(entries[0]["isDir"])
        self.assertEqual(entries[1]["path"], "movies/clip.mp4")
        self.assertEqual(entries[1]["size"], 2048)
        ok, entries, detail = rclone.list_files("media", "../outside")
        self.assertFalse(ok)
        self.assertEqual(entries, [])
        self.assertEqual(detail, "网盘路径无效")

    def test_s3_root_permission_error_is_actionable(self):
        rclone = providers.RcloneProvider()

        def request(path, **_kwargs):
            if path == "/operations/list":
                raise RuntimeError("HTTP Error 500: Internal Server Error")
            return {"type": "s3"}

        rclone._req = Mock(side_effect=request)

        ok, entries, detail = rclone.list_files("r2", "")

        self.assertFalse(ok)
        self.assertEqual(entries, [])
        self.assertEqual(detail, "S3/R2 根目录没有 bucket 列表权限，请在路径框中输入 bucket 名称")

    def test_remote_copy_uses_async_rclone_job(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"jobid": 42})

        ok, job, detail = rclone.copy_remote("media", "movies/clip.mp4", "archive", "backup", False, "copy")

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertIsNotNone(job)
        self.assertEqual(job["action"], "copy")
        call = rclone._req.call_args
        self.assertEqual(call.args[0], "/operations/copyfile")
        payload = json.loads(call.kwargs["data"])
        self.assertEqual(payload["srcFs"], "media:")
        self.assertEqual(payload["srcRemote"], "movies/clip.mp4")
        self.assertEqual(payload["dstFs"], "archive:")
        self.assertEqual(payload["dstRemote"], "backup/clip.mp4")
        self.assertTrue(payload["_async"])

    def test_local_torrent_upload_uses_async_rclone_copy(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"jobid": 44})
        old_mount = os.environ.get("AURORA_LOCAL_MOUNT")
        with tempfile.TemporaryDirectory() as td:
            os.environ["AURORA_LOCAL_MOUNT"] = td
            source = Path(td) / "release"
            source.mkdir()
            (source / "movie.mkv").write_bytes(b"data")
            try:
                ok, job, detail = rclone.upload_local("media", str(source), "movies/release")
            finally:
                if old_mount is None:
                    os.environ.pop("AURORA_LOCAL_MOUNT", None)
                else:
                    os.environ["AURORA_LOCAL_MOUNT"] = old_mount

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertIsNotNone(job)
        call = rclone._req.call_args
        self.assertEqual(call.args[0], "/sync/copy")
        payload = json.loads(call.kwargs["data"])
        self.assertEqual(payload["srcFs"], str(source))
        self.assertEqual(payload["srcRemote"], "")
        self.assertEqual(payload["dstFs"], "media:")
        self.assertEqual(payload["dstRemote"], "movies/release")
        self.assertTrue(payload["_async"])

    def test_remote_directory_copy_keeps_directory_name(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"jobid": 43})

        ok, job, detail = rclone.copy_remote("media", "movies/season", "archive", "backup", True, "copy")

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertIsNotNone(job)
        call = rclone._req.call_args
        self.assertEqual(call.args[0], "/sync/copy")
        payload = json.loads(call.kwargs["data"])
        self.assertEqual(payload["srcFs"], "media:movies/season")
        self.assertEqual(payload["dstFs"], "archive:backup/season")
        self.assertEqual(payload["dstRemote"], "")
        self.assertTrue(payload["_async"])

    def test_remote_download_rejects_local_path_traversal(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock()

        ok, job, detail = rclone.download_remote("media", "movies/clip.mp4", "../outside", False)

        self.assertFalse(ok)
        self.assertIsNone(job)
        self.assertEqual(detail, "网盘路径无效")
        rclone._req.assert_not_called()

    def test_failed_remote_transfer_can_be_retried(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"jobid": 42})

        ok, job, detail = rclone.copy_remote("media", "clip.mp4", "archive", "backup", False, "copy")

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        internal = rclone._transfer_jobs[job["id"]]
        internal["status"] = "error"
        internal["detail"] = "temporary failure"
        rclone._req.reset_mock()
        rclone._req.return_value = {"jobid": 43}

        ok, retry, detail = rclone.retry_transfer(job["id"])

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(retry["status"], "running")
        self.assertEqual(rclone._req.call_args.args[0], "/operations/copyfile")
        payload = json.loads(rclone._req.call_args.kwargs["data"])
        self.assertEqual(payload["srcRemote"], "clip.mp4")
        self.assertTrue(payload["_async"])

    def test_upload_transfer_is_not_retryable_after_staging_cleanup(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"jobid": 44})
        with tempfile.TemporaryDirectory() as td:
            stage = Path(td) / "sample.bin"
            stage.write_bytes(b"data")
            old_data_dir = providers._DATA_DIR
            providers._DATA_DIR = td
            try:
                ok, job, detail = rclone.upload_file("media", str(stage), "sample.bin", "", 4)
                self.assertTrue(ok)
                self.assertEqual(detail, "")
                internal = rclone._transfer_jobs[job["id"]]
                self.assertFalse(internal["retryable"])
                internal["status"] = "error"
                ok, retry, detail = rclone.retry_transfer(job["id"])
                self.assertFalse(ok)
                self.assertIsNone(retry)
                self.assertEqual(detail, "该任务无法重试")
            finally:
                providers._DATA_DIR = old_data_dir

    def test_remote_file_operations_reject_unsafe_paths_without_request(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock()

        ok, entries, detail = rclone.list_files("media", "folder/../secret")

        self.assertFalse(ok)
        self.assertEqual(entries, [])
        self.assertEqual(detail, "网盘路径无效")
        rclone._req.assert_not_called()

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
            "client_secret": "   ",
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

    def test_remote_rename_recreates_complete_config_and_removes_old_name(self):
        rclone = providers.RcloneProvider()

        def request(path, **kwargs):
            if path == "/config/listremotes":
                return {"remotes": ["old:"]}
            if path == "/config/get":
                payload = kwargs.get("data", "")
                return {"type": "webdav", "url": "https://dav.example", "user": "alice",
                        "pass": "obscured-secret", "custom_option": "preserve"} if "old" in payload else {"type": "webdav"}
            return {}

        rclone._req = Mock(side_effect=request)

        ok, detail = rclone.rename_remote("old", "renamed")

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        calls = rclone._req.call_args_list
        create = next(call for call in calls if call.args[0] == "/config/create")
        payload = json.loads(create.kwargs["data"])
        self.assertEqual(payload["name"], "renamed")
        self.assertEqual(payload["type"], "webdav")
        self.assertEqual(payload["parameters"]["pass"], "obscured-secret")
        self.assertEqual(payload["parameters"]["custom_option"], "preserve")
        self.assertTrue(payload["opt"]["noObscure"])
        delete_names = [
            call.kwargs.get("data", "") for call in calls if call.args[0] == "/config/delete"
        ]
        self.assertIn("name=old", delete_names)

    def test_remote_rename_rejects_existing_name_without_requesting_config(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"remotes": ["old:", "renamed:"]})

        ok, detail = rclone.rename_remote("old", "renamed")

        self.assertFalse(ok)
        self.assertEqual(detail, "目标名称已存在")
        rclone._req.assert_called_once_with("/config/listremotes", timeout=5.0, method="POST")


if __name__ == "__main__":
    unittest.main()
