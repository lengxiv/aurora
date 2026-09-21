"""Aurora providers — real adapters that auto-engage when their service is up.

Each provider probes a live endpoint; if the backend service isn't running yet
it reports `available()==False` and the aggregator falls back to demo data.
Deploy the real service (rclone rc / qBittorrent / Jellyfin), point env vars at
it, and the matching section flips to real readings with no frontend change.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

# ---- infohash helpers (manual / demo queue) ----

import re as _re

_MANUAL: list[dict] = []


def add_magnet(magnet: str) -> dict:
    """Queue a magnet locally (demo path) or forward to a live qBittorrent."""
    name = "magnet-queue"
    m = _re.search(r"btih:([0-9a-fA-F]{40})", magnet)
    if m:
        name = f"magnet[{m.group(1)[:6]}…]"
    entry = {"id": f"x{len(_MANUAL) + 1}", "name": name, "state": "queued",
             "progress": 0, "speed": 0, "sizeGb": 0, "leechers": 0, "seeders": 0}
    _MANUAL.append(entry)
    _log("torrent.add", name)
    return entry


_LOG: list[dict] = []
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("AURORA_DATA_DIR", os.path.join(_PROJECT_DIR, "data"))
))
_LOG_FILE = os.path.join(_DATA_DIR, "activity.json")
_TRASH_FILE = os.path.join(_DATA_DIR, "trash.json")
_DATA_LOCK = threading.RLock()


def _atomic_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


try:
    with open(_LOG_FILE) as _f:
        _LOG = json.load(_f)[-200:]
except Exception:
    _LOG = []


def _log(event: str, detail: str = ""):
    with _DATA_LOCK:
        _LOG.append({"ts": int(time.time()), "event": event, "detail": detail})
        if len(_LOG) > 200:
            _LOG[:] = _LOG[-200:]
        try:
            _atomic_json(_LOG_FILE, _LOG)
        except Exception:
            pass


def logs(limit: int = 20):
    with _DATA_LOCK:
        return _LOG[-limit:][::-1]


def _trash_load() -> list[dict]:
    try:
        with open(_TRASH_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def trash_list() -> list[dict]:
    with _DATA_LOCK:
        return sorted(_trash_load(), key=lambda x: x.get("deleted", 0), reverse=True)


def trash_get(item_id: str) -> dict | None:
    with _DATA_LOCK:
        return next((x for x in _trash_load() if x.get("id") == item_id), None)


def trash_add(item_id: str, path: str, name: str, size: int) -> None:
    with _DATA_LOCK:
        rows = _trash_load()
        rows.append({"id": item_id, "path": path, "name": name, "size": size, "deleted": int(time.time())})
        _atomic_json(_TRASH_FILE, rows)


def trash_remove(item_id: str) -> None:
    with _DATA_LOCK:
        _atomic_json(_TRASH_FILE, [x for x in _trash_load() if x.get("id") != item_id])


def mutate(tid: str, action: str) -> bool:
    """Operating on the local (demo/manual) queue: remove / pause / resume."""
    for i, m in enumerate(_MANUAL):
        if m["id"] == tid:
            if action == "remove":
                _MANUAL.pop(i); _log("torrent.remove", m["name"])
            elif action == "pause":
                m["state"] = "paused"; _log("torrent.pause", m["name"])
            elif action == "resume":
                m["state"] = "queued"; _log("torrent.resume", m["name"])
            else:
                return False
            return True
    return False


# ---------------------------------------------------------------------------
# demo fixtures: intentionally EMPTY — no fabricated data. Real adapters only.



def _demo_mounts():
    return []


def _demo_torrents():
    return []


def _demo_streams():
    return []


# ---------------------------------------------------------------------------
# http helper


def _http(url, headers=None, timeout=3.0, decode=True, method=None, auth=None, data=None):
    body = None
    if data is not None:
        body = data.encode() if isinstance(data, str) else data
        method = method or "POST"
        headers = dict(headers or {})
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, headers=headers or {}, method=method, data=body)
    if auth:
        import base64
        token = base64.b64encode(auth.encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp_body = resp.read()
    return json.loads(resp_body) if decode else resp_body


def _probe(url, headers=None, timeout=2.5, method=None, auth=None):
    try:
        req = urllib.request.Request(url, headers=headers or {}, method=method)
        if auth:
            import base64
            token = base64.b64encode(auth.encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except Exception:
        return None


# ---------------------------------------------------------------------------
# System: disk + bandwidth (real, always)


class SystemProvider:
    name = "system"

    def __init__(self):
        self._last = {"net_in": None, "net_out": None, "ts": 0.0, "rd": None, "wr": None, "bts": 0.0, "bw": (0.0, 0.0)}

    def disk(self) -> dict:
        u = shutil.disk_usage("/")
        rd, wr = self._io_rates()
        return {"usedGb": round(u.used / 1073741824, 1), "capGb": round(u.total / 1073741824, 1), "rw": rd + wr}

    def _io_rates(self):
        now = time.time()
        rd = wr = 0
        try:
            with open("/proc/diskstats") as f:
                tr = tw = 0
                for line in f:
                    p = line.split()
                    if len(p) > 13 and p[2].startswith(("sd", "vd", "nvme", "mmc")):
                        tr += int(p[5]) * 512
                        tw += int(p[9]) * 512
            if self._last["rd"] is not None:
                dt = max(now - self._last["ts"], 1e-3)
                rd = max(0, (tr - self._last["rd"]) / dt)
                wr = max(0, (tw - self._last["wr"]) / dt)
            self._last["rd"], self._last["wr"], self._last["ts"] = tr, tw, now
        except Exception:
            pass
        return rd, wr

    def bandwidth(self) -> dict:
        now = time.time()
        try:
            with open("/proc/net/dev") as f:
                next(f)
                tin = tout = 0
                for line in f:
                    if ":" not in line:
                        continue
                    dev = line.split(":", 1)[0].strip()
                    if dev.startswith(("lo", "docker", "veth", "vb-", "ifb", "tun", "tap", "br-", "virbr")):
                        continue
                    if not dev.startswith(("eth", "enp", "ens", "eno", "en")):
                        continue
                    parts = line.split(":", 1)[1].split()
                    tin += int(parts[0])
                    tout += int(parts[8])
            if self._last["net_in"] is not None:
                dt = now - self._last.get("bts", now)
                if dt >= 1.0:
                    b_in = max(0, (tin - self._last["net_in"]) / dt) * 8 / 1e6
                    b_out = max(0, (tout - self._last["net_out"]) / dt) * 8 / 1e6
                    self._last["bw"] = (b_in, b_out)
                else:
                    b_in, b_out = self._last.get("bw", (0.0, 0.0))
            else:
                b_in = b_out = 0.0
            self._last["net_in"], self._last["net_out"], self._last["bts"] = tin, tout, now
            return {"in": round(b_in, 2), "out": round(b_out, 2)}
        except Exception:
            return {"in": 0.0, "out": 0.0}


# ---------------------------------------------------------------------------
# rclone (remote control)

_QBIT_STATES = {
    "downloading": "downloading", "stalledDL": "downloading", "forcedDL": "downloading",
    "uploading": "seeding", "stalledUP": "seeding", "forcedUP": "seeding", "stoppedUP": "seeding",
    "queuedDL": "queued", "queuedUP": "queued",
    "error": "error", "missingFiles": "error", "unknown": "error",
    "pausedUP": "done", "pausedDL": "done", "checkingDL": "done", "checkingUP": "done",
    "stoppedUP": "paused", "stoppedDL": "paused",   # qBittorrent 5.x stop/start
}


class RcloneProvider:
    name = "rclone"
    driver = "rclone"

    def __init__(self):
        self.base = os.environ.get("AURORA_RCLONE_RC", "http://127.0.0.1:5572").rstrip("/")
        # rc 启用 Basic Auth 时提供凭据（用户:密码，纯回环仍建议开，公网反代必须开）
        self.auth = os.environ.get("AURORA_RCLONE_RC_AUTH", "")

    def _req(self, path, **kw):
        return _http(f"{self.base}{path}", auth=self.auth or None, **kw)

    def available(self) -> bool:
        # rclone rcd 的 rc API 只接受 POST（--rc-serve 仅 serve 命令支持）
        return _probe(f"{self.base}/core/version", method="POST", auth=self.auth or None) == 200

    def mounts(self):
        remotes = self._req("/config/listremotes", timeout=3.0, method="POST")
        remotes = [r.rstrip(":") for r in (remotes or {}).get("remotes", [])]
        try:
            stats = self._req("/core/stats", timeout=3.0, method="POST")
            reads = int((stats or {}).get("transfers", 0))
        except Exception:
            reads = 0
        out = []
        for i, r in enumerate(remotes):
            meta = {"name": r, "type": ""}
            try:
                meta = self._req("/config/get", timeout=3.0, method="POST", data=f"name={urllib.parse.quote(r)}")
            except Exception:
                pass
            out.append({
                "id": f"r{i}", "name": r, "driver": "rclone",
                "provider": r, "type": (meta or {}).get("type", ""),
                "usedGb": 0, "capGb": 0, "status": "online",
                "reads": 0, "latencyMs": None,
            })
        return out or _demo_mounts()

    def remotes(self) -> list[dict]:
        """可视化配置用：remote 列表 + 类型 + 简单探活。"""
        try:
            data = self._req("/config/listremotes", timeout=3.0, method="POST") or {}
        except Exception:
            return []
        out = []
        for r in (data.get("remotes") or []):
            r = r.rstrip(":")
            typ = ""
            try:
                meta = self._req("/config/get", timeout=3.0, method="POST", data=f"name={urllib.parse.quote(r)}")
                typ = (meta or {}).get("type", "")
            except Exception:
                pass
            out.append({"name": r, "type": typ})
        return out

    def test_remote(self, name: str) -> tuple[bool, str, int]:
        """Read the remote root to verify credentials and connectivity."""
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name or ""):
            return False, "网盘名称无效", 0
        started = time.monotonic()
        try:
            import json as _json
            self._req(
                "/operations/list",
                timeout=8.0,
                method="POST",
                data=_json.dumps({"fs": f"{name}:", "remote": ""}),
                headers={"Content-Type": "application/json"},
            )
            return True, "根目录读取成功", round((time.monotonic() - started) * 1000)
        except Exception as e:
            detail = str(e).strip() or "远端无响应"
            return False, f"连接失败：{detail[:180]}", round((time.monotonic() - started) * 1000)

    def create_remote(self, name: str, ftype: str, params: dict) -> tuple[bool, str]:
        """rc config/create：name=名字&type=类型&参数。name 必须安全字符。"""
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name or ""):
            return False, "名字只允许字母数字-_"
        allowed_types = {
            "webdav", "s3", "alias", "crypt", "ftp", "sftp", "local", "http",
            "smb", "drive", "onedrive", "aliyundrive_open", "dropbox", "box",
        }
        if ftype not in allowed_types:
            return False, f"暂不支持的类型 {ftype}"
        body = {"name": name, "type": ftype}
        # 只透传白名单内的字符串参数，避免注入任意 rc 字段
        safe = {}
        for k, v in (params or {}).items():
            if k in ("url", "vendor", "provider", "endpoint", "region", "user",
                     "pass", "access_key_id", "secret_access_key", "acl",
                     "client_id", "client_secret", "token", "refresh_token",
                     "server", "share", "path", "root_folder_id", "chunk_size",
                     "upload_cutoff", "bucket"):
                safe[k] = str(v)[:512]
        body["parameters"] = safe
        try:
            import json as _json
            self._req("/config/create", timeout=6.0, method="POST",
                      data=_json.dumps(body),
                      headers={"Content-Type": "application/json"})
            return True, ""
        except Exception as e:
            return False, str(e)

    def delete_remote(self, name: str) -> tuple[bool, str]:
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name or ""):
            return False, "bad name"
        try:
            self._req("/config/delete", timeout=5.0, method="POST", data=f"name={name}")
            return True, ""
        except Exception as e:
            return False, str(e)


class QbittorrentProvider:
    name = "qbittorrent"

    def __init__(self):
        self.base = os.environ.get("AURORA_QBIT_URL", "http://127.0.0.1:8080").rstrip("/")
        self.user = os.environ.get("AURORA_QBIT_USER", "")
        self.pw = os.environ.get("AURORA_QBIT_PASS", "")

    def _ensure(self):
        if getattr(self, "_sess", None) is None:
            import requests
            s = requests.Session()
            if self.user:
                r = s.post(f"{self.base}/api/v2/auth/login",
                           data={"username": self.user, "password": self.pw}, timeout=4)
                if r.status_code >= 300:
                    raise RuntimeError("qbit auth failed")
            self._sess = s
        return self._sess

    def available(self) -> bool:
        """Probe qBittorrent. If the cached session is stale (qbit restarted /
        cookie expired), drop it and rebuild so we don't silently fall back to
        demo data until the whole service is restarted."""
        for _ in range(2):
            try:
                s = self._ensure()
                if s.get(f"{self.base}/api/v2/app/version", timeout=4).status_code == 200:
                    return True
                self._sess = None            # stale/reused connection -> force re-login
            except Exception:
                self._sess = None
        return False

    def torrents(self):
        out = []
        try:
            s = self._ensure()
            data = s.get(f"{self.base}/api/v2/torrents/info", timeout=5).json()
        except Exception:
            data = []
        for t in data or []:
            st = _QBIT_STATES.get(t.get("state", "unknown"), "done")
            out.append({
                "id": t.get("hash", ""), "name": t.get("name", "?"), "state": st,
                "progress": t.get("progress", 0),
                "speed": round(t.get("dlspeed", 0) / 1e6, 2),
                "sizeGb": round(t.get("size", 0) / 1073741824, 2),
                "leechers": t.get("num_leechs", 0), "seeders": t.get("num_seeds", 0),
                "upspeed": round(t.get("upspeed", 0) / 1e6, 2),
                "ratio": round(t.get("ratio", 0), 2),
                "upGb": round(t.get("uploaded", 0) / 1073741824, 2),
                "downGb": round(t.get("downloaded", 0) / 1073741824, 2),
                "conns": t.get("connections_count", 0),
                "hash": t.get("hash", ""),
                "completion_on": t.get("completion_on", 0) or 0,
                "upB": t.get("uploaded", 0) or 0,
                "downB": t.get("downloaded", 0) or 0,
            })
        return out

    def peers(self, hash_):
        """当前连到这个种子的对等方（谁在从我们这里下载）。"""
        try:
            s = self._ensure()
            r = s.get(f"{self.base}/api/v2/sync/torrentPeers", params={"hash": hash_}, timeout=5)
            d = r.json()
        except Exception:
            return {"peers": [], "connected": 0, "seeds": 0, "leechers": 0}
        out = []
        for ip, p in (d.get("peers") or {}).items():
            out.append({
                "ip": ip,
                "client": p.get("client", "?"),
                "country": p.get("country", ""),
                "country_code": p.get("country_code", ""),
                "progress": round(p.get("progress", 0), 3),
                "up_speed": p.get("up_speed", 0),       # 从我们这里拉取 B/s
                "down_speed": p.get("down_speed", 0),   # 给我们的 B/s
                "uploaded": p.get("uploaded", 0),       # 累计从我们这里拉取的 B
                "downloaded": p.get("downloaded", 0),   # 累计给我们的 B
            })
        out.sort(key=lambda x: x["uploaded"], reverse=True)
        return {
            "peers": out,
            "connected": d.get("peers_connected", len(out)),
            "seeds": d.get("peers_seeds", 0),
            "leechers": d.get("peers_leechers", 0),
        }

    def add(self, magnet: str) -> bool:
        try:
            if not magnet.startswith("magnet:"):
                return False
            s = self._ensure()
            r = s.post(f"{self.base}/api/v2/torrents/add", data={"urls": magnet}, timeout=6)
            return r.status_code in (200, 201)   # 409=已存在/无效 -> False
        except Exception:
            return False

    def add_file(self, filename: str, content: bytes) -> bool:
        """Forward one .torrent file to qBittorrent's multipart upload endpoint."""
        try:
            if not filename.lower().endswith(".torrent") or not content:
                return False
            s = self._ensure()
            r = s.post(
                f"{self.base}/api/v2/torrents/add",
                files={"torrents": (filename, content, "application/x-bittorrent")},
                timeout=15,
            )
            return r.status_code in (200, 201)
        except Exception:
            return False

    def action(self, hash_, action):
        try:
            # qBittorrent 5.x removed pause/resume -> use stop/start
            cmd = {"remove": "delete", "pause": "stop", "resume": "start"}.get(action)
            if not cmd:
                return False
            s = self._ensure()
            # qbit 5.x delete REQUIRES deleteFiles param (400 "Missing required parameters" without it)
            # 仅移除任务、保留已下载文件（前端提示语如此），故 deleteFiles=false
            data = {"hashes": hash_, "deleteFiles": "false"} if cmd == "delete" else {"hashes": hash_}
            r = s.post(f"{self.base}/api/v2/torrents/{cmd}", data=data, timeout=5)
            return r.status_code == 200          # 404/400=hash 无效 -> False
        except Exception:
            return False


    _UP_STATES = {"uploading", "stalledUP", "forcedUP", "queuedUP", "stoppedUP", "pausedUP"}

    def seeding_files(self) -> set[str]:
        """做种相关（含暂停）torrent 引用的宿主文件绝对路径集合，供媒资库毁种提示。"""
        try:
            s = self._ensure()
            info = s.get(f"{self.base}/api/v2/torrents/info", timeout=5).json() or []
        except Exception:
            return set()
        base = os.environ.get("AURORA_LOCAL_MOUNT", "/opt/aurora/qbit/downloads")
        out = set()
        for t in info:
            if t.get("state") not in self._UP_STATES:
                continue
            h = t.get("hash", "")
            if not h:
                continue
            try:
                files = s.get(f"{self.base}/api/v2/torrents/files", params={"hash": h}, timeout=5).json() or []
            except Exception:
                continue
            sp = (t.get("save_path") or "/downloads").rstrip("/")
            rel_dir = sp[len("/downloads"):].lstrip("/") if sp.startswith("/downloads") else ""
            for f in files:
                name = (f or {}).get("name", "")
                if not name:
                    continue
                out.add(os.path.normpath(os.path.join(base, rel_dir, name)))
        return out


    def seed_map(self) -> dict[str, str]:
        """宿主文件绝对路径 -> 所属做种种子 hash，供媒资库联动移动。"""
        try:
            s = self._ensure()
            info = s.get(f"{self.base}/api/v2/torrents/info", timeout=5).json() or []
        except Exception:
            return {}
        base = os.environ.get("AURORA_LOCAL_MOUNT", "/opt/aurora/qbit/downloads")
        out = {}
        for t in info:
            if t.get("state") not in self._UP_STATES:
                continue
            h = t.get("hash", "")
            if not h:
                continue
            try:
                files = s.get(f"{self.base}/api/v2/torrents/files", params={"hash": h}, timeout=5).json() or []
            except Exception:
                continue
            sp = (t.get("save_path") or "/downloads").rstrip("/")
            rel_dir = sp[len("/downloads"):].lstrip("/") if sp.startswith("/downloads") else ""
            for f in files:
                name = (f or {}).get("name", "")
                if not name:
                    continue
                out[os.path.normpath(os.path.join(base, rel_dir, name))] = h
        return out

    def move_seed(self, hash_: str, location: str) -> bool:
        """整种子移动到容器内目录（qbit 自己搬文件并更新路径，保持做种）。"""
        try:
            s = self._ensure()
            r = s.post(f"{self.base}/api/v2/torrents/setLocation",
                       data={"hashes": hash_, "location": location}, timeout=10)
            return r.status_code == 200
        except Exception:
            return False

class JellyfinProvider:
    name = "jellyfin"

    def __init__(self):
        self.base = os.environ.get("AURORA_JELLYFIN", "http://127.0.0.1:8096").rstrip("/")
        self.token = os.environ.get("AURORA_JELLYFIN_TOKEN", "")

    def available(self) -> bool:
        return _probe(f"{self.base}/System/Info/Public") == 200

    def streams(self):
        if not self.token:
            return []
        h = {"X-Emby-Token": self.token, "Accept": "application/json"}
        sessions = _http(f"{self.base}/Sessions", h, timeout=4.0)
        out, i = [], 0
        for s in sessions or []:
            if not s.get("NowPlayingItem"):
                continue
            item = s.get("NowPlayingItem", {})
            i += 1
            out.append({
                "id": f"j{i}", "title": item.get("Name", item.get("SeriesName", "?")),
                "source": "jellyfin", "client": s.get("Client") or s.get("DeviceName") or "web",
                "bitrate": round((item.get("MediaStreams") or [{}])[0].get("BitRate", 0) / 1e6, 1)
                if item.get("MediaStreams") else 0,
                "devices": 1,
                "status": "live" if s.get("PlayState", {}).get("IsPaused") is False else "buffering",
            })
        return out

    def library(self):
        """海报墙：电影 + 剧集条目（含海报、年份、本地路径）。"""
        if not self.token:
            return []
        h = {"X-Emby-Token": self.token, "Accept": "application/json"}
        params = {
            "Recursive": "true", "IncludeItemTypes": "Movie",
            "Fields": "PrimaryImageAspectRatio,Overview,Path", "ImageTypeLimit": "1",
            "EnableImageTypes": "Primary", "SortBy": "SortName",
        }
        import urllib.parse
        qs = urllib.parse.urlencode(params)
        try:
            data = _http(f"{self.base}/Items?{qs}", h, timeout=6.0)
        except Exception:
            return []
        items = data.get("Items", []) if isinstance(data, dict) else []
        out = []
        for it in items:
            tags = it.get("ImageTags") or {}
            primary = it.get("PrimaryImageTag") or tags.get("Primary") or ""
            path = it.get("Path", "") or ""
            local = path[len("/media/"):] if path.startswith("/media/") else ""
            out.append({
                "id": it.get("Id", ""),
                "name": it.get("Name", "?"),
                "type": it.get("Type", ""),
                "year": it.get("ProductionYear"),
                "overview": (it.get("Overview") or "")[:160],
                "hasImage": bool(primary),
                "primaryTag": primary,
                "localPath": local,
                "path": path,
            })
        return out


# ---------------------------------------------------------------------------
# aggregator

class LocalMountProvider:
    name = "local"
    driver = "local"

    def __init__(self):
        self.dir = os.environ.get("AURORA_LOCAL_MOUNT", "/opt/aurora/qbit/downloads")
        # 独立采样器：不要复用全局 _sys 的 _io_rates()，否则与 SystemProvider.disk()
        # 在同一轮 metrics 内互相污染 last 状态（第二次调用的 dt 只有毫秒级，
        # 会把速率极端放大到 100+ MB/s）。
        self._io = SystemProvider()

    def available(self) -> bool:
        return os.path.isdir(self.dir)

    def mounts(self):
        u = shutil.disk_usage(self.dir)
        # 真实读速率（B/s）：独立 SystemProvider 采样 /proc/diskstats；
        # 本地盘无 rclone 的 latency/transfer 语义 → reads 返回真实字节速率, latencyMs 置 None 由前端显示 —
        rd, _wr = self._io._io_rates()
        return [{
            "id": "local0",
            "name": os.path.basename(self.dir) or "local",
            "driver": "local",
            "provider": "本机磁盘",
            "usedGb": round(u.used / 1073741824, 1),
            "capGb": round(u.total / 1073741824, 1),
            "status": "online",
            "reads": round(rd),
            "latencyMs": None,
        }]


# ---------------------------------------------------------------------------


_sys = SystemProvider()
_rclone = RcloneProvider()
_qbit = QbittorrentProvider()
_jelly = JellyfinProvider()
_local = LocalMountProvider()

# ---------------------------------------------------------------------------
# notifications + daily stats

_STATS_FILE = os.path.join(_DATA_DIR, "stats.json")
_SETTINGS_FILE = os.path.join(_DATA_DIR, "settings.json")
_NOTIFY_FILE = os.path.join(_DATA_DIR, "torrent_notify.json")
_disk_warned = False


def _default_settings() -> dict:
    tok = os.environ.get("AURORA_TG_BOT_TOKEN", "")
    chat = os.environ.get("AURORA_TG_CHAT_ID", "")
    tokens = [{"name": "主", "token": tok, "chat_id": chat}] if tok and chat else []
    return {
        "alerts": {"torrent": True, "disk": True, "diskWarn": 90.0},
        "daily": {"enabled": False, "time": "21:00"},
        "tg": {"enabled": bool(tokens), "tokens": tokens},
    }


def load_settings() -> dict:
    d = _default_settings()
    try:
        with open(_SETTINGS_FILE) as f:
            s = json.load(f)
        for k in d:
            if k in s and isinstance(s[k], dict):
                d[k].update({kk: vv for kk, vv in s[k].items() if kk in d[k]})
        if isinstance(s.get("tg", {}).get("tokens"), list):
            d["tg"]["tokens"] = s["tg"]["tokens"]
    except Exception:
        pass
    return d


def save_settings(s: dict):
    d = _default_settings()
    for k in d:
        if k in s and isinstance(s[k], dict):
            d[k].update({kk: vv for kk, vv in s[k].items() if kk in d[k]})
    if isinstance(s.get("tg", {}).get("tokens"), list):
        d["tg"]["tokens"] = s["tg"]["tokens"]
    try:
        d["alerts"]["diskWarn"] = min(99.0, max(10.0, float(d["alerts"]["diskWarn"])))
    except (TypeError, ValueError):
        d["alerts"]["diskWarn"] = 90.0
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(d["daily"].get("time", ""))):
        d["daily"]["time"] = "21:00"
    clean_tokens = []
    for token in d["tg"].get("tokens", [])[:2]:
        if not isinstance(token, dict):
            continue
        clean_tokens.append({
            "name": str(token.get("name", ""))[:20],
            "token": str(token.get("token", ""))[:256],
            "chat_id": str(token.get("chat_id", ""))[:128],
        })
    d["tg"]["tokens"] = clean_tokens
    with _DATA_LOCK:
        _atomic_json(_SETTINGS_FILE, d)
    return d


def _tg_send(token: str, chat_id: str, text: str):
    try:
        import requests
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text}, timeout=8)
        if r.status_code == 200:
            return True, ""
        try:
            desc = r.json().get("description") or f"HTTP {r.status_code}"
        except Exception:
            desc = f"HTTP {r.status_code}"
        return False, desc
    except Exception as e:
        return False, str(e)


def _tg(msg: str) -> bool:
    st = load_settings().get("tg", {})
    if not st.get("enabled"):
        return False
    sent = False
    for t in st.get("tokens", []):
        tok = t.get("token", ""); chat = t.get("chat_id", "")
        if tok and chat:
            ok, _detail = _tg_send(tok, chat, msg)
            sent = sent or ok
    return sent


def _load_notify_state() -> dict:
    try:
        with open(_NOTIFY_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_notify_state(st: dict):
    try:
        with _DATA_LOCK:
            _atomic_json(_NOTIFY_FILE, st)
    except Exception:
        pass


def _check_torrent_notify(torrents: list):
    if not load_settings().get("alerts", {}).get("torrent", True):
        return
    st = _load_notify_state()
    # 首次运行（修复上线）：用当前已完成的种子做基线，只记录不补发，避免老种子轰炸
    baseline = not st
    cur = {t["id"]: t for t in torrents}
    for tid, t in cur.items():
        done_ts = t.get("completion_on") or 0
        if done_ts > 0:
            prev = st.get(tid)
            if not baseline and prev != done_ts:
                _log("torrent.done", t["name"])
                _tg(f"下载完成：{t['name']}")
            st[tid] = done_ts
        elif t.get("state") == "error" and st.get(tid) != "error":
            _log("torrent.error", t["name"])
            _tg(f"下载失败：{t['name']}")
            st[tid] = "error"
    # 清掉已删除种子的记录（重加同种子时 completion_on 会变，仍能触发通知）
    st = {k: v for k, v in st.items() if k in cur}
    _save_notify_state(st)


# ---------------------------------------------------------------------------
# 每日做种/上传日报（定时推送 TG）

_SNAP_FILE = os.path.join(_DATA_DIR, "torrent_daily.json")


def _load_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _fmt_bytes(b: float) -> str:
    if b >= 1073741824:
        return f"{b / 1073741824:.2f} GB"
    if b >= 1048576:
        return f"{b / 1048576:.0f} MB"
    return f"{int(b // 1024)} KB"


def _build_daily_text(torrents: list, now: datetime) -> str:
    snap = _load_json(_SNAP_FILE, {})
    prev_date = snap.get("date")
    prev = snap.get("seeds", {})
    today = now.strftime("%Y-%m-%d")
    rows = []
    today_up = 0
    for t in torrents:
        upB = t.get("upB", 0) or 0
        old = prev.get(t["id"], {}).get("uploaded") if prev_date else None
        delta = max(0, upB - old) if old is not None else 0
        today_up += delta
        rows.append({
            "name": t.get("name", "?"), "delta": delta,
            "up": t.get("upB", 0) or 0, "ratio": t.get("ratio", 0) or 0,
            "state": t.get("state", ""),
        })
    rows.sort(key=lambda r: r["delta"], reverse=True)
    seeding = [r for r in rows if r["state"] == "seeding"]
    up_rate = sum(t.get("upspeed", 0) or 0 for t in torrents) * 1048576  # MB/s -> B/s
    total_up = sum(t.get("upB", 0) or 0 for t in torrents)
    total_down = sum(t.get("downB", 0) or 0 for t in torrents)
    if total_down > 0:
        ratio_s = f"{total_up / total_down:.2f}"
    elif total_up > 0:
        ratio_s = "∞"
    else:
        ratio_s = "—"
    line = []
    line.append(f"每日做种日报 · {now.strftime('%m-%d')}")
    line.append("────────────────")
    if seeding:
        line.append(f"做种 {len(seeding)} 个 · 分享率 {ratio_s} · 今日上传 {_fmt_bytes(today_up)}")
    else:
        line.append("当前无做种任务")
    line.append(f"当前上传 {_fmt_bytes(up_rate)}/s · 累计上传 {_fmt_bytes(total_up)}")
    line.append("")
    for r in rows[:10]:
        line.append(f"● {r['name'][:40]}")
        line.append(f"  今日 +{_fmt_bytes(r['delta'])} · 累计 {_fmt_bytes(r['up'])} · 分享率 {r['ratio']:.1f}")
    stats = _load_json(_STATS_FILE, {})
    rec = stats.get(today, {})
    du = shutil.disk_usage("/")
    line.append("")
    line.append(f"带宽(整机) 今日出 {rec.get('out_gb', 0):.2f} GB / 入 {rec.get('in_gb', 0):.2f} GB")
    line.append(f"磁盘 已用 {du.used / du.total * 100:.0f}% · 可用 {_fmt_bytes(du.free)}")
    return "\n".join(line)


def _send_daily_report(now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now()
    st = load_settings().get("daily", {})
    if not st.get("enabled"):
        return False, "日报未开启"
    snap = _load_json(_SNAP_FILE, {})
    today = now.strftime("%Y-%m-%d")
    if snap.get("date") == today:
        return False, "今日已发送"
    torrents = _qbit.torrents() if _qbit.available() else []
    text = _build_daily_text(torrents, now)
    if not _tg(text):
        return False, "Telegram 发送失败"
    # 发送成功后更新上传基线 + 记录日期（防重复）
    seeds = {t["id"]: {"name": t.get("name", "?"), "uploaded": t.get("upB", 0) or 0} for t in torrents}
    with _DATA_LOCK:
        _atomic_json(_SNAP_FILE, {"date": today, "seeds": seeds})
    n_seed = sum(1 for t in torrents if t.get("state") == "seeding")
    _log("report.daily", f"做种 {n_seed} 个")
    return True, ""


def _daily_due(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    st = load_settings().get("daily", {})
    if not st.get("enabled"):
        return False
    try:
        hh, mm = (st.get("time") or "21:00").split(":")
        target = int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        target = 21 * 60
    cur = now.hour * 60 + now.minute
    # 过了约定时间且今天尚未发送即补发（不再要求整点后 5 分钟窗口）
    if cur < target:
        return False
    snap = _load_json(_SNAP_FILE, {})
    return snap.get("date") != now.strftime("%Y-%m-%d")


_sched_started = False


def start_daily_scheduler():
    global _sched_started
    if _sched_started:
        return
    _sched_started = True

    def loop():
        while True:
            try:
                if _daily_due():
                    ok, err = _send_daily_report()
                    if not ok:
                        _log("report.daily.skip", err or "未发送")
            except Exception as e:
                _log("report.daily.error", str(e))
            time.sleep(30)

    threading.Thread(target=loop, daemon=True).start()


def _check_disk_warn(used_gb: float, cap_gb: float):
    global _disk_warned
    st = load_settings().get("alerts", {})
    if not st.get("disk", True):
        return
    try:
        thr = float(st.get("diskWarn", 90.0))
    except (TypeError, ValueError):
        thr = 90.0
    pct = used_gb / cap_gb * 100 if cap_gb else 0
    if pct >= thr and not _disk_warned:
        _disk_warned = True
        _log("disk.warn", f"{pct:.0f}%"); _tg(f"磁盘告警：已用 {pct:.0f}% ({used_gb:.0f}/{cap_gb:.0f} GB)")
    elif pct < thr - 5:
        _disk_warned = False


def _load_stats() -> dict:
    try:
        with open(_STATS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_stats(data: dict):
    with _DATA_LOCK:
        _atomic_json(_STATS_FILE, data)


def _net_tot() -> tuple:
    tin = tout = 0
    try:
        with open("/proc/net/dev") as f:
            next(f)
            for line in f:
                if ":" not in line:
                    continue
                dev = line.split(":", 1)[0].strip()
                if dev.startswith(("lo", "docker", "veth", "vb-", "ifb", "tun", "tap", "br-", "virbr")):
                    continue
                if not dev.startswith(("eth", "enp", "ens", "eno", "en")):
                    continue
                p = line.split(":", 1)[1].split()
                tin += int(p[0]); tout += int(p[8])
    except Exception:
        pass
    return tin, tout


_last_net_tot: tuple | None = None
_stats_cache: dict | None = None
_last_stats_persist = 0.0


def _record_stats():
    global _last_net_tot, _stats_cache, _last_stats_persist
    day = time.strftime("%Y-%m-%d")
    tot = _net_tot()
    if _stats_cache is None:
        _stats_cache = _load_stats()
    rec = _stats_cache.setdefault(day, {"in_gb": 0.0, "out_gb": 0.0, "disk_gb": 0.0})
    if _last_net_tot is not None and tot[0] >= _last_net_tot[0] and tot[1] >= _last_net_tot[1]:
        rec["in_gb"] += (tot[0] - _last_net_tot[0]) / 1073741824
        rec["out_gb"] += (tot[1] - _last_net_tot[1]) / 1073741824
    _last_net_tot = tot
    rec["disk_gb"] = round(shutil.disk_usage("/").used / 1073741824, 1)
    if len(_stats_cache) > 31:
        _stats_cache = {k: _stats_cache[k] for k in sorted(_stats_cache.keys())[-31:]}
    # 内存累计，最多每 60s 落盘一次，避免 metrics 每 4s 全量写文件的写放大
    now = time.time()
    if now - _last_stats_persist >= 60:
        try:
            _save_stats(_stats_cache)
            _last_stats_persist = now
        except Exception:
            pass


def stats(limit: int = 14) -> list:
    data = _stats_cache if _stats_cache is not None else _load_stats()
    days = sorted(data.keys())[-limit:]
    return [{"date": d, **data[d]} for d in days]


_METRICS_LOCK = threading.Lock()
_METRICS_CACHE: dict | None = None
_METRICS_CACHE_TS = 0.0


def metrics() -> dict:
    global _METRICS_CACHE, _METRICS_CACHE_TS
    now = time.monotonic()
    with _METRICS_LOCK:
        if _METRICS_CACHE is not None and now - _METRICS_CACHE_TS < 1.0:
            return _METRICS_CACHE
        result = _metrics_uncached()
        _METRICS_CACHE = result
        _METRICS_CACHE_TS = time.monotonic()
        return result


def _metrics_uncached() -> dict:
    _use = {}
    mounts = None
    if _rclone.available():
        try:
            mounts, _use["mounts"] = _rclone.mounts(), "rclone"
        except Exception:
            mounts = None
    # rclone 在线但未配置任何 remote 时回退本地盘（不遮蔽现有挂载）
    if not mounts and _local.available():
        try:
            mount = _local.mounts()
            if mount:
                mounts, _use["mounts"] = mount, "local"
        except Exception:
            mounts = None
    if mounts is None:
        mounts, _use["mounts"] = _demo_mounts(), "none"

    torrents = None
    if _qbit.available():
        try:
            torrents, _use["torrents"] = _qbit.torrents(), "qbittorrent"
        except Exception:
            torrents = None
    if torrents is None:
        torrents, _use["torrents"] = _demo_torrents(), "none"
    if _MANUAL:
        torrents = (torrents or []) + list(_MANUAL)

    streams = None
    if _jelly.available():
        try:
            streams, _use["streams"] = _jelly.streams(), "jellyfin"
        except Exception:
            streams = None
    if streams is None:
        streams, _use["streams"] = _demo_streams(), "none"

    disk = _sys.disk()
    _record_stats()
    _check_torrent_notify(torrents)
    _check_disk_warn(disk["usedGb"], disk["capGb"])
    return {
        "mounts": mounts,
        "torrents": torrents,
        "streams": streams,
        "disk": disk,
        "bandwidth": _sys.bandwidth(),
        "sources": {**_use, "disk": "system", "bandwidth": "system"},
    }
