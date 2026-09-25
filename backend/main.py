import hashlib
import hmac
import mimetypes
import os
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import providers

PROJECT_DIR = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("AURORA_STATE_DIR", str(PROJECT_DIR))).expanduser()
STATIC_DIR = PROJECT_DIR / "static"
AUTH_FILE = Path(os.environ.get("AURORA_AUTH_FILE", str(STATE_DIR / ".auth"))).expanduser()
SECRET_FILE = Path(os.environ.get("AURORA_SECRET_FILE", str(STATE_DIR / ".secret"))).expanduser()

app = FastAPI(
    title="Aurora Media Hub",
    version="0.4.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# ---------------------------------------------------------------------------
# auth


def _load_secret() -> bytes:
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes().strip()
    import stat
    s = secrets.token_hex(32).encode()
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.write_bytes(s)
    SECRET_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return s


def _load_credentials() -> tuple[str, str]:
    user = os.environ.get("AURORA_AUTH_USER", "admin")
    pw = os.environ.get("AURORA_AUTH_PASS", "")
    if not pw and AUTH_FILE.exists():
        line = AUTH_FILE.read_text().strip()
        if ":" in line:
            u, p = line.split(":", 1)
            user, pw = u, p
    if not pw:
        import stat
        pw = secrets.token_urlsafe(18)
        AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = AUTH_FILE.with_name(AUTH_FILE.name + ".tmp")
        tmp.write_text(f"{user}:{pw}\n")
        tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(AUTH_FILE)
        print(f"[aurora] 已生成初始密码并写入 {AUTH_FILE}（权限 600），请从该文件读取；日志不打印密码")
    return user, pw


_SECRET = _load_secret()
_AUTH_USER, _AUTH_PASS = _load_credentials()
_COOKIE = "aurora_sid"
_AGE = 12 * 3600
_SESSIONS: dict[str, dict] = {}
_SESSION_LOCK = threading.Lock()

# login throttle (per IP)
FAIL_LIMIT = 5
FAIL_LOCK = 300
_FAILS: dict[str, list[float]] = {}
_FAILS_LOCK = threading.Lock()   # sync 路由跑线程池：check 与 append 之间的竞态会漏记失败


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "?"
    if peer in ("127.0.0.1", "::1"):
        # 只信任可信反代强制覆盖的 X-Real-IP（deploy/nginx 示例会覆盖该头）。
        # X-Forwarded-For 的第一段完全由客户端控制，可被轮换伪造，
        # 一旦采信，失败计数永远记不到 FAIL_LIMIT，锁定形同虚设。
        return request.headers.get("x-real-ip", "").strip() or peer
    return peer


def _record_fail(ip: str) -> None:
    with _FAILS_LOCK:
        lst = [t for t in _FAILS.get(ip, []) if time.time() - t < FAIL_LOCK]
        lst.append(time.time())
        _FAILS[ip] = lst


def _clear_fail(ip: str) -> None:
    with _FAILS_LOCK:
        _FAILS.pop(ip, None)


def _locked(ip: str) -> bool:
    now = time.time()
    with _FAILS_LOCK:
        # cap the table so a flood of distinct IPs can't grow memory unbounded
        if len(_FAILS) > 5000:
            for k in list(_FAILS):
                if now - (_FAILS[k][-1] if _FAILS[k] else 0) > FAIL_LOCK:
                    del _FAILS[k]
        if len(_FAILS) > 5000:
            # 仍然超限：只淘汰最旧的条目。不能整表 clear()——那会一次性清掉
            # 所有攻击者的失败记录，等价于免费解锁。
            oldest = sorted(_FAILS.items(), key=lambda kv: kv[1][-1] if kv[1] else 0)
            for k, _ in oldest[:len(_FAILS) - 5000]:
                _FAILS.pop(k, None)
        recent = [t for t in _FAILS.get(ip, []) if now - t < FAIL_LOCK]
        _FAILS[ip] = recent
        return len(recent) >= FAIL_LIMIT


def _new_sid(user: str, ip: str = "?", user_agent: str = "") -> str:
    exp = int(time.time()) + _AGE
    sid = secrets.token_urlsafe(32)
    with _SESSION_LOCK:
        now = int(time.time())
        for token, session in list(_SESSIONS.items()):
            if session["exp"] < now:
                del _SESSIONS[token]
        _SESSIONS[sid] = {
            "id": secrets.token_hex(8), "user": user, "created": now, "exp": exp,
            "ip": ip[:64], "user_agent": user_agent[:240],
        }
    return sid


def _check_sid(sid: str) -> str | None:
    with _SESSION_LOCK:
        session = _SESSIONS.get(sid)
        if not session:
            return None
        if session["exp"] < time.time():
            _SESSIONS.pop(sid, None)
            return None
        return session["user"]


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        site = request.headers.get("sec-fetch-site", "")
        if site and site not in ("same-origin", "none"):
            return JSONResponse({"detail": "cross-site request rejected"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://static.cloudflareinsights.com; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; "
        "connect-src 'self' https://cloudflareinsights.com; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    if request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        response.headers["Cache-Control"] = "no-store"
    return response


class Login(BaseModel):
    username: str
    password: str


def require_auth(sid: str | None = Cookie(default=None, alias=_COOKIE)) -> str:
    user = _check_sid(sid) if sid else None
    if not user:
        raise HTTPException(status_code=401, detail="unauthorized")
    return user


def _consteq(a: str, b: str) -> bool:
    """Timing-safe comparison that tolerates non-ASCII input.

    hmac.compare_digest raises TypeError on non-ASCII str, which turned any
    Unicode password attempt into a 500 — and a non-ASCII password saved via
    /api/auth/password would have locked the admin out permanently.
    """
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


@app.post("/api/auth/login")
def login(body: Login, request: Request):
    ip = _client_ip(request)
    if _locked(ip):
        raise HTTPException(status_code=429, detail="too many attempts")
    match = _consteq(body.username, _AUTH_USER) and _consteq(body.password, _AUTH_PASS)
    if not match:
        _record_fail(ip)
        raise HTTPException(status_code=401, detail="bad credentials")
    _clear_fail(ip)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").startswith("https")
    resp = JSONResponse({"ok": True, "user": body.username})
    resp.set_cookie(_COOKIE, _new_sid(body.username, ip, request.headers.get("user-agent", "")), max_age=_AGE,
                    httponly=True, samesite="lax", secure=secure, path="/")
    return resp


@app.post("/api/auth/logout")
def logout(sid: str | None = Cookie(default=None, alias=_COOKIE)):
    if sid:
        with _SESSION_LOCK:
            _SESSIONS.pop(sid, None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_COOKIE, path="/")
    return resp


@app.get("/api/auth/me")
def me(user: str = Depends(require_auth)):
    return {"user": user}


class ChangePassword(BaseModel):
    current: str
    new: str


class RevokeSession(BaseModel):
    id: str


@app.get("/api/auth/sessions")
def auth_sessions(_user: str = Depends(require_auth), sid: str | None = Cookie(default=None, alias=_COOKIE)):
    now = int(time.time())
    with _SESSION_LOCK:
        rows = []
        for token, session in _SESSIONS.items():
            if session["exp"] < now:
                continue
            rows.append({
                "id": session["id"], "created": session["created"], "expires": session["exp"],
                "ip": session["ip"], "device": session["user_agent"],
                "current": token == sid,
            })
    return {"sessions": sorted(rows, key=lambda x: x["created"], reverse=True)}


@app.post("/api/auth/sessions/revoke")
def revoke_session(body: RevokeSession, _user: str = Depends(require_auth), sid: str | None = Cookie(default=None, alias=_COOKIE)):
    with _SESSION_LOCK:
        for token, session in list(_SESSIONS.items()):
            if session["id"] == body.id:
                if token == sid:
                    raise HTTPException(status_code=400, detail="请使用退出登录结束当前会话")
                del _SESSIONS[token]
                return {"ok": True}
    raise HTTPException(status_code=404, detail="会话不存在")


@app.post("/api/auth/password")
def change_password(body: ChangePassword, _user: str = Depends(require_auth), sid: str | None = Cookie(default=None, alias=_COOKIE)):
    global _AUTH_PASS
    if os.environ.get("AURORA_AUTH_PASS"):
        raise HTTPException(status_code=409, detail="密码由环境变量管理，无法在线修改")
    if not _consteq(body.current, _AUTH_PASS):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if len(body.new) < 12 or len(body.new) > 128:
        raise HTTPException(status_code=400, detail="新密码长度须为 12-128 位")
    if _consteq(body.new, body.current):
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_FILE.with_name(AUTH_FILE.name + ".tmp")
    tmp.write_text(f"{_AUTH_USER}:{body.new}\n")
    tmp.chmod(0o600)
    tmp.replace(AUTH_FILE)
    _AUTH_PASS = body.new
    with _SESSION_LOCK:
        for token in list(_SESSIONS):
            if token != sid:
                del _SESSIONS[token]
    providers._log("auth.password", "密码已修改，其他会话已撤销")
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"status": "ok", "ts": int(time.time())}


@app.get("/openapi.json", include_in_schema=False)
@app.get("/docs", include_in_schema=False)
@app.get("/redoc", include_in_schema=False)
def hidden_api_docs():
    raise HTTPException(status_code=404, detail="not found")


@app.on_event("startup")
def _startup():
    # 每日做种日报定时推送线程（daemon，跟随服务生命周期）
    providers.start_daily_scheduler()
    # 本地下载完成后自动转存到用户选择的网盘目标
    providers.start_torrent_destination_scheduler()
    # 做种策略默认关闭；开启后仅按设置页中保存的规则执行。
    providers.start_torrent_policy_scheduler()


@app.get("/api/metrics")
def metrics(_user: str = Depends(require_auth)):
    return providers.metrics()


@app.get("/api/sources")
def sources(_user: str = Depends(require_auth)):
    # 探测失败与"正常无数据"必须可区分，否则前端无法告警
    try:
        return providers.metrics().get("sources", {})
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e).strip()[:180] or "sources unavailable")


def _qbit_category(value: str) -> str:
    value = str(value or "").strip()
    if value and not providers.QbittorrentProvider._valid_label(value):
        raise HTTPException(status_code=400, detail="分类名称无效")
    return value


def _qbit_tags(values) -> list[str]:
    raw = values.split(",") if isinstance(values, str) else (values or [])
    if len(list(raw)) > 32 or any(str(item or "").strip().startswith("aurora-") for item in raw):
        raise HTTPException(status_code=400, detail="标签包含保留名称或数量过多")
    tags = providers.QbittorrentProvider._clean_labels(raw)
    if any(not providers.QbittorrentProvider._valid_label(item) for item in tags):
        raise HTTPException(status_code=400, detail="标签名称无效")
    return tags


def _torrent_mapping_payload(values) -> dict[str, dict]:
    if not isinstance(values, list) or len(values) > 64:
        raise HTTPException(status_code=400, detail="分类映射最多 64 条")
    result = {}
    for item in values:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="分类映射格式无效")
        category = _qbit_category(item.get("category", ""))
        if not category or category in result:
            raise HTTPException(status_code=400, detail="分类映射名称重复或无效")
        local_path = str(item.get("local_path") or "").strip()
        if local_path:
            qbit_path = _torrent_save_path(local_path)
            local_path = qbit_path[len("/downloads"):].lstrip("/")
        remote = str(item.get("destination_remote") or "").strip()
        remote_path = str(item.get("destination_path") or "").strip()
        if remote:
            if not providers._rclone.available():
                raise HTTPException(status_code=503, detail="网盘服务未接入，无法保存默认转存目标")
            ok, remote_path, detail = providers._rclone.validate_destination(remote, remote_path)
            if not ok:
                raise HTTPException(status_code=400, detail=detail or "默认网盘目标无效")
        elif remote_path:
            raise HTTPException(status_code=400, detail="未选择网盘时不能填写网盘目录")
        result[category] = {
            "category": category,
            "local_path": local_path,
            "destination_remote": remote,
            "destination_path": remote_path,
        }
    return result


class AddMagnet(BaseModel):
    magnet: str
    save_path: str = ""
    destination_remote: str = ""
    destination_path: str = ""
    destination_mode: str = "default"
    category: str = ""
    tags: list[str] = []


_MAX_TORRENT_FILE = 20 * 1024 * 1024


@app.post("/api/torrents/add")
def add_torrent(body: AddMagnet, _user: str = Depends(require_auth)):
    magnet = body.magnet.strip()
    if not magnet.startswith("magnet:"):
        raise HTTPException(status_code=400, detail="not a magnet link")
    if not providers._qbit.available():
        raise HTTPException(status_code=503, detail="qBittorrent 未接入，无法添加磁力")
    category = _qbit_category(body.category)
    tags = _qbit_tags(body.tags)
    if body.destination_mode not in ("default", "local", "remote"):
        raise HTTPException(status_code=400, detail="下载目标模式无效")
    save_input, remote_input, path_input, mapping_applied = providers.apply_category_mapping(
        category, body.save_path, body.destination_remote, body.destination_path,
        apply_destination=body.destination_mode != "local",
    )
    save_path = _torrent_save_path(save_input)
    marker, destination_path, detail = providers.register_torrent_destination(
        remote_input, path_input,
    )
    if detail:
        raise HTTPException(status_code=400, detail=detail)
    if not providers._qbit.add(magnet, save_path, marker, category, tags):
        providers.discard_torrent_destination(marker)
        raise HTTPException(status_code=502, detail="qBittorrent 拒绝该磁力（链接可能已存在或无效）")
    return {
        "ok": True, "mode": "qbittorrent", "save_path": save_input.strip(),
        "destination_remote": remote_input, "destination_path": destination_path,
        "category": category, "tags": tags, "mapping_applied": mapping_applied,
    }


@app.post("/api/torrents/upload")
async def upload_torrent(
    file: UploadFile = File(...),
    save_path: str = Form(""),
    destination_remote: str = Form(""),
    destination_path: str = Form(""),
    destination_mode: str = Form("default"),
    category: str = Form(""),
    tags: str = Form(""),
    _user: str = Depends(require_auth),
):
    name = Path(file.filename or "").name
    if not name or name == ".torrent" or not name.lower().endswith(".torrent"):
        raise HTTPException(status_code=400, detail="仅支持 .torrent 文件")
    try:
        content = await file.read(_MAX_TORRENT_FILE + 1)
    finally:
        await file.close()
    if not content:
        raise HTTPException(status_code=400, detail="种子文件为空")
    if len(content) > _MAX_TORRENT_FILE:
        raise HTTPException(status_code=413, detail="种子文件不能超过 20 MB")

    # available()/register_torrent_destination/add_file 都是阻塞网络调用，
    # 必须放进线程池，否则 qbit/rclone 卡顿时整个事件循环一起停摆。
    def _submit() -> dict:
        if not providers._qbit.available():
            raise HTTPException(status_code=503, detail="qBittorrent 未接入，无法上传种子")
        category_ = _qbit_category(category)
        tags_ = _qbit_tags(tags)
        if destination_mode not in ("default", "local", "remote"):
            raise HTTPException(status_code=400, detail="下载目标模式无效")
        save_input, remote_input, path_input, mapping_applied = providers.apply_category_mapping(
            category_, save_path, destination_remote, destination_path,
            apply_destination=destination_mode != "local",
        )
        qbit_save_path = _torrent_save_path(save_input)
        marker, destination_path_, detail = providers.register_torrent_destination(
            remote_input, path_input,
        )
        if detail:
            raise HTTPException(status_code=400, detail=detail)
        if not providers._qbit.add_file(name, content, qbit_save_path, marker, category_, tags_):
            providers.discard_torrent_destination(marker)
            raise HTTPException(status_code=502, detail="qBittorrent 拒绝该种子文件")
        return {
            "ok": True, "mode": "qbittorrent", "name": name, "save_path": save_input.strip(),
            "destination_remote": remote_input, "destination_path": destination_path_,
            "category": category_, "tags": tags_, "mapping_applied": mapping_applied,
        }

    return await run_in_threadpool(_submit)


class BatchAction(BaseModel):
    action: str
    ids: list[str]
    category: str = ""
    limit_kib: int | None = None
    location: str = ""
    destination_remote: str = ""
    destination_path: str = ""


@app.post("/api/torrents/batch")
def batch_torrent(body: BatchAction, _user: str = Depends(require_auth)):
    advanced_actions = {"set_download_limit", "set_upload_limit", "set_location", "transfer"}
    if body.action not in {"remove", "pause", "resume"} | advanced_actions:
        raise HTTPException(status_code=400, detail="unknown action")
    qbit = providers._qbit.available()
    if body.action in advanced_actions and not qbit:
        raise HTTPException(status_code=503, detail="qBittorrent 未接入")
    if body.category and not providers.QbittorrentProvider._valid_label(body.category):
        raise HTTPException(status_code=400, detail="分类名称无效")
    if body.action in ("set_download_limit", "set_upload_limit"):
        if body.limit_kib is None or not 0 <= body.limit_kib <= 10_000_000:
            raise HTTPException(status_code=400, detail="限速必须在 0-10000000 KiB/s 之间")
        value = body.limit_kib * 1024
    else:
        value = None
    location = _torrent_save_path(body.location) if body.action == "set_location" else ""
    if body.action == "set_location" and not body.location.strip():
        raise HTTPException(status_code=400, detail="请选择移动目录")
    if body.action == "transfer":
        if not body.destination_remote.strip():
            raise HTTPException(status_code=400, detail="请选择目标网盘")
        if not providers._rclone.available():
            raise HTTPException(status_code=503, detail="网盘服务未接入")
        valid, normalized_path, detail = providers._rclone.validate_destination(
            body.destination_remote.strip(), body.destination_path.strip(),
        )
        if not valid:
            raise HTTPException(status_code=400, detail=detail or "网盘目标无效")
    else:
        normalized_path = ""

    ids = [str(item).strip() for item in body.ids if str(item).strip()]
    if qbit and body.category:
        details = providers._qbit.torrent_details()
        if not getattr(providers._qbit, "_last_details_ok", True):
            raise HTTPException(status_code=502, detail="读取 qBittorrent 任务失败")
        category_ids = {str(item.get("hash") or "") for item in details if str(item.get("category") or "").strip() == body.category}
        ids = [item for item in (ids or sorted(category_ids)) if item in category_ids]
    if not ids:
        raise HTTPException(status_code=400, detail="没有可操作的任务")
    done = failed = 0
    errors = []
    for tid in ids:
        if qbit and body.action in advanced_actions:
            if body.action == "transfer":
                marker, _path, reserve_error = providers.register_torrent_destination(
                    body.destination_remote.strip(), normalized_path,
                )
                changed, error = (False, reserve_error or "网盘目标无效")
                if marker:
                    changed, error = providers._qbit.add_system_tags(tid, [marker])
                    if not changed:
                        providers.discard_torrent_destination(marker)
            else:
                changed, error = providers._qbit.advanced_action(tid, body.action, value=value, location=location)
        elif qbit and providers._qbit.action(tid, body.action):
            changed, error = True, ""
        elif not qbit and providers.mutate(tid, body.action):
            changed, error = True, ""
        else:
            changed, error = False, "任务操作失败"
        if changed:
            done += 1
        else:
            failed += 1
            if len(errors) < 20:
                errors.append({"id": tid, "detail": error})
    return {"ok": True, "done": done, "failed": failed, "errors": errors, "mode": "qbittorrent" if qbit else "demo"}


class TorrentAdvancedAction(BaseModel):
    id: str
    action: str
    limit_kib: int | None = None
    location: str = ""
    delete_files: bool = False
    file_ids: list[int] = []
    all_file_ids: list[int] = []
    priority: int | None = None


@app.get("/api/torrents/detail")
def torrent_detail(hash: str, _user: str = Depends(require_auth)):
    if not providers._qbit.available():
        raise HTTPException(status_code=503, detail="qBittorrent 未接入")
    ok, detail, error = providers._qbit.torrent_detail(hash)
    if not ok:
        raise HTTPException(status_code=404 if error == "任务不存在" else 502, detail=error)
    return {"online": True, "torrent": detail}


@app.post("/api/torrents/advanced")
def torrent_advanced(body: TorrentAdvancedAction, _user: str = Depends(require_auth)):
    if not providers._qbit.available():
        raise HTTPException(status_code=503, detail="qBittorrent 未接入")
    location = ""
    if body.action == "set_location":
        location = _torrent_save_path(body.location)
    if body.action in ("set_download_limit", "set_upload_limit"):
        if body.limit_kib is None or not 0 <= body.limit_kib <= 10_000_000:
            raise HTTPException(status_code=400, detail="限速必须在 0-10000000 KiB/s 之间")
        limit = body.limit_kib * 1024
    else:
        limit = None
    if body.action in ("set_file_priority", "set_file_selection"):
        if not body.file_ids or len(body.file_ids) > 4096:
            raise HTTPException(status_code=400, detail="文件优先级参数无效")
        if body.action == "set_file_priority" and (body.priority is None or not 0 <= body.priority <= 7):
            raise HTTPException(status_code=400, detail="文件优先级参数无效")
        if body.action == "set_file_selection" and (not body.all_file_ids or len(body.all_file_ids) > 4096):
            raise HTTPException(status_code=400, detail="文件选择参数无效")
    ok, detail = providers._qbit.advanced_action(
        body.id, body.action, value=limit, delete_files=body.delete_files, location=location,
        file_ids=body.file_ids, all_file_ids=body.all_file_ids, priority=body.priority,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True}


class TorrentLabelsBody(BaseModel):
    id: str
    category: str | None = None
    tags: list[str] | None = None


class TorrentMappingsBody(BaseModel):
    mappings: list[dict] = []


@app.get("/api/torrents/mappings")
def torrent_mappings(_user: str = Depends(require_auth)):
    return {"mappings": providers.torrent_category_mappings()}


@app.post("/api/torrents/mappings")
def save_torrent_mappings(body: TorrentMappingsBody, _user: str = Depends(require_auth)):
    mappings = _torrent_mapping_payload(body.mappings)
    return {"ok": True, "mappings": providers.save_torrent_category_mappings(mappings)}


@app.get("/api/torrents/labels")
def torrent_labels(_user: str = Depends(require_auth)):
    if not providers._qbit.available():
        return {"online": False, "categories": [], "tags": [], "detail": "qBittorrent 未接入"}
    ok, data, detail = providers._qbit.labels()
    if not ok:
        raise HTTPException(status_code=502, detail=detail or "读取分类标签失败")
    return {"online": True, **data}


class TorrentCategoryBody(BaseModel):
    name: str
    save_path: str = ""


@app.post("/api/torrents/category")
def create_torrent_category(body: TorrentCategoryBody, _user: str = Depends(require_auth)):
    if not providers._qbit.available():
        raise HTTPException(status_code=503, detail="qBittorrent 未接入")
    name = _qbit_category(body.name)
    save_path = _torrent_save_path(body.save_path) if body.save_path.strip() else "/downloads"
    ok, detail = providers._qbit.create_category(name, save_path)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True}


@app.post("/api/torrents/category/edit")
def edit_torrent_category(body: TorrentCategoryBody, _user: str = Depends(require_auth)):
    if not providers._qbit.available():
        raise HTTPException(status_code=503, detail="qBittorrent 未接入")
    name = _qbit_category(body.name)
    save_path = _torrent_save_path(body.save_path) if body.save_path.strip() else "/downloads"
    ok, detail = providers._qbit.edit_category(name, save_path)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True}


@app.post("/api/torrents/category/delete")
def delete_torrent_category(body: TorrentCategoryBody, _user: str = Depends(require_auth)):
    if not providers._qbit.available():
        raise HTTPException(status_code=503, detail="qBittorrent 未接入")
    ok, detail = providers._qbit.delete_category(_qbit_category(body.name))
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True}


class TorrentTagsBody(BaseModel):
    tags: list[str]


@app.post("/api/torrents/tag")
def create_torrent_tags(body: TorrentTagsBody, _user: str = Depends(require_auth)):
    if not providers._qbit.available():
        raise HTTPException(status_code=503, detail="qBittorrent 未接入")
    tags = _qbit_tags(body.tags)
    ok, detail = providers._qbit.create_tags(tags)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True}


@app.post("/api/torrents/tag/delete")
def delete_torrent_tags(body: TorrentTagsBody, _user: str = Depends(require_auth)):
    if not providers._qbit.available():
        raise HTTPException(status_code=503, detail="qBittorrent 未接入")
    tags = _qbit_tags(body.tags)
    ok, detail = providers._qbit.delete_tags(tags)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True}


@app.post("/api/torrents/labels")
def update_torrent_labels(body: TorrentLabelsBody, _user: str = Depends(require_auth)):
    if not providers._qbit.available():
        raise HTTPException(status_code=503, detail="qBittorrent 未接入")
    category = None if body.category is None else _qbit_category(body.category)
    tags = None if body.tags is None else _qbit_tags(body.tags)
    ok, detail, error = providers._qbit.update_labels(body.id, category, tags)
    if not ok:
        raise HTTPException(status_code=400, detail=error)
    return {"ok": True, "torrent": detail}


class TorrentAction(BaseModel):
    id: str


@app.post("/api/torrents/{action}")
def torrent_action(action: str, body: TorrentAction, _user: str = Depends(require_auth)):
    if action not in ("remove", "pause", "resume"):
        raise HTTPException(status_code=400, detail="unknown action")
    if providers._qbit.available() and providers._qbit.action(body.id, action):
        return {"ok": True, "mode": "qbittorrent"}
    ok = providers.mutate(body.id, action)
    return {"ok": ok, "mode": "demo" if ok else ""}


@app.post("/api/torrents/destination/retry")
def torrent_destination_retry(body: TorrentAction, _user: str = Depends(require_auth)):
    ok, detail = providers.retry_torrent_destination(body.id)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True}


@app.get("/api/torrents/peers")
def torrent_peers(hash: str, _user: str = Depends(require_auth)):
    """某个种子的当前对等方（谁在从我们这里下载）。"""
    if providers._qbit.available():
        return providers._qbit.peers(hash)
    return {"peers": [], "connected": 0, "seeds": 0, "leechers": 0}


@app.get("/api/logs")
def logs(_user: str = Depends(require_auth)):
    return {"logs": providers.logs(20)}


@app.get("/api/stats")
def stats(_user: str = Depends(require_auth)):
    return {"days": providers.stats(14)}


@app.get("/api/settings")
def get_settings(_user: str = Depends(require_auth)):
    # TG token 只回显掩码；前端原样回传即表示未修改（providers.save_settings 还原）
    return providers._mask_tokens(providers.load_settings())


class SettingsBody(BaseModel):
    settings: dict


@app.post("/api/settings")
def post_settings(body: SettingsBody, _user: str = Depends(require_auth)):
    return providers.save_settings(body.settings)


class TorrentPoliciesBody(BaseModel):
    enabled: bool = False
    interval: int = 300
    rules: list[dict] = []


@app.get("/api/torrents/policies")
def get_torrent_policies(_user: str = Depends(require_auth)):
    return providers.torrent_policies()


@app.post("/api/torrents/policies")
def post_torrent_policies(body: TorrentPoliciesBody, _user: str = Depends(require_auth)):
    if body.interval < 60 or body.interval > 86400:
        raise HTTPException(status_code=400, detail="策略检查间隔必须在 60-86400 秒之间")
    if len(body.rules) > 32:
        raise HTTPException(status_code=400, detail="策略最多 32 条")
    return {"ok": True, "policies": providers.save_torrent_policies(body.model_dump())}


@app.get("/api/torrents/policies/preview")
def preview_torrent_policies(_user: str = Depends(require_auth)):
    ok, data, detail = providers.preview_torrent_policies()
    if not ok:
        raise HTTPException(status_code=503, detail=detail)
    return data


class TorrentPoliciesApplyBody(BaseModel):
    confirm: bool = False


@app.post("/api/torrents/policies/apply")
def apply_torrent_policies(body: TorrentPoliciesApplyBody, _user: str = Depends(require_auth)):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="请明确确认后应用策略")
    ok, data, detail = providers.apply_torrent_policies(confirm=True)
    if not ok:
        raise HTTPException(status_code=503, detail=detail)
    return {"ok": True, **data}


class QbitQueueBody(BaseModel):
    queueing_enabled: bool
    max_active_torrents: int
    max_active_downloads: int
    max_active_uploads: int
    max_active_checking_torrents: int
    add_to_top_of_queue: bool


def _validate_qbit_queue(body: QbitQueueBody) -> None:
    # qBittorrent treats zero as no active slots, so Aurora uses 9999 as the
    # explicit "unlimited" value shown by the settings UI.
    limits = {
        "max_active_torrents": (body.max_active_torrents, 1, 9999),
        "max_active_downloads": (body.max_active_downloads, 1, 9999),
        "max_active_uploads": (body.max_active_uploads, 1, 9999),
        "max_active_checking_torrents": (body.max_active_checking_torrents, 1, 64),
    }
    for name, (value, minimum, maximum) in limits.items():
        if not minimum <= value <= maximum:
            raise HTTPException(status_code=400, detail=f"{name} 必须在 {minimum}-{maximum} 之间")


@app.get("/api/qbittorrent/queue")
def get_qbit_queue(_user: str = Depends(require_auth)):
    if not providers._qbit.available():
        return {"online": False, "settings": None, "detail": "qBittorrent 未接入"}
    ok, settings, detail = providers._qbit.queue_settings()
    if not ok:
        raise HTTPException(status_code=502, detail=detail or "读取 qBittorrent 队列设置失败")
    return {"online": True, "settings": settings}


@app.post("/api/qbittorrent/queue")
def post_qbit_queue(body: QbitQueueBody, _user: str = Depends(require_auth)):
    _validate_qbit_queue(body)
    if not providers._qbit.available():
        raise HTTPException(status_code=503, detail="qBittorrent 未接入")
    ok, settings, detail = providers._qbit.update_queue_settings(body.model_dump())
    if not ok:
        raise HTTPException(status_code=502, detail=detail or "保存 qBittorrent 队列设置失败")
    return {"ok": True, "settings": settings}


class TgTestBody(BaseModel):
    token: str
    chat_id: str


@app.get("/api/rclone/remotes")
def rclone_remotes(_user: str = Depends(require_auth)):
    """网盘 remote 列表（可视化对接面板用）。"""
    return {"online": providers._rclone.available(), "remotes": providers._rclone.remotes()}


class RcloneTestBody(BaseModel):
    name: str


@app.post("/api/rclone/remotes/test")
def rclone_test(body: RcloneTestBody, _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    ok, detail, latency = providers._rclone.test_remote(body.name)
    return {"ok": ok, "name": body.name, "detail": detail, "latencyMs": latency}


@app.get("/api/rclone/remotes/config")
def rclone_config(name: str, _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    ok, config, detail = providers._rclone.get_remote(name)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return config


class RcloneUpdateBody(BaseModel):
    name: str
    new_name: str = ""
    params: dict = {}


@app.post("/api/rclone/remotes/update")
def rclone_update(body: RcloneUpdateBody, _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    target_name = body.name
    renamed = False
    if body.new_name.strip() and body.new_name.strip() != body.name:
        ok, detail = providers._rclone.rename_remote(body.name, body.new_name)
        if not ok:
            raise HTTPException(status_code=400, detail=detail)
        target_name = body.new_name.strip()
        renamed = True
    if body.params:
        ok, detail = providers._rclone.update_remote(target_name, body.params)
        if not ok and detail != "没有需要更新的配置":
            if renamed:
                providers._rclone.rename_remote(target_name, body.name)
            raise HTTPException(status_code=400, detail=detail)
        if not ok and not renamed:
            raise HTTPException(status_code=400, detail=detail)
    elif not renamed:
        raise HTTPException(status_code=400, detail="没有需要更新的配置")
    return {"ok": True, "name": target_name}


class RcloneCreateBody(BaseModel):
    name: str
    type: str
    params: dict = {}


@app.post("/api/rclone/remotes")
def rclone_create(body: RcloneCreateBody, _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    ok, err = providers._rclone.create_remote(body.name, body.type, body.params)
    if not ok:
        raise HTTPException(status_code=400, detail=err or "创建失败")
    return {"ok": True}


class RcloneDeleteBody(BaseModel):
    name: str


@app.post("/api/rclone/remotes/delete")
def rclone_delete(body: RcloneDeleteBody, _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    ok, err = providers._rclone.delete_remote(body.name)
    if not ok:
        raise HTTPException(status_code=400, detail=err or "删除失败")
    return {"ok": True}


class RclonePathBody(BaseModel):
    name: str
    path: str = ""


@app.get("/api/rclone/files")
def rclone_files(name: str, path: str = "", _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    ok, entries, detail = providers._rclone.list_files(name, path)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"name": name, "path": path, "items": entries}


@app.post("/api/rclone/files/mkdir")
def rclone_mkdir(body: RclonePathBody, _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    ok, detail = providers._rclone.mkdir_remote(body.name, body.path)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True}


class RcloneFileDeleteBody(RclonePathBody):
    is_dir: bool = False


@app.post("/api/rclone/files/delete")
def rclone_file_delete(body: RcloneFileDeleteBody, _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    ok, job, detail = providers._rclone.delete_remote_file(body.name, body.path, body.is_dir)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True, "job": job}


class RcloneRenameBody(RclonePathBody):
    new_name: str
    is_dir: bool = False


@app.post("/api/rclone/files/rename")
def rclone_file_rename(body: RcloneRenameBody, _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    ok, job, detail = providers._rclone.rename_remote_entry(body.name, body.path, body.new_name, body.is_dir)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True, "job": job}


class RcloneCopyBody(BaseModel):
    source_name: str
    source_path: str
    destination_name: str
    destination_path: str = ""
    is_dir: bool = False
    action: str = "copy"


@app.post("/api/rclone/transfers/copy")
def rclone_copy(body: RcloneCopyBody, _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    ok, job, detail = providers._rclone.copy_remote(
        body.source_name, body.source_path, body.destination_name,
        body.destination_path, body.is_dir, body.action,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True, "job": job}


class RcloneDownloadBody(RclonePathBody):
    destination: str = ""
    is_dir: bool = False


@app.post("/api/rclone/transfers/download")
def rclone_download(body: RcloneDownloadBody, _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    ok, job, detail = providers._rclone.download_remote(
        body.name, body.path, body.destination, body.is_dir,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True, "job": job}


@app.post("/api/rclone/transfers/upload")
async def rclone_upload(
    name: str = Form(...),
    path: str = Form(""),
    file: UploadFile = File(...),
    _user: str = Depends(require_auth),
):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    raw_name = str(file.filename or "").replace("\\", "/")
    filename = Path(raw_name).name
    if not filename or filename in (".", "..") or "/" in filename:
        raise HTTPException(status_code=400, detail="文件名无效")
    staging_dir = Path(providers._DATA_DIR) / "rclone-staging"
    staging_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage = staging_dir / f"{secrets.token_hex(16)}-{filename}"
    total = 0
    try:
        with stage.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > providers._RCLONE_UPLOAD_LIMIT:
                    raise HTTPException(status_code=413, detail="文件不能超过 2 GB")
                out.write(chunk)
        # upload_file 内部是阻塞 HTTP（超时 10s），放线程池避免卡住事件循环
        def _submit():
            return providers._rclone.upload_file(name, str(stage), filename, path, total)
        ok, job, detail = await run_in_threadpool(_submit)
        if not ok:
            raise HTTPException(status_code=400, detail=detail)
        return {"ok": True, "job": job}
    except HTTPException:
        stage.unlink(missing_ok=True)
        raise
    except Exception as e:
        stage.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e).strip()[:180] or "上传失败")
    finally:
        await file.close()


@app.get("/api/rclone/transfers")
def rclone_transfers(_user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    return {"jobs": providers._rclone.transfers()}


class RcloneTransferCancelBody(BaseModel):
    id: str


@app.post("/api/rclone/transfers/cancel")
def rclone_transfer_cancel(body: RcloneTransferCancelBody, _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    ok, detail = providers._rclone.cancel_transfer(body.id)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True}


@app.post("/api/rclone/transfers/retry")
def rclone_transfer_retry(body: RcloneTransferCancelBody, _user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    ok, job, detail = providers._rclone.retry_transfer(body.id)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True, "job": job}


@app.post("/api/rclone/transfers/clear")
def rclone_transfer_clear(_user: str = Depends(require_auth)):
    if not providers._rclone.available():
        raise HTTPException(status_code=503, detail="rclone 未接入")
    return {"ok": True, "cleared": providers._rclone.clear_transfers()}


@app.post("/api/tg/test")
def tg_test(body: TgTestBody, _user: str = Depends(require_auth)):
    ok, detail = providers._tg_send(body.token, body.chat_id, "Aurora 通知连通性测试")
    return {"ok": ok, "detail": detail}


@app.get("/api/info")
def info(_user: str = Depends(require_auth)):
    m = providers.metrics()
    return {
        "name": "Aurora Media Hub",
        "version": app.version,
        "providers": {
            "rclone": {"url": providers._rclone.base, "online": providers._rclone.available()},
            "qbittorrent": {"url": providers._qbit.base, "online": providers._qbit.available(),
                            "auth": bool(providers._qbit.user)},
            "jellyfin": {"url": providers._jelly.base, "online": providers._jelly.available()},
        },
        "sources": m.get("sources", {}),
        "auth": "cookie-session",
    }


# ---------------------------------------------------------------------------
# local media browser

_VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".flv", ".mpg", ".mpeg", ".m4v"}
_AUDIO_EXT = {".mp3", ".flac", ".wav", ".aac", ".m4a", ".ogg", ".opus", ".wma"}
_SUB_EXT = {".srt", ".ass", ".vtt", ".ssa"}
_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _media_base() -> Path:
    d = os.environ.get("AURORA_LOCAL_MOUNT", "/opt/aurora/qbit/downloads")
    return Path(d)


def _media_base_name() -> str:
    try:
        return _media_base().name
    except Exception:
        return "local"


def _media_safe(p: Path) -> Path:
    base = _media_base().resolve()
    fp = (base / p).resolve()
    if fp != base and not str(fp).startswith(str(base) + os.sep):
        raise HTTPException(status_code=400, detail="bad path")
    # 回收站目录只允许通过 /api/media/trash/* 流程访问，禁止经 stream/subtitle/
    # thumb 等端点直接读取已删除的文件
    if ".aurora-trash" in fp.relative_to(base).parts:
        raise HTTPException(status_code=400, detail="bad path")
    return fp


def _torrent_save_path(path: str | None) -> str:
    """Convert a host-relative media directory to qBittorrent's container path."""
    raw = (path or "").strip()
    if not raw or raw == ".":
        target = _media_base().resolve()
    else:
        if raw.startswith(("/", "\\")) or "\\" in raw:
            raise HTTPException(status_code=400, detail="下载目录必须是下载盘内的相对路径")
        target = _media_safe(Path(raw))
    base = _media_base().resolve()
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="下载目录不存在")
    try:
        relative = target.relative_to(base).as_posix()
    except ValueError:
        raise HTTPException(status_code=400, detail="下载目录必须位于下载盘内")
    if relative == ".":
        relative = ""
    return "/downloads" + (("/" + relative) if relative else "")


class media_path_body(BaseModel):
    path: str


class media_rename_body(BaseModel):
    path: str
    new: str


@app.get("/api/media")
def media(_user: str = Depends(require_auth)):
    base = _media_base()
    files = []
    # 做种相关 torrent 的文件 -> 种子 hash 映射，用于前端"毁种"提示 + 联动移动
    seed_map = {}
    if providers._qbit.available():
        try:
            seed_map = providers._qbit.seed_map()
        except Exception:
            seed_map = {}
    if base.is_dir():
        for root, _dirs, fns in os.walk(base):
            _dirs[:] = [d for d in _dirs if not d.startswith(".")]
            for fn in fns:
                if fn.startswith(".") or fn.endswith(("~", ".part", ".torrent")):
                    continue
                p = Path(root) / fn
                try:
                    rel = p.relative_to(base).as_posix()
                except ValueError:
                    continue
                ext = p.suffix.lower()
                kind = ("video" if ext in _VIDEO_EXT else
                        "audio" if ext in _AUDIO_EXT else
                        "sub" if ext in _SUB_EXT else
                        "image" if ext in _IMG_EXT else "file")
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                ab = os.path.abspath(p)
                files.append({"path": rel, "name": p.name, "dir": rel.rsplit("/", 1)[0] if "/" in rel else "",
                              "size": size, "ext": ext, "kind": kind,
                              "seeding": ab in seed_map, "seedHash": seed_map.get(ab) or ""})
    files.sort(key=lambda x: x["path"].lower())
    return {"base": _media_base_name(), "files": files}


@app.get("/api/media/stream")
def media_stream(path: str, _user: str = Depends(require_auth)):
    fp = _media_safe(Path(path))
    if not fp.is_file():
        raise HTTPException(status_code=404, detail="not found")
    media_type = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
    return FileResponse(fp, media_type=media_type)


def _subtitle_text(fp: Path) -> str:
    raw = fp.read_bytes()
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _vtt_time(value: str) -> str:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 2:
        value = "00:" + value
    elif len(parts) == 1:
        value = "00:00:" + value
    return value


def _ass_time(value: str) -> str:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) != 3:
        return "00:00:00.000"
    hours, minutes, seconds = parts
    try:
        whole, fraction = (seconds.split(".", 1) + ["0"])[:2]
        milliseconds = int(float(f"0.{fraction}") * 1000)
        return f"{int(hours):02d}:{int(minutes):02d}:{int(whole):02d}.{milliseconds:03d}"
    except ValueError:
        return "00:00:00.000"


def _subtitle_vtt(fp: Path) -> bytes:
    """Normalize common subtitle formats to WebVTT for browser TextTrack."""
    text = _subtitle_text(fp).replace("\r\n", "\n").replace("\r", "\n")
    ext = fp.suffix.lower()
    if ext == ".vtt":
        return (text if text.lstrip().startswith("WEBVTT") else "WEBVTT\n\n" + text).encode("utf-8")

    cues: list[tuple[str, str, str]] = []
    if ext == ".srt":
        blocks = re.split(r"\n\s*\n", text.strip())
        for block in blocks:
            lines = block.split("\n")
            timing_index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
            if timing_index < 0:
                continue
            start, end = (part.strip() for part in lines[timing_index].split("-->", 1))
            caption = "\n".join(lines[timing_index + 1:]).strip()
            if caption:
                cues.append((_vtt_time(start), _vtt_time(end), caption))
    elif ext in (".ass", ".ssa"):
        for line in text.split("\n"):
            if not line.lower().startswith("dialogue:"):
                continue
            fields = line.split(":", 1)[1].lstrip().split(",", 9)
            if len(fields) < 3:
                continue
            caption = fields[9] if len(fields) > 9 else ""
            caption = re.sub(r"\{[^}]*\}", "", caption)
            caption = caption.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ").strip()
            if caption:
                cues.append((_ass_time(fields[1]), _ass_time(fields[2]), caption))
    else:
        raise HTTPException(status_code=415, detail="不支持的字幕格式")

    out = ["WEBVTT", ""]
    for start, end, caption in cues:
        out.extend([f"{start} --> {end}", caption, ""])
    return "\n".join(out).encode("utf-8")


@app.get("/api/media/subtitle")
def media_subtitle(path: str, _user: str = Depends(require_auth)):
    fp = _media_safe(Path(path))
    if not fp.is_file():
        raise HTTPException(status_code=404, detail="not found")
    if fp.suffix.lower() not in _SUB_EXT:
        raise HTTPException(status_code=415, detail="not a subtitle")
    return Response(content=_subtitle_vtt(fp), media_type="text/vtt; charset=utf-8")


@app.post("/api/media/delete")
def media_delete(body: media_path_body, _user: str = Depends(require_auth)):
    fp = _media_safe(Path(body.path))
    if not fp.is_file():
        raise HTTPException(status_code=404)
    trash_id = secrets.token_hex(12)
    trash_dir = _media_base().resolve() / ".aurora-trash"
    trash_dir.mkdir(mode=0o700, exist_ok=True)
    target = trash_dir / trash_id
    fp.rename(target)
    try:
        providers.trash_add(trash_id, body.path, fp.name, target.stat().st_size)
    except OSError:
        # 元数据写失败（磁盘满/权限）时把文件放回原位，否则会出现 UI 永远
        # 看不见、也无法恢复或清理的"幽灵回收站"文件
        try:
            fp.parent.mkdir(parents=True, exist_ok=True)
            target.rename(fp)
        except OSError:
            pass
        raise HTTPException(status_code=503, detail="回收站元数据写入失败，文件未删除，请检查磁盘后重试")
    providers._log("media.trash", body.path)
    return {"ok": True, "trash_id": trash_id}


@app.get("/api/media/trash")
def media_trash(_user: str = Depends(require_auth)):
    return {"items": providers.trash_list()}


@app.post("/api/media/trash/restore")
def media_trash_restore(body: media_path_body, _user: str = Depends(require_auth)):
    item = providers.trash_get(body.path)
    if not item:
        raise HTTPException(status_code=404, detail="回收站项目不存在")
    src = _media_base().resolve() / ".aurora-trash" / item["id"]
    dst = _media_safe(Path(item["path"]))
    if not src.is_file():
        raise HTTPException(status_code=404, detail="回收站文件不存在")
    if dst.exists():
        raise HTTPException(status_code=409, detail="原路径已有同名文件")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    providers.trash_remove(item["id"])
    providers._log("media.restore", item["path"])
    return {"ok": True, "path": item["path"]}


@app.post("/api/media/trash/purge")
def media_trash_purge(body: media_path_body, _user: str = Depends(require_auth)):
    item = providers.trash_get(body.path)
    if not item:
        raise HTTPException(status_code=404, detail="回收站项目不存在")
    src = _media_base().resolve() / ".aurora-trash" / item["id"]
    if src.exists():
        src.unlink()
    providers.trash_remove(item["id"])
    providers._log("media.purge", item["path"])
    return {"ok": True}


@app.post("/api/media/rename")
def media_rename(body: media_rename_body, _user: str = Depends(require_auth)):
    src = _media_safe(Path(body.path))
    if not src.exists():
        raise HTTPException(status_code=404)
    # 仅允许改本层名字，不允许含 / 或 .. 的跨层移动/穿越
    new = (body.new or "").strip()
    if not new or "/" in new or new in (".", ".."):
        raise HTTPException(status_code=400, detail="bad name")
    parent = str(Path(body.path).parent)
    newrel = (parent + "/" + new) if parent != "." else new
    dst = _media_safe(Path(newrel))
    if dst.exists():
        raise HTTPException(status_code=400, detail="exists")
    src.rename(dst)
    return {"ok": True}


class media_move_body(BaseModel):
    path: str
    to: str


@app.post("/api/media/move")
def media_move(body: media_move_body, _user: str = Depends(require_auth)):
    src = _media_safe(Path(body.path))
    if not src.is_file():
        raise HTTPException(status_code=404)
    base = _media_base().resolve()
    target_dir = _media_safe(Path(body.to))
    if not str(target_dir).startswith(str(base) + os.sep):
        raise HTTPException(status_code=400, detail="bad target")
    target_dir.mkdir(parents=True, exist_ok=True)
    dst = target_dir / src.name
    if dst.exists():
        raise HTTPException(status_code=400, detail="exists")
    src.rename(dst)
    return {"ok": True, "new": str(Path(body.to) / src.name)}


class media_move_seed_body(BaseModel):
    hash: str
    dir: str


@app.post("/api/media/move_seed")
def media_move_seed(body: media_move_seed_body, _user: str = Depends(require_auth)):
    """整种子联动移动：qbit setLocation 搬文件并更新路径，保持做种。dir 为宿主相对目录。"""
    if not providers._qbit.available():
        raise HTTPException(status_code=503, detail="qBittorrent 未接入")
    base = _media_base().resolve()
    target = _media_safe(Path(body.dir))
    if target == base:
        raise HTTPException(status_code=400, detail="bad target")
    rel = target.relative_to(base).as_posix()
    loc = "/downloads" + (("/" + rel) if rel else "")
    if not providers._qbit.move_seed(body.hash, loc):
        raise HTTPException(status_code=502, detail="qBittorrent 移动种子失败")
    return {"ok": True, "new": rel}


@app.get("/api/media/dirs")
def media_dirs(_user: str = Depends(require_auth)):
    """列出下载盘内全部目录（含空目录），供目录管理用。"""
    base = _media_base()
    dirs = []
    if base.is_dir():
        for root, ds, _fns in os.walk(base):
            for d in ds:
                if d.startswith("."):
                    continue
                try:
                    rel = (Path(root) / d).relative_to(base).as_posix()
                except ValueError:
                    continue
                dirs.append({"path": rel, "name": d})
    dirs.sort(key=lambda x: x["path"].lower())
    return {"dirs": dirs}


@app.post("/api/media/mkdir")
def media_mkdir(body: media_path_body, _user: str = Depends(require_auth)):
    base = _media_base().resolve()
    dp = _media_safe(Path(body.path))
    if dp == base:
        raise HTTPException(status_code=400, detail="bad path")
    if dp.exists():
        raise HTTPException(status_code=400, detail="exists")
    try:
        dp.mkdir(parents=True, exist_ok=False)
    except OSError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "new": str(dp.relative_to(base))}


@app.post("/api/media/rmdir")
def media_rmdir(body: media_path_body, _user: str = Depends(require_auth)):
    base = _media_base().resolve()
    dp = _media_safe(Path(body.path))
    if dp == base:
        raise HTTPException(status_code=400, detail="bad path")
    if not dp.is_dir():
        raise HTTPException(status_code=404, detail="not a dir")
    try:
        dp.rmdir()   # 仅空目录，非空抛 39/ENOTEMPTY
    except OSError as e:
        if e.errno == 39:
            raise HTTPException(status_code=400, detail="not empty")
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


_THUMB_DIR = Path(providers._DATA_DIR) / "thumbs"   # 跟随 AURORA_DATA_DIR，不用共享 /tmp
_THUMB_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
_THUMB_GEN_LOCK = threading.Lock()   # 串行化生成，避免同一视频并发起多个 ffmpeg
_THUMB_LAST_PRUNE = 0.0
_THUMB_MAX_AGE = 7 * 86400


def _prune_thumbs() -> None:
    """缩略图按 mtime 生成键，旧文件不会复用也不会自愈——定期清理防止目录无限增长。"""
    global _THUMB_LAST_PRUNE
    now = time.time()
    if now - _THUMB_LAST_PRUNE < 3600:
        return
    _THUMB_LAST_PRUNE = now
    try:
        for p in _THUMB_DIR.glob("*.jpg"):
            try:
                if p.stat().st_mtime < now - _THUMB_MAX_AGE:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


@app.get("/api/media/thumb")
def media_thumb(path: str, _user: str = Depends(require_auth)):
    import hashlib
    import subprocess
    _prune_thumbs()
    fp = _media_safe(Path(path))
    if not fp.is_file():
        raise HTTPException(status_code=404, detail="not found")
    try:
        st = fp.stat()
    except OSError:
        raise HTTPException(status_code=404, detail="not found")
    # 键包含 mtime/size：同名文件被替换后不会永远命中旧缩略图
    key = hashlib.md5(f"{path}\0{st.st_mtime_ns}\0{st.st_size}".encode()).hexdigest()
    out = _THUMB_DIR / f"{key}.jpg"
    if not out.exists():
        with _THUMB_GEN_LOCK:
            if not out.exists():   # 等锁期间可能已被其他请求生成
                ext = fp.suffix.lower()
                args = ["ffmpeg", "-y"]
                if ext not in _IMG_EXT:
                    args += ["-ss", "0.5"]
                args += ["-i", str(fp), "-vf", "scale=480:-2", "-frames:v", "1", "-q:v", "5", str(out)]
                try:
                    result = subprocess.run(args, capture_output=True, timeout=25)
                    ok = result.returncode == 0
                    if not ok:
                        providers._LOGGER.warning(
                            "thumbnail failed for %s: %s", path,
                            (result.stderr or b"")[-200:])
                except Exception as exc:
                    ok = False
                    providers._LOGGER.warning("thumbnail failed for %s: %s", path, exc)
                if not ok:
                    out.unlink(missing_ok=True)   # 失败不留半截文件充当缓存
    if out.exists():
        return FileResponse(out, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="no thumb")


# ---------------------------------------------------------------------------
# jellyfin (海报墙 + 播放代理)


@app.get("/api/jellyfin/library")
def jf_library(_user: str = Depends(require_auth)):
    online = providers._jelly.available() and bool(providers._jelly.token)
    items = providers._jelly.library() if online else []
    return {"online": online, "source": "jellyfin" if online else "none", "items": items}


@app.get("/api/jellyfin/image")
def jf_image(item_id: str, tag: str = "", _user: str = Depends(require_auth)):
    import re as _re
    import urllib.parse
    if not _re.fullmatch(r"[0-9A-Za-z\-]{1,64}", item_id or ""):
        raise HTTPException(status_code=400, detail="bad item_id")
    import requests
    base = providers._jelly.base
    token = providers._jelly.token
    if not token:
        raise HTTPException(status_code=404, detail="jellyfin 未接入")
    query = f"?{urllib.parse.urlencode({'tag': tag})}" if tag else ""
    try:
        up = requests.get(f"{base}/Items/{item_id}/Images/Primary{query}",
                          headers={"X-Emby-Token": token}, timeout=10)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Jellyfin 海报服务不可用") from exc
    if up.status_code != 200:
        raise HTTPException(status_code=up.status_code, detail="poster not found")
    return Response(content=up.content, media_type=up.headers.get("content-type", "image/jpeg"))


@app.get("/api/jellyfin/stream")
def jf_stream(item_id: str, request: Request, _user: str = Depends(require_auth)):
    import re as _re
    if not _re.fullmatch(r"[0-9A-Za-z\-]{1,64}", item_id or ""):
        raise HTTPException(status_code=400, detail="bad item_id")
    import requests
    base = providers._jelly.base
    token = providers._jelly.token
    if not token:
        raise HTTPException(status_code=404, detail="jellyfin 未接入")
    headers = {"X-Emby-Token": token}
    rng = request.headers.get("range")
    if rng:
        headers["Range"] = rng
    try:
        up = requests.get(f"{base}/Videos/{item_id}/stream?static=true",
                          headers=headers, stream=True, timeout=(5, 60))
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Jellyfin 播放服务不可用") from exc
    if up.status_code >= 400:
        status = up.status_code if up.status_code < 500 else 502
        up.close()
        raise HTTPException(status_code=status, detail="Jellyfin 无法提供媒体流")
    hdr = {}
    for k in ("content-type", "content-range", "accept-ranges", "content-length",
              "content-disposition", "etag", "last-modified"):
        if k in up.headers:
            hdr[k] = up.headers[k]

    def gen():
        try:
            for chunk in up.iter_content(65536):
                yield chunk
        finally:
            up.close()

    return StreamingResponse(gen(), status_code=up.status_code, headers=hdr)


# ---------------------------------------------------------------------------
# SPA

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str, request: Request, sid: str | None = Cookie(default=None, alias=_COOKIE)):
        # always allow the login screen + its assets
        if full_path in ("login", "login/") or full_path.startswith("login/"):
            return FileResponse(STATIC_DIR / "index.html")
        if full_path:
            # path traversal guard: resolve and require the result to stay inside STATIC_DIR
            base = STATIC_DIR.resolve()
            cand = (STATIC_DIR / full_path).resolve()
            if str(cand).startswith(str(base) + os.sep) and cand.is_file():
                return FileResponse(cand)
        # everything else requires auth; unauthenticated deep-links go to /login
        if not _check_sid(sid):
            return RedirectResponse("/login")
        return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    # 管理界面默认只听回环；需要对外时必须显式设置 AURORA_HOST 并自行做好反代/鉴权
    uvicorn.run(app, host=os.environ.get("AURORA_HOST", "127.0.0.1"),
                port=int(os.environ.get("AURORA_PORT", "8787")))
