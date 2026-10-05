"""Aurora providers — real adapters that auto-engage when their service is up.

Each provider probes a live endpoint; if the backend service isn't running yet
it reports `available()==False` and the aggregator falls back to demo data.
Deploy the real service (rclone rc / qBittorrent / Jellyfin), point env vars at
it, and the matching section flips to real readings with no frontend change.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta


_LOGGER = logging.getLogger(__name__)

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
_TORRENT_DEST_FILE = os.path.join(_DATA_DIR, "torrent_destinations.json")
_DATA_LOCK = threading.RLock()
_TORRENT_DEST_LOCK = threading.RLock()
_TORRENT_DEST_ORPHAN_GRACE = 60 * 60
_TORRENT_DEST_RETENTION = 7 * 24 * 60 * 60


def _atomic_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # tmp 名带上 pid/线程：多进程或多线程并发写同一状态文件时互不踩踏
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())   # 断电时避免 ext4 延迟分配留下 0 字节文件
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


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
        except Exception as exc:
            # Activity history is best-effort, but a permissions or disk error
            # must remain visible in journald instead of silently disappearing.
            _LOGGER.warning("activity log write failed: %s", exc)


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

_RCLONE_PARAM_KEYS = frozenset({
    "url", "vendor", "provider", "endpoint", "region", "user", "pass",
    "access_key_id", "secret_access_key", "acl", "client_id", "client_secret",
    "token", "refresh_token", "server", "share", "path", "root_folder_id",
    "chunk_size", "upload_cutoff", "bucket",
})
_RCLONE_SECRET_KEYS = frozenset({"pass", "secret_access_key", "client_secret", "token", "refresh_token"})
_RCLONE_TRANSFER_LIMIT = 100
_RCLONE_UPLOAD_LIMIT = 2 * 1024 * 1024 * 1024
_RCLONE_BUCKETS_FILE = os.path.join(_DATA_DIR, "rclone-buckets.json")
_RCLONE_TRANSFER_FILE = os.path.join(_DATA_DIR, "rclone-transfers.json")
_RCLONE_TERMINAL_STATUSES = frozenset({"done", "error", "canceled"})
_RCLONE_TRANSFER_RETENTION = 7 * 24 * 60 * 60
_RCLONE_STATUS_FAILURE_LIMIT = 30

_S3_PROVIDER_NAMES = {
    "aws": "AWS",
    "alibaba": "Alibaba",
    "aliyun": "Alibaba",
    "ceph": "Ceph",
    "cloudflare": "Cloudflare",
    "digitalocean": "DigitalOcean",
    "dreamhost": "Dreamhost",
    "huaweiobs": "HuaweiOBS",
    "ibmcos": "IBMCOS",
    "idrive": "IDrive",
    "ionos": "IONOS",
    "lyvecloud": "LyveCloud",
    "minio": "Minio",
    "netease": "Netease",
    "rackcorp": "RackCorp",
    "scaleway": "Scaleway",
    "seaweedfs": "SeaweedFS",
    "stackpath": "StackPath",
    "storj": "Storj",
    "tencentcos": "TencentCOS",
    "wasabi": "Wasabi",
    "qiniu": "Qiniu",
    "other": "Other",
}


def _canonical_s3_provider(value: str) -> str:
    raw = str(value or "").strip()
    return _S3_PROVIDER_NAMES.get(raw.casefold(), raw)


def _normalize_s3_params(params: dict) -> dict:
    """Normalize S3 values before handing them to rclone's config API."""
    normalized = dict(params or {})
    provider = _canonical_s3_provider(normalized.get("provider", ""))
    if provider:
        normalized["provider"] = provider
    endpoint = str(normalized.get("endpoint", "")).strip()
    if provider == "Cloudflare" and endpoint:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Cloudflare R2 端点不能包含 bucket 路径，请填写 r2.cloudflarestorage.com 地址")
        normalized["endpoint"] = endpoint.rstrip("/")
    return normalized


def _friendly_rclone_error(detail: str, *, write: bool = False) -> str:
    message = str(detail or "").strip()
    if "HeadObjectInput.Key" in message:
        return "S3/R2 目标路径为空，请先进入 bucket 目录后再上传"
    if "AccessDenied" in message or "status code: 403" in message:
        return "远端拒绝访问，请检查网盘权限"
    return message[:180]


def _friendly_remote_error(name: str, detail: str, *, write: bool = False) -> str:
    message = str(detail or "").strip()
    if write and _rclone_bucket(name) and (
        "HTTP Error 500" in message or "AccessDenied" in message or "status code: 403" in message
    ):
        return "R2 写入被拒绝，请为该 bucket 的 API Token 授予 Object Read & Write 权限"
    return _friendly_rclone_error(message, write=write)


def _clean_rclone_path(path: str, allow_empty: bool = True) -> str:
    """Normalize a remote-relative path without allowing traversal."""
    raw = str(path or "").replace("\\", "/")
    if "\x00" in raw or raw.startswith("/"):
        raise ValueError("网盘路径无效")
    parts = raw.split("/")
    if any(part == ".." for part in parts):
        raise ValueError("网盘路径无效")
    clean = posixpath.normpath("/".join(part for part in parts if part not in ("", ".")))
    if clean == ".":
        clean = ""
    if not allow_empty and not clean:
        raise ValueError("网盘路径不能为空")
    if len(clean) > 2048:
        raise ValueError("网盘路径过长")
    return clean


def _validate_rclone_bucket(bucket: str) -> str:
    value = str(bucket or "").strip()
    if not value or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}", value):
        raise ValueError("bucket 名称无效")
    return value


def _load_rclone_buckets() -> dict[str, str]:
    try:
        with open(_RCLONE_BUCKETS_FILE) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        result = {}
        for name, bucket in data.items():
            try:
                result[str(name)] = _validate_rclone_bucket(bucket)
            except (TypeError, ValueError):
                continue
        return result
    except Exception:
        return {}


def _rclone_bucket(name: str) -> str:
    with _DATA_LOCK:
        return _load_rclone_buckets().get(name, "")


def _set_rclone_bucket(name: str, bucket: str) -> bool:
    try:
        value = _validate_rclone_bucket(bucket) if bucket else ""
        with _DATA_LOCK:
            data = _load_rclone_buckets()
            if value:
                data[name] = value
            else:
                data.pop(name, None)
            _atomic_json(_RCLONE_BUCKETS_FILE, data)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _rclone_scoped_fs(name: str) -> str:
    """Use a configured S3 bucket as the remote root for bucket-scoped tokens."""
    return _rclone_fs(name, _rclone_bucket(name))


def _join_rclone_path(parent: str, child: str) -> str:
    child = str(child or "").replace("\\", "/")
    if not child or "/" in child or child in (".", ".."):
        raise ValueError("网盘文件名无效")
    return _clean_rclone_path(posixpath.join(parent, child), allow_empty=False)


def _rclone_fs(name: str, path: str = "") -> str:
    return f"{name}:{path}" if path else f"{name}:"

_QBIT_STATES = {
    "downloading": "downloading", "stalledDL": "stalled", "forcedDL": "downloading",
    "uploading": "seeding", "stalledUP": "seeding", "forcedUP": "seeding",
    "queuedDL": "queued", "queuedUP": "queued",
    "error": "error", "missingFiles": "error", "unknown": "error",
    "pausedUP": "done", "pausedDL": "done", "checkingDL": "done", "checkingUP": "done",
    "stoppedUP": "paused", "stoppedDL": "paused",   # qBittorrent 5.x stop/start
    # 4.x/5.x 其余过渡态：不能回退成 done，否则刚添加、正在拉元数据的种子会在 UI 显示"已完成"
    "metaDL": "downloading", "metadataDL": "downloading",
    "forcedMetaDL": "downloading", "forcedMetaDownload": "downloading",
    "allocating": "downloading", "moving": "queued", "checkingResumeData": "queued",
    "drunning": "downloading", "dstopped": "paused",
}


class RcloneProvider:
    name = "rclone"
    driver = "rclone"

    def __init__(self):
        self.base = os.environ.get("AURORA_RCLONE_RC", "http://127.0.0.1:5572/rclone").rstrip("/")
        # rc 启用 Basic Auth 时提供凭据（用户:密码，纯回环仍建议开，公网反代必须开）
        self.auth = os.environ.get("AURORA_RCLONE_RC_AUTH", "")
        # 探测失败退避：metrics 每 2s 轮询，rclone 掉线时若每次都打满探测超时，
        # 持锁的 metrics 计算会让所有并发请求排队
        self._down_until = 0.0
        self._transfer_lock = threading.RLock()
        self._transfer_jobs: dict[str, dict] = {}
        self._load_transfer_jobs()

    def _req(self, path, **kw):
        return _http(f"{self.base}{path}", auth=self.auth or None, **kw)

    def _load_transfer_jobs(self) -> None:
        """Restore the local view of transfers after an Aurora restart.

        rclone owns the actual job; this journal only preserves the correlation
        id, labels and retry request so the UI and torrent scheduler do not
        forget an in-flight task when the API process is restarted.
        """
        try:
            with open(_RCLONE_TRANSFER_FILE) as f:
                rows = json.load(f)
        except Exception:
            return
        if not isinstance(rows, list):
            return
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            job = dict(raw)
            job_id = str(job.get("id") or "")
            if not job_id or job.get("rcloneJobId") is None:
                continue
            status = str(job.get("status") or "running")
            if status not in _RCLONE_TERMINAL_STATUSES and status != "running":
                continue
            job["id"] = job_id
            job["status"] = status
            job.setdefault("created", int(time.time()))
            job.setdefault("finished", 0)
            job.setdefault("bytes", 0)
            job.setdefault("total", None)
            job.setdefault("speed", 0)
            job.setdefault("progress", None)
            job.setdefault("detail", "")
            job.setdefault("retryable", status in ("error", "canceled"))
            job.setdefault("status_failures", 0)
            self._transfer_jobs[job_id] = job

    def _persist_transfer_jobs_locked(self) -> bool:
        try:
            _atomic_json(_RCLONE_TRANSFER_FILE, list(self._transfer_jobs.values()))
            return True
        except Exception as exc:
            _LOGGER.warning("rclone transfer journal write failed: %s", exc)
            return False

    def available(self) -> bool:
        # rclone rcd 的 rc API 只接受 POST（--rc-serve 仅 serve 命令支持）
        if time.monotonic() < self._down_until:
            return False
        ok = _probe(f"{self.base}/core/version", method="POST", auth=self.auth or None) == 200
        # 只缓存"掉线"结论 15s：在线路径每次真实探测，恢复感知不受影响
        self._down_until = 0.0 if ok else time.monotonic() + 15.0
        return ok

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
            out.append({"name": r, "type": typ, "bucket": _rclone_bucket(r) if typ == "s3" else ""})
        return out

    def validate_destination(self, name: str, path: str = "") -> tuple[bool, str, str]:
        """Validate a remote destination before a torrent is accepted."""
        try:
            name, path = self._validate_name_path(name, path)
            data = self._req("/config/listremotes", timeout=5.0, method="POST") or {}
            remotes = {str(item).rstrip(":") for item in (data.get("remotes") or [])}
            if name not in remotes:
                return False, "", "目标网盘不存在"
            if not path and self._remote_type(name) == "s3" and not _rclone_bucket(name):
                return False, "", "S3/R2 目标请填写 bucket 名称或目录"
            return True, path, ""
        except Exception as e:
            return False, "", str(e).strip()[:180] or "网盘目标路径无效"

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
                data=_json.dumps({"fs": _rclone_scoped_fs(name), "remote": ""}),
                headers={"Content-Type": "application/json"},
            )
            return True, "根目录读取成功", round((time.monotonic() - started) * 1000)
        except Exception as e:
            detail = str(e).strip() or "远端无响应"
            return False, f"连接失败：{detail[:180]}", round((time.monotonic() - started) * 1000)

    def _validate_name_path(self, name: str, path: str = "", allow_empty: bool = True) -> tuple[str, str]:
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name or ""):
            raise ValueError("网盘名称无效")
        return name, _clean_rclone_path(path, allow_empty=allow_empty)

    def _remote_type(self, name: str) -> str:
        try:
            data = self._req(
                "/config/get", timeout=5.0, method="POST",
                data=f"name={urllib.parse.quote(name)}",
            ) or {}
            return str(data.get("type") or "").casefold()
        except Exception:
            return ""

    def list_files(self, name: str, path: str = "") -> tuple[bool, list[dict], str]:
        """List one remote directory without exposing backend-specific fields."""
        try:
            name, path = self._validate_name_path(name, path)
            data = self._req(
                "/operations/list", timeout=12.0, method="POST",
                data=json.dumps({
                    "fs": _rclone_scoped_fs(name),
                    "remote": path,
                    "opt": {"recurse": False, "noModTime": False, "noMimeType": False},
                }),
                headers={"Content-Type": "application/json"},
            ) or {}
            entries = []
            for item in data.get("list") or []:
                raw_name = str(item.get("Name") or posixpath.basename(str(item.get("Path") or "")))
                if not raw_name or raw_name in (".", "..") or "/" in raw_name or "\\" in raw_name:
                    continue
                try:
                    child_path = _join_rclone_path(path, raw_name)
                except ValueError:
                    continue
                is_dir = bool(item.get("IsDir", False))
                try:
                    size = max(0, int(item.get("Size", 0) or 0))
                except (TypeError, ValueError):
                    size = 0
                entries.append({
                    "name": raw_name,
                    "path": child_path,
                    "isDir": is_dir,
                    "size": size,
                    "modTime": str(item.get("ModTime") or ""),
                    "mimeType": str(item.get("MimeType") or ""),
                })
            entries.sort(key=lambda item: (not item["isDir"], item["name"].casefold()))
            return True, entries, ""
        except Exception as e:
            detail = str(e).strip()
            if not path and self._remote_type(name) == "s3":
                if _rclone_bucket(name):
                    detail = "S3/R2 bucket 读取失败，请检查 bucket 权限"
                else:
                    detail = "S3/R2 根目录没有 bucket 列表权限，请在路径框中输入 bucket 名称"
            return False, [], detail[:180] or "读取网盘目录失败"

    def _start_job(self, endpoint: str, payload: dict, *, action: str, label: str,
                   total: int | None = None, cleanup: str = "") -> dict:
        request_payload = dict(payload)
        body = dict(payload)
        body["_async"] = True
        data = self._req(
            endpoint, timeout=10.0, method="POST", data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        ) or {}
        remote_job = data.get("jobid")
        if remote_job is None:
            raise RuntimeError("rclone 未返回任务编号")
        job = {
            "id": uuid.uuid4().hex[:16],
            "rcloneJobId": int(remote_job),
            "action": action,
            "label": label[:240],
            "status": "running",
            "phase": "transferring",
            "progress": 0.0 if total else None,
            "bytes": 0,
            "total": total,
            "speed": 0,
            "detail": "",
            "created": int(time.time()),
            "finished": 0,
            "cleanup": cleanup,
            "retryable": True,
            "status_failures": 0,
            "request": {"endpoint": endpoint, "payload": request_payload},
        }
        persisted = False
        with self._transfer_lock:
            self._transfer_jobs[job["id"]] = job
            persisted = self._persist_transfer_jobs_locked()
            if not persisted:
                self._transfer_jobs.pop(job["id"], None)
        if not persisted:
            stopped = False
            try:
                self._req(
                    "/job/stop", timeout=5.0, method="POST",
                    data=json.dumps({"jobid": int(remote_job)}),
                    headers={"Content-Type": "application/json"},
                )
                stopped = True
            except Exception as exc:
                _LOGGER.warning("unable to stop unjournaled rclone job %s: %s", remote_job, exc)
            if stopped and cleanup:
                try:
                    if os.path.isfile(cleanup):
                        os.unlink(cleanup)
                except OSError:
                    pass
            raise RuntimeError("传输任务状态保存失败，请检查 Aurora 数据目录权限或磁盘空间")
        return self._public_transfer(job)

    @staticmethod
    def _public_transfer(job: dict) -> dict:
        return {
            k: v for k, v in job.items()
            if k not in ("rcloneJobId", "cleanup", "request", "status_failures")
        }

    def _cleanup_transfer(self, job: dict) -> None:
        path = job.get("cleanup") or ""
        if not path:
            return
        try:
            if os.path.isfile(path):
                os.unlink(path)
        except OSError:
            pass
        job["cleanup"] = ""
        job["retryable"] = False

    @staticmethod
    def _job_status_missing(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        message = str(exc).casefold()
        return code == 404 or any(text in message for text in ("job not found", "unknown job", "no such job"))

    def _refresh_transfer(self, job: dict) -> bool:
        with self._transfer_lock:
            if job.get("status") in _RCLONE_TERMINAL_STATUSES:
                return False
            remote_job = job.get("rcloneJobId")
        try:
            status = self._req(
                "/job/status", timeout=4.0, method="POST",
                data=json.dumps({"jobid": remote_job}),
                headers={"Content-Type": "application/json"},
            ) or {}
        except Exception as e:
            # A temporary status failure should not make an active transfer
            # look failed. If the job remains unreadable, expose a retryable
            # terminal state instead of leaving the torrent scheduler stuck in
            # "uploading" forever. The staging file is deliberately retained.
            with self._transfer_lock:
                failures = int(job.get("status_failures") or 0) + 1
                job["status_failures"] = failures
                if self._job_status_missing(e) or failures >= _RCLONE_STATUS_FAILURE_LIMIT:
                    job["status"] = "error"
                    job["detail"] = "rclone 传输任务状态已丢失，请检查服务后重试"
                    job["finished"] = int(time.time())
                    job["retryable"] = bool(job.get("request"))
                    self._persist_transfer_jobs_locked()
                    return True
                job["detail"] = str(e).strip()[:180]
            return False
        if status.get("finished"):
            with self._transfer_lock:
                job["status_failures"] = 0
                job["status"] = "done" if status.get("success") else "error"
                if status.get("success"):
                    job["detail"] = ""
                    job["progress"] = 1.0
                    self._cleanup_transfer(job)
                else:
                    request = job.get("request") or {}
                    payload = request.get("payload") or {}
                    destination = str(payload.get("dstFs") or payload.get("fs") or "")
                    remote_name = destination.split(":", 1)[0] if ":" in destination else ""
                    job["detail"] = _friendly_remote_error(
                        remote_name, status.get("error") or "传输失败", write=job.get("action") != "download",
                    )
                    # Keep an upload staging file on failure so the user can
                    # retry without uploading the browser file again.
                    job["retryable"] = bool(job.get("request"))
                job["finished"] = int(time.time())
                self._persist_transfer_jobs_locked()
            return True
        try:
            stats = self._req("/core/stats", timeout=3.0, method="POST") or {}
            with self._transfer_lock:
                active = [
                    item for item in self._transfer_jobs.values()
                    if item.get("status") == "running"
                ]
            rows = stats.get("transferring") or []
            request = job.get("request") or {}
            payload = request.get("payload") or {}
            candidates = {
                os.path.basename(str(payload.get("srcRemote") or "")),
                os.path.basename(str(payload.get("dstRemote") or "")),
            }
            candidates.discard("")
            row = next(
                (item for item in rows if str(item.get("name") or "") in candidates),
                None,
            )
            # rclone exposes aggregate stats when it cannot identify a single
            # transfer. They are safe to use only when this is the sole active
            # job; otherwise one upload would display another one's progress.
            if row is None and len(active) == 1:
                row = stats
            if row is not None:
                done = max(0, int(row.get("bytes", stats.get("bytes", 0)) or 0))
                total = row.get("size") or row.get("totalBytes") or stats.get("totalBytes")
                speed = row.get("speed", stats.get("speed", 0))
                percentage = row.get("percentage")
                with self._transfer_lock:
                    job["status_failures"] = 0
                    job["bytes"] = max(int(job.get("bytes") or 0), done)
                    if total:
                        job["total"] = max(int(total), int(job.get("total") or 0))
                    job["speed"] = max(0, int(float(speed or 0)))
                    if percentage is not None:
                        job["progress"] = min(1.0, max(0.0, float(percentage) / 100))
                    elif job.get("total"):
                        job["progress"] = min(1.0, job["bytes"] / job["total"])
        except Exception:
            pass
        return False

    def transfers(self) -> list[dict]:
        with self._transfer_lock:
            jobs = list(self._transfer_jobs.values())
        for job in jobs:
            self._refresh_transfer(job)
        with self._transfer_lock:
            ordered = sorted(self._transfer_jobs.values(), key=lambda item: item["created"], reverse=True)
            active = [job for job in ordered if job.get("status") == "running"]
            finished = [job for job in ordered if job.get("status") in _RCLONE_TERMINAL_STATUSES]
            now = int(time.time())
            removed = False
            for job in finished:
                finished_at = int(job.get("finished") or job.get("created") or now)
                if now - finished_at > _RCLONE_TRANSFER_RETENTION:
                    self._cleanup_transfer(job)
                    self._transfer_jobs.pop(job["id"], None)
                    removed = True
            finished = [job for job in finished if job.get("id") in self._transfer_jobs]
            if removed:
                self._persist_transfer_jobs_locked()
            # Keep every running task visible. The UI cap applies only to
            # historical rows; the internal journal retains newer terminal
            # rows so torrent destinations can still resolve their job id.
            return [self._public_transfer(job) for job in active + finished[:_RCLONE_TRANSFER_LIMIT]]

    def get_transfer(self, transfer_id: str) -> dict | None:
        """Refresh and return one transfer, including older retained rows."""
        with self._transfer_lock:
            job = self._transfer_jobs.get(str(transfer_id))
        if not job:
            return None
        self._refresh_transfer(job)
        return self._public_transfer(job)

    def cancel_transfer(self, transfer_id: str) -> tuple[bool, str]:
        with self._transfer_lock:
            job = self._transfer_jobs.get(transfer_id)
            if not job:
                return False, "传输任务不存在"
            if job.get("status") in ("done", "error", "canceled"):
                return True, ""
        try:
            self._req(
                "/job/stop", timeout=5.0, method="POST",
                data=json.dumps({"jobid": job["rcloneJobId"]}),
                headers={"Content-Type": "application/json"},
            )
        except Exception as e:
            return False, str(e).strip()[:180] or "取消传输失败"
        with self._transfer_lock:
            # 网络调用期间 _refresh_transfer 可能已把它标成 done/error，以先到者为准
            if job.get("status") in ("done", "error", "canceled"):
                return True, ""
            job["status"] = "canceled"
            job["detail"] = "已取消"
            job["finished"] = int(time.time())
            self._cleanup_transfer(job)
            self._persist_transfer_jobs_locked()
        return True, ""

    def retry_transfer(self, transfer_id: str) -> tuple[bool, dict | None, str]:
        with self._transfer_lock:
            job = self._transfer_jobs.get(transfer_id)
            if not job:
                return False, None, "传输任务不存在"
            if job.get("status") == "running":
                return False, None, "任务正在执行"
            if job.get("status") not in ("error", "canceled"):
                return False, None, "只有失败或已取消的任务可以重试"
            if not job.get("retryable") or not job.get("request"):
                return False, None, "该任务无法重试"
            request = dict(job["request"])
            action = str(job.get("action") or "transfer")
            label = str(job.get("label") or "传输任务")
            total = job.get("total")
            cleanup = str(job.get("cleanup") or "")
        try:
            retry = self._start_job(
                request["endpoint"], request["payload"], action=action,
                label=label, total=total, cleanup=cleanup,
            )
            return True, retry, ""
        except Exception as e:
            return False, None, str(e).strip()[:180] or "重试失败"

    def clear_transfers(self) -> int:
        with self._transfer_lock:
            removable = [
                job for job in self._transfer_jobs.values()
                if job.get("status") in ("done", "error", "canceled")
            ]
            for job in removable:
                self._cleanup_transfer(job)
                self._transfer_jobs.pop(job["id"], None)
            if removable:
                self._persist_transfer_jobs_locked()
            return len(removable)

    def mkdir_remote(self, name: str, path: str) -> tuple[bool, str]:
        try:
            name, path = self._validate_name_path(name, path, allow_empty=False)
            self._req(
                "/operations/mkdir", timeout=12.0, method="POST",
                data=json.dumps({"fs": _rclone_scoped_fs(name), "remote": path}),
                headers={"Content-Type": "application/json"},
            )
            return True, ""
        except Exception as e:
            return False, _friendly_remote_error(name, str(e), write=True) or "新建目录失败"

    def delete_remote_file(self, name: str, path: str, is_dir: bool) -> tuple[bool, dict | None, str]:
        try:
            name, path = self._validate_name_path(name, path, allow_empty=False)
            if is_dir:
                job = self._start_job(
                    "/operations/purge", {"fs": _rclone_scoped_fs(name), "remote": path},
                    action="delete", label=f"删除目录 {name}:{path}",
                )
                return True, job, ""
            self._req(
                "/operations/deletefile", timeout=12.0, method="POST",
                data=json.dumps({"fs": _rclone_scoped_fs(name), "remote": path}),
                headers={"Content-Type": "application/json"},
            )
            return True, None, ""
        except Exception as e:
            return False, None, _friendly_remote_error(name, str(e), write=True) or "删除失败"

    def rename_remote_entry(self, name: str, path: str, new_name: str, is_dir: bool) -> tuple[bool, dict | None, str]:
        """Rename a file/directory entry inside a remote.

        之前与本类的 rename_remote(name, new_name)（remote 改名）同名，
        后定义者覆盖前者，导致 /api/rclone/files/rename 必然 TypeError。
        """
        try:
            name, path = self._validate_name_path(name, path, allow_empty=False)
            new_name = str(new_name or "").strip()
            if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
                return False, None, "新名称无效"
            parent = posixpath.dirname(path)
            destination = _join_rclone_path(parent, new_name)
            if is_dir:
                if _rclone_bucket(name):
                    src_fs, src_remote = _rclone_scoped_fs(name), path
                    dst_fs, dst_remote = _rclone_scoped_fs(name), destination
                else:
                    src_fs, src_remote = _rclone_fs(name, path), ""
                    dst_fs, dst_remote = _rclone_fs(name, destination), ""
                job = self._start_job(
                    "/sync/move", {"srcFs": src_fs, "srcRemote": src_remote, "dstFs": dst_fs, "dstRemote": dst_remote},
                    action="move", label=f"重命名 {name}:{path}",
                )
            else:
                job = self._start_job(
                    "/operations/movefile",
                    {"srcFs": _rclone_scoped_fs(name), "srcRemote": path, "dstFs": _rclone_scoped_fs(name), "dstRemote": destination},
                    action="move", label=f"重命名 {name}:{path}",
                )
            return True, job, ""
        except Exception as e:
            return False, None, _friendly_remote_error(name, str(e), write=True) or "重命名失败"

    def copy_remote(self, source_name: str, source_path: str, destination_name: str,
                    destination_path: str, is_dir: bool, action: str) -> tuple[bool, dict | None, str]:
        try:
            source_name, source_path = self._validate_name_path(source_name, source_path, allow_empty=False)
            destination_name, destination_path = self._validate_name_path(destination_name, destination_path)
            if action not in ("copy", "move"):
                return False, None, "传输动作无效"
            if is_dir:
                endpoint = "/sync/copy" if action == "copy" else "/sync/move"
                dirname = posixpath.basename(source_path)
                target = _join_rclone_path(destination_path, dirname)
                source_bucket = _rclone_bucket(source_name)
                destination_bucket = _rclone_bucket(destination_name)
                payload = {
                    "srcFs": _rclone_scoped_fs(source_name) if source_bucket else _rclone_fs(source_name, source_path),
                    "srcRemote": source_path if source_bucket else "",
                    "dstFs": _rclone_scoped_fs(destination_name) if destination_bucket else _rclone_fs(destination_name, target),
                    "dstRemote": target if destination_bucket else "",
                }
            else:
                endpoint = "/operations/copyfile" if action == "copy" else "/operations/movefile"
                filename = posixpath.basename(source_path)
                target = _join_rclone_path(destination_path, filename)
                payload = {
                    "srcFs": _rclone_scoped_fs(source_name), "srcRemote": source_path,
                    "dstFs": _rclone_scoped_fs(destination_name), "dstRemote": target,
                }
            job = self._start_job(
                endpoint, payload, action=action,
                label=f"{action == 'copy' and '复制' or '移动'} {source_name}:{source_path}",
            )
            return True, job, ""
        except Exception as e:
            return False, None, _friendly_remote_error(destination_name, str(e), write=True) or "传输失败"

    def download_remote(self, name: str, path: str, destination: str, is_dir: bool) -> tuple[bool, dict | None, str]:
        try:
            name, path = self._validate_name_path(name, path, allow_empty=False)
            destination = _clean_rclone_path(destination)
            base = os.path.realpath(os.path.expanduser(os.environ.get("AURORA_LOCAL_MOUNT", "/opt/aurora/qbit/downloads")))
            target = os.path.realpath(os.path.join(base, destination))
            if target != base and not target.startswith(base + os.sep):
                return False, None, "本地目标路径无效"
            os.makedirs(target, exist_ok=True)
            if is_dir:
                if _rclone_bucket(name):
                    payload = {"srcFs": _rclone_scoped_fs(name), "srcRemote": path, "dstFs": target, "dstRemote": ""}
                else:
                    payload = {"srcFs": _rclone_fs(name, path), "srcRemote": "", "dstFs": target, "dstRemote": ""}
                endpoint = "/sync/copy"
                total = None
            else:
                filename = posixpath.basename(path)
                payload = {
                    "srcFs": _rclone_scoped_fs(name), "srcRemote": path,
                    "dstFs": target, "dstRemote": filename,
                }
                endpoint = "/operations/copyfile"
                total = None
                try:
                    stat = self._req(
                        "/operations/stat", timeout=8.0, method="POST",
                        data=json.dumps({"fs": _rclone_scoped_fs(name), "remote": path}),
                        headers={"Content-Type": "application/json"},
                    ) or {}
                    item = stat.get("item") or {}
                    total = max(0, int(item.get("Size", 0) or 0)) or None
                except Exception:
                    pass
            job = self._start_job(
                endpoint, payload, action="download", label=f"下载 {name}:{path}", total=total,
            )
            return True, job, ""
        except Exception as e:
            return False, None, str(e).strip()[:180] or "下载失败"

    def upload_file(self, name: str, staging_path: str, filename: str,
                    destination: str, total: int) -> tuple[bool, dict | None, str]:
        try:
            name, destination = self._validate_name_path(name, destination)
            filename = str(filename or "").strip()
            if not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
                return False, None, "文件名无效"
            if total <= 0 or total > _RCLONE_UPLOAD_LIMIT:
                return False, None, "文件大小超出限制"
            stage = os.path.realpath(staging_path)
            data_dir = os.path.realpath(_DATA_DIR)
            if not stage.startswith(data_dir + os.sep) or not os.path.isfile(stage):
                return False, None, "上传文件暂存失败"
            actual_total = os.path.getsize(stage)
            if actual_total <= 0 or actual_total > _RCLONE_UPLOAD_LIMIT:
                return False, None, "文件大小超出限制"
            # The multipart length is client-controlled metadata. Use the
            # server-written file size for progress and validation.
            total = actual_total
            if not destination:
                if self._remote_type(name) == "s3" and not _rclone_bucket(name):
                    return False, None, "S3/R2 上传请先进入 bucket 目录后再上传"
            target = _join_rclone_path(destination, filename)
            job = self._start_job(
                "/operations/copyfile",
                {"srcFs": os.path.dirname(stage), "srcRemote": os.path.basename(stage),
                 "dstFs": _rclone_scoped_fs(name), "dstRemote": target},
                action="upload", label=f"上传 {name}:{target}", total=total, cleanup=stage,
            )
            return True, job, ""
        except Exception as e:
            return False, None, _friendly_remote_error(name, str(e), write=True) or "上传失败"

    def upload_local(self, name: str, local_path: str, destination: str) -> tuple[bool, dict | None, str]:
        """Copy a completed local torrent payload to a remote directory."""
        try:
            name, destination = self._validate_name_path(name, destination, allow_empty=False)
            source = os.path.realpath(local_path)
            base = os.path.realpath(os.path.expanduser(
                os.environ.get("AURORA_LOCAL_MOUNT", "/opt/aurora/qbit/downloads")
            ))
            if source != base and not source.startswith(base + os.sep):
                return False, None, "本地源路径无效"
            if not os.path.exists(source):
                return False, None, "本地下载文件尚未就绪"
            if os.path.isdir(source):
                endpoint = "/sync/copy"
                payload = {
                    "srcFs": source,
                    "srcRemote": "",
                    "dstFs": _rclone_scoped_fs(name),
                    "dstRemote": destination,
                }
                total = None
            else:
                endpoint = "/operations/copyfile"
                payload = {
                    "srcFs": os.path.dirname(source),
                    "srcRemote": os.path.basename(source),
                    "dstFs": _rclone_scoped_fs(name),
                    "dstRemote": destination,
                }
                total = os.path.getsize(source)
            job = self._start_job(
                endpoint, payload, action="upload",
                label=f"下载完成上传 {name}:{destination}", total=total,
            )
            return True, job, ""
        except Exception as e:
            return False, None, _friendly_remote_error(name, str(e), write=True) or "上传失败"

    def get_remote(self, name: str) -> tuple[bool, dict, str]:
        """Return editable non-secret fields and the names of stored secrets."""
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name or ""):
            return False, {}, "网盘名称无效"
        try:
            data = self._req(
                "/config/get", timeout=5.0, method="POST",
                data=f"name={urllib.parse.quote(name)}",
            ) or {}
            ftype = str(data.get("type", ""))
            if not ftype:
                return False, {}, "网盘配置不存在"
            params = {}
            secrets = []
            for key, value in data.items():
                if key not in _RCLONE_PARAM_KEYS:
                    continue
                if key in _RCLONE_SECRET_KEYS:
                    if value:
                        secrets.append(key)
                elif isinstance(value, (str, int, float, bool)):
                    params[key] = _canonical_s3_provider(value) if ftype == "s3" and key == "provider" else str(value)
            if ftype == "s3":
                params["bucket"] = _rclone_bucket(name)
            return True, {"name": name, "type": ftype, "params": params, "secretFields": secrets}, ""
        except Exception as e:
            return False, {}, str(e).strip()[:180] or "读取网盘配置失败"

    def rename_remote(self, name: str, new_name: str) -> tuple[bool, str]:
        """Rename a remote (its config) by recreating it under a new name."""
        import re as _re
        name = str(name or "").strip()
        new_name = str(new_name or "").strip()
        name_pattern = r"[A-Za-z0-9_\-]{1,64}"
        if not _re.fullmatch(name_pattern, name) or not _re.fullmatch(name_pattern, new_name):
            return False, "名字只允许字母数字-_"
        if name == new_name:
            return True, ""
        try:
            remotes = self._req(
                "/config/listremotes", timeout=5.0, method="POST",
            ) or {}
            existing = {str(item).rstrip(":") for item in (remotes.get("remotes") or [])}
            if new_name in existing:
                return False, "目标名称已存在"

            old_config = self._req(
                "/config/get", timeout=5.0, method="POST",
                data=f"name={urllib.parse.quote(name)}",
            ) or {}
            remote_type = str(old_config.get("type") or "")
            if not remote_type:
                return False, "网盘配置不存在"
            bucket = _rclone_bucket(name)

            # config/get returns rclone's already-obscured secret values. Keep every
            # scalar config key and ask config/create not to obscure them again.
            parameters = {}
            for key, value in old_config.items():
                if key == "type" or not _re.fullmatch(r"[A-Za-z0-9_\-]{1,128}", str(key)):
                    continue
                if isinstance(value, (str, int, float, bool)):
                    parameters[str(key)] = str(value)
            self._req(
                "/config/create", timeout=8.0, method="POST",
                data=json.dumps({
                    "name": new_name,
                    "type": remote_type,
                    "parameters": parameters,
                    "opt": {"noObscure": True, "noOutput": True, "nonInteractive": True},
                }),
                headers={"Content-Type": "application/json"},
            )
            created = self._req(
                "/config/get", timeout=5.0, method="POST",
                data=f"name={urllib.parse.quote(new_name)}",
            ) or {}
            if str(created.get("type") or "") != remote_type:
                try:
                    self._req(
                        "/config/delete", timeout=5.0, method="POST",
                        data=f"name={urllib.parse.quote(new_name)}",
                    )
                except Exception:
                    pass
                return False, "新名称配置校验失败"
            try:
                self._req(
                    "/config/delete", timeout=5.0, method="POST",
                    data=f"name={urllib.parse.quote(name)}",
                )
            except Exception as e:
                try:
                    self._req(
                        "/config/delete", timeout=5.0, method="POST",
                        data=f"name={urllib.parse.quote(new_name)}",
                    )
                except Exception:
                    pass
                return False, f"旧名称删除失败：{str(e).strip()[:140]}"
            if bucket and (not _set_rclone_bucket(new_name, bucket) or not _set_rclone_bucket(name, "")):
                return False, "bucket 配置迁移失败"
            return True, ""
        except Exception as e:
            return False, str(e).strip()[:180] or "修改网盘名称失败"

    def update_remote(self, name: str, params: dict) -> tuple[bool, str]:
        """Update non-empty fields while preserving omitted or blank secrets."""
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name or ""):
            return False, "网盘名称无效"
        safe = {}
        bucket_present = "bucket" in (params or {})
        bucket = str((params or {}).get("bucket") or "").strip()
        if bucket_present and bucket:
            try:
                bucket = _validate_rclone_bucket(bucket)
            except ValueError as e:
                return False, str(e)
        for key, value in (params or {}).items():
            if key not in _RCLONE_PARAM_KEYS:
                continue
            if key == "bucket":
                continue
            value = str(value)[:512]
            if key in _RCLONE_SECRET_KEYS and not value.strip():
                continue
            safe[key] = value
        if str(safe.get("provider") or "").strip() or str(safe.get("endpoint") or "").strip():
            try:
                safe = _normalize_s3_params(safe)
            except ValueError as e:
                return False, str(e)
        if not safe and not bucket_present:
            return False, "没有需要更新的配置"
        try:
            import json as _json
            if safe:
                self._req(
                    "/config/update", timeout=8.0, method="POST",
                    data=_json.dumps({
                        "name": name,
                        "parameters": safe,
                        "opt": {"obscure": True, "noOutput": True, "nonInteractive": True},
                    }),
                    headers={"Content-Type": "application/json"},
                )
            if bucket_present and not _set_rclone_bucket(name, bucket):
                return False, "bucket 配置保存失败"
            return True, ""
        except Exception as e:
            return False, str(e).strip()[:180] or "更新网盘配置失败"

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
        bucket = str((params or {}).get("bucket") or "").strip() if ftype == "s3" else ""
        if ftype == "s3" and bucket:
            try:
                bucket = _validate_rclone_bucket(bucket)
            except ValueError as e:
                return False, str(e)
        for k, v in (params or {}).items():
            if k in _RCLONE_PARAM_KEYS:
                if k == "bucket":
                    continue
                safe[k] = str(v)[:512]
        if ftype == "s3":
            try:
                safe = _normalize_s3_params(safe)
            except ValueError as e:
                return False, str(e)
        body["parameters"] = safe
        try:
            import json as _json
            self._req("/config/create", timeout=6.0, method="POST",
                      data=_json.dumps(body),
                      headers={"Content-Type": "application/json"})
            if ftype == "s3" and not _set_rclone_bucket(name, bucket):
                return False, "bucket 配置保存失败"
            return True, ""
        except Exception as e:
            return False, str(e)

    def delete_remote(self, name: str) -> tuple[bool, str]:
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name or ""):
            return False, "bad name"
        try:
            self._req("/config/delete", timeout=5.0, method="POST", data=f"name={name}")
            _set_rclone_bucket(name, "")
            return True, ""
        except Exception as e:
            return False, str(e)


class QbittorrentProvider:
    name = "qbittorrent"
    _HASH_RE = re.compile(r"^[0-9a-fA-F]{40}$")
    _LABEL_RE = re.compile(r"^[^,\x00-\x1f\x7f]{1,64}$")
    _QUEUE_KEYS = (
        "queueing_enabled", "max_active_torrents", "max_active_downloads",
        "max_active_uploads", "max_active_checking_torrents", "add_to_top_of_queue",
    )

    def __init__(self):
        self.base = os.environ.get("AURORA_QBIT_URL", "http://127.0.0.1:8080").rstrip("/")
        self.user = os.environ.get("AURORA_QBIT_USER", "")
        self.pw = os.environ.get("AURORA_QBIT_PASS", "")
        self._session_lock = threading.RLock()
        self._last_details_ok = False
        # 探测连续失败后的退避：metrics 每 2s 轮询一次 available()，
        # 若密码错误或 qbit 掉线还每次都打登录接口，会触发 qBittorrent 的
        # 失败封禁（默认 5 次封 IP），把 Aurora 自己和反代后的用户一起封出去
        self._auth_backoff_until = 0.0

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
        with self._session_lock:
            if time.monotonic() < self._auth_backoff_until:
                return False
            for _ in range(2):
                try:
                    s = self._ensure()
                    if s.get(f"{self.base}/api/v2/app/version", timeout=4).status_code == 200:
                        self._auth_backoff_until = 0.0
                        return True
                    self._sess = None        # stale/reused connection -> force re-login
                except Exception:
                    self._sess = None
                    self._auth_backoff_until = time.monotonic() + 60.0
        return False

    def queue_settings(self) -> tuple[bool, dict, str]:
        """Read the queue controls exposed by qBittorrent's preferences API."""
        with self._session_lock:
            try:
                s = self._ensure()
                r = s.get(f"{self.base}/api/v2/app/preferences", timeout=5)
                if r.status_code != 200:
                    return False, {}, f"qBittorrent preferences HTTP {r.status_code}"
                data = r.json() or {}
                return True, {
                    "queueing_enabled": bool(data.get("queueing_enabled", True)),
                    "max_active_torrents": int(data.get("max_active_torrents", 8)),
                    "max_active_downloads": int(data.get("max_active_downloads", 3)),
                    "max_active_uploads": int(data.get("max_active_uploads", 3)),
                    "max_active_checking_torrents": int(data.get("max_active_checking_torrents", 1)),
                    "add_to_top_of_queue": bool(data.get("add_to_top_of_queue", False)),
                }, ""
            except Exception as exc:
                return False, {}, str(exc)

    def update_queue_settings(self, values: dict) -> tuple[bool, dict, str]:
        """Update only queue-related preferences, then return qBittorrent's result."""
        payload = {key: values[key] for key in self._QUEUE_KEYS if key in values}
        if not payload:
            return False, {}, "没有可更新的队列参数"
        with self._session_lock:
            try:
                s = self._ensure()
                r = s.post(
                    f"{self.base}/api/v2/app/setPreferences",
                    data={"json": json.dumps(payload, separators=(",", ":"))},
                    timeout=5,
                )
                if r.status_code != 200:
                    return False, {}, f"qBittorrent setPreferences HTTP {r.status_code}"
                return self.queue_settings()
            except Exception as exc:
                return False, {}, str(exc)

    @classmethod
    def _valid_hash(cls, hash_: str) -> bool:
        return bool(cls._HASH_RE.fullmatch(str(hash_ or "")))

    @classmethod
    def _valid_label(cls, value: str) -> bool:
        value = str(value or "").strip()
        return bool(cls._LABEL_RE.fullmatch(value)) and value not in (".", "..")

    @classmethod
    def _clean_labels(cls, values) -> list[str]:
        if isinstance(values, str):
            values = values.split(",")
        out = []
        for value in values or []:
            value = str(value or "").strip()
            if value and cls._valid_label(value) and value not in out:
                out.append(value)
        return out[:32]

    @staticmethod
    def _torrent_fields(t: dict) -> dict:
        tags = [item.strip() for item in str(t.get("tags") or "").split(",") if item.strip()]
        return {
            "hash": str(t.get("hash") or ""),
            "name": str(t.get("name") or ""),
            "state": str(t.get("state") or "unknown"),
            "progress": float(t.get("progress") or 0),
            "size": max(0, int(t.get("size") or 0)),
            "downloaded": max(0, int(t.get("downloaded") or 0)),
            "uploaded": max(0, int(t.get("uploaded") or 0)),
            "dlspeed": max(0, int(t.get("dlspeed") or 0)),
            "upspeed": max(0, int(t.get("upspeed") or 0)),
            "eta": int(t.get("eta") or 0),
            "ratio": float(t.get("ratio") or 0),
            "seeders": int(t.get("num_seeds") or 0),
            "leechers": int(t.get("num_leechs") or 0),
            "connections": int(t.get("connections_count") or 0),
            "save_path": str(t.get("save_path") or ""),
            "content_path": str(t.get("content_path") or ""),
            "category": str(t.get("category") or ""),
            "tags": tags,
            "comment": str(t.get("comment") or ""),
            "tracker": str(t.get("tracker") or ""),
            "error": int(t.get("last_seen") or 0) if t.get("state") == "error" else 0,
            "error_string": str(t.get("error_string") or ""),
            "added_on": int(t.get("added_on") or 0),
            "completion_on": int(t.get("completion_on") or 0),
            "last_activity": int(t.get("last_activity") or 0),
            "seeding_time": int(t.get("seeding_time") or 0),
            "inactive_seeding_time": int(t.get("inactive_seeding_time") or 0),
            "queue_position": int(t.get("priority") or 0),
            "download_limit": int(t.get("dl_limit") or 0),
            "upload_limit": int(t.get("up_limit") or 0),
            "ratio_limit": float(t.get("ratio_limit") or -1),
            "seeding_time_limit": int(t.get("seeding_time_limit") or -1),
            "inactive_seeding_time_limit": int(t.get("inactive_seeding_time_limit") or -1),
        }

    def torrent_detail(self, hash_: str) -> tuple[bool, dict, str]:
        """Return a safe, useful task view without leaking qBittorrent internals."""
        if not self._valid_hash(hash_):
            return False, {}, "种子 Hash 无效"
        with self._session_lock:
            try:
                s = self._ensure()
                info = s.get(f"{self.base}/api/v2/torrents/info", params={"hash": hash_}, timeout=5)
                if info.status_code != 200:
                    return False, {}, f"qBittorrent 任务信息 HTTP {info.status_code}"
                rows = info.json() or []
                if not rows:
                    return False, {}, "任务不存在"
                torrent = self._torrent_fields(rows[0])
                files = s.get(f"{self.base}/api/v2/torrents/files", params={"hash": hash_}, timeout=5)
                trackers = s.get(f"{self.base}/api/v2/torrents/trackers", params={"hash": hash_}, timeout=5)
                if files.status_code != 200 or trackers.status_code != 200:
                    return False, {}, "读取任务文件或 Tracker 失败"
                torrent["files"] = [{
                    "index": int(item.get("index") or 0),
                    "name": str(item.get("name") or ""),
                    "size": max(0, int(item.get("size") or 0)),
                    "progress": float(item.get("progress") or 0),
                    "priority": int(item.get("priority") or 0),
                    "availability": float(item.get("availability") or 0),
                    "is_seed": bool(item.get("is_seed", False)),
                } for item in (files.json() or []) if isinstance(item, dict)]
                torrent["trackers"] = [{
                    "url": str(item.get("url") or ""),
                    "status": int(item.get("status") or 0),
                    "tier": int(item.get("tier") or 0),
                    "num_peers": int(item.get("num_peers") or 0),
                    "num_seeds": int(item.get("num_seeds") or 0),
                    "num_leeches": int(item.get("num_leeches") or 0),
                    "msg": str(item.get("msg") or ""),
                } for item in (trackers.json() or []) if isinstance(item, dict)]
                return True, torrent, ""
            except Exception as exc:
                return False, {}, str(exc)

    def labels(self) -> tuple[bool, dict, str]:
        """Read categories and tags used by the task manager."""
        with self._session_lock:
            try:
                s = self._ensure()
                categories = s.get(f"{self.base}/api/v2/torrents/categories", timeout=5)
                tags = s.get(f"{self.base}/api/v2/torrents/tags", timeout=5)
                if categories.status_code != 200 or tags.status_code != 200:
                    return False, {}, "读取 qBittorrent 分类或标签失败"
                raw_categories = categories.json() or {}
                category_rows = [{
                    "name": str(name),
                    "save_path": str((value or {}).get("savePath") or ""),
                } for name, value in raw_categories.items() if isinstance(value, dict)]
                category_rows.sort(key=lambda item: item["name"].casefold())
                raw_tags = tags.json() or []
                if isinstance(raw_tags, dict):
                    raw_tags = raw_tags.get("tags") or []
                tag_rows = sorted({str(item).strip() for item in raw_tags if str(item).strip()}, key=str.casefold)
                return True, {"categories": category_rows, "tags": tag_rows}, ""
            except Exception as exc:
                return False, {}, str(exc)

    def create_category(self, name: str, save_path: str) -> tuple[bool, str]:
        if not self._valid_label(name):
            return False, "分类名称无效"
        with self._session_lock:
            try:
                r = self._ensure().post(
                    f"{self.base}/api/v2/torrents/createCategory",
                    data={"category": name.strip(), "savePath": save_path}, timeout=5,
                )
                return r.status_code == 200, "" if r.status_code == 200 else f"qBittorrent HTTP {r.status_code}"
            except Exception as exc:
                return False, str(exc)

    def edit_category(self, name: str, save_path: str) -> tuple[bool, str]:
        if not self._valid_label(name):
            return False, "分类名称无效"
        with self._session_lock:
            try:
                r = self._ensure().post(
                    f"{self.base}/api/v2/torrents/editCategory",
                    data={"category": name.strip(), "savePath": save_path}, timeout=5,
                )
                return r.status_code == 200, "" if r.status_code == 200 else f"qBittorrent HTTP {r.status_code}"
            except Exception as exc:
                return False, str(exc)

    def delete_category(self, name: str) -> tuple[bool, str]:
        if not self._valid_label(name):
            return False, "分类名称无效"
        with self._session_lock:
            try:
                r = self._ensure().post(
                    f"{self.base}/api/v2/torrents/removeCategories",
                    data={"categories": name.strip()}, timeout=5,
                )
                return r.status_code == 200, "" if r.status_code == 200 else f"qBittorrent HTTP {r.status_code}"
            except Exception as exc:
                return False, str(exc)

    def create_tags(self, tags) -> tuple[bool, str]:
        values = self._clean_labels(tags)
        if not values:
            return False, "标签名称无效"
        if any(item.startswith("aurora-") for item in values):
            return False, "aurora- 前缀为系统标签"
        with self._session_lock:
            try:
                r = self._ensure().post(
                    f"{self.base}/api/v2/torrents/createTags",
                    data={"tags": ",".join(values)}, timeout=5,
                )
                return r.status_code == 200, "" if r.status_code == 200 else f"qBittorrent HTTP {r.status_code}"
            except Exception as exc:
                return False, str(exc)

    def delete_tags(self, tags) -> tuple[bool, str]:
        values = self._clean_labels(tags)
        if not values or any(item.startswith("aurora-") for item in values):
            return False, "不能删除系统标签或无效标签"
        with self._session_lock:
            try:
                r = self._ensure().post(
                    f"{self.base}/api/v2/torrents/deleteTags",
                    data={"tags": ",".join(values)}, timeout=5,
                )
                return r.status_code == 200, "" if r.status_code == 200 else f"qBittorrent HTTP {r.status_code}"
            except Exception as exc:
                return False, str(exc)

    def update_labels(self, hash_: str, category: str | None = None, tags=None) -> tuple[bool, dict, str]:
        if not self._valid_hash(hash_):
            return False, {}, "种子 Hash 无效"
        if category is not None and category and not self._valid_label(category):
            return False, {}, "分类名称无效"
        wanted = None if tags is None else self._clean_labels(tags)
        if wanted is not None and any(item.startswith("aurora-") for item in wanted):
            return False, {}, "不能修改系统标签"
        with self._session_lock:
            try:
                s = self._ensure()
                info = s.get(f"{self.base}/api/v2/torrents/info", params={"hash": hash_}, timeout=5)
                rows = info.json() if info.status_code == 200 else []
                if not rows:
                    return False, {}, "任务不存在"
                current = [item.strip() for item in str(rows[0].get("tags") or "").split(",") if item.strip()]
                if category is not None:
                    r = s.post(f"{self.base}/api/v2/torrents/setCategory", data={"hashes": hash_, "category": category.strip()}, timeout=5)
                    if r.status_code != 200:
                        return False, {}, f"设置分类失败：HTTP {r.status_code}"
                if wanted is not None:
                    system = [item for item in current if item.startswith("aurora-")]
                    user_current = [item for item in current if not item.startswith("aurora-")]
                    remove = [item for item in user_current if item not in wanted]
                    add = [item for item in wanted if item not in user_current]
                    if remove:
                        r = s.post(f"{self.base}/api/v2/torrents/removeTags", data={"hashes": hash_, "tags": ",".join(remove)}, timeout=5)
                        if r.status_code != 200:
                            return False, {}, f"移除标签失败：HTTP {r.status_code}"
                    if add:
                        r = s.post(f"{self.base}/api/v2/torrents/addTags", data={"hashes": hash_, "tags": ",".join(add)}, timeout=5)
                        if r.status_code != 200:
                            return False, {}, f"添加标签失败：HTTP {r.status_code}"
                    _ = system
                ok, detail, error = self.torrent_detail(hash_)
                return ok, detail, error
            except Exception as exc:
                return False, {}, str(exc)

    def add_system_tags(self, hash_: str, tags) -> tuple[bool, str]:
        """Attach Aurora-owned correlation tags without exposing them as user labels."""
        if not self._valid_hash(hash_):
            return False, "种子 Hash 无效"
        values = [str(item or "").strip() for item in (tags or [])]
        if not values or any(not item.startswith("aurora-") or not self._valid_label(item) for item in values):
            return False, "系统标签无效"
        with self._session_lock:
            try:
                r = self._ensure().post(
                    f"{self.base}/api/v2/torrents/addTags",
                    data={"hashes": hash_, "tags": ",".join(dict.fromkeys(values))}, timeout=5,
                )
                return r.status_code == 200, "" if r.status_code == 200 else f"qBittorrent HTTP {r.status_code}"
            except Exception as exc:
                return False, str(exc)

    def advanced_action(self, hash_: str, action: str, *, value: int | None = None,
                        delete_files: bool = False, location: str = "", file_ids=None,
                        all_file_ids=None, priority: int | None = None) -> tuple[bool, str]:
        if not self._valid_hash(hash_):
            return False, "种子 Hash 无效"
        endpoints = {
            "pause": ("stop", {}),
            "resume": ("start", {}),
            "force_start": ("setForceStart", {"value": "true"}),
            "force_stop": ("setForceStart", {"value": "false"}),
            "recheck": ("recheck", {}),
            "reannounce": ("reannounce", {}),
            "queue_top": ("queuePositionTop", {}),
            "queue_bottom": ("queuePositionBottom", {}),
            "queue_up": ("queuePositionUp", {}),
            "queue_down": ("queuePositionDown", {}),
            "toggle_sequential": ("toggleSequentialDownload", {}),
            "toggle_first_last": ("toggleFirstLastPiecePrio", {}),
        }
        if action == "remove":
            endpoint, data = "delete", {"deleteFiles": "true" if delete_files else "false"}
        elif action in ("set_download_limit", "set_upload_limit"):
            if value is None or not 0 <= int(value) <= 10_000_000 * 1024:
                return False, "限速必须在 0-10000000 KiB/s 之间"
            endpoint = "setDownloadLimit" if action == "set_download_limit" else "setUploadLimit"
            data = {"limit": str(int(value))}
        elif action == "set_location":
            if not location or len(location) > 4096 or not location.startswith("/downloads"):
                return False, "保存目录无效"
            endpoint, data = "setLocation", {"location": location}
        elif action in ("set_file_priority", "set_file_selection"):
            try:
                ids = sorted({int(item) for item in (file_ids or [])})
                all_ids = sorted({int(item) for item in (all_file_ids or [])})
            except (TypeError, ValueError):
                return False, "文件优先级参数无效"
            if not ids or any(item < 0 for item in ids) or len(ids) > 4096:
                return False, "文件优先级参数无效"
            if action == "set_file_priority":
                if priority is None or not 0 <= int(priority) <= 7:
                    return False, "文件优先级参数无效"
                endpoint, data = "filePrio", {"id": ",".join(str(item) for item in ids), "priority": str(int(priority))}
            else:
                if not all_ids or len(all_ids) > 4096 or any(item < 0 for item in all_ids) or not set(ids).issubset(all_ids):
                    return False, "文件选择参数无效"
                unselected = [item for item in all_ids if item not in set(ids)]
                with self._session_lock:
                    try:
                        session = self._ensure()
                        for target_ids, target_priority in ((unselected, 0), (ids, 1)):
                            if not target_ids:
                                continue
                            response = session.post(
                                f"{self.base}/api/v2/torrents/filePrio",
                                data={"hashes": hash_, "id": ",".join(str(item) for item in target_ids), "priority": str(target_priority)},
                                timeout=10,
                            )
                            if response.status_code != 200:
                                return False, f"qBittorrent HTTP {response.status_code}"
                        return True, ""
                    except Exception as exc:
                        return False, str(exc)
        elif action in endpoints:
            endpoint, data = endpoints[action]
        else:
            return False, "不支持的任务操作"
        data = {"hashes": hash_, **data}
        with self._session_lock:
            try:
                r = self._ensure().post(f"{self.base}/api/v2/torrents/{endpoint}", data=data, timeout=10)
                return r.status_code == 200, "" if r.status_code == 200 else f"qBittorrent HTTP {r.status_code}"
            except Exception as exc:
                return False, str(exc)

    def export_torrent(self, hash_: str) -> tuple[bool, bytes, str]:
        """导出 .torrent 原始文件。做种策略删除任务前留档，撤销时用它重加任务
        （磁力添加的任务在 qbit 侧没有元数据，不留档就永远无法恢复）。"""
        if not self._valid_hash(hash_):
            return False, b"", "种子 Hash 无效"
        with self._session_lock:
            try:
                r = self._ensure().get(f"{self.base}/api/v2/torrents/export",
                                       params={"hash": hash_}, timeout=10)
                if r.status_code != 200 or not r.content:
                    return False, b"", f"导出种子失败（HTTP {r.status_code}）"
                return True, r.content, ""
            except Exception as exc:
                return False, b"", str(exc)

    def torrent_details(self) -> list[dict]:
        with self._session_lock:
            try:
                s = self._ensure()
                response = s.get(f"{self.base}/api/v2/torrents/info", timeout=5)
                if response.status_code >= 300:
                    raise RuntimeError(f"qbit info HTTP {response.status_code}")
                data = response.json() or []
                self._last_details_ok = isinstance(data, list)
                return data if isinstance(data, list) else []
            except Exception:
                self._last_details_ok = False
                return []

    def torrents(self):
        out = []
        data = self.torrent_details()
        for t in data or []:
            st = _QBIT_STATES.get(t.get("state", "unknown"), "unknown")
            row = {
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
                "eta": int(t.get("eta") or 0),
                "savePath": t.get("save_path", ""),
                "category": t.get("category", ""),
                "tags": [item.strip() for item in str(t.get("tags") or "").split(",") if item.strip()],
                "seedingTime": int(t.get("seeding_time") or 0),
            }
            destination = torrent_destination_for_tags(t.get("tags", ""))
            if destination:
                row["destination"] = destination
            out.append(row)
        return out

    def peers(self, hash_):
        """当前连到这个种子的对等方（谁在从我们这里下载）。"""
        if not self._valid_hash(hash_):
            return {"peers": [], "connected": 0, "seeds": 0, "leechers": 0}
        with self._session_lock:
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
                "up_speed": p.get("up_speed", 0),
                "down_speed": p.get("down_speed", 0),
                "uploaded": p.get("uploaded", 0),
                "downloaded": p.get("downloaded", 0),
            })
        out.sort(key=lambda x: x["uploaded"], reverse=True)
        return {
            "peers": out,
            "connected": d.get("peers_connected", len(out)),
            "seeds": d.get("peers_seeds", 0),
            "leechers": d.get("peers_leechers", 0),
        }

    def add(self, magnet: str, save_path: str = "", tag: str = "", category: str = "", tags=None) -> bool:
        with self._session_lock:
            try:
                if not magnet.startswith("magnet:"):
                    return False
                s = self._ensure()
                data = {"urls": magnet}
                if save_path:
                    data["savepath"] = save_path
                labels = self._clean_labels(tags)
                if tag and tag not in labels:
                    labels.append(tag)
                if labels:
                    data["tags"] = ",".join(labels)
                if category:
                    if not self._valid_label(category):
                        return False
                    data["category"] = category.strip()
                r = s.post(f"{self.base}/api/v2/torrents/add", data=data, timeout=6)
                return r.status_code in (200, 201)   # 409=已存在/无效 -> False
            except Exception:
                return False

    def add_file(self, filename: str, content: bytes, save_path: str = "", tag: str = "", category: str = "", tags=None) -> bool:
        """Forward one .torrent file to qBittorrent's multipart upload endpoint."""
        with self._session_lock:
            try:
                if not filename.lower().endswith(".torrent") or not content:
                    return False
                s = self._ensure()
                data = {}
                if save_path:
                    data["savepath"] = save_path
                labels = self._clean_labels(tags)
                if tag and tag not in labels:
                    labels.append(tag)
                if labels:
                    data["tags"] = ",".join(labels)
                if category:
                    if not self._valid_label(category):
                        return False
                    data["category"] = category.strip()
                r = s.post(
                    f"{self.base}/api/v2/torrents/add",
                    files={"torrents": (filename, content, "application/x-bittorrent")},
                    data=data or None,
                    timeout=15,
                )
                return r.status_code in (200, 201)
            except Exception:
                return False
    def action(self, hash_, action):
        if not self._valid_hash(hash_):
            return False
        with self._session_lock:
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
        with self._session_lock:
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
            with self._session_lock:
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
        with self._session_lock:
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
            if not self._valid_hash(h):
                continue
            with self._session_lock:
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
        if not self._valid_hash(hash_):
            return False
        with self._session_lock:
            try:
                s = self._ensure()
                r = s.post(f"{self.base}/api/v2/torrents/setLocation",
                           data={"hashes": hash_, "location": location}, timeout=10)
                return r.status_code == 200
            except Exception:
                return False

    # ---- RSS 自动订阅：Aurora 只做管理与预览，抓取/匹配/自动下载由 qbit 完成 ----

    def rss_overview(self) -> tuple[bool, dict, str]:
        with self._session_lock:
            try:
                s = self._ensure()
                feeds_r = s.get(f"{self.base}/api/v2/rss/items", timeout=8)
                rules_r = s.get(f"{self.base}/api/v2/rss/rules", timeout=8)
                if feeds_r.status_code != 200 or rules_r.status_code != 200:
                    return False, {}, f"qBittorrent RSS HTTP {feeds_r.status_code}/{rules_r.status_code}"
                feeds = _flatten_rss_feeds(feeds_r.json() or {}, "")
                rules = {name: _normalize_rss_rule(name, raw or {})
                         for name, raw in (rules_r.json() or {}).items()}
                return True, {"feeds": feeds, "rules": dict(sorted(rules.items()))}, ""
            except Exception as exc:
                return False, {}, str(exc)

    def _rss_post(self, endpoint: str, data: dict) -> tuple[bool, str]:
        with self._session_lock:
            try:
                r = self._ensure().post(f"{self.base}/api/v2/rss/{endpoint}", data=data, timeout=10)
                if r.status_code == 200:
                    return True, ""
                return False, f"qBittorrent RSS HTTP {r.status_code}"
            except Exception as exc:
                return False, str(exc)

    def rss_add_feed(self, path: str, url: str) -> tuple[bool, str]:
        return self._rss_post("addFeed", {"path": path, "url": url})

    def rss_remove_item(self, path: str) -> tuple[bool, str]:
        return self._rss_post("removeItem", {"path": path})

    def rss_rename_item(self, path: str, new_path: str) -> tuple[bool, str]:
        return self._rss_post("moveItem", {"itemPath": path, "destPath": new_path})

    def rss_set_feed_url(self, path: str, url: str) -> tuple[bool, str]:
        return self._rss_post("setFeedURL", {"path": path, "url": url})

    def rss_refresh(self, item_path: str) -> tuple[bool, str]:
        return self._rss_post("refreshItem", {"itemPath": item_path})

    def rss_save_rule(self, name: str, rule_def: dict) -> tuple[bool, str]:
        return self._rss_post("setRule", {"ruleName": name, "ruleDef": json.dumps(rule_def)})

    def rss_remove_rule(self, name: str) -> tuple[bool, str]:
        return self._rss_post("removeRule", {"ruleName": name})

    def rss_rename_rule(self, name: str, new_name: str) -> tuple[bool, str]:
        return self._rss_post("renameRule", {"ruleName": name, "newRuleName": new_name})

    def rss_matching(self, name: str) -> tuple[bool, list, str]:
        """规则当前命中的文章（须先保存规则；与规则是否启用无关）。"""
        with self._session_lock:
            try:
                r = self._ensure().post(f"{self.base}/api/v2/rss/matchingArticles",
                                        data={"ruleName": name}, timeout=10)
                if r.status_code != 200:
                    return False, [], f"匹配预览失败（HTTP {r.status_code}）"
                items = []
                for feed, articles in (r.json() or {}).items():
                    for a in articles or []:
                        items.append({
                            "title": str(a.get("title") or ""),
                            "feed": str(feed),
                            "url": str(a.get("torrentURL") or a.get("url") or ""),
                        })
                items.sort(key=lambda x: x["title"])
                return True, items, ""
            except Exception as exc:
                return False, [], str(exc)

_RSS_SEGMENT_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff \-.()（）]*$", re.UNICODE)


def _valid_rss_path(value: str) -> bool:
    """qBittorrent RSS 用 \\ 作路径分隔符（Folder\\Feed）；逐段校验。"""
    v = str(value or "").strip()
    if not v or len(v) > 256:
        return False
    return all(_RSS_SEGMENT_RE.match(seg) for seg in v.split("\\") if seg != "")


def _flatten_rss_feeds(node: dict, prefix: str) -> list[dict]:
    """把 rss/items 的嵌套结构拍平成带 path 的订阅源列表。"""
    out = []
    for key, value in (node or {}).items():
        if not isinstance(value, dict):
            continue
        path = f"{prefix}\\{key}" if prefix else key
        if "url" in value or "articles" in value:
            out.append({
                "path": path,
                "name": key,
                "url": str(value.get("url") or ""),
                "has_error": bool(value.get("hasError")),
                "loading": bool(value.get("isLoading")),
            })
        else:
            out.extend(_flatten_rss_feeds(value, path))
    return out


def _rss_rule_def(rule: dict) -> dict:
    """构造 qbit RSS 规则定义，双写新旧字段：
    5.x 只认 torrentParams（旧字段已移除），4.1-4.5 只认旧字段，
    双方都会忽略自己不认识的键，一个定义两个大版本都能落库。"""
    save_path = str(rule.get("save_path") or "")
    category = str(rule.get("category") or "")
    tags = [str(t) for t in (rule.get("tags") or [])]
    return {
        "enabled": bool(rule.get("enabled", True)),
        "useRegex": bool(rule.get("use_regex")),
        "mustContain": str(rule.get("must_contain") or ""),
        "mustNotContain": str(rule.get("must_not_contain") or ""),
        "episodeFilter": str(rule.get("episode_filter") or ""),
        "affectedFeeds": [str(f) for f in (rule.get("affected_feeds") or [])],
        "torrentParams": {"save_path": save_path, "category": category, "tags": tags, "stopped": False},
        "savePath": save_path,
        "assignedCategory": category,
        "category": category,
        "addPaused": False,
        "tags": tags,
    }


def _normalize_rss_rule(name: str, raw: dict) -> dict:
    params = raw.get("torrentParams") or {}
    return {
        "name": str(name),
        "enabled": bool(raw.get("enabled", True)),
        "use_regex": bool(raw.get("useRegex")),
        "must_contain": str(raw.get("mustContain") or ""),
        "must_not_contain": str(raw.get("mustNotContain") or ""),
        "episode_filter": str(raw.get("episodeFilter") or ""),
        "affected_feeds": [str(f) for f in (raw.get("affectedFeeds") or [])],
        "save_path": str(params.get("save_path") or raw.get("savePath") or ""),
        "category": str(params.get("category") or raw.get("assignedCategory") or raw.get("category") or ""),
        "tags": [str(t) for t in (params.get("tags") or raw.get("tags") or [])],
        "last_match": int(raw.get("lastMatch") or 0),
    }


class JellyfinProvider:
    name = "jellyfin"

    def __init__(self):
        self.base = os.environ.get("AURORA_JELLYFIN", "http://127.0.0.1:8096").rstrip("/")
        self.token = os.environ.get("AURORA_JELLYFIN_TOKEN", "")
        self._down_until = 0.0   # 同 rclone：掉线结论缓存 15s，避免 2s 轮询打满探测超时

    def available(self) -> bool:
        if time.monotonic() < self._down_until:
            return False
        ok = _probe(f"{self.base}/System/Info/Public") == 200
        self._down_until = 0.0 if ok else time.monotonic() + 15.0
        return ok

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

    def refresh_library(self) -> tuple[bool, str]:
        """触发 Jellyfin 全库扫描（媒体整理完成后调用）。"""
        if not self.token:
            return False, "未配置 Jellyfin Token"
        try:
            # /Library/Refresh 返回 204 No Content，decode=False 避免空 body 解析失败
            _http(f"{self.base}/Library/Refresh",
                  {"X-Emby-Token": self.token}, timeout=6.0, method="POST", decode=False)
            return True, ""
        except Exception as exc:
            return False, str(exc)

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


_DEST_CACHE: dict[str, dict] = {}
_DEST_CACHE_STAMP: object = object()   # 哨兵：首次调用必然未命中


def _load_torrent_destinations() -> dict[str, dict]:
    """Read the destination state file, cached by (mtime, size).

    metrics 每 2s 会对每个种子调用 torrent_destination_for_tags（N+1），
    按文件指纹缓存后一个轮询周期只解析一次；写入路径在保存后主动失效。
    调用方需持有 _TORRENT_DEST_LOCK（写路径均已如此）。
    """
    global _DEST_CACHE, _DEST_CACHE_STAMP
    try:
        st = os.stat(_TORRENT_DEST_FILE)
        stamp: object = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    if stamp == _DEST_CACHE_STAMP:
        return _DEST_CACHE
    try:
        with open(_TORRENT_DEST_FILE) as f:
            data = json.load(f)
        parsed = {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
    except Exception:
        parsed = {}
    _DEST_CACHE = parsed
    _DEST_CACHE_STAMP = stamp
    return parsed


def _save_torrent_destinations(data: dict[str, dict]) -> bool:
    global _DEST_CACHE_STAMP
    try:
        _atomic_json(_TORRENT_DEST_FILE, data)
    except Exception as exc:
        _LOGGER.warning("torrent destination state write failed: %s", exc)
        _DEST_CACHE_STAMP = object()
        return False
    # 写入后强制下一次 load 重新读盘，避免缓存与磁盘分叉
    _DEST_CACHE_STAMP = object()
    return True


def _tag_list(tags: str) -> list[str]:
    if isinstance(tags, (list, tuple, set)):
        return [str(item).strip() for item in tags if str(item).strip()]
    return [item.strip() for item in str(tags or "").split(",") if item.strip()]


def torrent_destination_for_tags(tags: str) -> dict | None:
    """Return the public remote destination attached to a qBittorrent task."""
    with _TORRENT_DEST_LOCK:
        data = _load_torrent_destinations()
        for tag in _tag_list(tags):
            entry = data.get(tag)
            if entry:
                return {
                    "id": tag,
                    "remote": entry.get("remote", ""),
                    "path": entry.get("path", ""),
                    "status": entry.get("status", "waiting"),
                    "detail": entry.get("detail", ""),
                }
    return None


def register_torrent_destination(remote: str, path: str = "") -> tuple[str, str, str]:
    """Reserve a remote destination and return the qBittorrent correlation tag."""
    remote = str(remote or "").strip()
    if not remote:
        return "", "", ""
    if not _rclone.available():
        return "", "", "网盘服务未接入"
    ok, path, detail = _rclone.validate_destination(remote, path)
    if not ok:
        return "", "", detail
    marker = f"aurora-remote-{uuid.uuid4().hex[:16]}"
    entry = {
        "remote": remote,
        "path": path,
        "status": "waiting",
        "detail": "",
        "hash": "",
        "name": "",
        "local_path": "",
        "transfer_id": "",
        "created": int(time.time()),
        "finished": 0,
    }
    with _TORRENT_DEST_LOCK:
        data = dict(_load_torrent_destinations())
        data[marker] = entry
        if not _save_torrent_destinations(data):
            return "", "", "转存任务状态保存失败，请检查 Aurora 数据目录权限或磁盘空间"
    return marker, path, ""


def discard_torrent_destination(marker: str) -> None:
    if not marker:
        return
    with _TORRENT_DEST_LOCK:
        data = dict(_load_torrent_destinations())
        if marker in data:
            data.pop(marker, None)
            _save_torrent_destinations(data)

def retry_torrent_destination(marker: str) -> tuple[bool, str]:
    """Put a failed torrent-to-remote transfer back in the scheduler queue."""
    marker = str(marker or "").strip()
    if not re.fullmatch(r"aurora-remote-[0-9a-f]{16}", marker):
        return False, "转存任务编号无效"
    with _TORRENT_DEST_LOCK:
        data = dict(_load_torrent_destinations())
        entry = data.get(marker)
        if not entry:
            return False, "转存任务不存在"
        if entry.get("status") != "error":
            return False, "当前任务不需要重试"
        updated = dict(entry)
        updated.update({"status": "waiting", "detail": "", "transfer_id": "", "finished": 0})
        data[marker] = updated
        if not _save_torrent_destinations(data):
            return False, "转存任务状态保存失败，请检查 Aurora 数据目录权限或磁盘空间"
        entry = updated
    _log("torrent.remote.retry", entry.get("name") or marker)
    return True, ""


def _torrent_host_path(torrent: dict) -> str:
    """Map qBittorrent's container path back to Aurora's host download path."""
    base = os.path.realpath(os.path.expanduser(
        os.environ.get("AURORA_LOCAL_MOUNT", "/opt/aurora/qbit/downloads")
    ))
    raw = str(torrent.get("content_path") or "")
    if not raw:
        save_path = str(torrent.get("save_path") or "/downloads").rstrip("/")
        raw = f"{save_path}/{torrent.get('name', '')}"
    if raw == "/downloads" or raw.startswith("/downloads/"):
        relative = raw[len("/downloads"):].lstrip("/")
        target = os.path.join(base, relative)
    else:
        target = raw
    target = os.path.realpath(target)
    if target != base and not target.startswith(base + os.sep):
        return ""
    return target


def _process_torrent_destinations() -> None:
    if not _qbit.available() or not _rclone.available():
        return
    details = _qbit.torrent_details()
    # An empty result after a failed qBittorrent request must not be treated as
    # proof that every tracked torrent was deleted.
    if not getattr(_qbit, "_last_details_ok", True):
        return
    with _TORRENT_DEST_LOCK:
        destinations = _load_torrent_destinations()
    # 浅拷贝隔离：调度线程会在锁外逐字段修改 entry，不能让缓存里的对象
    # 被并发读者看到中间状态
    destinations = {k: dict(v) for k, v in destinations.items()}
    if not destinations:
        return
    jobs = {str(job.get("id")): job for job in _rclone.transfers()}
    now = int(time.time())
    by_marker = {}
    for torrent in details:
        for tag in _tag_list(torrent.get("tags", "")):
            if tag in destinations:
                by_marker[tag] = torrent
    touched: set[str] = set()   # 本轮实际修改/移除的 marker，写回时按键合并
    for marker, entry in list(destinations.items()):
        status = str(entry.get("status") or "waiting")
        if status == "done":
            continue
        if status == "uploading":
            job = jobs.get(str(entry.get("transfer_id") or ""))
            if not job and entry.get("transfer_id"):
                get_transfer = getattr(_rclone, "get_transfer", None)
                if get_transfer:
                    job = get_transfer(str(entry.get("transfer_id")))
            if job and job.get("status") == "done":
                entry["status"] = "done"
                entry["detail"] = ""
                entry["finished"] = now
                touched.add(marker)
                continue
            if job and job.get("status") == "error":
                entry["status"] = "error"
                entry["detail"] = job.get("detail") or "网盘上传失败"
                entry["finished"] = now
                touched.add(marker)
                continue
            if job and job.get("status") == "canceled":
                entry["status"] = "error"
                entry["detail"] = "网盘上传已取消，请点击重试"
                entry["finished"] = now
                touched.add(marker)
                continue
            if job:
                continue
            # Never silently return to waiting: doing so can upload a partially
            # completed remote object a second time after a service restart.
            entry["status"] = "error"
            entry["detail"] = "网盘转存任务状态已丢失，请点击重试"
            entry["finished"] = now
            touched.add(marker)
            continue

        torrent = by_marker.get(marker)
        if not torrent:
            try:
                age = max(0, now - int(entry.get("created") or now))
            except (TypeError, ValueError):
                age = 0
            if age >= _TORRENT_DEST_ORPHAN_GRACE and status != "orphaned":
                entry["status"] = "orphaned"
                entry["detail"] = "qBittorrent 任务已不存在，请确认任务后再重试"
                entry["finished"] = now
                touched.add(marker)
            elif status == "orphaned" and age >= _TORRENT_DEST_RETENTION:
                destinations.pop(marker, None)
                touched.add(marker)
            continue

        for key, value in (("hash", torrent.get("hash", "")),
                           ("name", torrent.get("name", "")),
                           ("last_seen", now)):
            if entry.get(key) != value:
                entry[key] = value
                touched.add(marker)
        if status in ("error", "orphaned"):
            continue
        if float(torrent.get("progress", 0) or 0) < 0.999999:
            continue
        local_path = _torrent_host_path(torrent)
        if not local_path or not os.path.exists(local_path):
            if entry.get("detail") != "等待本地下载文件就绪":
                entry["detail"] = "等待本地下载文件就绪"
                touched.add(marker)
            continue
        item_name = os.path.basename(local_path.rstrip(os.sep))
        if not item_name:
            entry["status"] = "error"
            entry["detail"] = "无法确定本地下载文件名"
            touched.add(marker)
            continue
        try:
            target = _join_rclone_path(entry.get("path", ""), item_name)
        except ValueError:
            entry["status"] = "error"
            entry["detail"] = "网盘目标路径无效"
            touched.add(marker)
            continue
        ok, job, detail = _rclone.upload_local(entry["remote"], local_path, target)
        if ok and job:
            entry["status"] = "uploading"
            entry["detail"] = ""
            entry["local_path"] = local_path
            entry["transfer_id"] = job.get("id", "")
            entry["target_path"] = target
        else:
            entry["status"] = "error"
            entry["detail"] = detail or "网盘上传失败"
        entry["finished"] = 0 if ok else now
        touched.add(marker)
    if touched:
        with _TORRENT_DEST_LOCK:
            # 写回前重读最新状态、只按本轮处理过的 marker 合并：处理过程在锁外
            # 做了秒级网络调用，期间 register/discard/retry 可能已写入别的条目，
            # 把整份旧快照覆盖回去会静默抹掉它们（丢单且 UI 无任何报错）。
            current = {key: dict(value) for key, value in _load_torrent_destinations().items()}
            for marker in touched:
                if marker in destinations:
                    current[marker] = dict(destinations[marker])
                else:
                    current.pop(marker, None)   # 本轮因保留期到期被移除的孤儿
            _save_torrent_destinations(current)


_destination_sched_started = False


def start_torrent_destination_scheduler() -> None:
    global _destination_sched_started
    if _destination_sched_started:
        return
    _destination_sched_started = True

    def loop():
        while True:
            try:
                _process_torrent_destinations()
            except Exception as e:
                _log("torrent.remote.error", str(e))
            time.sleep(8)

    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------------------
# notifications + daily stats

_STATS_FILE = os.path.join(_DATA_DIR, "stats.json")
_SETTINGS_FILE = os.path.join(_DATA_DIR, "settings.json")
_NOTIFY_FILE = os.path.join(_DATA_DIR, "torrent_notify.json")
# ---------------------------------------------------------------------------
# 做种策略撤销（回收站式清理）：删除前导出 .torrent 留档、内容整体移入媒体
# 回收站；撤销 = 文件回原位 + 用留档重加任务。日志保留 7 天、上限 50 条。


_POLICY_UNDO_FILE = os.path.join(_DATA_DIR, "torrent_policy_undo.json")
_POLICY_UNDO_DIR = os.path.join(_DATA_DIR, "policy_undo")
_POLICY_UNDO_LOCK = threading.RLock()
_POLICY_UNDO_RETENTION = 7 * 24 * 60 * 60
_POLICY_UNDO_LIMIT = 50


def _load_policy_undo() -> list[dict]:
    try:
        with open(_POLICY_UNDO_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_policy_undo(entries: list[dict]) -> None:
    _atomic_json(_POLICY_UNDO_FILE, entries)


def _policy_undo_discard_file(entry: dict) -> None:
    f = str(entry.get("torrent_file") or "")
    if f and os.path.isfile(f):
        try:
            os.unlink(f)
        except OSError:
            _LOGGER.warning("policy undo torrent file delete failed: %s", f)


def _prune_policy_undo(entries: list[dict]) -> list[dict]:
    # 被裁掉的条目（过期或超出上限）必须同步删除留档 .torrent：
    # 里面含 tracker announce URL（私有站 passkey），不能超过保留期滞留磁盘
    cutoff = time.time() - _POLICY_UNDO_RETENTION
    kept = [e for e in entries if e.get("time", 0) >= cutoff][-_POLICY_UNDO_LIMIT:]
    kept_ids = {e.get("id") for e in kept}
    for e in entries:
        if e.get("id") not in kept_ids:
            _policy_undo_discard_file(e)
    return kept


def _policy_undo_add(entry: dict) -> None:
    with _POLICY_UNDO_LOCK:
        entries = _prune_policy_undo(_load_policy_undo())
        entries.append(entry)
        _save_policy_undo(entries)


def policy_undo_list() -> list[dict]:
    with _POLICY_UNDO_LOCK:
        entries = _prune_policy_undo(_load_policy_undo())
        _save_policy_undo(entries)
        return [{
            "id": e.get("id"), "time": e.get("time"), "action": e.get("action"),
            "name": e.get("name"), "rule_name": e.get("rule_name"),
            "reason": e.get("reason"), "category": e.get("category"),
            "trash_id": e.get("trash_id"),
            "recoverable": bool(e.get("torrent_file")) or e.get("action") == "pause",
        } for e in reversed(entries)]


def _media_base_dir() -> str:
    return os.path.realpath(os.path.expanduser(
        os.environ.get("AURORA_LOCAL_MOUNT", "/opt/aurora/qbit/downloads")
    ))


def _content_trashable(content_path: str) -> bool:
    base = _media_base_dir()
    src = os.path.realpath(str(content_path or ""))
    return bool(src) and src != base and src.startswith(base + os.sep) and os.path.exists(src)


def _policy_trash_content(content_path: str) -> tuple[str, str]:
    """把种子内容整体移入媒体回收站（.aurora-trash），返回 (trash_id, error)。

    与 UI 单文件删除共用 trash.json：回收站里看得到，也能从媒资库恢复。
    """
    if not _content_trashable(content_path):
        return "", "内容不在媒体目录内，无法进入回收站"
    base = _media_base_dir()
    src = os.path.realpath(content_path)
    rel = os.path.relpath(src, base)
    trash_id = secrets.token_hex(12)
    # target 必须在 try 外赋值：makedirs 失败（盘掉线/磁盘满）时 except 分支
    # 还要靠它回滚，未绑定会抛 NameError 把整个策略应用打成 500
    target = os.path.join(base, ".aurora-trash", trash_id)
    try:
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        shutil.move(src, target)
        size = 0
        try:
            if os.path.isdir(target):
                for root, _dirs, files in os.walk(target):
                    for name in files:
                        size += os.path.getsize(os.path.join(root, name))
            else:
                size = os.path.getsize(target)
        except OSError:
            pass
        trash_add(trash_id, rel.replace(os.sep, "/"), os.path.basename(src) or rel, size)
    except OSError as exc:
        # 元数据写失败时文件必须回原位，否则产生幽灵文件（同 media_delete 的约定）
        try:
            shutil.move(target, src)
        except OSError:
            _LOGGER.warning("policy trash rollback failed for %s", src)
        return "", f"移入回收站失败：{exc}"
    _log("torrent.policy.trash", rel)
    return trash_id, ""


def _policy_restore_trash(trash_id: str) -> tuple[bool, str]:
    """把回收站条目移回原位；原路径被占用时拒绝（绝不覆盖）。"""
    item = trash_get(trash_id)
    if not item:
        return False, "回收站条目不存在"
    base = _media_base_dir()
    src = os.path.join(base, ".aurora-trash", trash_id)
    if not os.path.exists(src):
        trash_remove(trash_id)
        return False, "回收站文件已不存在"
    dst = os.path.realpath(os.path.join(base, str(item.get("path") or "").replace("/", os.sep)))
    if dst != base and not dst.startswith(base + os.sep):
        return False, "恢复路径无效"
    if os.path.exists(dst):
        return False, "原路径已有同名文件，撤销中止"
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
    except OSError as exc:
        return False, f"恢复失败：{exc}"
    trash_remove(trash_id)
    _log("torrent.policy.undo_trash", str(item.get("path") or ""))
    return True, ""


def policy_undo_execute(entry_id: str) -> tuple[bool, str]:
    with _POLICY_UNDO_LOCK:
        entries = _load_policy_undo()
        index = next((i for i, e in enumerate(entries) if e.get("id") == entry_id), -1)
        if index < 0:
            return False, "撤销记录不存在或已过期"
        entry = entries[index]
        if entry.get("action") == "pause":
            ok, error = _qbit.advanced_action(str(entry.get("hash") or ""), "resume")
            detail = "任务已恢复做种"
        else:
            trash_id = str(entry.get("trash_id") or "")
            if trash_id:
                ok, error = _policy_restore_trash(trash_id)
                if not ok:
                    return False, f"文件未恢复，撤销中止：{error}"
                # 文件已回原位：立即清空条目内的 trash_id 并落盘。
                # 否则 add_file 失败后的重试会卡在"回收站条目已不存在"，
                # 永远走不到重加任务那一步
                entry["trash_id"] = ""
                _save_policy_undo(entries)
            torrent_file = str(entry.get("torrent_file") or "")
            content = b""
            if torrent_file and os.path.isfile(torrent_file):
                try:
                    with open(torrent_file, "rb") as f:
                        content = f.read()
                except OSError as exc:
                    return False, f"读取留档种子失败：{exc}"
            if content:
                ok, error = _qbit.add_file(
                    f"{entry.get('name') or 'undo'}.torrent", content,
                    save_path=str(entry.get("save_path") or ""),
                    category=str(entry.get("category") or ""),
                    tags=[str(t) for t in (entry.get("tags") or [])],
                )
                detail = "文件已回原位，任务已重新添加"
            elif trash_id:
                ok, detail, error = True, "文件已回原位；留档种子缺失，请校验后手动重加", ""
            else:
                ok, detail, error = False, "", "没有可用的留档种子，无法撤销"
        if not ok:
            return False, error or "撤销失败"
        torrent_file = str(entry.get("torrent_file") or "")
        if torrent_file and os.path.isfile(torrent_file):
            try:
                os.unlink(torrent_file)
            except OSError:
                pass
        entries.pop(index)
        _save_policy_undo(entries)
        _log("torrent.policy.undo", f"{entry.get('name') or entry_id} · {detail}")
        return True, detail


_POLICY_NOTIFY_FILE = os.path.join(_DATA_DIR, "torrent_policy_notify.json")
_disk_warned = False


_POLICY_ACTIONS = {"pause", "notify", "remove"}
def _strict_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    return value if isinstance(value, bool) else False


def _normalize_policy(raw: dict, index: int = 0) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or f"策略 {index + 1}").strip()[:40]
    if not name:
        return None
    category = str(raw.get("category") or "").strip()[:64]
    hash_filter = str(raw.get("hash") or "").strip().lower()
    if hash_filter and not re.fullmatch(r"[0-9a-f]{40}", hash_filter):
        hash_filter = ""
    action = str(raw.get("action") or "pause").strip().lower()
    if action not in _POLICY_ACTIONS | {"transfer"}:
        action = "pause"

    def integer(key: str, default: int, maximum: int, minimum: int = -1) -> int:
        try:
            return min(maximum, max(minimum, int(raw.get(key, default))))
        except (TypeError, ValueError):
            return default

    def ratio(key: str, default: float) -> float:
        try:
            value = float(raw.get(key, default))
            return round(min(100000.0, max(-1.0, value)), 3)
        except (TypeError, ValueError):
            return default

    policy_id = str(raw.get("id") or uuid.uuid4().hex[:12])
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,40}", policy_id):
        policy_id = uuid.uuid4().hex[:12]
    return {
        "id": policy_id,
        "name": name,
        "category": category,
        "hash": hash_filter,
        "enabled": _strict_bool(raw.get("enabled"), True),
        "action": action,
        "min_seed_minutes": integer("min_seed_minutes", 0, 525600, 0),
        "max_seed_minutes": integer("max_seed_minutes", -1, 525600),
        "max_inactive_minutes": integer("max_inactive_minutes", -1, 525600),
        "max_ratio": ratio("max_ratio", -1.0),
        "allow_delete": _strict_bool(raw.get("allow_delete")),
        "delete_files": _strict_bool(raw.get("delete_files")),
        "destination_remote": str(raw.get("destination_remote") or "").strip()[:64],
        "destination_path": str(raw.get("destination_path") or "").strip()[:2048],
    }


def _normalize_policies(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    rules = []
    seen = set()
    for index, item in enumerate(raw.get("rules") or []):
        policy = _normalize_policy(item, index)
        if policy and policy["id"] not in seen:
            rules.append(policy)
            seen.add(policy["id"])
        if len(rules) >= 32:
            break
    try:
        interval = min(86400, max(60, int(raw.get("interval", 300))))
    except (TypeError, ValueError):
        interval = 300
    return {"enabled": _strict_bool(raw.get("enabled")), "interval": interval, "rules": rules}


def _clean_category_mapping_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or raw == ".":
        return ""
    if "\x00" in raw or raw.startswith("/") or any(part == ".." for part in raw.split("/")):
        return ""
    clean = posixpath.normpath("/".join(part for part in raw.split("/") if part not in ("", ".")))
    return "" if clean == "." else clean[:2048]


def _normalize_category_mappings(raw) -> dict[str, dict]:
    if isinstance(raw, list):
        entries = [(item.get("category", ""), item) for item in raw if isinstance(item, dict)]
    elif isinstance(raw, dict):
        entries = [(name, value) for name, value in raw.items()]
    else:
        entries = []
    result = {}
    for category, value in entries:
        if not isinstance(value, dict):
            continue
        category = str(value.get("category") or category or "").strip()[:64]
        if not QbittorrentProvider._valid_label(category) or category in result:
            continue
        remote = str(value.get("destination_remote") or "").strip()[:64]
        remote_path = ""
        if remote:
            try:
                remote_path = _clean_rclone_path(value.get("destination_path", ""))
            except ValueError:
                remote_path = ""
        result[category] = {
            "category": category,
            "local_path": _clean_category_mapping_path(value.get("local_path", "")),
            "destination_remote": remote,
            "destination_path": remote_path,
        }
        if len(result) >= 64:
            break
    return result


def _default_settings() -> dict:
    tok = os.environ.get("AURORA_TG_BOT_TOKEN", "")
    chat = os.environ.get("AURORA_TG_CHAT_ID", "")
    tokens = [{"name": "主", "token": tok, "chat_id": chat}] if tok and chat else []
    return {
        "alerts": {"torrent": True, "disk": True, "diskWarn": 90.0},
        "daily": {"enabled": False, "time": "21:00"},
        "tg": {"enabled": bool(tokens), "tokens": tokens},
        "policies": {"enabled": False, "interval": 300, "rules": []},
        "category_mappings": {},
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
        d["policies"] = _normalize_policies(s.get("policies"))
        d["category_mappings"] = _normalize_category_mappings(s.get("category_mappings"))
    except Exception:
        pass
    return d


# 设置接口对 secret 字段的回显掩码：前端原样回传表示"未修改"，
# save_settings 用磁盘上的原值替换，避免把掩码存成真实 token
_TOKEN_MASK = "__aurora_masked__"


def _mask_tokens(settings: dict) -> dict:
    tg = settings.get("tg")
    if isinstance(tg, dict) and isinstance(tg.get("tokens"), list):
        tg["tokens"] = [
            {**t, "token": _TOKEN_MASK} if isinstance(t, dict) and t.get("token") else t
            for t in tg["tokens"]
        ]
    return settings


def save_settings(s: dict):
    d = _default_settings()
    for k in d:
        if k == "category_mappings":
            d[k] = _normalize_category_mappings(s.get(k))
        elif k in s and isinstance(s[k], dict):
            d[k].update({kk: vv for kk, vv in s[k].items() if kk in d[k]})
    if isinstance(s.get("tg", {}).get("tokens"), list):
        d["tg"]["tokens"] = s["tg"]["tokens"]
    d["policies"] = _normalize_policies(s.get("policies"))
    try:
        d["alerts"]["diskWarn"] = min(99.0, max(10.0, float(d["alerts"]["diskWarn"])))
    except (TypeError, ValueError):
        d["alerts"]["diskWarn"] = 90.0
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(d["daily"].get("time", ""))):
        d["daily"]["time"] = "21:00"
    # 掩码值按槽位还原为磁盘上的原 token；用户清空输入则真正删除 token
    stored_tokens: dict[int, dict] = {}
    try:
        with open(_SETTINGS_FILE) as f:
            stored = json.load(f)
        if isinstance(stored, dict) and isinstance(stored.get("tg", {}).get("tokens"), list):
            stored_tokens = {i: t for i, t in enumerate(stored["tg"]["tokens"]) if isinstance(t, dict)}
    except Exception:
        pass
    clean_tokens = []
    for idx, token in enumerate(d["tg"].get("tokens", [])[:2]):
        if not isinstance(token, dict):
            continue
        token_value = str(token.get("token", ""))[:256]
        if token_value == _TOKEN_MASK:
            token_value = str(stored_tokens.get(idx, {}).get("token", ""))[:256]
        clean_tokens.append({
            "name": str(token.get("name", ""))[:20],
            "token": token_value,
            "chat_id": str(token.get("chat_id", ""))[:128],
        })
    d["tg"]["tokens"] = clean_tokens
    with _DATA_LOCK:
        _atomic_json(_SETTINGS_FILE, d)
    return _mask_tokens(d)


def torrent_category_mappings() -> list[dict]:
    mappings = load_settings().get("category_mappings", {})
    return [mappings[key] for key in sorted(mappings, key=str.casefold)]


def save_torrent_category_mappings(value) -> list[dict]:
    # load → modify → save 必须整体持锁：与 save_torrent_policies 并发时，
    # 无锁读到的旧快照整体覆盖写回会丢掉对方刚保存的区块
    with _DATA_LOCK:
        current = load_settings()
        current["category_mappings"] = _normalize_category_mappings(value)
        saved = save_settings(current)
    mappings = saved.get("category_mappings", {})
    _log("torrent.category_mappings", f"{len(mappings)} 条映射")
    return [mappings[key] for key in sorted(mappings, key=str.casefold)]


def apply_category_mapping(category: str, save_path: str = "", destination_remote: str = "",
                           destination_path: str = "", apply_destination: bool = True) -> tuple[str, str, str, bool]:
    """Fill only omitted torrent targets from the selected category mapping."""
    category = str(category or "").strip()
    mapping = load_settings().get("category_mappings", {}).get(category)
    if not isinstance(mapping, dict):
        return save_path, destination_remote, destination_path, False
    local = str(save_path or "").strip() or str(mapping.get("local_path") or "")
    remote = str(destination_remote or "").strip()
    remote_path = str(destination_path or "").strip()
    if apply_destination and not remote:
        remote = str(mapping.get("destination_remote") or "").strip()
        if remote and not remote_path:
            remote_path = str(mapping.get("destination_path") or "").strip()
    return local, remote, remote_path, bool(local != str(save_path or "").strip() or remote != str(destination_remote or "").strip() or remote_path != str(destination_path or "").strip())


def torrent_policies() -> dict:
    policies = load_settings().get("policies", {})
    return {"enabled": bool(policies.get("enabled")), "interval": int(policies.get("interval", 300)), "rules": policies.get("rules", [])}


def save_torrent_policies(value: dict) -> dict:
    with _DATA_LOCK:   # 同 save_torrent_category_mappings：读改写整体持锁
        current = load_settings()
        current["policies"] = _normalize_policies(value)
        saved = save_settings(current)
    _log("torrent.policy.settings", f"{len(saved['policies']['rules'])} 条规则")
    return saved["policies"]


def _load_policy_notifications() -> dict:
    try:
        with open(_POLICY_NOTIFY_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_policy_notifications(data: dict) -> None:
    try:
        _atomic_json(_POLICY_NOTIFY_FILE, data)
    except Exception as exc:
        _LOGGER.warning("torrent policy notification state write failed: %s", exc)


def _policy_matches(rule: dict, torrent: dict) -> bool:
    hash_filter = str(rule.get("hash") or "").strip().lower()
    if hash_filter and hash_filter != str(torrent.get("hash") or "").strip().lower():
        return False
    category = str(rule.get("category") or "").strip()
    return not category or category == "*" or category == str(torrent.get("category") or "").strip()


def _policy_trigger(rule: dict, torrent: dict) -> str:
    try:
        seed_minutes = max(0, int(torrent.get("seeding_time") or 0) // 60)
        inactive_minutes = max(0, int(torrent.get("inactive_seeding_time") or 0) // 60)
    except (TypeError, ValueError):
        seed_minutes = inactive_minutes = 0
    try:
        ratio_value = float(torrent.get("ratio") or 0)
    except (TypeError, ValueError):
        ratio_value = 0.0
    if seed_minutes < int(rule.get("min_seed_minutes", 0)):
        return ""
    if int(rule.get("max_seed_minutes", -1)) >= 0 and seed_minutes >= int(rule["max_seed_minutes"]):
        return f"做种时间达到 {seed_minutes} 分钟"
    if int(rule.get("max_inactive_minutes", -1)) >= 0 and inactive_minutes >= int(rule["max_inactive_minutes"]):
        return f"空闲做种达到 {inactive_minutes} 分钟"
    if float(rule.get("max_ratio", -1)) >= 0 and ratio_value >= float(rule["max_ratio"]):
        return f"分享率达到 {ratio_value:.2f}"
    return ""


def _policy_protection(torrent: dict) -> str:
    tags = _tag_list(torrent.get("tags", ""))
    if "aurora-protected" in tags:
        return "任务带有 aurora-protected 保护标签"
    destination = torrent_destination_for_tags(torrent.get("tags", ""))
    if destination and destination.get("status") != "done":
        return "网盘转存尚未完成"
    return ""


def preview_torrent_policies() -> tuple[bool, dict, str]:
    if not _qbit.available():
        return False, {}, "qBittorrent 未接入"
    details = _qbit.torrent_details()
    if not getattr(_qbit, "_last_details_ok", True):
        return False, {}, "qBittorrent 任务读取失败"
    settings = torrent_policies()
    rules = [rule for rule in settings["rules"] if rule.get("enabled", True)]
    items = []
    for torrent in details:
        state = str(torrent.get("state") or "")
        if state not in QbittorrentProvider._UP_STATES:
            continue
        for rule in rules:
            if not _policy_matches(rule, torrent):
                continue
            reason = _policy_trigger(rule, torrent)
            if not reason:
                continue
            protection = _policy_protection(torrent)
            items.append({
                "hash": str(torrent.get("hash") or ""),
                "name": str(torrent.get("name") or ""),
                "category": str(torrent.get("category") or ""),
                "tags": _tag_list(torrent.get("tags", "")),
                "save_path": str(torrent.get("save_path") or ""),
                # 宿主机映射路径：回收站搬移与可恢复判定都基于它
                "content_path": _torrent_host_path(torrent) or str(torrent.get("content_path") or ""),
                "size": max(0, int(torrent.get("size") or 0)),
                "ratio": float(torrent.get("ratio") or 0),
                "seeding_minutes": max(0, int(torrent.get("seeding_time") or 0) // 60),
                "rule_id": rule["id"],
                "rule_name": rule["name"],
                "action": rule["action"],
                "reason": reason,
                "protected": bool(protection),
                "protection": protection,
                "delete_files": bool(rule.get("delete_files")) and bool(rule.get("allow_delete")),
            })
            break
    return True, {"enabled": settings["enabled"], "interval": settings["interval"], "items": items}, ""


def apply_torrent_policies(confirm: bool = False, automatic: bool = False,
                           dry_run: bool = False) -> tuple[bool, dict, str]:
    if not confirm and not automatic:
        return False, {}, "请先确认应用策略"
    ok, preview, detail = preview_torrent_policies()
    if not ok:
        return False, {}, detail
    settings = torrent_policies()
    by_id = {rule["id"]: rule for rule in settings["rules"]}
    if dry_run:
        # 预演：完整走一遍判定分支但不产生任何副作用（不动任务、不发通知）
        items = []
        for item in preview["items"]:
            rule = by_id.get(item["rule_id"], {})
            action = item["action"]
            if item["protected"] and action == "remove":
                items.append({**item, "status": "protected"})
                continue
            if automatic and action == "remove" and not rule.get("allow_delete"):
                items.append({**item, "status": "skipped"})
                continue
            if action == "notify":
                key = f"{datetime.now().strftime('%Y-%m-%d')}:{item['rule_id']}:{item['hash']}:{item['reason']}"
                status = "already_notified" if _load_policy_notifications().get(key) else "would_notify"
                items.append({**item, "status": status})
                continue
            if action == "pause":
                items.append({**item, "status": "would_pause"})
                continue
            if action == "transfer":
                if torrent_destination_for_tags(item.get("tags", [])):
                    items.append({**item, "status": "protected", "protection": "已有网盘转存记录"})
                elif not str(rule.get("destination_remote") or "").strip():
                    items.append({**item, "status": "would_error", "detail": "策略未配置目标网盘"})
                else:
                    items.append({**item, "status": "would_transfer"})
                continue
            items.append({**item, "status": "would_remove",
                          "recoverable": _content_trashable(item.get("content_path", ""))})
        return True, {"items": items, "applied": 0, "dry_run": True}, ""
    results = []
    notifications = _load_policy_notifications()
    today = datetime.now().strftime("%Y-%m-%d")
    for item in preview["items"]:
        rule = by_id.get(item["rule_id"], {})
        action = item["action"]
        if item["protected"] and action == "remove":
            results.append({**item, "status": "protected"})
            continue
        if automatic and action == "remove" and not rule.get("allow_delete"):
            results.append({**item, "status": "skipped"})
            continue
        if action == "notify":
            key = f"{today}:{item['rule_id']}:{item['hash']}:{item['reason']}"
            if notifications.get(key):
                results.append({**item, "status": "already_notified"})
                continue
            message = f"Aurora 做种策略提醒\n{item['name']}\n原因：{item['reason']}"
            sent = _tg(message)
            if sent:
                notifications[key] = int(time.time())
            # 发送失败不记录：下一轮检查会重试，避免"通知失败却永远不再发"
            _log("torrent.policy.notify", f"{item['name']} · {item['reason']}" + (" · 已发送" if sent else " · 发送失败，待重试"))
            results.append({**item, "status": "notified" if sent else "logged"})
            continue
        if action == "pause":
            changed, error = _qbit.advanced_action(item["hash"], "pause")
        elif action == "transfer":
            existing = torrent_destination_for_tags(item.get("tags", []))
            if existing:
                results.append({**item, "status": "protected", "protection": "已有网盘转存记录"})
                continue
            remote = str(rule.get("destination_remote") or "").strip()
            path = str(rule.get("destination_path") or "").strip()
            if not remote:
                results.append({**item, "status": "error", "detail": "策略未配置目标网盘"})
                continue
            marker, _path, error = register_torrent_destination(remote, path)
            if not marker:
                results.append({**item, "status": "error", "detail": error or "网盘目标无效"})
                continue
            changed, error = _qbit.add_system_tags(item["hash"], [marker])
            if not changed:
                discard_torrent_destination(marker)
            else:
                _log("torrent.policy.transfer", f"{item['name']} -> {remote}:{_path or '/'}")
        else:
            delete_files = bool(rule.get("delete_files")) and bool(rule.get("allow_delete"))
            undo_id = uuid.uuid4().hex[:12]
            torrent_file = ""
            trash_id = ""
            # 任何 remove 都先导出 .torrent 留档：磁力任务没有元数据，
            # 不留档就永远无法撤销重加
            ok_export, torrent_data, export_error = _qbit.export_torrent(item["hash"])
            if not ok_export:
                results.append({**item, "status": "error",
                                "detail": export_error or "导出种子留档失败，已跳过删除"})
                continue
            os.makedirs(_POLICY_UNDO_DIR, exist_ok=True)
            torrent_file = os.path.join(_POLICY_UNDO_DIR, f"{undo_id}.torrent")
            with open(torrent_file, "wb") as fh:
                fh.write(torrent_data)
            try:
                os.chmod(torrent_file, 0o600)
            except OSError:
                pass
            delete_via_qbit = False
            if delete_files:
                # 回收站式清理：内容整体移入 .aurora-trash（与媒资库回收站共用），
                # 再移除任务本体且不连带删文件；失败路径都会回滚
                trash_id, _trash_error = _policy_trash_content(item.get("content_path", ""))
                # trash_id 为空 = 内容不在媒体目录内（自定义保存路径），
                # 回退旧行为由 qbit 连带删除，撤销只能重加任务
                delete_via_qbit = not trash_id
            changed, error = _qbit.advanced_action(item["hash"], "remove", delete_files=delete_via_qbit)
            if not changed:
                if trash_id:
                    _policy_restore_trash(trash_id)
                try:
                    os.unlink(torrent_file)
                except OSError:
                    pass
                results.append({**item, "status": "error", "detail": error or "移除任务失败"})
                continue
            _policy_undo_add({
                "id": undo_id,
                "time": int(time.time()),
                "action": "remove",
                "hash": item["hash"],
                "name": item["name"],
                "save_path": item.get("save_path") or "",
                "category": item.get("category") or "",
                "tags": item.get("tags") or [],
                "rule_name": rule.get("name") or "",
                "reason": item.get("reason") or "",
                "trash_id": trash_id,
                "torrent_file": torrent_file,
            })
        status = "applied" if changed else "error"
        _log("torrent.policy.apply", f"{item['name']} · {action} · {item['reason']}" + (f" · {error}" if error else ""))
        results.append({**item, "status": status, "detail": error})
    # 键以 "YYYY-MM-DD:" 开头：按日期裁剪，避免文件随运行时间无限膨胀
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    notifications = {k: v for k, v in notifications.items()
                     if str(k).split(":", 1)[0] >= cutoff}
    _save_policy_notifications(notifications)
    return True, {"items": results, "applied": sum(1 for item in results if item["status"] == "applied")}, ""


_policy_sched_started = False


def start_torrent_policy_scheduler() -> None:
    global _policy_sched_started
    if _policy_sched_started:
        return
    _policy_sched_started = True

    def loop():
        while True:
            settings = torrent_policies()
            if settings.get("enabled") and settings.get("rules"):
                try:
                    apply_torrent_policies(confirm=True, automatic=True)
                except Exception as exc:
                    _log("torrent.policy.error", str(exc))
            time.sleep(max(60, min(3600, int(settings.get("interval", 300)))))

    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------------------
# 下载完成自动整理：完成的任务按分类规则硬链接/拷贝/移动到媒体库目录，
# 可选触发 Jellyfin 全库扫描。硬链接优先——同盘瞬时完成且不打断做种；
# move 是破坏性动作，仅跨盘保护通过且任务不受保护时执行。


_MEDIA_ORG_FILE = os.path.join(_DATA_DIR, "media_organize.json")
_MEDIA_ORG_LOCK = threading.RLock()
_MEDIA_ORG_HISTORY_LIMIT = 200
_ORG_SCHED_STARTED = False
_ORG_MODES = ("hardlink", "copy", "move")


def _safe_org_name(name: str) -> str:
    v = str(name or "").strip().replace("\\", "/").split("/")[-1]
    v = v.replace("..", "_").strip()
    return (v or "untitled")[:150]


def _clean_org_target(value) -> str:
    """媒体库目录必须是媒体目录内的安全相对路径（禁止绝对路径 / .. / 回收站）。"""
    raw = str(value or "").strip()
    if not raw or raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        return ""
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p in ("..", ".aurora-trash") for p in parts) or len("/".join(parts)) > 400:
        return ""
    return "/".join(parts)


def _org_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _normalize_org_rules(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    rules, seen = [], set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id") or uuid.uuid4().hex[:12])
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,40}", rid) or rid in seen:
            rid = uuid.uuid4().hex[:12]
        seen.add(rid)
        target = _clean_org_target(item.get("target_dir"))
        if not target:
            continue
        mode = str(item.get("mode") or "hardlink")
        if mode not in _ORG_MODES:
            mode = "hardlink"
        rules.append({
            "id": rid,
            "name": str(item.get("name") or f"规则 {index + 1}")[:60],
            "category": str(item.get("category") or "").strip()[:64],
            "enabled": _strict_bool(item.get("enabled"), True),
            "target_dir": target,
            "mode": mode,
            "use_subfolder": _strict_bool(item.get("use_subfolder"), True),
            "min_size_mb": _org_int(item.get("min_size_mb"), 0, 0, 1024 * 1024),
        })
    return rules


def media_organize_settings() -> dict:
    try:
        with open(_MEDIA_ORG_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "enabled": _strict_bool(data.get("enabled"), False),
        "interval": _org_int(data.get("interval"), 300, 60, 86400),
        "jellyfin_refresh": _strict_bool(data.get("jellyfin_refresh"), True),
        "rules": _normalize_org_rules(data.get("rules")),
        "history": [h for h in (data.get("history") or []) if isinstance(h, dict)][-_MEDIA_ORG_HISTORY_LIMIT:],
    }


def save_media_organize_settings(value: dict) -> dict:
    if not isinstance(value, dict):
        value = {}
    current = media_organize_settings()
    with _MEDIA_ORG_LOCK:
        merged = {
            "enabled": _strict_bool(value.get("enabled"), current["enabled"]),
            "interval": _org_int(value.get("interval"), current["interval"], 60, 86400),
            "jellyfin_refresh": _strict_bool(value.get("jellyfin_refresh"), current["jellyfin_refresh"]),
            "rules": _normalize_org_rules(value.get("rules") if value.get("rules") is not None else current["rules"]),
            "history": current["history"],
        }
        _atomic_json(_MEDIA_ORG_FILE, merged)
    _log("media.organize.settings", f"{len(merged['rules'])} 条规则 · {'已启用' if merged['enabled'] else '未启用'}")
    return merged


def _org_history_add(entry: dict) -> None:
    with _MEDIA_ORG_LOCK:
        data = media_organize_settings()
        history = [*data["history"], entry][-_MEDIA_ORG_HISTORY_LIMIT:]
        _atomic_json(_MEDIA_ORG_FILE, {**data, "history": history})


def _org_target(rule: dict, torrent: dict) -> tuple[str, str]:
    """返回 (目标完整路径, 用于展示的相对路径)。"""
    base = _media_base_dir()
    name = _safe_org_name(torrent.get("name") or "")
    rel = rule["target_dir"] if rule.get("use_subfolder", True) else rule["target_dir"]
    if rule.get("use_subfolder", True):
        rel = f"{rule['target_dir']}/{name}"
    return os.path.join(base, rel.replace("/", os.sep)), rel


def preview_media_organize() -> tuple[bool, dict, str]:
    if not _qbit.available():
        return False, {}, "qBittorrent 未接入"
    details = _qbit.torrent_details()
    if not getattr(_qbit, "_last_details_ok", True):
        return False, {}, "qBittorrent 任务读取失败"
    settings = media_organize_settings()
    rules = [r for r in settings["rules"] if r["enabled"]]
    done_hashes = {h.get("hash") for h in settings["history"] if h.get("status") == "done"}
    items = []
    for torrent in details:
        if float(torrent.get("progress") or 0) < 0.999999:
            continue
        if str(torrent.get("state") or "") not in QbittorrentProvider._UP_STATES:
            continue
        rule = next((r for r in rules
                     if r["category"] in ("", "*") or r["category"] == str(torrent.get("category") or "").strip()), None)
        if not rule:
            continue
        host = _torrent_host_path(torrent)
        size = max(0, int(torrent.get("size") or 0))
        target_path, target_rel = _org_target(rule, torrent)
        skipped = ""
        if not host or not os.path.exists(host):
            skipped = "内容不在媒体目录内"
        elif rule["min_size_mb"] and size < rule["min_size_mb"] * 1024 * 1024:
            skipped = f"小于规则最小 {rule['min_size_mb']} MB"
        elif rule["mode"] == "move" and _policy_protection(torrent):
            skipped = f"移动模式受保护：{_policy_protection(torrent)}"
        items.append({
            "hash": str(torrent.get("hash") or ""),
            "name": str(torrent.get("name") or ""),
            "category": str(torrent.get("category") or ""),
            "size": size,
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "mode": rule["mode"],
            "source": host,
            "target": target_rel,
            "target_path": target_path,
            "exists": os.path.exists(target_path),
            "processed": str(torrent.get("hash") or "") in done_hashes,
            "skipped_reason": skipped,
        })
    return True, {"enabled": settings["enabled"], "jellyfin_refresh": settings["jellyfin_refresh"],
                  "rule_count": len(rules), "items": items}, ""


def _link_tree_into(src: str, dst: str) -> None:
    """把目录内容逐项硬链接进 dst（dst 必须已存在，由调用方独占创建）。"""
    for entry in os.scandir(src):
        s, d = entry.path, os.path.join(dst, entry.name)
        if entry.is_dir(follow_symlinks=False):
            os.mkdir(d)
            _link_tree_into(s, d)
        else:
            os.link(s, d)


def _org_cleanup(dst: str) -> None:
    try:
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        elif os.path.exists(dst):
            os.unlink(dst)
    except OSError:
        pass


def _org_transfer(source: str, target_path: str, mode: str) -> tuple[bool, str]:
    src = os.path.realpath(source)
    if os.path.exists(target_path):
        return False, "目标已有同名内容，不覆盖"
    if not os.path.exists(src):
        return False, "源内容不存在"
    try:
        if mode == "move" and os.stat(src).st_dev != os.stat(_media_base_dir()).st_dev:
            return False, "源与媒体目录跨磁盘，move 不可用，请改用硬链接或拷贝"
    except OSError as exc:
        return False, f"整理失败：{exc}"

    if mode == "move":
        # 同盘 rename 原子完成，无半成品需要清理；检查后瞬间被占用的窗口
        # 只会返回失败，不存在误删他人内容的问题
        try:
            shutil.move(src, target_path)
        except OSError as exc:
            return False, f"整理失败：{exc}"
        return True, ""

    # hardlink / copy：目标目录必须由本调用独占创建。mkdir 撞车（调度器与
    # 手动整理并发、多条规则同目标）时返回冲突，绝不能 rmtree——那会把
    # 并发方刚写完的整理成果一起删掉
    is_dir_src = os.path.isdir(src)
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        os.mkdir(target_path)
    except FileExistsError:
        return False, "目标已有同名内容，不覆盖"
    except OSError as exc:
        return False, f"整理失败：{exc}"

    try:
        if is_dir_src:
            if mode == "hardlink":
                _link_tree_into(src, target_path)
            else:
                shutil.copytree(src, target_path)
        else:
            if mode == "hardlink":
                os.link(src, target_path)
            else:
                shutil.copy2(src, target_path)
    except FileExistsError:
        # 目录是本调用建的，中途撞名说明有并发写者：清掉自己的半成品即可
        if is_dir_src:
            _org_cleanup(target_path)
        return False, "目标已有同名内容，不覆盖"
    except OSError as exc:
        if is_dir_src:
            _org_cleanup(target_path)
        else:
            # 单文件半截拷贝清掉；hardlink 失败时目标不存在，unlink 为空操作
            try:
                os.unlink(target_path)
            except OSError:
                pass
        return False, f"整理失败：{exc}"
    return True, ""


def apply_media_organize(confirm: bool = False, dry_run: bool = False) -> tuple[bool, dict, str]:
    if not confirm:
        return False, {}, "请先确认执行整理"
    ok, preview, detail = preview_media_organize()
    if not ok:
        return False, {}, detail
    results = []
    organized = 0
    for item in preview["items"]:
        if item["processed"]:
            results.append({**item, "status": "already_done"})
            continue
        if item["skipped_reason"]:
            results.append({**item, "status": "skipped", "detail": item["skipped_reason"]})
            continue
        if item["exists"]:
            results.append({**item, "status": "conflict", "detail": "目标已有同名内容"})
            continue
        if dry_run:
            results.append({**item, "status": "would_organize"})
            continue
        ok_move, error = _org_transfer(item["source"], item["target_path"], item["mode"])
        if ok_move and item["mode"] == "move":
            # move 已把内容搬出下载目录，任务继续做种只会停在"文件丢失"：
            # 主动暂停并留痕（不删除，去留交给用户决定）
            paused, pause_error = _qbit.advanced_action(item["hash"], "pause")
            note = "文件已移出下载目录，任务已自动暂停" if paused \
                else f"文件已移出下载目录，自动暂停任务失败：{pause_error}"
            error = f"{error}；{note}" if error else note
        _org_history_add({
            "time": int(time.time()), "hash": item["hash"], "name": item["name"],
            "rule_name": item["rule_name"], "mode": item["mode"], "target": item["target"],
            "status": "done" if ok_move else "error", "detail": error,
        })
        organized += 1 if ok_move else 0
        results.append({**item, "status": "done" if ok_move else "error", "detail": error})
    refreshed = False
    refresh_detail = ""
    if not dry_run and organized and media_organize_settings()["jellyfin_refresh"]:
        refreshed, refresh_detail = _jelly.refresh_library()
        _log("media.organize.jellyfin", "已触发 Jellyfin 全库扫描" if refreshed else f"Jellyfin 刷新失败：{refresh_detail}")
    _log("media.organize.apply", f"整理 {organized} 项" + (f" · Jellyfin {'已刷新' if refreshed else '未刷新'}" if not dry_run else " · 预演"))
    return True, {"items": results, "organized": organized, "dry_run": dry_run,
                  "jellyfin_refreshed": refreshed, "jellyfin_detail": refresh_detail}, ""


def start_media_organizer_scheduler() -> None:
    global _ORG_SCHED_STARTED
    if _ORG_SCHED_STARTED:
        return
    _ORG_SCHED_STARTED = True

    def loop():
        while True:
            settings = media_organize_settings()
            if settings["enabled"] and settings["rules"]:
                try:
                    apply_media_organize(confirm=True)
                except Exception as exc:
                    _log("media.organize.error", str(exc))
            time.sleep(max(60, min(3600, settings["interval"])))

    threading.Thread(target=loop, daemon=True).start()


def _tg_proxies() -> dict | None:
    """TG 通道代理：api.telegram.org 在部分网络不可直连。AURORA_TG_PROXY
    只代理 Telegram（rclone/qbit/Jellyfin 适配器流量不受影响）；未设置时
    返回 None，保持 requests 默认行为（仍会读标准 HTTPS_PROXY 环境变量）。"""
    p = os.environ.get("AURORA_TG_PROXY", "").strip()
    return {"http": p, "https": p} if p else None


def _tg_send(token: str, chat_id: str, text: str):
    import requests
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text}, timeout=8,
                          proxies=_tg_proxies())
        if r.status_code == 200:
            return True, ""
        try:
            desc = r.json().get("description") or f"HTTP {r.status_code}"
        except Exception:
            desc = f"HTTP {r.status_code}"
        return False, desc
    except requests.exceptions.Timeout:
        return False, ("连接 api.telegram.org 超时：服务器网络可能无法直连 Telegram。"
                       "请配置 AURORA_TG_PROXY（如 http://host.docker.internal:7890）后重试")
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
    # qBittorrent 掉线时 torrents 为空：直接跳过。绝不能在这里清空基线，
    # 否则恢复后"首次运行基线"会把掉线期间完成/失败的种子全部静默吞掉
    if not torrents:
        return
    st = _load_notify_state()
    # 首次运行（修复上线）：用当前已完成的种子做基线，只记录不补发，避免老种子轰炸
    baseline = not st
    cur = {t["id"]: t for t in torrents}
    changed = False
    for tid, t in cur.items():
        done_ts = t.get("completion_on") or 0
        if done_ts > 0:
            prev = st.get(tid)
            if not baseline and prev != done_ts:
                _log("torrent.done", t["name"])
                _tg(f"下载完成：{t['name']}")
            if prev != done_ts:
                st[tid] = done_ts
                changed = True
        elif t.get("state") == "error" and st.get(tid) != "error":
            _log("torrent.error", t["name"])
            _tg(f"下载失败：{t['name']}")
            st[tid] = "error"
            changed = True
    # 清掉已删除种子的记录（重加同种子时 completion_on 会变，仍能触发通知）
    removed = any(k not in cur for k in st)
    if changed or removed:
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
    up_rate = sum(t.get("upspeed", 0) or 0 for t in torrents) * 1000000  # MB/s(1e6) -> B/s
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
    if not _qbit.available():
        # qbit 掉线时宁可缺一天日报：空数据会生成"当前无做种任务"的错误日报，
        # 且空基线写入快照后，次日"今日上传"会全部按 0 基线计算而虚高
        return False, "qBittorrent 未接入，跳过今日日报"
    torrents = _qbit.torrents()
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
    # 通知/告警必须在锁外执行：TG 发送单通道超时 8s、双通道 16s，放在锁内
    # 会让所有并发 metrics 请求排队假死。_AFTER_LOCK 串行化，保证并发 tick
    # 不会对同一个完成事件重复发送
    _notify_after_metrics(result)
    return result


_AFTER_LOCK = threading.Lock()


def _notify_after_metrics(result: dict) -> None:
    with _AFTER_LOCK:
        try:
            _check_torrent_notify(result.get("torrents") or [])
        except Exception as exc:
            _LOGGER.warning("torrent notify check failed: %s", exc)
        try:
            disk = result.get("disk") or {}
            _check_disk_warn(disk.get("usedGb", 0.0), disk.get("capGb", 0.0))
        except Exception as exc:
            _LOGGER.warning("disk warn check failed: %s", exc)


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
    # 通知与磁盘告警由 metrics() 在锁外触发（_notify_after_metrics），
    # 这里不再同步执行 TG 发送
    return {
        "mounts": mounts,
        "torrents": torrents,
        "streams": streams,
        "disk": disk,
        "bandwidth": _sys.bandwidth(),
        "sources": {**_use, "disk": "system", "bandwidth": "system"},
    }
