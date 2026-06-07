from __future__ import annotations
"""
Agent 具体实现：psutil / docker SDK / subprocess（禁止 shell=True）
"""
import subprocess
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

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


# ── 工具函数 ──────────────────────────────────────────────────────────

def _get_primary_interface() -> str:
    """解析 'ip route show default'，返回出口网卡名"""
    try:
        r = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        )
        parts = r.stdout.split()
        if "dev" in parts:
            idx = parts.index("dev")
            if idx + 1 < len(parts):
                return parts[idx + 1]
    except Exception:
        pass
    for name in psutil.net_if_addrs():
        if name != "lo":
            return name
    return "eth0"


def _ping_avg_ms(host: str, count: int = 3) -> Optional[float]:
    """对指定主机 ping count 次，返回平均延迟（ms），失败返回 None"""
    try:
        r = subprocess.run(
            ["ping", "-c", str(count), "-W", "3", host],
            capture_output=True, text=True, timeout=15,
        )
        for line in r.stdout.splitlines():
            if "avg" in line and "=" in line:
                avg_str = line.split("=")[1].strip().split("/")[1]
                return round(float(avg_str))
    except Exception:
        pass
    return None


# ── 本月流量 ──────────────────────────────────────────────────────────

def get_traffic() -> dict[str, Any]:
    """读取 vnstat 本月流量；vnstat 未安装时在 JSON 中返回 error 字段"""
    try:
        import json
        r = subprocess.run(
            ["vnstat", "--json"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return {"error": "vnstat 运行失败，请先运行: vnstat --add -i <interface>", "used_gb": 0}

        data = json.loads(r.stdout)
        for iface in data.get("interfaces", []):
            if iface["name"] == "lo":
                continue
            months = iface.get("traffic", {}).get("months", [])
            if not months:
                continue
            m = months[-1]
            rx, tx = m.get("rx", 0), m.get("tx", 0)
            total = rx + tx
            return {
                "interface": iface["name"],
                "used_gb": round(total / 1024 ** 3, 2),
                "rx_gb":   round(rx    / 1024 ** 3, 2),
                "tx_gb":   round(tx    / 1024 ** 3, 2),
                "month":   f"{m['date']['year']}-{m['date']['month']:02d}",
            }
        return {"error": "vnstat 无月度数据，等待第一次统计周期", "used_gb": 0}
    except FileNotFoundError:
        return {"error": "vnstat 未安装，请运行: apt install vnstat", "used_gb": 0}
    except Exception as e:
        return {"error": str(e), "used_gb": 0}


# ── 实时网速 ──────────────────────────────────────────────────────────

def get_network_speed() -> dict[str, Any]:
    """采样 1 秒，返回当前入/出站速率（Mbps）"""
    iface = _get_primary_interface()
    per_nic = psutil.net_io_counters(pernic=True)
    if iface in per_nic:
        c1 = per_nic[iface]
        time.sleep(1)
        c2 = psutil.net_io_counters(pernic=True)[iface]
    else:
        c1 = psutil.net_io_counters()
        time.sleep(1)
        c2 = psutil.net_io_counters()
        iface = "all"

    return {
        "interface": iface,
        "rx_mbps": round((c2.bytes_recv - c1.bytes_recv) * 8 / 1e6, 2),
        "tx_mbps": round((c2.bytes_sent - c1.bytes_sent) * 8 / 1e6, 2),
    }


# ── 网络测速 ──────────────────────────────────────────────────────────

# 三网代表性 IP（仅用于 ICMP ping，不作任何其他用途）
_ISP_PING_HOSTS = {
    "telecom": "202.96.209.5",   # 上海电信 DNS
    "unicom":  "210.22.97.1",    # 上海联通 DNS
    "mobile":  "221.130.33.52",  # 中国移动
}


def run_speedtest() -> dict[str, Any]:
    """并行 ping 三网 + speedtest-cli 下载/上传测速（同步，应在线程池中调用）"""
    # 并行 ping 三网
    ping_results: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {k: pool.submit(_ping_avg_ms, v) for k, v in _ISP_PING_HOSTS.items()}
        for isp, fut in futures.items():
            val = fut.result()
            if val is not None:
                ping_results[isp] = val

    # 下载 / 上传测速
    try:
        import speedtest  # type: ignore[import]
        st = speedtest.Speedtest(secure=True)
        st.get_best_server()
        dl_bps = st.download()
        ul_bps = st.upload()
        res_d  = st.results.dict()
        srv    = res_d.get("server", {})
        server = f"{srv.get('sponsor', '')} {srv.get('name', '')}".strip()
        return {
            "ping_results":  ping_results,
            "download_mbps": round(dl_bps / 1e6, 1),
            "upload_mbps":   round(ul_bps / 1e6, 1),
            "server":        server or "N/A",
        }
    except ImportError:
        return {
            "ping_results": ping_results,
            "error": "speedtest-cli 未安装，请运行: pip install speedtest-cli==2.1.3",
            "download_mbps": 0,
            "upload_mbps": 0,
            "server": "N/A",
        }
    except Exception as e:
        return {
            "ping_results": ping_results,
            "error": str(e),
            "download_mbps": 0,
            "upload_mbps": 0,
            "server": "N/A",
        }
