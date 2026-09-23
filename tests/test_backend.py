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


class MediaPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.old_mount = os.environ.get("AURORA_LOCAL_MOUNT")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AURORA_LOCAL_MOUNT"] = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()
        if self.old_mount is None:
            os.environ.pop("AURORA_LOCAL_MOUNT", None)
        else:
            os.environ["AURORA_LOCAL_MOUNT"] = self.old_mount

    def test_media_stream_advertises_mime_and_range_support(self):
        Path(self.tmp.name, "clip.mp4").write_bytes(b"media")
        response = main.media_stream("clip.mp4", "admin")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertEqual(response.headers["accept-ranges"], "bytes")

    def test_srt_is_converted_to_webvtt(self):
        Path(self.tmp.name, "movie.zh.srt").write_text(
            "1\n00:00:01,000 --> 00:00:03,500\n你好，世界\n", encoding="utf-8"
        )
        response = main.media_subtitle("movie.zh.srt", "admin")
        self.assertEqual(response.media_type, "text/vtt; charset=utf-8")
        self.assertIn(b"WEBVTT", response.body)
        self.assertIn(b"00:00:01.000 --> 00:00:03.500", response.body)
        self.assertIn("你好，世界".encode(), response.body)

    def test_ass_override_tags_are_removed(self):
        Path(self.tmp.name, "movie.ass").write_text(
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\an8}字幕\\N第二行\n",
            encoding="utf-8",
        )
        body = main.media_subtitle("movie.ass", "admin").body
        self.assertIn(b"00:00:01.000 --> 00:00:02.500", body)
        self.assertIn("字幕\n第二行".encode(), body)
        self.assertNotIn(b"\\an8", body)


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


class QbitQueueTests(unittest.TestCase):
    def test_queue_settings_reads_only_supported_preferences(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        response = Mock(status_code=200)
        response.json.return_value = {
            "queueing_enabled": True,
            "max_active_torrents": 9999,
            "max_active_downloads": 3,
            "max_active_uploads": 9999,
            "max_active_checking_torrents": 1,
            "add_to_top_of_queue": False,
            "web_ui_password": "must not escape",
        }
        session.get.return_value = response
        qbit._sess = session

        ok, settings, detail = qbit.queue_settings()

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(settings["max_active_uploads"], 9999)
        self.assertNotIn("web_ui_password", settings)

    def test_queue_settings_posts_allowlisted_preferences(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        session.post.return_value = Mock(status_code=200)
        response = Mock(status_code=200)
        response.json.return_value = {
            "queueing_enabled": True,
            "max_active_torrents": 20,
            "max_active_downloads": 3,
            "max_active_uploads": 20,
            "max_active_checking_torrents": 2,
            "add_to_top_of_queue": True,
        }
        session.get.return_value = response
        qbit._sess = session

        ok, settings, detail = qbit.update_queue_settings({
            "queueing_enabled": True,
            "max_active_torrents": 20,
            "max_active_downloads": 3,
            "max_active_uploads": 20,
            "max_active_checking_torrents": 2,
            "add_to_top_of_queue": True,
            "web_ui_password": "must not be sent",
        })

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(settings["max_active_torrents"], 20)
        payload = json.loads(session.post.call_args.kwargs["data"]["json"])
        self.assertNotIn("web_ui_password", payload)
        self.assertEqual(payload["max_active_uploads"], 20)

    def test_zero_queue_limit_is_rejected(self):
        with self.assertRaises(main.HTTPException):
            main._validate_qbit_queue(main.QbitQueueBody(
                queueing_enabled=True,
                max_active_torrents=0,
                max_active_downloads=3,
                max_active_uploads=9999,
                max_active_checking_torrents=1,
                add_to_top_of_queue=False,
            ))


class QbitAdvancedTests(unittest.TestCase):
    def test_torrent_detail_maps_files_and_trackers(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        info = Mock(status_code=200)
        info.json.return_value = [{
            "hash": "a" * 40, "name": "release", "state": "stalledUP",
            "progress": 1, "size": 1024, "ratio": 2.5, "save_path": "/downloads/movies",
            "tags": "movie,aurora-remote-deadbeefdeadbeef", "web_ui_password": "secret",
        }]
        files = Mock(status_code=200)
        files.json.return_value = [{"index": 0, "name": "movie.mkv", "size": 1024, "progress": 1, "priority": 6}]
        trackers = Mock(status_code=200)
        trackers.json.return_value = [{"url": "https://tracker.example/announce", "status": 2, "num_peers": 3}]
        session.get.side_effect = [info, files, trackers]
        qbit._sess = session

        ok, detail, error = qbit.torrent_detail("a" * 40)

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(detail["files"][0]["name"], "movie.mkv")
        self.assertEqual(detail["trackers"][0]["num_peers"], 3)
        self.assertNotIn("web_ui_password", detail)

    def test_advanced_action_uses_allowlisted_endpoint_and_payload(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        session.post.return_value = Mock(status_code=200)
        qbit._sess = session

        ok, detail = qbit.advanced_action("b" * 40, "set_file_priority", file_ids=[0, 2], priority=6)

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(session.post.call_args.args[0].rsplit("/", 1)[-1], "filePrio")
        self.assertEqual(session.post.call_args.kwargs["data"], {
            "hashes": "b" * 40, "id": "0,2", "priority": "6",
        })

    def test_system_tags_cannot_be_created_or_deleted(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        qbit._sess = session

        ok, detail = qbit.create_tags(["aurora-protected"])
        self.assertFalse(ok)
        self.assertIn("系统标签", detail)
        session.post.assert_not_called()


class TorrentPolicyTests(unittest.TestCase):
    def test_policy_preview_and_apply_pause_matching_torrent(self):
        old_settings = providers._SETTINGS_FILE
        old_notify = providers._POLICY_NOTIFY_FILE
        old_qbit = providers._qbit
        with tempfile.TemporaryDirectory() as td:
            providers._SETTINGS_FILE = str(Path(td) / "settings.json")
            providers._POLICY_NOTIFY_FILE = str(Path(td) / "policy-notify.json")

            class FakeQbit:
                _last_details_ok = True
                _UP_STATES = providers.QbittorrentProvider._UP_STATES

                def __init__(self):
                    self.actions = []

                def available(self):
                    return True

                def torrent_details(self):
                    return [{
                        "hash": "c" * 40, "name": "old-release", "state": "stalledUP",
                        "category": "movies", "tags": "", "ratio": 2.0,
                        "seeding_time": 7200, "inactive_seeding_time": 0,
                    }]

                def advanced_action(self, hash_, action, **kwargs):
                    self.actions.append((hash_, action, kwargs))
                    return True, ""

            fake = FakeQbit()
            providers._qbit = fake
            providers.save_torrent_policies({
                "enabled": False,
                "interval": 300,
                "rules": [{
                    "id": "policy-one", "name": "电影两小时", "category": "movies", "enabled": True,
                    "action": "pause", "min_seed_minutes": 60, "max_seed_minutes": 90,
                    "max_inactive_minutes": -1, "max_ratio": -1,
                }],
            })

            ok, preview, detail = providers.preview_torrent_policies()
            self.assertTrue(ok)
            self.assertEqual(detail, "")
            self.assertEqual(len(preview["items"]), 1)
            self.assertEqual(preview["items"][0]["action"], "pause")

            ok, result, detail = providers.apply_torrent_policies(confirm=True)
            self.assertTrue(ok)
            self.assertEqual(detail, "")
            self.assertEqual(result["applied"], 1)
            self.assertEqual(fake.actions[0][1], "pause")
        providers._SETTINGS_FILE = old_settings
        providers._POLICY_NOTIFY_FILE = old_notify
        providers._qbit = old_qbit


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

    def test_missing_rclone_job_does_not_requeue_upload(self):
        old_dest_file = providers._TORRENT_DEST_FILE
        old_mount = os.environ.get("AURORA_LOCAL_MOUNT")
        old_qbit = providers._qbit
        old_rclone = providers._rclone

        class FakeQbit:
            _last_details_ok = True

            def available(self):
                return True

            def torrent_details(self):
                return [{
                    "hash": "abc", "name": "release", "tags": "aurora-remote-test",
                    "progress": 1.0, "content_path": "/downloads/release",
                }]

        class FakeRclone:
            def available(self):
                return True

            def transfers(self):
                return []

            def upload_local(self, *_args):
                raise AssertionError("a missing job must not start a duplicate upload")

        with tempfile.TemporaryDirectory() as td:
            try:
                providers._TORRENT_DEST_FILE = str(Path(td) / "destinations.json")
                os.environ["AURORA_LOCAL_MOUNT"] = td
                source = Path(td) / "release"
                source.mkdir()
                Path(providers._TORRENT_DEST_FILE).write_text(json.dumps({
                    "aurora-remote-test": {
                        "remote": "media", "path": "movies", "status": "uploading",
                        "detail": "", "hash": "abc", "name": "release",
                        "local_path": str(source), "transfer_id": "job-lost",
                        "created": int(time.time()), "finished": 0,
                    },
                }))
                providers._qbit = FakeQbit()
                providers._rclone = FakeRclone()

                providers._process_torrent_destinations()
                state = json.loads(Path(providers._TORRENT_DEST_FILE).read_text())["aurora-remote-test"]
                self.assertEqual(state["status"], "error")
                self.assertIn("状态已丢失", state["detail"])
            finally:
                providers._TORRENT_DEST_FILE = old_dest_file
                providers._qbit = old_qbit
                providers._rclone = old_rclone
                if old_mount is None:
                    os.environ.pop("AURORA_LOCAL_MOUNT", None)
                else:
                    os.environ["AURORA_LOCAL_MOUNT"] = old_mount

    def test_failed_torrent_destination_can_be_retried(self):
        old_dest_file = providers._TORRENT_DEST_FILE
        old_rclone = providers._rclone
        with tempfile.TemporaryDirectory() as td:
            try:
                providers._TORRENT_DEST_FILE = str(Path(td) / "destinations.json")
                Path(providers._TORRENT_DEST_FILE).write_text(json.dumps({
                    "aurora-remote-0123456789abcdef": {
                        "remote": "media", "path": "movies", "status": "error",
                        "detail": "failed", "transfer_id": "job-1",
                    },
                }))
                providers._rclone = Mock()
                ok, detail = providers.retry_torrent_destination("aurora-remote-0123456789abcdef")
                self.assertTrue(ok)
                self.assertEqual(detail, "")
                state = json.loads(Path(providers._TORRENT_DEST_FILE).read_text())["aurora-remote-0123456789abcdef"]
                self.assertEqual(state["status"], "waiting")
                self.assertEqual(state["transfer_id"], "")
            finally:
                providers._TORRENT_DEST_FILE = old_dest_file
                providers._rclone = old_rclone


class TorrentStateTests(unittest.TestCase):
    def test_stalled_download_is_distinguished_from_active_download(self):
        self.assertEqual(providers._QBIT_STATES["stalledDL"], "stalled")


class RcloneTests(unittest.TestCase):
    def setUp(self):
        self._old_transfer_file = providers._RCLONE_TRANSFER_FILE
        self._transfer_dir = tempfile.TemporaryDirectory()
        providers._RCLONE_TRANSFER_FILE = str(Path(self._transfer_dir.name) / "transfers.json")

    def tearDown(self):
        providers._RCLONE_TRANSFER_FILE = self._old_transfer_file
        self._transfer_dir.cleanup()

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

    def test_s3_torrent_destination_requires_bucket_when_not_configured(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(side_effect=[{"remotes": ["r2:"]}, {"type": "s3"}])

        old_buckets_file = providers._RCLONE_BUCKETS_FILE
        with tempfile.TemporaryDirectory() as td:
            providers._RCLONE_BUCKETS_FILE = str(Path(td) / "buckets.json")
            try:
                ok, path, detail = rclone.validate_destination("r2", "")
            finally:
                providers._RCLONE_BUCKETS_FILE = old_buckets_file

        self.assertFalse(ok)
        self.assertEqual(path, "")
        self.assertEqual(detail, "S3/R2 目标请填写 bucket 名称或目录")

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

    def test_failed_upload_keeps_staging_file_for_retry(self):
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
                self.assertTrue(internal["retryable"])
                self.assertEqual(internal["cleanup"], str(stage))
                internal["status"] = "error"
                rclone._req.return_value = {"jobid": 45}
                ok, retry, detail = rclone.retry_transfer(job["id"])
                self.assertTrue(ok)
                self.assertIsNotNone(retry)
                self.assertEqual(detail, "")
            finally:
                providers._DATA_DIR = old_data_dir

    def test_running_transfers_are_not_evicted_by_history_limit(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"finished": False, "transferring": []})
        for index in range(providers._RCLONE_TRANSFER_LIMIT + 1):
            rclone._transfer_jobs[f"job-{index}"] = {
                "id": f"job-{index}", "rcloneJobId": index, "status": "running",
                "action": "copy", "label": "active", "progress": None,
                "bytes": 0, "total": None, "speed": 0, "detail": "",
                "created": index, "finished": 0, "cleanup": "", "retryable": True,
                "request": {"endpoint": "/sync/copy", "payload": {}},
            }

        jobs = rclone.transfers()

        self.assertEqual(len(jobs), providers._RCLONE_TRANSFER_LIMIT + 1)
        self.assertEqual(len(rclone._transfer_jobs), providers._RCLONE_TRANSFER_LIMIT + 1)

    def test_transfer_journal_is_restored_after_provider_restart(self):
        rclone = providers.RcloneProvider()
        rclone._req = Mock(return_value={"jobid": 77})
        ok, job, detail = rclone.copy_remote("media", "clip.mp4", "archive", "backup", False, "copy")
        self.assertTrue(ok)
        self.assertEqual(detail, "")

        restored = providers.RcloneProvider()

        self.assertIn(job["id"], restored._transfer_jobs)
        self.assertEqual(restored._transfer_jobs[job["id"]]["rcloneJobId"], 77)

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
