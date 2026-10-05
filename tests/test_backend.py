import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

# main 在 import 时就会生成 .auth/.secret、providers 会写活动日志：
# 必须在 import 前重定向状态/数据目录，否则本地跑测试会污染源码树。
# CI 已显式设置这三个变量，setdefault 不会覆盖。
os.environ.setdefault("AURORA_STATE_DIR", tempfile.mkdtemp(prefix="aurora-test-state-"))
os.environ.setdefault("AURORA_DATA_DIR", tempfile.mkdtemp(prefix="aurora-test-data-"))

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

    def test_category_mapping_defaults_fill_only_omitted_targets(self):
        old = providers._SETTINGS_FILE
        with tempfile.TemporaryDirectory() as td:
            providers._SETTINGS_FILE = str(Path(td) / "settings.json")
            providers.save_torrent_category_mappings({
                "movies": {
                    "category": "movies",
                    "local_path": "media/movies",
                    "destination_remote": "archive",
                    "destination_path": "films",
                },
            })
            self.assertEqual(
                providers.apply_category_mapping("movies"),
                ("media/movies", "archive", "films", True),
            )
            self.assertEqual(
                providers.apply_category_mapping("movies", "custom", "other", "manual"),
                ("custom", "other", "manual", False),
            )
            self.assertEqual(
                providers.apply_category_mapping("movies", "", "", "", apply_destination=False),
                ("media/movies", "", "", True),
            )
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

    def test_file_selection_sets_unselected_files_to_skip(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        session.post.side_effect = [Mock(status_code=200), Mock(status_code=200)]
        qbit._sess = session

        ok, detail = qbit.advanced_action(
            "d" * 40, "set_file_selection", file_ids=[0, 2], all_file_ids=[0, 1, 2],
        )

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(session.post.call_args_list[0].kwargs["data"], {
            "hashes": "d" * 40, "id": "1", "priority": "0",
        })
        self.assertEqual(session.post.call_args_list[1].kwargs["data"], {
            "hashes": "d" * 40, "id": "0,2", "priority": "1",
        })

    def test_system_tags_cannot_be_created_or_deleted(self):
        qbit = providers.QbittorrentProvider()
        session = Mock()
        qbit._sess = session

        ok, detail = qbit.create_tags(["aurora-protected"])
        self.assertFalse(ok)
        self.assertIn("系统标签", detail)
        session.post.assert_not_called()


class TorrentBatchTests(unittest.TestCase):
    def test_batch_location_can_target_all_tasks_in_category(self):
        old_qbit = providers._qbit
        old_mount = os.environ.get("AURORA_LOCAL_MOUNT")

        class FakeQbit:
            _last_details_ok = True

            def __init__(self):
                self.actions = []

            def available(self):
                return True

            def torrent_details(self):
                return [
                    {"hash": "e" * 40, "category": "movies"},
                    {"hash": "f" * 40, "category": "music"},
                ]

            def advanced_action(self, hash_, action, **kwargs):
                self.actions.append((hash_, action, kwargs))
                return True, ""

        with tempfile.TemporaryDirectory() as td:
            try:
                os.environ["AURORA_LOCAL_MOUNT"] = td
                Path(td, "archive").mkdir()
                fake = FakeQbit()
                providers._qbit = fake
                result = main.batch_torrent(
                    main.BatchAction(action="set_location", ids=[], category="movies", location="archive"),
                    "admin",
                )
                self.assertEqual(result["done"], 1)
                self.assertEqual(fake.actions[0][0], "e" * 40)
                self.assertEqual(fake.actions[0][2]["location"], "/downloads/archive")
            finally:
                providers._qbit = old_qbit
                if old_mount is None:
                    os.environ.pop("AURORA_LOCAL_MOUNT", None)
                else:
                    os.environ["AURORA_LOCAL_MOUNT"] = old_mount


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


class PolicyDryRunUndoTests(unittest.TestCase):
    """做种策略：预演（dry_run）必须零副作用；删除走回收站留档且可撤销。"""

    RULE = {
        "id": "policy-remove", "name": "清理", "category": "movies", "enabled": True,
        "action": "remove", "min_seed_minutes": 0, "max_seed_minutes": 90,
        "max_inactive_minutes": -1, "max_ratio": -1,
        "allow_delete": True, "delete_files": True,
    }

    def test_dry_run_reports_without_side_effects(self):
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
                        "hash": "d" * 40, "name": "old-release", "state": "stalledUP",
                        "category": "movies", "tags": "", "ratio": 2.0,
                        "seeding_time": 7200, "inactive_seeding_time": 0,
                        "save_path": "/downloads/movies",
                        "content_path": "/downloads/movies/old-release",
                    }]

                def advanced_action(self, hash_, action, **kwargs):
                    self.actions.append((hash_, action, kwargs))
                    return True, ""

            fake = FakeQbit()
            providers._qbit = fake
            providers.save_torrent_policies({"enabled": False, "interval": 300, "rules": [self.RULE]})
            ok, data, detail = providers.apply_torrent_policies(confirm=True, dry_run=True)
            self.assertTrue(ok, detail)
            self.assertTrue(data["dry_run"])
            self.assertEqual(len(data["items"]), 1)
            self.assertEqual(data["items"][0]["status"], "would_remove")
            # content_path 不在媒体目录内 → 预演即可判定该次删除不可恢复
            self.assertFalse(data["items"][0]["recoverable"])
            self.assertEqual(fake.actions, [])   # 预演不产生任何真实操作
        providers._SETTINGS_FILE = old_settings
        providers._POLICY_NOTIFY_FILE = old_notify
        providers._qbit = old_qbit

    def test_remove_goes_to_trash_and_undo_restores(self):
        old_settings = providers._SETTINGS_FILE
        old_notify = providers._POLICY_NOTIFY_FILE
        old_undo_file = providers._POLICY_UNDO_FILE
        old_undo_dir = providers._POLICY_UNDO_DIR
        old_qbit = providers._qbit
        old_mount = os.environ.get("AURORA_LOCAL_MOUNT")
        with tempfile.TemporaryDirectory() as td:
            media = Path(td) / "media"
            content = media / "movies" / "old-release"
            content.mkdir(parents=True)
            (content / "file.txt").write_text("data")
            os.environ["AURORA_LOCAL_MOUNT"] = str(media)
            providers._SETTINGS_FILE = str(Path(td) / "settings.json")
            providers._POLICY_NOTIFY_FILE = str(Path(td) / "policy-notify.json")
            providers._POLICY_UNDO_FILE = str(Path(td) / "undo.json")
            providers._POLICY_UNDO_DIR = str(Path(td) / "undo-dir")

            class FakeQbit:
                _last_details_ok = True
                _UP_STATES = providers.QbittorrentProvider._UP_STATES

                def __init__(self):
                    self.actions = []
                    self.added = []

                def available(self):
                    return True

                def torrent_details(self):
                    return [{
                        "hash": "e" * 40, "name": "old-release", "state": "stalledUP",
                        "category": "movies", "tags": "", "ratio": 2.0,
                        "seeding_time": 7200, "inactive_seeding_time": 0,
                        "save_path": "/downloads/movies",
                        "content_path": "/downloads/movies/old-release",
                    }]

                def advanced_action(self, hash_, action, **kwargs):
                    self.actions.append((hash_, action, kwargs))
                    return True, ""

                def export_torrent(self, hash_):
                    return True, b"d8:announce4:test", ""

                def add_file(self, filename, content, save_path="", tag="", category="", tags=None):
                    self.added.append({"filename": filename, "save_path": save_path,
                                       "category": category, "tags": list(tags or [])})
                    return True, ""

            fake = FakeQbit()
            providers._qbit = fake
            providers.save_torrent_policies({"enabled": False, "interval": 300, "rules": [self.RULE]})
            ok, data, detail = providers.apply_torrent_policies(confirm=True)
            self.assertTrue(ok, detail)
            self.assertEqual(data["applied"], 1)
            # 内容已进回收站，任务本体走"不连带删文件"的移除
            self.assertEqual(fake.actions, [("e" * 40, "remove", {"delete_files": False})])
            self.assertFalse(content.exists())
            self.assertEqual(len(providers.trash_list()), 1)
            undo_items = providers.policy_undo_list()
            self.assertEqual(len(undo_items), 1)
            self.assertTrue(undo_items[0]["recoverable"])
            # 撤销：文件回原位 + 用留档重加任务（save_path/category 原样还原）
            ok, detail = providers.policy_undo_execute(undo_items[0]["id"])
            self.assertTrue(ok, detail)
            self.assertTrue((content / "file.txt").exists())
            self.assertEqual(providers.trash_list(), [])
            self.assertEqual(len(fake.added), 1)
            self.assertEqual(fake.added[0]["save_path"], "/downloads/movies")
            self.assertEqual(fake.added[0]["category"], "movies")
            self.assertEqual(len(providers.policy_undo_list()), 0)
        providers._SETTINGS_FILE = old_settings
        providers._POLICY_NOTIFY_FILE = old_notify
        providers._POLICY_UNDO_FILE = old_undo_file
        providers._POLICY_UNDO_DIR = old_undo_dir
        providers._qbit = old_qbit
        if old_mount is None:
            os.environ.pop("AURORA_LOCAL_MOUNT", None)
        else:
            os.environ["AURORA_LOCAL_MOUNT"] = old_mount


class MediaOrganizeTests(unittest.TestCase):
    """下载完成自动整理：规则校验、硬链接、防重复、预演与 Jellyfin 刷新。"""

    def setUp(self):
        self._old_org = providers._MEDIA_ORG_FILE
        self._old_qbit = providers._qbit
        self._old_jelly = providers._jelly
        self._old_mount = os.environ.get("AURORA_LOCAL_MOUNT")
        self._tmp = tempfile.TemporaryDirectory()
        providers._MEDIA_ORG_FILE = str(Path(self._tmp.name) / "organize.json")
        self.media = Path(self._tmp.name) / "media"
        self.content = self.media / "movies" / "Some.Movie.2024"
        self.content.mkdir(parents=True)
        (self.content / "movie.mkv").write_text("video-data")
        os.environ["AURORA_LOCAL_MOUNT"] = str(self.media)

        class FakeQbit:
            _last_details_ok = True
            _UP_STATES = providers.QbittorrentProvider._UP_STATES

            def __init__(self):
                self.actions = []

            def available(self):
                return True

            def advanced_action(self, hash_, action, **kwargs):
                self.actions.append((hash_, action, kwargs))
                return True, ""

            def torrent_details(self):
                return [{
                    "hash": "f" * 40, "name": "Some.Movie.2024", "state": "stalledUP",
                    "category": "movies", "tags": "", "progress": 1.0, "size": 4096,
                    "save_path": "/downloads/movies",
                    "content_path": "/downloads/movies/Some.Movie.2024",
                }]

        self.qbit = FakeQbit()
        providers._qbit = self.qbit
        providers.save_media_organize_settings({
            "enabled": True, "interval": 300, "jellyfin_refresh": True,
            "rules": [{"id": "org-movies", "name": "电影入库", "category": "movies",
                       "enabled": True, "target_dir": "library/movies",
                       "mode": "hardlink", "use_subfolder": True, "min_size_mb": 0}],
        })
        self.refreshed = []

    def tearDown(self):
        providers._MEDIA_ORG_FILE = self._old_org
        providers._qbit = self._old_qbit
        providers._jelly = self._old_jelly
        if self._old_mount is None:
            os.environ.pop("AURORA_LOCAL_MOUNT", None)
        else:
            os.environ["AURORA_LOCAL_MOUNT"] = self._old_mount
        self._tmp.cleanup()

    def _patch_jelly(self):
        class FakeJelly:
            def refresh_library(self_inner):
                self.refreshed.append(1)
                return True, ""
        providers._jelly = FakeJelly()

    def test_preview_lists_completed_torrent_with_target(self):
        self._patch_jelly()
        ok, data, detail = providers.preview_media_organize()
        self.assertTrue(ok, detail)
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        self.assertEqual(item["rule_name"], "电影入库")
        self.assertFalse(item["processed"])
        self.assertFalse(item["exists"])
        self.assertEqual(item["target"], "library/movies/Some.Movie.2024")

    def test_mkdir_race_returns_conflict_without_deleting_target(self):
        # 复现并发竞态：目标在 exists 检查之后、mkdir 之前被并发方创建。
        # mkdir 撞车必须返回冲突且绝不 rmtree——那会删掉并发方刚整理好的成果
        target = self.media / "library" / "movies" / "Some.Movie.2024"
        target.mkdir(parents=True)
        (target / "already-organized.mkv").write_text("precious")
        real_exists = os.path.exists

        def exists_lies(path, *args, **kwargs):
            if os.path.abspath(str(path)) == os.path.abspath(str(target)):
                return False   # 检查时谎称目标不存在，模拟时间窗
            return real_exists(path, *args, **kwargs)

        with patch("os.path.exists", side_effect=exists_lies):
            ok, error = providers._org_transfer(str(self.content), str(target), "hardlink")
        self.assertFalse(ok)
        self.assertIn("不覆盖", error)
        self.assertTrue((target / "already-organized.mkv").exists())   # 并发方成果完好

    def test_hardlink_apply_then_skip_on_second_run(self):
        self._patch_jelly()
        ok, data, detail = providers.apply_media_organize(confirm=True)
        self.assertTrue(ok, detail)
        self.assertEqual(data["organized"], 1)
        self.assertEqual(self.refreshed, [1])
        target = self.media / "library" / "movies" / "Some.Movie.2024" / "movie.mkv"
        self.assertTrue(target.exists())
        # 硬链接：同一 inode，源文件保留（做种不中断）
        self.assertEqual(target.stat().st_ino, (self.content / "movie.mkv").stat().st_ino)
        self.assertTrue((self.content / "movie.mkv").exists())
        # 第二轮：已整理的任务不再重复处理
        ok2, data2, _ = providers.apply_media_organize(confirm=True)
        self.assertTrue(ok2)
        self.assertEqual(data2["organized"], 0)
        statuses = [item["status"] for item in data2["items"]]
        self.assertEqual(statuses, ["already_done"])
        self.assertEqual(self.refreshed, [1])   # 没有新增整理就不再次刷库

    def test_move_mode_relocates_and_pauses_torrent(self):
        # move 会把内容搬出下载目录：任务必须自动暂停，否则停在"文件丢失"
        providers.save_media_organize_settings({
            "enabled": True, "interval": 300, "jellyfin_refresh": False,
            "rules": [{"id": "org-mv", "name": "电影搬移", "category": "movies",
                       "enabled": True, "target_dir": "library/movies",
                       "mode": "move", "use_subfolder": True, "min_size_mb": 0}],
        })
        ok, data, detail = providers.apply_media_organize(confirm=True)
        self.assertTrue(ok, detail)
        self.assertEqual(data["organized"], 1)
        target = self.media / "library" / "movies" / "Some.Movie.2024" / "movie.mkv"
        self.assertTrue(target.exists())
        self.assertFalse((self.content / "movie.mkv").exists())
        self.assertEqual(self.qbit.actions, [("f" * 40, "pause", {})])

    def test_target_conflict_is_reported_not_overwritten(self):
        (self.media / "library" / "movies" / "Some.Movie.2024").mkdir(parents=True)
        (self.media / "library" / "movies" / "Some.Movie.2024" / "movie.mkv").write_text("existing")
        self._patch_jelly()
        ok, data, _ = providers.apply_media_organize(confirm=True)
        self.assertTrue(ok)
        statuses = [item["status"] for item in data["items"]]
        self.assertEqual(statuses, ["conflict"])
        self.assertEqual(data["organized"], 0)
        self.assertEqual((self.media / "library" / "movies" / "Some.Movie.2024" / "movie.mkv").read_text(), "existing")

    def test_unsafe_target_dir_is_rejected(self):
        for bad in ("/abs/path", "../outside", "a/../..", ".aurora-trash/x", ""):
            self.assertEqual(providers._clean_org_target(bad), "", bad)
        self.assertEqual(providers._clean_org_target("library/movies"), "library/movies")

    def test_dry_run_creates_nothing(self):
        self._patch_jelly()
        ok, data, _ = providers.apply_media_organize(confirm=True, dry_run=True)
        self.assertTrue(ok)
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["organized"], 0)
        self.assertFalse((self.media / "library").exists())
        self.assertEqual(self.refreshed, [])


class RssHelpersTests(unittest.TestCase):
    """RSS 订阅：路径校验、4.x/5.x 规则双写与读取兼容、feeds 树拍平。"""

    def test_rss_path_validation(self):
        self.assertTrue(providers._valid_rss_path("动漫订阅"))
        self.assertTrue(providers._valid_rss_path("Shows\\TV RSS"))
        self.assertTrue(providers._valid_rss_path("Movie (2024)"))
        self.assertFalse(providers._valid_rss_path(""))
        self.assertFalse(providers._valid_rss_path("a/b"))
        self.assertFalse(providers._valid_rss_path("bad<name"))
        self.assertFalse(providers._valid_rss_path("bad:name"))

    def test_rule_def_dual_write(self):
        d = providers._rss_rule_def({
            "enabled": True, "use_regex": True,
            "must_contain": "1080p", "must_not_contain": "CAM",
            "episode_filter": "S01E01-",
            "affected_feeds": ["Anime"], "save_path": "/downloads/anime",
            "category": "anime", "tags": ["rss"],
        })
        # 5.x 只认 torrentParams，4.1-4.5 只认旧字段：必须双写
        self.assertEqual(d["torrentParams"]["save_path"], "/downloads/anime")
        self.assertEqual(d["savePath"], "/downloads/anime")
        self.assertEqual(d["torrentParams"]["category"], "anime")
        self.assertEqual(d["assignedCategory"], "anime")
        self.assertEqual(d["torrentParams"]["tags"], ["rss"])
        self.assertEqual(d["tags"], ["rss"])
        self.assertTrue(d["useRegex"])
        self.assertEqual(d["affectedFeeds"], ["Anime"])
        self.assertFalse(d["torrentParams"]["stopped"])

    def test_normalize_rss_rule_reads_both_shapes(self):
        new_shape = providers._normalize_rss_rule("r1", {
            "enabled": False, "useRegex": True, "mustContain": "x",
            "mustNotContain": "y", "episodeFilter": "S01",
            "torrentParams": {"save_path": "/downloads/a", "category": "c", "tags": ["t"]},
            "affectedFeeds": ["F"], "lastMatch": 123,
        })
        self.assertEqual(new_shape["save_path"], "/downloads/a")
        self.assertEqual(new_shape["category"], "c")
        self.assertEqual(new_shape["tags"], ["t"])
        self.assertEqual(new_shape["last_match"], 123)
        self.assertEqual(new_shape["must_not_contain"], "y")
        old_shape = providers._normalize_rss_rule("r2", {
            "enabled": True, "mustContain": "y",
            "savePath": "/downloads/b", "assignedCategory": "c2", "tags": ["t2"],
        })
        self.assertEqual(old_shape["save_path"], "/downloads/b")
        self.assertEqual(old_shape["category"], "c2")
        self.assertEqual(old_shape["tags"], ["t2"])

    def test_flatten_rss_feeds(self):
        tree = {"Folder": {"Feed A": {"url": "http://x", "hasError": True}},
                "Top Feed": {"url": "http://y", "articles": []}}
        feeds = providers._flatten_rss_feeds(tree, "")
        by_path = {f["path"]: f for f in feeds}
        self.assertEqual(by_path["Folder\\Feed A"]["has_error"], True)
        self.assertEqual(by_path["Top Feed"]["url"], "http://y")


class TgProxyTests(unittest.TestCase):
    """TG 通道代理：AURORA_TG_PROXY 只影响 Telegram 请求；超时给可行动提示。"""

    def setUp(self):
        self._old = os.environ.get("AURORA_TG_PROXY")
        os.environ.pop("AURORA_TG_PROXY", None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("AURORA_TG_PROXY", None)
        else:
            os.environ["AURORA_TG_PROXY"] = self._old

    class _FakeResp:
        status_code = 200

        def json(self):
            return {}

    def test_explicit_proxy_is_applied(self):
        os.environ["AURORA_TG_PROXY"] = "http://127.0.0.1:7890"
        captured = {}

        def fake_post(url, **kwargs):
            captured.update(kwargs)
            captured["url"] = url
            return self._FakeResp()

        with patch("requests.post", side_effect=fake_post):
            ok, detail = providers._tg_send("tok", "chat", "hello")
        self.assertTrue(ok, detail)
        self.assertEqual(captured["proxies"],
                         {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"})
        self.assertIn("/bottok/sendMessage", captured["url"])

    def test_default_keeps_trust_env_when_unset(self):
        captured = {}

        def fake_post(url, **kwargs):
            captured.update(kwargs)
            return self._FakeResp()

        with patch("requests.post", side_effect=fake_post):
            ok, _detail = providers._tg_send("tok", "chat", "hello")
        self.assertTrue(ok)
        self.assertIsNone(captured.get("proxies"))   # 交给 requests 的 trust_env 行为

    def test_timeout_returns_actionable_hint(self):
        import requests as _requests

        with patch("requests.post", side_effect=_requests.exceptions.ConnectTimeout("boom")):
            ok, detail = providers._tg_send("tok", "chat", "hello")
        self.assertFalse(ok)
        self.assertIn("AURORA_TG_PROXY", detail)


    def test_trash_failure_returns_error_not_raise(self):
        # 盘掉线/磁盘满时 makedirs 失败：必须返回错误而非 NameError 穿透成 500
        old_mount = os.environ.get("AURORA_LOCAL_MOUNT")
        with tempfile.TemporaryDirectory() as td:
            content = Path(td) / "release"
            content.mkdir()
            (content / "f.txt").write_text("x")
            os.environ["AURORA_LOCAL_MOUNT"] = td
            with patch("os.makedirs", side_effect=OSError(28, "no space left on device")):
                trash_id, error = providers._policy_trash_content(str(content))
            self.assertEqual(trash_id, "")
            self.assertIn("失败", error)
            self.assertTrue(content.exists())   # 源文件未被移动
        if old_mount is None:
            os.environ.pop("AURORA_LOCAL_MOUNT", None)
        else:
            os.environ["AURORA_LOCAL_MOUNT"] = old_mount

    def test_prune_deletes_stale_torrent_files(self):
        old_undo_file = providers._POLICY_UNDO_FILE
        old_undo_dir = providers._POLICY_UNDO_DIR
        with tempfile.TemporaryDirectory() as td:
            providers._POLICY_UNDO_FILE = str(Path(td) / "undo.json")
            providers._POLICY_UNDO_DIR = str(Path(td) / "undo-dir")
            os.makedirs(providers._POLICY_UNDO_DIR)
            stale = Path(providers._POLICY_UNDO_DIR) / "old.torrent"
            fresh = Path(providers._POLICY_UNDO_DIR) / "new.torrent"
            stale.write_bytes(b"x")
            fresh.write_bytes(b"y")
            expired = int(time.time()) - providers._POLICY_UNDO_RETENTION - 60
            providers._save_policy_undo([
                {"id": "old", "time": expired, "action": "remove", "torrent_file": str(stale)},
                {"id": "new", "time": int(time.time()), "action": "remove", "torrent_file": str(fresh)},
            ])
            providers._policy_undo_add({"id": "x", "time": int(time.time()), "action": "remove",
                                        "torrent_file": str(fresh)})
            self.assertFalse(stale.exists())   # 过期条目的留档文件被同步删除
            self.assertTrue(fresh.exists())
            self.assertEqual([e["id"] for e in providers._load_policy_undo()], ["new", "x"])
        providers._POLICY_UNDO_FILE = old_undo_file
        providers._POLICY_UNDO_DIR = old_undo_dir

    def test_undo_can_retry_when_readd_fails(self):
        # 文件已回原位（trash_id 已消耗）但重加任务失败：条目必须保留且可重试
        old_undo_file = providers._POLICY_UNDO_FILE
        old_undo_dir = providers._POLICY_UNDO_DIR
        old_qbit = providers._qbit
        with tempfile.TemporaryDirectory() as td:
            providers._POLICY_UNDO_FILE = str(Path(td) / "undo.json")
            providers._POLICY_UNDO_DIR = str(Path(td) / "undo-dir")
            torrent_file = Path(td) / "keep.torrent"
            torrent_file.write_bytes(b"d8:announce4:test")
            providers._save_policy_undo([{
                "id": "e1", "time": int(time.time()), "action": "remove",
                "hash": "a" * 40, "name": "n", "save_path": "/downloads",
                "category": "", "tags": [], "trash_id": "",
                "torrent_file": str(torrent_file),
            }])

            class FakeQbit:
                def __init__(self):
                    self.calls = 0

                def add_file(self, filename, content, save_path="", tag="", category="", tags=None):
                    self.calls += 1
                    if self.calls == 1:
                        return False, "qBittorrent 暂时不可用"
                    return True, ""

                def advanced_action(self, hash_, action, **kwargs):
                    return True, ""

            fake = FakeQbit()
            providers._qbit = fake
            ok, _detail = providers.policy_undo_execute("e1")
            self.assertFalse(ok)   # 第一次重加失败
            ok2, detail2 = providers.policy_undo_execute("e1")
            self.assertTrue(ok2, detail2)   # 条目仍在，重试直接走重加
            self.assertFalse(torrent_file.exists())
            self.assertEqual(providers._load_policy_undo(), [])
        providers._POLICY_UNDO_FILE = old_undo_file
        providers._POLICY_UNDO_DIR = old_undo_dir
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


class AuthHardeningTests(unittest.TestCase):
    def test_consteq_handles_non_ascii(self):
        # hmac.compare_digest 对非 ASCII str 抛 TypeError；曾经会让登录 500、
        # 甚至设置非 ASCII 密码后把自己永久锁死
        self.assertTrue(main._consteq("密码口令十二位", "密码口令十二位"))
        self.assertFalse(main._consteq("密码口令十二位", "另一个密码十二"))

    def test_client_ip_ignores_client_controlled_forwarded_for(self):
        # X-Forwarded-For 第一段由客户端控制：采信它会让失败计数永远记不满
        req = Mock()
        req.client.host = "127.0.0.1"
        req.headers = {"x-forwarded-for": "203.0.113.9"}
        self.assertEqual(main._client_ip(req), "127.0.0.1")
        req.headers = {"x-real-ip": "198.51.100.7", "x-forwarded-for": "203.0.113.9"}
        self.assertEqual(main._client_ip(req), "198.51.100.7")

    def test_lock_table_overflow_keeps_existing_counts(self):
        now = time.time()
        main._FAILS.clear()
        try:
            main._FAILS["victim"] = [now] * main.FAIL_LIMIT
            for i in range(6000):
                main._FAILS[f"10.{i // 256}.{i % 256}.1"] = [now - 10]
            # 洪泛不能整表清空：新条目被淘汰，victim 的失败记录必须保留
            self.assertTrue(main._locked("victim"))
            self.assertIn("victim", main._FAILS)
        finally:
            main._FAILS.clear()


class MediaSafetyTests(unittest.TestCase):
    def test_media_paths_cannot_reach_trash(self):
        old_mount = os.environ.get("AURORA_LOCAL_MOUNT")
        with tempfile.TemporaryDirectory() as td:
            os.environ["AURORA_LOCAL_MOUNT"] = td
            trash = Path(td, ".aurora-trash")
            trash.mkdir()
            (trash / "f.txt").write_text("deleted")
            with self.assertRaises(main.HTTPException):
                main._media_safe(Path(".aurora-trash/f.txt"))
        if old_mount is None:
            os.environ.pop("AURORA_LOCAL_MOUNT", None)
        else:
            os.environ["AURORA_LOCAL_MOUNT"] = old_mount

    def test_media_delete_rolls_back_when_metadata_write_fails(self):
        old_mount = os.environ.get("AURORA_LOCAL_MOUNT")
        old_trash = providers._TRASH_FILE
        old_add = providers.trash_add
        with tempfile.TemporaryDirectory() as td:
            os.environ["AURORA_LOCAL_MOUNT"] = td
            providers._TRASH_FILE = str(Path(td) / "trash-meta.json")
            source = Path(td) / "sample.txt"
            source.write_text("test")
            providers.trash_add = Mock(side_effect=OSError("disk full"))
            try:
                with self.assertRaises(main.HTTPException) as ctx:
                    main.media_delete(main.media_path_body(path="sample.txt"), "admin")
                self.assertEqual(ctx.exception.status_code, 503)
                # 元数据写失败必须回滚：文件回到原位，不能变成回收站里看不见的幽灵
                self.assertTrue(source.exists())
                self.assertEqual(source.read_text(), "test")
            finally:
                providers.trash_add = old_add
        providers._TRASH_FILE = old_trash
        if old_mount is None:
            os.environ.pop("AURORA_LOCAL_MOUNT", None)
        else:
            os.environ["AURORA_LOCAL_MOUNT"] = old_mount


class QbitAdapterTests(unittest.TestCase):
    def test_unknown_state_falls_back_to_unknown_not_done(self):
        qbit = providers.QbittorrentProvider()
        qbit.torrent_details = Mock(return_value=[
            {"hash": "a" * 40, "name": "mystery", "state": "brandNewState", "progress": 0.5},
        ])
        rows = qbit.torrents()
        self.assertEqual(rows[0]["state"], "unknown")

    def test_metadata_download_state_is_not_done(self):
        self.assertEqual(providers._QBIT_STATES["metaDL"], "downloading")
        self.assertEqual(providers._QBIT_STATES["metadataDL"], "downloading")

    def test_available_backs_off_after_failures(self):
        qbit = providers.QbittorrentProvider()
        qbit._ensure = Mock(side_effect=RuntimeError("qbit down"))
        self.assertFalse(qbit.available())
        self.assertEqual(qbit._ensure.call_count, 2)   # 单轮探测最多两次
        self.assertFalse(qbit.available())             # 退避期内不再探测
        self.assertEqual(qbit._ensure.call_count, 2)
        qbit._auth_backoff_until = 0.0
        self.assertFalse(qbit.available())
        self.assertEqual(qbit._ensure.call_count, 4)   # 退避结束恢复探测


class RcloneRenameTests(unittest.TestCase):
    def setUp(self):
        self.rclone = providers.RcloneProvider()
        self.rclone._start_job = Mock(return_value={"id": "job-1"})
        self.old_bucket = providers._rclone_bucket
        providers._rclone_bucket = lambda name: ""

    def tearDown(self):
        providers._rclone_bucket = self.old_bucket

    def test_rename_remote_entry_moves_file(self):
        ok, job, detail = self.rclone.rename_remote_entry("media", "dir/file.txt", "renamed.txt", False)

        self.assertTrue(ok, detail)
        self.assertEqual(job, {"id": "job-1"})
        path, payload = self.rclone._start_job.call_args[0]
        self.assertEqual(path, "/operations/movefile")
        self.assertEqual(payload["srcFs"], "media:")
        self.assertEqual(payload["srcRemote"], "dir/file.txt")
        self.assertEqual(payload["dstRemote"], "dir/renamed.txt")

    def test_rename_remote_entry_moves_dir(self):
        ok, job, detail = self.rclone.rename_remote_entry("media", "dir", "dir2", True)

        self.assertTrue(ok, detail)
        path, payload = self.rclone._start_job.call_args[0]
        self.assertEqual(path, "/sync/move")
        self.assertEqual(payload["srcFs"], "media:dir")
        self.assertEqual(payload["dstFs"], "media:dir2")

    def test_rename_remote_entry_rejects_bad_name(self):
        ok, job, detail = self.rclone.rename_remote_entry("media", "dir/f.txt", "../escape", False)

        self.assertFalse(ok)
        self.assertIsNone(job)
        self.rclone._start_job.assert_not_called()


class TorrentDestinationMergeTests(unittest.TestCase):
    def test_write_back_merges_with_concurrent_registration(self):
        """调度线程处理期间并发登记的转存目标不能被旧快照覆盖掉。"""
        old_dest = providers._TORRENT_DEST_FILE
        old_mount = os.environ.get("AURORA_LOCAL_MOUNT")
        old_qbit, old_rclone = providers._qbit, providers._rclone
        with tempfile.TemporaryDirectory() as td:
            providers._TORRENT_DEST_FILE = str(Path(td) / "dest.json")
            os.environ["AURORA_LOCAL_MOUNT"] = td
            item = Path(td) / "item"
            item.write_text("data")
            marker = "aurora-remote-" + "a" * 16
            providers._save_torrent_destinations({marker: {
                "remote": "media", "path": "dest", "status": "waiting",
                "created": int(time.time()),
            }})

            def fake_upload(remote, local, target):
                # 模拟并发：上传期间另一个请求登记了新的转存目标
                with providers._TORRENT_DEST_LOCK:
                    data = providers._load_torrent_destinations()
                    data["aurora-remote-" + "b" * 16] = {
                        "remote": "other", "path": "x", "status": "waiting",
                        "created": int(time.time()),
                    }
                    providers._save_torrent_destinations(data)
                return True, {"id": "job-1"}, ""

            qbit, rclone = Mock(), Mock()
            qbit.available.return_value = True
            qbit.torrent_details.return_value = [{
                "hash": "c" * 40, "name": "item", "tags": marker,
                "progress": 1.0, "save_path": "/downloads", "content_path": "",
            }]
            rclone.available.return_value = True
            rclone.transfers.return_value = []
            rclone.upload_local = Mock(side_effect=fake_upload)
            providers._qbit, providers._rclone = qbit, rclone
            try:
                providers._process_torrent_destinations()
                data = providers._load_torrent_destinations()
                self.assertEqual(data[marker]["status"], "uploading")
                self.assertEqual(data[marker]["transfer_id"], "job-1")
                # 回归点：并发登记的新条目必须存活
                self.assertIn("aurora-remote-" + "b" * 16, data)
            finally:
                providers._qbit, providers._rclone = old_qbit, old_rclone
        providers._TORRENT_DEST_FILE = old_dest
        if old_mount is None:
            os.environ.pop("AURORA_LOCAL_MOUNT", None)
        else:
            os.environ["AURORA_LOCAL_MOUNT"] = old_mount


class NotifyStateTests(unittest.TestCase):
    def test_empty_probe_preserves_baseline(self):
        """qbit 掉线（torrents 为空）时不得清空通知基线。"""
        old_notify = providers._NOTIFY_FILE
        old_settings = providers._SETTINGS_FILE
        with tempfile.TemporaryDirectory() as td:
            providers._NOTIFY_FILE = str(Path(td) / "notify.json")
            providers._SETTINGS_FILE = str(Path(td) / "settings.json")
            Path(providers._NOTIFY_FILE).write_text(json.dumps({"abc" * 10: 123}))
            providers._check_torrent_notify([])
            self.assertEqual(
                json.loads(Path(providers._NOTIFY_FILE).read_text()),
                {"abc" * 10: 123},
            )
        providers._NOTIFY_FILE = old_notify
        providers._SETTINGS_FILE = old_settings


class AtomicJsonTests(unittest.TestCase):
    def test_atomic_json_writes_and_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "state.json")
            providers._atomic_json(path, {"a": 1})
            self.assertEqual(json.loads(Path(path).read_text()), {"a": 1})
            leftovers = [p.name for p in Path(td).iterdir() if p.name != "state.json"]
            self.assertEqual(leftovers, [])


class VersionTests(unittest.TestCase):
    def test_prerelease_is_lower_than_release(self):
        # SemVer：1.2.3-rc.1 < 1.2.3
        self.assertLess(main._parse_version("1.2.3-rc.1"), main._parse_version("1.2.3"))
        self.assertLess(main._parse_version("v0.4.0-beta.2"), main._parse_version("0.4.0"))

    def test_numeric_ordering_not_lexical(self):
        self.assertLess(main._parse_version("0.9.1"), main._parse_version("0.10.0"))
        self.assertLess(main._parse_version("v0.9.9"), main._parse_version("v0.10.0"))

    def test_equal_versions_are_not_newer(self):
        self.assertFalse(main._parse_version("v0.4.0") > main._parse_version("0.4.0"))

    def test_garbage_falls_back_to_zero(self):
        self.assertEqual(main._parse_version(""), main._parse_version("0.0.0"))
        self.assertEqual(main._parse_version("not-a-version"), main._parse_version("0.0.0"))


class UpdateCheckTests(unittest.TestCase):
    """在线检查更新：缓存命中不重复请求 GitHub，失败可区分，仓库配置可覆盖。"""

    def setUp(self):
        main._UPDATE_CACHE.clear()
        self._old_repo = os.environ.get("AURORA_REPO")
        os.environ["AURORA_REPO"] = "example/aurora"

    def tearDown(self):
        main._UPDATE_CACHE.clear()
        if self._old_repo is None:
            os.environ.pop("AURORA_REPO", None)
        else:
            os.environ["AURORA_REPO"] = self._old_repo

    def _release(self, tag):
        return {"tag": tag, "latest": tag.lstrip("vV"),
                "url": f"https://github.com/example/aurora/releases/tag/{tag}",
                "published_at": "2026-10-01T00:00:00Z"}

    def test_update_available_when_latest_is_newer(self):
        with patch.object(main, "_github_latest_release", return_value=self._release("v99.0.0")) as m:
            data = main.update_check(force=1, _user="t")
        self.assertTrue(data["ok"])
        self.assertEqual(data["repo"], "example/aurora")
        self.assertEqual(data["latest"], "99.0.0")
        self.assertTrue(data["update_available"])
        m.assert_called_once_with("example/aurora")

    def test_cache_within_ttl_avoids_second_request(self):
        with patch.object(main, "_github_latest_release", return_value=self._release("v0.4.0")) as m:
            first = main.update_check(force=1, _user="t")
            self.assertFalse(first["update_available"])
            second = main.update_check(_user="t")   # 不 force：命中 30 分钟缓存
        self.assertTrue(second["cached"])
        self.assertFalse(second["update_available"])
        m.assert_called_once()   # 第二次没有打 GitHub

    def test_failure_is_reported_not_raised(self):
        with patch.object(main, "_github_latest_release", side_effect=RuntimeError("GitHub API 限流或被拒绝，请稍后再试")):
            data = main.update_check(force=1, _user="t")
        self.assertFalse(data["ok"])
        self.assertIn("限流", data["detail"])
        self.assertEqual(data["current"], main.APP_VERSION)

    def test_repo_accepts_full_url(self):
        os.environ["AURORA_REPO"] = "https://github.com/example/aurora.git/"
        self.assertEqual(main._github_repo(), "example/aurora")
        os.environ["AURORA_REPO"] = "https://github.com/example/aurora"
        self.assertEqual(main._github_repo(), "example/aurora")

    def test_missing_repo_disables_check(self):
        os.environ["AURORA_REPO"] = ""
        data = main.update_check(force=1, _user="t")
        self.assertFalse(data["ok"])
        self.assertIn("AURORA_REPO", data["detail"])


class HttpLayerTests(unittest.TestCase):
    """HTTP 层测试（TestClient）：认证依赖、登录限流、CSRF 中间件、
    会话撤销端点、改密全链路、回收站冲突——此前全部零覆盖。"""

    PASSWORD = "http-test-pass-123"

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        cls._old_pass = main._AUTH_PASS
        main._AUTH_PASS = cls.PASSWORD   # _consteq 在请求时读模块全局，可安全替换
        main._FAILS.clear()
        # 不进入上下文管理器：避免触发 startup 里启动的常驻调度线程
        cls.client = TestClient(main.app)

    @classmethod
    def tearDownClass(cls):
        main._AUTH_PASS = cls._old_pass
        main._FAILS.clear()

    def setUp(self):
        # 用例按字母序执行：改密用例会把模块级 _AUTH_PASS 改掉，若不恢复，
        # 后面所有需要登录的用例全部 401（真实踩过的顺序污染）
        main._AUTH_PASS = self.PASSWORD
        main._FAILS.clear()
        self.client.cookies.clear()

    def _login(self):
        r = self.client.post("/api/auth/login",
                             json={"username": main._AUTH_USER, "password": self.PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        return r

    def test_protected_endpoint_requires_auth(self):
        r = self.client.get("/api/metrics")
        self.assertEqual(r.status_code, 401)

    def test_login_sets_httponly_session_cookie(self):
        r = self._login()
        cookie = r.headers.get("set-cookie", "").lower()
        self.assertIn("aurora_sid=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["user"], main._AUTH_USER)

    def test_login_rate_limit_locks_then_recovers(self):
        for _ in range(main.FAIL_LIMIT):
            r = self.client.post("/api/auth/login",
                                 json={"username": main._AUTH_USER, "password": "definitely-wrong"})
            self.assertEqual(r.status_code, 401)
        # 达到上限后，正确密码也被锁定
        r = self.client.post("/api/auth/login",
                             json={"username": main._AUTH_USER, "password": self.PASSWORD})
        self.assertEqual(r.status_code, 429)
        main._FAILS.clear()   # 模拟锁定窗口（300s）过去
        r = self.client.post("/api/auth/login",
                             json={"username": main._AUTH_USER, "password": self.PASSWORD})
        self.assertEqual(r.status_code, 200)

    def test_cross_site_post_is_rejected(self):
        r = self.client.post("/api/auth/logout", headers={"sec-fetch-site": "cross-site"})
        self.assertEqual(r.status_code, 403)

    def test_session_revoke_endpoint_rules(self):
        self._login()
        rows = self.client.get("/api/auth/sessions").json()["sessions"]
        self.assertTrue(any(x["current"] for x in rows))
        current = next(x for x in rows if x["current"])
        # 当前会话必须走退出登录，不允许自撤销
        r = self.client.post("/api/auth/sessions/revoke", json={"id": current["id"]})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/auth/sessions/revoke", json={"id": "nonexistent"})
        self.assertEqual(r.status_code, 404)

    @unittest.skipIf(os.environ.get("AURORA_AUTH_PASS"), "密码由环境变量管理时禁用在线改密")
    def test_change_password_full_flow(self):
        self._login()
        r = self.client.post("/api/auth/password",
                             json={"current": "wrong-current-pass", "new": "brand-new-pass-456"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/auth/password",
                             json={"current": self.PASSWORD, "new": "brand-new-pass-456"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(main._AUTH_PASS, "brand-new-pass-456")
        self.client.cookies.clear()
        r = self.client.post("/api/auth/login",
                             json={"username": main._AUTH_USER, "password": self.PASSWORD})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/auth/login",
                             json={"username": main._AUTH_USER, "password": "brand-new-pass-456"})
        self.assertEqual(r.status_code, 200)

    def test_media_restore_conflict_returns_409(self):
        old = os.environ.get("AURORA_LOCAL_MOUNT")
        with tempfile.TemporaryDirectory() as td:
            os.environ["AURORA_LOCAL_MOUNT"] = td
            try:
                self._login()
                source = Path(td) / "movie.txt"
                source.write_text("v1")
                r = self.client.post("/api/media/delete", json={"path": "movie.txt"})
                self.assertEqual(r.status_code, 200, r.text)
                trash_id = r.json()["trash_id"]
                self.assertFalse(source.exists())
                source.write_text("v2")   # 原路径出现同名新文件
                r2 = self.client.post("/api/media/trash/restore", json={"path": trash_id})
                self.assertEqual(r2.status_code, 409)
                r3 = self.client.post("/api/media/trash/purge", json={"path": trash_id})
                self.assertEqual(r3.status_code, 200)
                self.assertEqual(providers.trash_list(), [])
            finally:
                if old is None:
                    os.environ.pop("AURORA_LOCAL_MOUNT", None)
                else:
                    os.environ["AURORA_LOCAL_MOUNT"] = old

    def test_metrics_falls_back_without_adapters(self):
        """qbit/rclone/jellyfin 都探测不到时，metrics 仍返回 200 并标注来源回退。"""
        self._login()
        r = self.client.get("/api/metrics")
        self.assertEqual(r.status_code, 200, r.text)
        sources = r.json().get("sources", {})
        self.assertIn(sources.get("torrents"), ("none", "qbittorrent"))
        self.assertEqual(sources.get("disk"), "system")

    def test_api_docs_are_hidden(self):
        r = self.client.get("/docs")
        # static 未构建时 FastAPI 兜底 404；构建后 SPA 兜底把未登录访问重定向到 /login
        self.assertIn(r.status_code, (404, 307))

    def test_health_exposes_version(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["version"])
        self.assertEqual(body["version"], main.APP_VERSION)

    def test_update_check_requires_auth(self):
        r = self.client.get("/api/update/check")
        self.assertEqual(r.status_code, 401)

    def test_rss_endpoints_require_auth(self):
        self.assertEqual(self.client.get("/api/rss/overview").status_code, 401)
        self.assertEqual(self.client.post("/api/rss/rules/save", json={"name": "x"}).status_code, 401)

    def test_rss_overview_echoes_destination_binding(self):
        # 规则上的 aurora-remote-* 标记必须反查成网盘目标回显，
        # 否则前端编辑保存会静默丢掉转存绑定
        marker = "aurora-remote-0123456789abcdef"
        old_dest_file = providers._TORRENT_DEST_FILE
        old_qbit = providers._qbit
        providers._atomic_json(providers._TORRENT_DEST_FILE, {
            marker: {"remote": "gdrive", "path": "/media", "status": "waiting",
                     "detail": "", "hash": "", "name": "", "local_path": "",
                     "transfer_id": "", "created": int(time.time()), "finished": 0},
        })

        class FakeQbit:
            def available(self):
                return True

            def rss_overview(self):
                return True, {"feeds": [], "rules": {
                    "r1": {"name": "r1", "enabled": True, "tags": [marker],
                           "affected_feeds": [], "category": "tv"},
                }}, ""

        providers._qbit = FakeQbit()
        try:
            self._login()
            r = self.client.get("/api/rss/overview")
            self.assertEqual(r.status_code, 200, r.text)
            rule = r.json()["rules"]["r1"]
            self.assertEqual(rule["destination_remote"], "gdrive")
            self.assertEqual(rule["destination_path"], "/media")
        finally:
            providers._qbit = old_qbit
            providers._TORRENT_DEST_FILE = old_dest_file

    def test_update_check_endpoint_uses_cache_not_network(self):
        # 端到端：登录后经 HTTP 调用，GitHub 结果被 mock，不依赖外网
        main._UPDATE_CACHE.clear()
        payload = {"tag": "v0.4.0", "latest": "0.4.0",
                   "url": "https://github.com/example/aurora/releases/tag/v0.4.0",
                   "published_at": "2026-10-01T00:00:00Z"}
        self._login()
        with patch.object(main, "_github_latest_release", return_value=payload):
            r = self.client.get("/api/update/check")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["current"], main.APP_VERSION)
        self.assertFalse(body["update_available"])
        main._UPDATE_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
