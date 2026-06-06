import os
import re
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_instance: Optional["Config"] = None


def _resolve_env_vars(value: Any) -> Any:
    """递归替换 yaml 值中的 ${VAR_NAME} 占位符为环境变量"""
    if isinstance(value, str):
        def _replace(match: re.Match) -> str:
            var_name = match.group(1)
            env_val = os.environ.get(var_name, "")
            if not env_val:
                logger.warning("环境变量 ${%s} 未设置，使用空值", var_name)
            return env_val
        return re.sub(r"\$\{(\w+)\}", _replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


class Config:
    def __init__(self, config_path: str = "config/config.yaml", env_path: str = ".env"):
        env_file = Path(env_path)
        if env_file.exists():
            load_dotenv(env_file)
            logger.debug("已加载 %s", env_file)
        else:
            logger.info(".env 文件不存在，依赖系统环境变量")

        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        with open(config_file, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        self._data: Dict[str, Any] = _resolve_env_vars(raw)
        self._validate()

    def _validate(self) -> None:
        missing = [f for f in ("allowed_users", "allowed_services", "nodes") if f not in self._data]
        if missing:
            raise ValueError(f"config.yaml 缺少必要字段: {missing}")

        users = self._data["allowed_users"]
        for platform in ("telegram", "qq"):
            if platform not in users:
                raise ValueError(f"config.yaml: allowed_users 缺少 {platform} 分组")

        if not self._data["nodes"]:
            raise ValueError("config.yaml: nodes 不能为空")

    # -------- 公共查询方法 --------

    def get_node(self, name: str) -> Dict[str, Any]:
        nodes = self._data.get("nodes", {})
        if name not in nodes:
            raise KeyError(f"节点 '{name}' 不存在，可用: {list(nodes.keys())}")
        return nodes[name]

    def is_allowed_user(self, platform: str, user_id: str) -> bool:
        users: List[str] = self._data.get("allowed_users", {}).get(platform, [])
        return str(user_id) in [str(u) for u in users]

    def is_allowed_service(self, name: str) -> bool:
        return name in self._data.get("allowed_services", [])

    # -------- 属性 --------

    @property
    def nodes(self) -> Dict[str, Any]:
        return self._data.get("nodes", {})

    @property
    def allowed_services(self) -> List[str]:
        return self._data.get("allowed_services", [])

    @property
    def log_lines(self) -> int:
        return int(self._data.get("log_lines", 50))

    @property
    def ai_config(self) -> Dict[str, Any]:
        return self._data.get("ai", {})

    @property
    def telegram_token(self) -> str:
        return os.environ.get("TELEGRAM_BOT_TOKEN", "")

    # ── QQ Bot（OneBot V11 直连）────────────────────────────────────

    @property
    def qq_ws_url(self) -> str:
        return os.environ.get("QQ_WS_URL", "")

    @property
    def qq_http_url(self) -> str:
        return os.environ.get("QQ_HTTP_URL", "")

    @property
    def qq_access_token(self) -> str:
        return os.environ.get("QQ_ACCESS_TOKEN", "")


def get_config() -> Config:
    """返回全局单例 Config，首次调用时初始化"""
    global _instance
    if _instance is None:
        _instance = Config()
    return _instance
