"""
轻量 HTTP Agent（部署在每台目标 VPS）
  - 默认端口 9000
  - Bearer Token 认证（环境变量 AGENT_TOKEN）
  - 允许操作的服务由 ALLOWED_SERVICES 控制（逗号分隔）
"""
import asyncio
import os
import socket
import sys
import logging

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import handlers

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── 启动校验 ──────────────────────────────────────────────────────────

AGENT_TOKEN: str = os.environ.get("AGENT_TOKEN", "")
if not AGENT_TOKEN:
    logger.error("环境变量 AGENT_TOKEN 未设置，拒绝启动")
    sys.exit(1)

_raw_services = os.environ.get("ALLOWED_SERVICES", "nginx,trilium,x-ui")
ALLOWED_SERVICES: list[str] = [s.strip() for s in _raw_services.split(",") if s.strip()]
logger.info("允许操作的服务: %s", ALLOWED_SERVICES)

# ── FastAPI 应用 ──────────────────────────────────────────────────────

app = FastAPI(title="Ops Agent", version="1.0.0", docs_url=None, redoc_url=None)
_bearer = HTTPBearer()


# ── 认证依赖 ──────────────────────────────────────────────────────────

def verify_token(cred: HTTPAuthorizationCredentials = Security(_bearer)) -> None:
    if cred.credentials != AGENT_TOKEN:
        raise HTTPException(status_code=403, detail={"error": "无效的认证 token", "code": 403})


# ── 统一错误响应格式 ──────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def _http_exc_handler(request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": str(exc.detail), "code": exc.status_code},
    )


def _check_service(service: str) -> None:
    if service not in ALLOWED_SERVICES:
        raise HTTPException(
            status_code=403,
            detail={"error": f"不支持的服务: {service}，可用: {','.join(ALLOWED_SERVICES)}", "code": 403},
        )


# ── 路由 ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health(_=Depends(verify_token)):
    return {"status": "ok", "host": socket.gethostname()}


@app.get("/status")
async def status(_=Depends(verify_token)):
    return handlers.get_status()


@app.get("/docker")
async def docker(_=Depends(verify_token)):
    try:
        return handlers.get_docker()
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e), "code": 500})


@app.get("/services")
async def services(_=Depends(verify_token)):
    return handlers.get_services(ALLOWED_SERVICES)


@app.get("/logs/{service}")
async def logs(service: str, lines: int = 50, _=Depends(verify_token)):
    _check_service(service)
    text = handlers.get_logs(service, lines)
    return {"service": service, "lines": lines, "logs": text}


@app.post("/restart/{service}")
async def restart(service: str, _=Depends(verify_token)):
    _check_service(service)
    return handlers.restart_service(service)


@app.get("/traffic")
async def traffic(_=Depends(verify_token)):
    return handlers.get_traffic()


@app.get("/network")
async def network(_=Depends(verify_token)):
    # 含 1 秒采样，用线程池执行避免阻塞事件循环
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, handlers.get_network_speed)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e), "code": 500})


@app.post("/speedtest")
async def speedtest_route(_=Depends(verify_token)):
    # 测速耗时 30-60 秒，必须在线程池中运行
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, handlers.run_speedtest)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": str(e), "code": 500})


# ── 启动入口 ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    PORT = int(os.environ.get("PORT", 9000))
    uvicorn.run(app, host="0.0.0.0", port=PORT)
