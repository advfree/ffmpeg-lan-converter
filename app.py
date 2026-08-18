#!/usr/bin/env python3
"""LAN-only FFmpeg conversion web application with no Python dependencies."""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", "/media")).resolve()
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/data")).resolve()
PORT = int(os.getenv("PORT", "10888"))
MAX_SCAN_FILES = int(os.getenv("MAX_SCAN_FILES", "10000"))
SESSION_TTL = 12 * 60 * 60
CONFIG_FILE = DATA_ROOT / "config.json"
MEDIA_SUFFIXES = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".mts", ".m2ts", ".flv", ".wmv",
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".ape",
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic",
}


PRESETS: dict[str, dict[str, Any]] = {
    "mp4_h264": {"label": "MP4 · H.264 + AAC", "ext": ".mp4", "group": "视频", "lossless": False,
                 "args": ["-map", "0:v:0?", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]},
    "mp4_h265": {"label": "MP4 · H.265 + AAC（更小）", "ext": ".mp4", "group": "视频", "lossless": False,
                 "args": ["-map", "0:v:0?", "-map", "0:a?", "-c:v", "libx265", "-preset", "medium", "-crf", "24", "-c:a", "aac", "-b:a", "192k", "-tag:v", "hvc1", "-movflags", "+faststart"]},
    "webm_vp9": {"label": "WebM · VP9 + Opus", "ext": ".webm", "group": "视频", "lossless": False,
                 "args": ["-map", "0:v:0?", "-map", "0:a?", "-c:v", "libvpx-vp9", "-crf", "31", "-b:v", "0", "-c:a", "libopus", "-b:a", "128k"]},
    "mkv_lossless": {"label": "MKV · FFV1 + FLAC（无损）", "ext": ".mkv", "group": "视频", "lossless": True,
                     "args": ["-map", "0:v:0?", "-map", "0:a?", "-c:v", "ffv1", "-level", "3", "-c:a", "flac"]},
    "mkv_remux": {"label": "MKV · 原流封装（无损/不重编码）", "ext": ".mkv", "group": "视频", "lossless": True,
                  "args": ["-map", "0", "-c", "copy"]},
    "gif": {"label": "GIF · 动图", "ext": ".gif", "group": "视频", "lossless": False,
            "args": ["-vf", "fps=12,scale='min(960,iw)':-2:flags=lanczos", "-loop", "0"]},
    "mp3": {"label": "MP3 · 192 kbps", "ext": ".mp3", "group": "音频", "lossless": False,
            "args": ["-vn", "-c:a", "libmp3lame", "-b:a", "192k"]},
    "m4a": {"label": "M4A · AAC 192 kbps（有损重编码）", "ext": ".m4a", "group": "音频", "lossless": False,
            "args": ["-vn", "-c:a", "aac", "-b:a", "192k"]},
    "m4a_remux": {"label": "M4A · 原音频封装（无损/不重编码，AAC/ALAC）", "ext": ".m4a", "group": "音频", "lossless": True, "remux": True,
                  "args": ["-vn", "-map", "0:a:0", "-c:a", "copy", "-movflags", "+faststart"]},
    "m4a_alac": {"label": "M4A · ALAC（无损）", "ext": ".m4a", "group": "音频", "lossless": True,
                 "args": ["-vn", "-map", "0:a:0", "-c:a", "alac", "-movflags", "+faststart"]},
    "opus": {"label": "Opus · 128 kbps", "ext": ".opus", "group": "音频", "lossless": False,
             "args": ["-vn", "-c:a", "libopus", "-b:a", "128k"]},
    "flac": {"label": "FLAC（无损）", "ext": ".flac", "group": "音频", "lossless": True,
             "args": ["-vn", "-c:a", "flac", "-compression_level", "8"]},
    "wav": {"label": "WAV · PCM 24-bit（无损）", "ext": ".wav", "group": "音频", "lossless": True,
            "args": ["-vn", "-c:a", "pcm_s24le"]},
    "png": {"label": "PNG（无损）", "ext": ".png", "group": "图片", "lossless": True,
            "args": ["-frames:v", "1", "-c:v", "png"]},
    "webp_lossless": {"label": "WebP（无损）", "ext": ".webp", "group": "图片", "lossless": True,
                      "args": ["-frames:v", "1", "-c:v", "libwebp", "-lossless", "1"]},
    "jpg": {"label": "JPEG · 高质量", "ext": ".jpg", "group": "图片", "lossless": False,
            "args": ["-frames:v", "1", "-q:v", "2"]},
}


def json_write_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def password_hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 310_000).hex()


def load_config() -> dict[str, str]:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    password = os.getenv("APP_PASSWORD") or secrets.token_urlsafe(15)
    username = os.getenv("APP_USERNAME", "root")
    salt = secrets.token_hex(16)
    config = {"username": username, "salt": salt, "password_hash": password_hash(password, salt), "secret": secrets.token_hex(32)}
    json_write_atomic(CONFIG_FILE, config)
    print("\n" + "=" * 68, flush=True)
    print(f"首次启动账号: {username}", flush=True)
    print(f"首次启动随机密码: {password}", flush=True)
    print("请登录后妥善保存；密码不会再次以明文显示。", flush=True)
    print("=" * 68 + "\n", flush=True)
    return config


CONFIG = load_config()
SESSIONS: dict[str, dict[str, Any]] = {}
LOGIN_FAILURES: dict[str, list[float]] = {}
STATE_LOCK = threading.RLock()


def safe_path(relative: str, *, must_exist: bool = True) -> Path:
    clean = urllib.parse.unquote(relative or "").replace("\\", "/").strip()
    # Accept absolute paths rooted at MEDIA_ROOT as displayed by the UI, while
    # still resolving every request strictly below that configured root.
    root_text = MEDIA_ROOT.as_posix().rstrip("/")
    if clean == root_text:
        clean = ""
    elif clean.startswith(root_text + "/"):
        clean = clean[len(root_text) + 1:]
    else:
        clean = clean.lstrip("/")
    candidate = (MEDIA_ROOT / clean).resolve()
    try:
        candidate.relative_to(MEDIA_ROOT)
    except ValueError as exc:
        raise ValueError("路径超出媒体根目录") from exc
    if must_exist and not candidate.exists():
        raise ValueError("路径不存在")
    return candidate


def relative_path(path: Path) -> str:
    relative = path.resolve().relative_to(MEDIA_ROOT).as_posix()
    return "" if relative == "." else relative


def human_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".ape"}:
        return "audio"
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".heic"}:
        return "image"
    return "video"


def file_info(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": relative_path(path), "name": path.name, "size": stat.st_size, "mtime": int(stat.st_mtime), "kind": human_kind(path)}


@dataclass
class RunningProcess:
    process: subprocess.Popen[str] | None = None


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.pending: queue.Queue[str] = queue.Queue()
        self.lock = threading.RLock()
        self.running: dict[str, RunningProcess] = {}
        threading.Thread(target=self._worker, daemon=True, name="conversion-worker").start()

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        preset_key = str(payload.get("preset", ""))
        if preset_key not in PRESETS:
            raise ValueError("未知的输出格式")
        raw_files = payload.get("files")
        if not isinstance(raw_files, list) or not raw_files or len(raw_files) > MAX_SCAN_FILES:
            raise ValueError("请选择 1 个以上且不超过扫描上限的文件")
        files = []
        for raw in raw_files:
            path = safe_path(str(raw))
            if not path.is_file() or path.suffix.lower() not in MEDIA_SUFFIXES:
                raise ValueError(f"不是支持的媒体文件: {raw}")
            files.append(relative_path(path))
        output_raw = str(payload.get("output_dir") or "").strip()
        output_dir = relative_path(safe_path(output_raw)) if output_raw else ""
        if output_dir and not safe_path(output_dir).is_dir():
            raise ValueError("输出位置必须是已有文件夹")
        overwrite = str(payload.get("overwrite", "rename"))
        if overwrite not in {"rename", "skip", "overwrite"}:
            raise ValueError("无效的重名策略")
        suffix = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(payload.get("suffix", "")))[:40]
        job_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        job = {
            "id": job_id, "state": "queued", "preset": preset_key, "preset_label": PRESETS[preset_key]["label"],
            "files": files, "output_dir": output_dir, "delete_sources": bool(payload.get("delete_sources", False)),
            "overwrite": overwrite, "suffix": suffix, "created_at": now, "updated_at": now,
            "current": 0, "total": len(files), "progress": 0.0, "results": [], "error": "", "cancel_requested": False,
        }
        with self.lock:
            self.jobs[job_id] = job
        self.pending.put(job_id)
        return self.public(job)

    def public(self, job: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(job, ensure_ascii=False))

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            values = sorted(self.jobs.values(), key=lambda x: x["created_at"], reverse=True)
            return [self.public(v) for v in values[:50]]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return self.public(job) if job else None

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise ValueError("任务不存在")
            if job["state"] in {"queued", "running"}:
                job["cancel_requested"] = True
                job["updated_at"] = int(time.time())
                holder = self.running.get(job_id)
                if holder and holder.process and holder.process.poll() is None:
                    holder.process.terminate()
            return self.public(job)

    def _worker(self) -> None:
        while True:
            job_id = self.pending.get()
            try:
                self._run_job(job_id)
            except Exception as exc:
                with self.lock:
                    job = self.jobs.get(job_id)
                    if job:
                        job.update(state="failed", error=str(exc), updated_at=int(time.time()))
            finally:
                with self.lock:
                    self.running.pop(job_id, None)
                self.pending.task_done()

    def _run_job(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            if job["cancel_requested"]:
                job.update(state="cancelled", updated_at=int(time.time()))
                return
            job.update(state="running", updated_at=int(time.time()))
            self.running[job_id] = RunningProcess()
        for index, rel in enumerate(job["files"]):
            with self.lock:
                if job["cancel_requested"]:
                    job.update(state="cancelled", updated_at=int(time.time()))
                    return
                job.update(current=index + 1, progress=0.0, current_file=rel, updated_at=int(time.time()))
            result = self._convert_one(job_id, job, rel)
            with self.lock:
                job["results"].append(result)
        with self.lock:
            failures = sum(r["status"] == "failed" for r in job["results"])
            job.update(state="completed_with_errors" if failures else "completed", progress=100.0, updated_at=int(time.time()))

    def _target_path(self, job: dict[str, Any], source: Path) -> tuple[Path | None, str | None]:
        preset = PRESETS[job["preset"]]
        directory = safe_path(job["output_dir"]) if job["output_dir"] else source.parent
        base = source.stem + job["suffix"]
        target = directory / (base + preset["ext"])
        target = target.resolve()
        try:
            target.relative_to(MEDIA_ROOT)
        except ValueError:
            raise ValueError("输出路径超出媒体根目录")
        if target == source:
            if job["overwrite"] == "skip":
                return None, "输出与源文件相同，已安全跳过"
            raise ValueError("输出文件不能覆盖源文件，请设置文件名后缀或选择“跳过”")
        if target.exists():
            if job["overwrite"] == "skip":
                return None, "目标已存在，已跳过"
            if job["overwrite"] == "rename":
                number = 2
                while target.exists():
                    target = directory / f"{base}_{number}{preset['ext']}"
                    number += 1
        return target, None

    @staticmethod
    def _duration(source: Path) -> float:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(source)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        try:
            return max(float(completed.stdout.strip()), 0.0)
        except ValueError:
            return 0.0

    @staticmethod
    def _verify_output(path: Path) -> None:
        if not path.exists() or path.stat().st_size <= 0:
            raise RuntimeError("输出文件为空")
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            raise RuntimeError("FFprobe 无法验证输出文件")

    def _convert_one(self, job_id: str, job: dict[str, Any], rel: str) -> dict[str, Any]:
        source = safe_path(rel)
        try:
            target, skip_reason = self._target_path(job, source)
            if target is None:
                return {"source": rel, "status": "skipped", "message": skip_reason}
            target.parent.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(target.parent)
            if usage.free < max(source.stat().st_size // 5, 64 * 1024 * 1024):
                raise RuntimeError("目标磁盘剩余空间过低")
            temp = target.with_name(f".{target.stem}.{uuid.uuid4().hex[:8]}.partial{target.suffix}")
            duration = self._duration(source)
            command = ["ffmpeg", "-hide_banner", "-y", "-i", str(source), *PRESETS[job["preset"]]["args"], "-progress", "pipe:1", "-nostats", str(temp)]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            with self.lock:
                self.running[job_id].process = process
            assert process.stdout is not None
            for line in process.stdout:
                if line.startswith("out_time_ms=") and duration > 0:
                    try:
                        progress = min(99.0, int(line.split("=", 1)[1]) / 1_000_000 / duration * 100)
                        with self.lock:
                            job.update(progress=round(progress, 1), updated_at=int(time.time()))
                    except ValueError:
                        pass
            stderr = process.stderr.read()[-6000:] if process.stderr else ""
            code = process.wait()
            if job["cancel_requested"]:
                temp.unlink(missing_ok=True)
                return {"source": rel, "status": "cancelled", "message": "任务已取消"}
            if code != 0:
                temp.unlink(missing_ok=True)
                raise RuntimeError(stderr.strip().splitlines()[-1] if stderr.strip() else f"FFmpeg 退出码 {code}")
            self._verify_output(temp)
            if target.exists() and job["overwrite"] == "overwrite":
                target.unlink()
            os.replace(temp, target)
            deleted = False
            if job["delete_sources"]:
                source.unlink()
                deleted = True
            return {"source": rel, "output": relative_path(target), "status": "success", "source_deleted": deleted, "size": target.stat().st_size}
        except Exception as exc:
            return {"source": rel, "status": "failed", "message": str(exc)}


JOBS = JobManager()


class AppHandler(BaseHTTPRequestHandler):
    server_version = f"FFmpegLAN/{APP_VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def json_response(self, status: int, value: Any, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:")
        for key, value_header in (headers or {}).items():
            self.send_header(key, value_header)
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("请求过大")
        return json.loads(self.rfile.read(length) or b"{}")

    def session(self) -> tuple[str, dict[str, Any]] | tuple[None, None]:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("ffmpeg_session")
        if not morsel:
            return None, None
        token = morsel.value
        with STATE_LOCK:
            session = SESSIONS.get(token)
            if not session or session["expires"] < time.time():
                SESSIONS.pop(token, None)
                return None, None
            return token, session

    def require_auth(self, *, csrf: bool = False) -> tuple[str, dict[str, Any]] | None:
        auth = self.session()
        if not auth[0]:
            self.json_response(HTTPStatus.UNAUTHORIZED, {"error": "请先登录"})
            return None
        if csrf and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), auth[1]["csrf"]):
            self.json_response(HTTPStatus.FORBIDDEN, {"error": "安全令牌无效，请刷新页面"})
            return None
        return auth  # type: ignore[return-value]

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/healthz":
            self.json_response(200, {"status": "ok", "version": APP_VERSION, "ffmpeg": shutil.which("ffmpeg") is not None})
            return
        if parsed.path.startswith("/api/"):
            if not self.require_auth():
                return
            try:
                self.handle_api_get(parsed)
            except ValueError as exc:
                self.json_response(400, {"error": str(exc)})
            except Exception as exc:
                self.json_response(500, {"error": f"服务器错误: {exc}"})
            return
        self.serve_static(parsed.path)

    def handle_api_get(self, parsed: urllib.parse.ParseResult) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/api/me":
            _, session = self.session()
            self.json_response(200, {"username": CONFIG["username"], "csrf": session["csrf"], "media_root": MEDIA_ROOT.as_posix()})
        elif parsed.path == "/api/presets":
            self.json_response(200, [{"id": key, **{k: v for k, v in value.items() if k != "args"}} for key, value in PRESETS.items()])
        elif parsed.path == "/api/browse":
            base = safe_path(query.get("path", [""])[0])
            if not base.is_dir():
                raise ValueError("不是文件夹")
            entries = []
            for child in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                try:
                    resolved = child.resolve()
                    resolved.relative_to(MEDIA_ROOT)
                    if child.is_dir():
                        entries.append({"name": child.name, "path": relative_path(child), "type": "directory"})
                    elif child.suffix.lower() in MEDIA_SUFFIXES:
                        entries.append({**file_info(child), "type": "file"})
                except (OSError, ValueError):
                    continue
            parent = "" if base == MEDIA_ROOT else relative_path(base.parent)
            self.json_response(200, {"path": relative_path(base), "parent": parent, "entries": entries[:2000]})
        elif parsed.path == "/api/scan":
            base = safe_path(query.get("path", [""])[0])
            recursive = query.get("recursive", ["false"])[0].lower() == "true"
            if base.is_file():
                paths = [base]
            elif base.is_dir():
                iterator = base.rglob("*") if recursive else base.iterdir()
                paths = (p for p in iterator if p.is_file())
            else:
                raise ValueError("请选择文件或文件夹")
            files = []
            truncated = False
            for path in paths:
                if path.suffix.lower() not in MEDIA_SUFFIXES:
                    continue
                try:
                    files.append(file_info(path))
                except (OSError, ValueError):
                    continue
                if len(files) >= MAX_SCAN_FILES:
                    truncated = True
                    break
            self.json_response(200, {"files": files, "truncated": truncated, "limit": MAX_SCAN_FILES})
        elif parsed.path == "/api/jobs":
            self.json_response(200, JOBS.list())
        elif re.fullmatch(r"/api/jobs/[a-f0-9]{12}", parsed.path):
            job = JOBS.get(parsed.path.rsplit("/", 1)[1])
            self.json_response(200, job) if job else self.json_response(404, {"error": "任务不存在"})
        else:
            self.json_response(404, {"error": "接口不存在"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/login":
            self.login()
            return
        if not self.require_auth(csrf=True):
            return
        try:
            payload = self.read_json()
            if parsed.path == "/api/logout":
                token, _ = self.session()
                with STATE_LOCK:
                    SESSIONS.pop(token, None)
                self.json_response(200, {"ok": True}, {"Set-Cookie": "ffmpeg_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
            elif parsed.path == "/api/jobs":
                self.json_response(201, JOBS.create(payload))
            elif re.fullmatch(r"/api/jobs/[a-f0-9]{12}/cancel", parsed.path):
                self.json_response(200, JOBS.cancel(parsed.path.split("/")[-2]))
            elif parsed.path == "/api/password":
                current = str(payload.get("current", ""))
                new = str(payload.get("new", ""))
                if not hmac.compare_digest(password_hash(current, CONFIG["salt"]), CONFIG["password_hash"]):
                    raise ValueError("当前密码错误")
                if len(new) < 12:
                    raise ValueError("新密码至少 12 个字符")
                CONFIG["salt"] = secrets.token_hex(16)
                CONFIG["password_hash"] = password_hash(new, CONFIG["salt"])
                json_write_atomic(CONFIG_FILE, CONFIG)
                self.json_response(200, {"ok": True})
            else:
                self.json_response(404, {"error": "接口不存在"})
        except (ValueError, json.JSONDecodeError) as exc:
            self.json_response(400, {"error": str(exc)})
        except Exception as exc:
            self.json_response(500, {"error": f"服务器错误: {exc}"})

    def login(self) -> None:
        ip = self.client_address[0]
        now = time.time()
        with STATE_LOCK:
            recent = [stamp for stamp in LOGIN_FAILURES.get(ip, []) if stamp > now - 300]
            LOGIN_FAILURES[ip] = recent
        if len(recent) >= 8:
            self.json_response(429, {"error": "登录失败次数过多，请 5 分钟后再试"})
            return
        try:
            payload = self.read_json()
            valid_user = hmac.compare_digest(str(payload.get("username", "")), CONFIG["username"])
            supplied_hash = password_hash(str(payload.get("password", "")), CONFIG["salt"])
            valid_password = hmac.compare_digest(supplied_hash, CONFIG["password_hash"])
            if not (valid_user and valid_password):
                with STATE_LOCK:
                    LOGIN_FAILURES[ip].append(now)
                self.json_response(401, {"error": "账号或密码错误"})
                return
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(24)
            with STATE_LOCK:
                SESSIONS[token] = {"expires": now + SESSION_TTL, "csrf": csrf}
                LOGIN_FAILURES.pop(ip, None)
            cookie = f"ffmpeg_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}"
            self.json_response(200, {"username": CONFIG["username"], "csrf": csrf, "media_root": MEDIA_ROOT.as_posix()}, {"Set-Cookie": cookie})
        except Exception:
            self.json_response(400, {"error": "无效请求"})

    def serve_static(self, url_path: str) -> None:
        raw = "index.html" if url_path in {"", "/"} else urllib.parse.unquote(url_path).lstrip("/")
        path = (STATIC_ROOT / raw).resolve()
        try:
            path.relative_to(STATIC_ROOT)
        except ValueError:
            self.send_error(404)
            return
        if not path.is_file():
            path = STATIC_ROOT / "index.html"
        content = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg/ffprobe 未安装")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AppHandler)
    server.daemon_threads = True
    print(f"FFmpeg LAN Converter listening on 0.0.0.0:{PORT}; media root={MEDIA_ROOT}", flush=True)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
