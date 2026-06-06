"""
Agent 具体实现：psutil / docker SDK / subprocess（禁止 shell=True）
"""
import subprocess
import time
import logging
from typing import Any

import psutil

logger = logging.getLogger(__name__)


# ── 系统状态 ──────────────────────────────────────────────────────────

def get_status() -> dict[str, Any]:
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime_sec = time.time() - psutil.boot_time()

    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    minutes = int((uptime_sec % 3600) // 60)
    uptime_str = f"{days}天{hours}小时{minutes}分钟"

    return {
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "memory": {
            "used_gb": round(mem.used / 1024 ** 3, 1),
            "total_gb": round(mem.total / 1024 ** 3, 1),
            "percent": mem.percent,
        },
        "disk": {
            "used_gb": round(disk.used / 1024 ** 3, 1),
            "total_gb": round(disk.total / 1024 ** 3, 1),
            "percent": disk.percent,
        },
        "uptime": uptime_str,
    }


# ── Docker 容器列表 ───────────────────────────────────────────────────

def get_docker() -> list[dict[str, str]]:
    import docker  # 延迟导入：VPS 上可能未安装 Docker
    client = docker.from_env()
    result = []
    for c in client.containers.list(all=True):
        image_tag = c.image.tags[0] if c.image.tags else c.image.short_id
        result.append({
            "id": c.short_id,
            "name": c.name,
            "status": c.status,
            "image": image_tag,
        })
    return result


# ── 服务存活状态 ──────────────────────────────────────────────────────

def get_services(allowed_services: list[str]) -> list[dict[str, Any]]:
    result = []
    for svc in allowed_services:
        r = subprocess.run(
            ["systemctl", "is-active", svc],
            capture_output=True, text=True, timeout=5,
        )
        status = r.stdout.strip()  # "active" / "inactive" / "failed" / "unknown"
        result.append({
            "service": svc,
            "active": status == "active",
            "status": status,
        })
    return result


# ── 日志 ─────────────────────────────────────────────────────────────

def _docker_container_names() -> set[str]:
    try:
        import docker
        return {c.name for c in docker.from_env().containers.list(all=True)}
    except Exception:
        return set()


def get_logs(service: str, lines: int = 50) -> str:
    """自动判断 systemd 服务 vs Docker 容器，选用对应命令取日志"""
    if service in _docker_container_names():
        r = subprocess.run(
            ["docker", "logs", service, "--tail", str(lines)],
            capture_output=True, text=True, timeout=20,
        )
        return r.stdout + r.stderr
    else:
        r = subprocess.run(
            ["journalctl", "-u", service, "-n", str(lines),
             "--no-pager", "--output=short-iso"],
            capture_output=True, text=True, timeout=20,
        )
        return r.stdout


# ── 重启服务 ──────────────────────────────────────────────────────────

def restart_service(service: str) -> dict[str, Any]:
    r = subprocess.run(
        ["systemctl", "restart", service],
        capture_output=True, text=True, timeout=30,
    )
    success = r.returncode == 0
    return {
        "service": service,
        "success": success,
        "message": "重启成功" if success else f"重启失败: {r.stderr.strip()}",
    }
