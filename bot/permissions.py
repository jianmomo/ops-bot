import asyncio
import functools
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)

DENIED_MSG = "⛔ 无访问权限"


class PermissionChecker:
    """基于 Config 白名单的权限校验器（单例）"""

    def __init__(self) -> None:
        # 延迟导入，避免循环依赖
        from bot.config import get_config
        self._cfg = get_config()

    def check(self, platform: str, user_id: str) -> bool:
        """检查 user_id 是否在 platform 白名单中"""
        result = self._cfg.is_allowed_user(platform, str(user_id))
        if not result:
            logger.warning("拒绝访问: platform=%s user_id=%s", platform, user_id)
        return result


_checker: PermissionChecker | None = None


def get_checker() -> PermissionChecker:
    global _checker
    if _checker is None:
        _checker = PermissionChecker()
    return _checker


def require_permission(func: Callable) -> Callable:
    """
    装饰器：校验被装饰函数的前两个参数 (platform, user_id)。
    校验失败时直接返回拒绝提示，不调用原函数、不抛异常。

    用法::

        @require_permission
        async def handle_status(platform: str, user_id: str, ...):
            ...
    """
    @functools.wraps(func)
    async def wrapper(platform: str, user_id: str, *args: Any, **kwargs: Any) -> Any:
        if not get_checker().check(platform, str(user_id)):
            return DENIED_MSG
        return await func(platform, user_id, *args, **kwargs)

    # 兼容同步函数
    if not asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        def sync_wrapper(platform: str, user_id: str, *args: Any, **kwargs: Any) -> Any:
            if not get_checker().check(platform, str(user_id)):
                return DENIED_MSG
            return func(platform, user_id, *args, **kwargs)
        return sync_wrapper

    return wrapper


# ──────────────────────────────────────────────
# 单元测试（python3 bot/permissions.py）
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    # 用允许的用户覆盖 config，避免依赖真实 .env
    from bot.config import Config, get_config
    import bot.config as _cfg_mod

    # 注入一个带测试数据的 Config
    class _TestConfig:
        def is_allowed_user(self, platform, user_id):
            whitelist = {
                "telegram": ["111", "222"],
                "qq": ["333"],
            }
            return str(user_id) in whitelist.get(platform, [])

    _cfg_mod._instance = _TestConfig()   # type: ignore[assignment]

    # 重置 checker 单例，使用新 config
    import bot.permissions as _pm
    _pm._checker = None

    checker = get_checker()

    PASS = "\033[32mPASS\033[0m"
    FAIL = "\033[31mFAIL\033[0m"

    def assert_eq(label, got, expected):
        ok = got == expected
        print(f"  [{PASS if ok else FAIL}] {label}")
        if not ok:
            print(f"         expected={expected!r}  got={got!r}")
        return ok

    all_ok = True

    print("\n─── PermissionChecker.check() ───")
    all_ok &= assert_eq("telegram 白名单用户 111",   checker.check("telegram", "111"), True)
    all_ok &= assert_eq("telegram 非白名单用户 999",  checker.check("telegram", "999"), False)
    all_ok &= assert_eq("qq 白名单用户 333",          checker.check("qq", "333"),       True)
    all_ok &= assert_eq("qq 非白名单用户 111",        checker.check("qq", "111"),       False)
    all_ok &= assert_eq("未知平台",                   checker.check("discord", "111"),  False)

    print("\n─── @require_permission 装饰器 ───")

    @require_permission
    async def _mock_handler(platform: str, user_id: str, payload: str = "") -> str:
        return f"ok:{payload}"

    async def _run_tests():
        inner_ok = True

        r1 = await _mock_handler("telegram", "111", "hello")
        inner_ok &= assert_eq("白名单用户调用成功",       r1,  "ok:hello")

        r2 = await _mock_handler("telegram", "999")
        inner_ok &= assert_eq("非白名单用户返回拒绝提示", r2,  DENIED_MSG)

        r3 = await _mock_handler("qq", "333", "world")
        inner_ok &= assert_eq("QQ 白名单用户调用成功",    r3,  "ok:world")

        return inner_ok

    all_ok &= asyncio.run(_run_tests())

    print()
    print("全部测试通过 ✓" if all_ok else "有测试失败 ✗")
    sys.exit(0 if all_ok else 1)
