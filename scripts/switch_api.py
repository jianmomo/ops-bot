#!/usr/bin/env python3
"""
快速切换 / 修改 OpenClaw AI 后端配置
用法: python3 scripts/switch_api.py  或  make api
"""
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    print("缺少 pyyaml，运行: pip3 install pyyaml")
    sys.exit(1)

PROFILES_FILE = Path(__file__).resolve().parent.parent / "config" / "api_profiles.yaml"
OPENCLAW_ENV  = Path.home() / ".openclaw" / ".env"

_FAMILIES = [
    ("GPT",      ["gpt-", "o1-", "o3-", "o4-"]),
    ("Claude",   ["claude-"]),
    ("DeepSeek", ["deepseek-"]),
    ("Gemini",   ["gemini-"]),
    ("Llama",    ["llama", "meta-"]),
    ("Qwen",     ["qwen", "qwq"]),
    ("Mistral",  ["mistral", "mixtral"]),
]


# ══ I/O ═══════════════════════════════════════════════════════════════════

def env_read(path: Path) -> dict:
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text("utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            result[k.strip()] = v.strip()
    return result


def env_write(path: Path, updates: dict) -> None:
    """原地更新 .env，已有 key 覆盖，没有的追加，注释保留"""
    lines = path.read_text("utf-8").splitlines() if path.exists() else []
    touched: set = set()
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in line:
            k = line.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                touched.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in touched:
            out.append(f"{k}={v}")
    path.write_text("\n".join(out) + "\n", "utf-8")


def yaml_set(field: str, value: str) -> None:
    """原地替换 api_profiles.yaml 顶层字段，保留注释"""
    if not PROFILES_FILE.exists():
        return
    text = PROFILES_FILE.read_text("utf-8")
    new = re.sub(
        rf'^({re.escape(field)}:\s*).*$',
        lambda m: f'{m.group(1)}"{value}"',
        text, flags=re.MULTILINE,
    )
    if new != text:
        PROFILES_FILE.write_text(new, "utf-8")


def shell(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=False)  # noqa: S602


def mask(s: str) -> str:
    return (s[:6] + "*" * min(6, len(s) - 6) + "...") if s else "（未设置）"


def get_current_model() -> str:
    r = subprocess.run(
        "openclaw config get agents.defaults.model.primary",
        shell=True, capture_output=True, text=True,
    )
    return r.stdout.strip()


# ══ HTTP ══════════════════════════════════════════════════════════════════

def _parse_http_error(e: urllib.error.HTTPError) -> str:
    raw = e.read().decode(errors="replace")
    try:
        msg = json.loads(raw).get("error", {}).get("message", raw[:100])
    except Exception:
        msg = raw[:100]
    if "bad_response_status_code" in msg or "upstream" in msg.lower():
        return "中转站上游错误（该模型暂不可用）"
    if e.code == 401:
        return "API Key 无效（401 Unauthorized）"
    if e.code == 404:
        return "路径不存在（404），base_url 可能缺少或多余 /v1"
    return f"HTTP {e.code}: {msg}"


def http_get(url: str, key: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read())


def http_post(url: str, key: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def probe_models(key: str, base_url: str):
    """
    自动探测有效的 base_url 变体，返回 (有效base_url, 模型列表)。
    尝试顺序：原始 URL → 补 /v1 → 去掉 /v1
    """
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        variants = [url, url[:-3]]
    else:
        variants = [url + "/v1", url]

    last_err = None
    for v in variants:
        try:
            data = http_get(v + "/models", key)
            models = sorted(m["id"] for m in data.get("data", []))
            return v, models
        except urllib.error.HTTPError as e:
            last_err = RuntimeError(_parse_http_error(e))
        except Exception as e:
            last_err = e
    raise last_err or RuntimeError("连接失败")


def chat_test(key: str, base_url: str, model: str) -> str:
    try:
        data = http_post(base_url.rstrip("/") + "/chat/completions", key, {
            "model": model,
            "messages": [{"role": "user", "content": "Reply: OK"}],
            "max_tokens": 8,
        })
        return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(_parse_http_error(e))


def group_models(models: list) -> dict:
    groups: dict = {}
    used: set = set()
    for name, prefixes in _FAMILIES:
        hits = [m for m in models if any(m.lower().startswith(p) for p in prefixes)]
        if hits:
            groups[name] = hits
            used.update(hits)
    rest = [m for m in models if m not in used]
    if rest:
        groups["其他"] = rest
    return groups


# ══ Actions ═══════════════════════════════════════════════════════════════

def act_test(key: str, url: str, model: str) -> None:
    if not key or not url:
        print("\n  ❌ 请先设置 API Key 和接入点\n")
        return
    print(f"\n  接入点 : {url}")
    print(f"  Key    : {mask(key)}\n")

    print("  ① 连接 + 发现模型...", end="", flush=True)
    try:
        valid_url, models = probe_models(key, url)
        print(f" ✅ {len(models)} 个模型")
        if valid_url != url:
            print(f"     ⚠️  建议将接入点改为: {valid_url}（当前有效路径）")
    except Exception as e:
        print(f" ❌ {e}\n")
        return

    if not model:
        return
    model_id = model.split("/", 1)[-1] if "/" in model else model
    if model_id not in models:
        print(f"  ② 模型 {model_id} 不在列表中（可能已下线）\n")
        return
    print(f"  ② 对话测试 {model_id}...", end="", flush=True)
    try:
        reply = chat_test(key, valid_url, model_id)
        print(f" ✅ 响应: {reply[:60]}\n")
    except Exception as e:
        print(f" ❌ {e}\n")


def act_discover(key: str, url: str):
    """发现并选择模型。返回 (model_id_or_None, corrected_url_or_None)"""
    if not key or not url:
        print("\n  ❌ 请先设置 API Key 和接入点\n")
        return None, None

    print("\n  查询可用模型...", end="", flush=True)
    try:
        valid_url, models = probe_models(key, url)
    except Exception as e:
        print(f" ❌ {e}\n")
        return None, None
    print(f" ✅ {len(models)} 个\n")

    # 如果探测到更好的 URL，记录下来供 main 修正
    corrected_url = valid_url if valid_url != url else None

    filt = input("  关键词过滤（留空显示全部）> ").strip().lower()
    filtered = [m for m in models if filt in m.lower()] if filt else models
    if not filtered:
        print("  无匹配模型\n")
        return None, corrected_url

    groups = group_models(filtered)
    flat: list = []
    for gname, gmodels in groups.items():
        print(f"\n  ── {gname} ──")
        for m in gmodels:
            flat.append(m)
            print(f"    {len(flat):3d}. {m}")
    print()

    raw = input(f"  选择 [1-{len(flat)}]（留空取消）> ").strip()
    if not raw:
        return None, corrected_url
    try:
        return flat[int(raw) - 1], corrected_url
    except (ValueError, IndexError):
        print("  无效输入\n")
        return None, corrected_url


def act_edit_url(cfg: dict, cur: dict):
    """修改接入点，自动探测并修正。返回新 url 或 None"""
    cur_url = cur.get("OPENAI_BASE_URL") or cfg.get("default_base_url", "")
    print(f"\n  当前接入点: {cur_url or '（未设置）'}")
    new_url = input("  新接入点 URL（留空取消）> ").strip()
    if not new_url:
        return None
    key = cur.get("OPENAI_API_KEY") or cfg.get("default_api_key", "")
    if key:
        print("  测试连接...", end="", flush=True)
        try:
            valid_url, models = probe_models(key, new_url)
            print(f" ✅ {len(models)} 个模型")
            if valid_url != new_url.rstrip("/"):
                print(f"  ⚙️  自动修正为: {valid_url}")
                new_url = valid_url
        except Exception as e:
            print(f" ⚠️  {e}（配置已保存，请确认 URL）")
    env_write(OPENCLAW_ENV, {"OPENAI_BASE_URL": new_url})
    yaml_set("default_base_url", new_url)
    return new_url


def act_edit_key(cfg: dict, cur: dict):
    """修改 API Key，自动测试。返回新 key 或 None"""
    cur_key = cur.get("OPENAI_API_KEY") or cfg.get("default_api_key", "")
    print(f"\n  当前 Key: {mask(cur_key)}")
    new_key = input("  新 API Key（留空取消）> ").strip()
    if not new_key:
        return None
    base_url = cur.get("OPENAI_BASE_URL") or cfg.get("default_base_url", "")
    if base_url:
        print("  测试 Key...", end="", flush=True)
        try:
            valid_url, models = probe_models(new_key, base_url)
            print(f" ✅ 有效，{len(models)} 个模型")
            # 顺便修正 base_url
            if valid_url != base_url.rstrip("/"):
                print(f"  ⚙️  同时修正接入点为: {valid_url}")
                env_write(OPENCLAW_ENV, {"OPENAI_BASE_URL": valid_url})
                yaml_set("default_base_url", valid_url)
        except Exception as e:
            print(f" ❌ {e}")
    env_write(OPENCLAW_ENV, {"OPENAI_API_KEY": new_key})
    yaml_set("default_api_key", new_key)
    return new_key


def act_apply_profile(profile: dict, cfg: dict, cur: dict) -> bool:
    """应用 profile，返回是否需要重启"""
    key = profile.get("api_key") or cfg.get("default_api_key") or cur.get("OPENAI_API_KEY", "")
    url = profile.get("base_url") or cfg.get("default_base_url") or cur.get("OPENAI_BASE_URL", "")

    if not key:
        key = input("  未设置 API Key，请输入 > ").strip()
        if not key:
            return False

    updates = {}
    if profile.get("api_key"):
        updates["OPENAI_API_KEY"] = profile["api_key"]
    if profile.get("base_url"):
        updates["OPENAI_BASE_URL"] = profile["base_url"]
    if updates:
        env_write(OPENCLAW_ENV, updates)

    model    = profile["model"]
    provider = profile.get("provider", "openai")
    full     = model if "/" in model else f"{provider}/{model}"
    model_id = full.split("/", 1)[-1]

    print(f"\n  测试 {model_id}...", end="", flush=True)
    try:
        reply = chat_test(key, url, model_id)
        print(f" ✅ {reply[:60]}")
    except Exception as e:
        print(f" ⚠️  {e}")

    shell(f'openclaw config set agents.defaults.model.primary "{full}"')
    print(f"  ✅ 已切换: {profile['name']} → {full}\n")
    return True


# ══ Main loop ═════════════════════════════════════════════════════════════

def load_state():
    with open(PROFILES_FILE, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cur = env_read(OPENCLAW_ENV)
    url = cur.get("OPENAI_BASE_URL") or cfg.get("default_base_url", "")
    key = cur.get("OPENAI_API_KEY")  or cfg.get("default_api_key", "")
    mdl = get_current_model()
    return cfg, cur, url, key, mdl


def show_menu(cfg: dict, url: str, key: str, mdl: str) -> None:
    profiles = cfg.get("profiles", [])
    W = 46

    def row(s=""): print(f"║  {s:<{W}}║")

    print()
    print("╔" + "═" * (W + 2) + "╗")
    row("切换 AI API 配置")
    print("╠" + "═" * (W + 2) + "╣")
    row(f"接入点 : {(url or '（未设置）')[:W - 9]}")
    row(f"Key    : {mask(key)}")
    row(f"当前   : {(mdl or '（未知）')[:W - 9]}")
    if profiles:
        print("╠" + "═" * (W + 2) + "╣")
        row("快捷 Profile：")
        for i, p in enumerate(profiles, 1):
            tag = "*" if p.get("api_key") else " "
            row(f"  {i:2d}.{tag} {p['name']:<22} {p.get('model', '')[:16]}")
    print("╠" + "═" * (W + 2) + "╣")
    row("[d] 动态发现并选择模型    [t] 测试当前配置")
    row("[e] 修改接入点 URL        [k] 修改 API Key")
    row("[0] 退出")
    print("╚" + "═" * (W + 2) + "╝")
    if profiles:
        print("    * = 使用独立 Key\n")


def main() -> None:
    if not PROFILES_FILE.exists():
        print(f"找不到配置文件: {PROFILES_FILE}")
        sys.exit(1)

    while True:
        cfg, cur, url, key, mdl = load_state()
        profiles = cfg.get("profiles", [])
        show_menu(cfg, url, key, mdl)

        raw = input("选择 > ").strip().lower()
        need_restart = False

        if raw in ("0", "q", ""):
            print("已退出")
            break

        elif raw == "t":
            act_test(key, url, mdl)

        elif raw == "d":
            model_id, corrected_url = act_discover(key, url)
            if corrected_url:
                print(f"  ⚙️  接入点已自动修正: {corrected_url}")
                env_write(OPENCLAW_ENV, {"OPENAI_BASE_URL": corrected_url})
                yaml_set("default_base_url", corrected_url)
                need_restart = True
            if model_id:
                full = f"openai/{model_id}"
                shell(f'openclaw config set agents.defaults.model.primary "{full}"')
                need_restart = True
                print(f"  ✅ 已选择: {full}\n")

        elif raw == "e":
            result = act_edit_url(cfg, cur)
            if result:
                need_restart = True
                print(f"  ✅ 接入点已保存\n")

        elif raw == "k":
            result = act_edit_key(cfg, cur)
            if result:
                need_restart = True
                print(f"  ✅ API Key 已保存: {mask(result)}\n")

        else:
            try:
                idx = int(raw) - 1
                if not 0 <= idx < len(profiles):
                    raise ValueError
            except ValueError:
                print("  无效输入\n")
                continue
            need_restart = act_apply_profile(profiles[idx], cfg, cur)

        if need_restart:
            print("  重启 OpenClaw...", end="", flush=True)
            shell("openclaw gateway restart")
            print(" 完成\n")


if __name__ == "__main__":
    main()
