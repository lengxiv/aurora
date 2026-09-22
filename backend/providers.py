"""Aurora providers — real adapters that auto-engage when their service is up.

Each provider probes a live endpoint; if the backend service isn't running yet
it reports `available()==False` and the aggregator falls back to demo data.
Deploy the real service (rclone rc / qBittorrent / Jellyfin), point env vars at
it, and the matching section flips to real readings with no frontend change.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import threading
import time
import uuid
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
_TORRENT_DEST_FILE = os.path.join(_DATA_DIR, "torrent_destinations.json")
_DATA_LOCK = threading.RLock()
_TORRENT_DEST_LOCK = threading.RLock()


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
        self._transfer_jobs: dict[str, dict] = {}
        self._transfer_lock = threading.RLock()

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
            "progress": 0.0 if total else None,
            "bytes": 0,
            "total": total,
            "speed": 0,
            "detail": "",
            "created": int(time.time()),
            "finished": 0,
            "cleanup": cleanup,
            "retryable": not bool(cleanup),
            "request": {"endpoint": endpoint, "payload": request_payload},
        }
        with self._transfer_lock:
            self._transfer_jobs[job["id"]] = job
        return self._public_transfer(job)

    @staticmethod
    def _public_transfer(job: dict) -> dict:
        return {k: v for k, v in job.items() if k not in ("rcloneJobId", "cleanup", "request")}

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

    def _refresh_transfer(self, job: dict) -> None:
        if job.get("status") in ("done", "error", "canceled"):
            return
        try:
            status = self._req(
                "/job/status", timeout=4.0, method="POST",
                data=json.dumps({"jobid": job["rcloneJobId"]}),
                headers={"Content-Type": "application/json"},
            ) or {}
        except Exception as e:
            # A temporary status failure should not make an active transfer look failed.
            job["detail"] = str(e).strip()[:180]
            return
        if status.get("finished"):
            job["status"] = "done" if status.get("success") else "error"
            if status.get("success"):
                job["detail"] = ""
            else:
                request = job.get("request") or {}
                payload = request.get("payload") or {}
                destination = str(payload.get("dstFs") or payload.get("fs") or "")
                remote_name = destination.split(":", 1)[0] if ":" in destination else ""
                job["detail"] = _friendly_remote_error(
                    remote_name, status.get("error") or "传输失败", write=job.get("action") != "download",
                )
            job["progress"] = 1.0 if job["status"] == "done" else job.get("progress")
            job["finished"] = int(time.time())
            self._cleanup_transfer(job)
            return
        try:
            stats = self._req("/core/stats", timeout=3.0, method="POST") or {}
            done = max(0, int(stats.get("bytes", 0) or 0))
            total = stats.get("totalBytes")
            if total:
                job["total"] = max(int(total), int(job.get("total") or 0))
            job["bytes"] = max(int(job.get("bytes") or 0), done)
            job["speed"] = max(0, int(float(stats.get("speed", 0) or 0)))
            if job.get("total"):
                job["progress"] = min(1.0, job["bytes"] / job["total"])
        except Exception:
            pass

    def transfers(self) -> list[dict]:
        with self._transfer_lock:
            jobs = list(self._transfer_jobs.values())
        for job in jobs:
            self._refresh_transfer(job)
        with self._transfer_lock:
            ordered = sorted(self._transfer_jobs.values(), key=lambda item: item["created"], reverse=True)
            for job in ordered[_RCLONE_TRANSFER_LIMIT:]:
                self._cleanup_transfer(job)
                self._transfer_jobs.pop(job["id"], None)
            return [self._public_transfer(job) for job in ordered[:_RCLONE_TRANSFER_LIMIT]]

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
            job["status"] = "canceled"
            job["detail"] = "已取消"
            job["finished"] = int(time.time())
            self._cleanup_transfer(job)
            return True, ""
        except Exception as e:
            return False, str(e).strip()[:180] or "取消传输失败"

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
        try:
            retry = self._start_job(
                request["endpoint"], request["payload"], action=action,
                label=label, total=total,
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

    def rename_remote(self, name: str, path: str, new_name: str, is_dir: bool) -> tuple[bool, dict | None, str]:
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
        """Rename a remote by recreating its complete config under a new name."""
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

    def torrent_details(self) -> list[dict]:
        try:
            s = self._ensure()
            return s.get(f"{self.base}/api/v2/torrents/info", timeout=5).json() or []
        except Exception:
            return []

    def torrents(self):
        out = []
        data = self.torrent_details()
        for t in data or []:
            st = _QBIT_STATES.get(t.get("state", "unknown"), "done")
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
            }
            destination = torrent_destination_for_tags(t.get("tags", ""))
            if destination:
                row["destination"] = destination
            out.append(row)
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

    def add(self, magnet: str, save_path: str = "", tag: str = "") -> bool:
        try:
            if not magnet.startswith("magnet:"):
                return False
            s = self._ensure()
            data = {"urls": magnet}
            if save_path:
                data["savepath"] = save_path
            if tag:
                data["tags"] = tag
            r = s.post(f"{self.base}/api/v2/torrents/add", data=data, timeout=6)
            return r.status_code in (200, 201)   # 409=已存在/无效 -> False
        except Exception:
            return False

    def add_file(self, filename: str, content: bytes, save_path: str = "", tag: str = "") -> bool:
        """Forward one .torrent file to qBittorrent's multipart upload endpoint."""
        try:
            if not filename.lower().endswith(".torrent") or not content:
                return False
            s = self._ensure()
            data = {}
            if save_path:
                data["savepath"] = save_path
            if tag:
                data["tags"] = tag
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


def _load_torrent_destinations() -> dict[str, dict]:
    try:
        with open(_TORRENT_DEST_FILE) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return {}


def _save_torrent_destinations(data: dict[str, dict]) -> None:
    try:
        _atomic_json(_TORRENT_DEST_FILE, data)
    except Exception:
        pass


def _tag_list(tags: str) -> list[str]:
    return [item.strip() for item in str(tags or "").split(",") if item.strip()]


def torrent_destination_for_tags(tags: str) -> dict | None:
    """Return the public remote destination attached to a qBittorrent task."""
    with _TORRENT_DEST_LOCK:
        data = _load_torrent_destinations()
        for tag in _tag_list(tags):
            entry = data.get(tag)
            if entry:
                return {
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
        data = _load_torrent_destinations()
        data[marker] = entry
        _save_torrent_destinations(data)
    return marker, path, ""


def discard_torrent_destination(marker: str) -> None:
    if not marker:
        return
    with _TORRENT_DEST_LOCK:
        data = _load_torrent_destinations()
        if marker in data:
            data.pop(marker, None)
            _save_torrent_destinations(data)


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
    with _TORRENT_DEST_LOCK:
        destinations = _load_torrent_destinations()
    if not destinations:
        return
    jobs = {str(job.get("id")): job for job in _rclone.transfers()}
    changed = False
    for marker, entry in destinations.items():
        status = str(entry.get("status") or "waiting")
        if status == "done":
            continue
        if status == "uploading":
            job = jobs.get(str(entry.get("transfer_id") or ""))
            if job and job.get("status") == "done":
                entry["status"] = "done"
                entry["detail"] = ""
                entry["finished"] = int(time.time())
                changed = True
                continue
            if job and job.get("status") == "error":
                entry["status"] = "error"
                entry["detail"] = job.get("detail") or "网盘上传失败"
                entry["finished"] = int(time.time())
                changed = True
                continue
            if job:
                continue
            # The in-memory rclone job may have disappeared after a restart or cleanup.
            if entry.get("local_path"):
                entry["status"] = "waiting"
                entry["transfer_id"] = ""
                changed = True
            continue
        if status == "error":
            continue
        torrent = next((item for item in details if marker in _tag_list(item.get("tags", ""))), None)
        if not torrent:
            continue
        entry["hash"] = torrent.get("hash", "")
        entry["name"] = torrent.get("name", "")
        if float(torrent.get("progress", 0) or 0) < 0.999999:
            changed = True
            continue
        local_path = _torrent_host_path(torrent)
        if not local_path or not os.path.exists(local_path):
            entry["detail"] = "等待本地下载文件就绪"
            changed = True
            continue
        item_name = os.path.basename(local_path.rstrip(os.sep))
        if not item_name:
            entry["status"] = "error"
            entry["detail"] = "无法确定本地下载文件名"
            changed = True
            continue
        try:
            target = _join_rclone_path(entry.get("path", ""), item_name)
        except ValueError:
            entry["status"] = "error"
            entry["detail"] = "网盘目标路径无效"
            changed = True
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
        changed = True
    if changed:
        with _TORRENT_DEST_LOCK:
            _save_torrent_destinations(destinations)


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
