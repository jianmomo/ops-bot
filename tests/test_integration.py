"""
集成测试 — Phase 1 MVP
不使用纯 mock，测试真实组件协作。
"""
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import bot.config as cfg_mod
import bot.permissions as perm_mod
from bot.config import Config
from bot.router import CommandRouter

FIXTURE_CFG = str(ROOT / "tests" / "fixtures" / "test_config.yaml")

# ══════════════════════════════════════════════════════════════════════
# A. Config 集成测试
# ══════════════════════════════════════════════════════════════════════

class TestConfig:
    """直接实例化 Config，不走单例，使用测试专用 yaml"""

    def setup_method(self):
        self.cfg = Config(FIXTURE_CFG, env_path="/dev/null")

    # ── 用户白名单 ─────────────────────────────────────────────────
    def test_telegram_user_allowed(self):
        assert self.cfg.is_allowed_user("telegram", "100001") is True

    def test_telegram_user_allowed_second(self):
        assert self.cfg.is_allowed_user("telegram", "100002") is True

    def test_telegram_user_denied(self):
        assert self.cfg.is_allowed_user("telegram", "999999") is False

    def test_qq_user_allowed(self):
        assert self.cfg.is_allowed_user("qq", "200001") is True

    def test_qq_user_denied_cross_platform(self):
        # telegram 用户不在 qq 白名单
        assert self.cfg.is_allowed_user("qq", "100001") is False

    def test_unknown_platform_denied(self):
        assert self.cfg.is_allowed_user("discord", "100001") is False

    # ── 服务白名单 ─────────────────────────────────────────────────
    def test_service_allowed(self):
        assert self.cfg.is_allowed_service("nginx") is True

    def test_service_allowed_second(self):
        assert self.cfg.is_allowed_service("trilium") is True

    def test_service_denied(self):
        # test_config.yaml 中不含 x-ui
        assert self.cfg.is_allowed_service("x-ui") is False

    # ── 节点查询 ───────────────────────────────────────────────────
    def test_get_node_vps1(self):
        node = self.cfg.get_node("vps1")
        assert node["host"] == "10.0.0.1"
        assert node["port"] == 9000
        assert node["token"] == "test-token-vps1"
        assert node["label"] == "测试节点1"

    def test_get_node_vps2(self):
        node = self.cfg.get_node("vps2")
        assert node["host"] == "10.0.0.2"

    def test_get_node_not_found(self):
        with pytest.raises(KeyError, match="vps999"):
            self.cfg.get_node("vps999")

    # ── 其他属性 ───────────────────────────────────────────────────
    def test_log_lines(self):
        assert self.cfg.log_lines == 30

    def test_ai_config(self):
        ai = self.cfg.ai_config
        assert ai["provider"] == "anthropic"
        assert "haiku" in ai["model"]
        assert ai["max_tokens"] == 500

    def test_allowed_services_list(self):
        assert self.cfg.allowed_services == ["nginx", "trilium"]


# ══════════════════════════════════════════════════════════════════════
# B. Router 集成测试
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture()
def router():
    """CommandRouter 使用测试配置；测试结束后恢复单例"""
    old_cfg = cfg_mod._instance
    old_chk = perm_mod._checker

    cfg_mod._instance = Config(FIXTURE_CFG, env_path="/dev/null")
    perm_mod._checker = None  # 强制重建，从新 config 读白名单

    yield CommandRouter()

    cfg_mod._instance = old_cfg
    perm_mod._checker = old_chk


class TestRouter:

    # ── 合法用户 /status → AgentClient.call_status 被调用 ─────────
    async def test_status_calls_agent(self, router):
        with patch("bot.handlers.status.AgentClient") as MockCls:
            inst = MagicMock()
            inst.call_status = AsyncMock(return_value="🖥 CPU: 10%")
            MockCls.return_value = inst

            result = await router.route("telegram", "100001", "/status")

        MockCls.assert_called_once()
        inst.call_status.assert_awaited_once()
        assert result == "🖥 CPU: 10%"

    # ── 非法用户 → 拒绝，不调用 AgentClient ───────────────────────
    async def test_unauthorized_blocked(self, router):
        with patch("bot.handlers.status.AgentClient") as MockCls:
            result = await router.route("telegram", "999999", "/status")

        assert "⛔" in result
        MockCls.assert_not_called()

    # ── /restart 白名单外服务 → 错误，不调用 AgentClient ──────────
    async def test_invalid_service_blocked(self, router):
        # test_config.yaml allowed_services: [nginx, trilium]，x-ui 不在其中
        with patch("bot.handlers.restart_handler.AgentClient") as MockCls:
            result = await router.route("telegram", "100001", "/restart x-ui")

        assert "❌" in result
        assert "x-ui" in result
        MockCls.assert_not_called()

    # ── /docker 合法调用 ───────────────────────────────────────────
    async def test_docker_calls_agent(self, router):
        with patch("bot.handlers.docker_handler.AgentClient") as MockCls:
            inst = MagicMock()
            inst.call_docker = AsyncMock(return_value="🐳 nginx running")
            MockCls.return_value = inst

            result = await router.route("telegram", "100001", "/docker")

        inst.call_docker.assert_awaited_once()
        assert result == "🐳 nginx running"

    # ── 未知命令 → 返回帮助文本 ────────────────────────────────────
    async def test_unknown_command_returns_help(self, router):
        result = await router.route("telegram", "100001", "/unknowncmd")
        assert "/status" in result
        assert "/services" in result

    # ── /log 无服务名 → 友好提示 ──────────────────────────────────
    async def test_log_missing_service(self, router):
        result = await router.route("telegram", "100001", "/log")
        assert "❌" in result
        assert "nginx" in result  # 示例服务名在提示里


# ══════════════════════════════════════════════════════════════════════
# C. Agent FastAPI 集成测试
# ══════════════════════════════════════════════════════════════════════

AGENT_DIR = str(ROOT / "agent")

@pytest.fixture(scope="module")
def agent_app():
    """
    加载 agent FastAPI app：
    - 真实 psutil 采集指标
    - ALLOWED_SERVICES=trilium,x-ui（nginx 故意排在外，用于 403 测试）
    - 不开真实端口，通过 ASGITransport 调用
    """
    os.environ["AGENT_TOKEN"] = "test-agent-token"
    os.environ["ALLOWED_SERVICES"] = "trilium,x-ui"

    if AGENT_DIR not in sys.path:
        sys.path.insert(0, AGENT_DIR)

    # 清除缓存，确保 env vars 生效
    for mod in ("main", "handlers"):
        sys.modules.pop(mod, None)

    import main as agent_main  # noqa: PLC0415
    return agent_main.app


@pytest.fixture()
def _transport(agent_app):
    return httpx.ASGITransport(app=agent_app)


TOKEN_HDR = {"Authorization": "Bearer test-agent-token"}


class TestAgentAPI:

    # ── /health → 200 ─────────────────────────────────────────────
    async def test_health_ok(self, _transport):
        async with httpx.AsyncClient(transport=_transport, base_url="http://test") as c:
            r = await c.get("/health", headers=TOKEN_HDR)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "host" in body

    # ── /status 无 token → 401（FastAPI HTTPBearer：无凭证时返回 401）
    async def test_status_no_token(self, _transport):
        async with httpx.AsyncClient(transport=_transport, base_url="http://test") as c:
            r = await c.get("/status")
        # 401 = 未提供凭证；403 = 凭证错误；两者均拒绝访问
        assert r.status_code in (401, 403)

    # ── /status 正确 token → 200 + 字段校验 ──────────────────────
    async def test_status_with_token(self, _transport):
        async with httpx.AsyncClient(transport=_transport, base_url="http://test") as c:
            r = await c.get("/status", headers=TOKEN_HDR)
        assert r.status_code == 200
        data = r.json()
        # 只校验字段存在（具体值因环境不同而异）
        assert "cpu_percent" in data
        assert "memory" in data
        assert "used_gb" in data["memory"]
        assert "total_gb" in data["memory"]
        assert "disk" in data
        assert "uptime" in data

    # ── /restart/nginx（白名单外）→ 403 ──────────────────────────
    async def test_restart_not_allowed_service(self, _transport):
        # ALLOWED_SERVICES=trilium,x-ui，nginx 不在其中
        async with httpx.AsyncClient(transport=_transport, base_url="http://test") as c:
            r = await c.post("/restart/nginx", headers=TOKEN_HDR)
        assert r.status_code == 403
        body = r.json()
        assert "error" in body
        assert "nginx" in body["error"]

    # ── 错误 token → 403 ──────────────────────────────────────────
    async def test_wrong_token(self, _transport):
        async with httpx.AsyncClient(transport=_transport, base_url="http://test") as c:
            r = await c.get("/status", headers={"Authorization": "Bearer wrong-token"})
        assert r.status_code == 403
