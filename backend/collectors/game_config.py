"""
游戏注册表加载器。

唯一事实来源是 `backend/config/games.yaml`，本模块把它读成结构化数据，
供采集器、CI 发布、后端 API 共用 —— 目标是**消除散落各处的硬编码 AppID**。

为什么不用 PyYAML：
    采集链路（本地计划任务 + GitHub Actions）刻意保持零第三方依赖。
    为一个扁平的配置列表引入一个包，会同时增加本地装包与 CI 失败的两种风险。
    因此这里实现一个**严格受限**的 YAML 子集解析器，只支持：
        - 顶层 `key:` 后接标量 / 映射 / 映射列表
        - `- key: value` 列表项及其缩进续行
        - 标量：字符串（含引号）、整数、浮点、true/false、null、行内列表 [a, b]
        - `#` 行内注释（引号内的 # 不当注释）
    若将来确实需要嵌套结构，正确做法是让 CI 安装 PyYAML 并在此处切换到
    `yaml.safe_load`，而不是继续扩展这个解析器。

用法：
    from game_config import load_games, enabled_games
    cfg = load_games()
    for g in cfg["games"]:
        print(g["appid"], g["name"])
"""

import json
import os
import re
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.dirname(BACKEND_DIR)
DEFAULT_CONFIG = os.path.join(BACKEND_DIR, "config", "games.yaml")

# 缺省配色：按出现顺序分配，保证新增游戏不必手工挑颜色
PALETTE = ["#35D9F2", "#9275FF", "#FFB45F", "#35D399", "#F0536B", "#A78BFA", "#5AD1A0", "#F5A524"]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?\d+\.\d+$")

DEFAULT_DEFAULTS = {
    "interval_minutes": 5,
    "window_hours": 72,
    "achievement_interval_hours": 12,
}


class GameConfigError(ValueError):
    """配置文件不合法。消息里必须带行号或字段名，否则排查成本会转移到使用者身上。"""


# --------------------------------------------------------------------------
# YAML 子集解析
# --------------------------------------------------------------------------

def _strip_comment(line):
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def _parse_scalar(raw, lineno):
    s = raw.strip()
    if s == "":
        raise GameConfigError(
            "第 %d 行：值为空。本项目配置只支持单层结构，不支持嵌套映射；"
            "若需嵌套请先改用 PyYAML（见文件头说明）。" % lineno
        )

    if s[0] in ("'", '"'):
        if len(s) < 2 or s[-1] != s[0]:
            raise GameConfigError("第 %d 行：引号未闭合 → %s" % (lineno, s))
        return s[1:-1]

    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(x, lineno) for x in inner.split(",")]

    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", ""):
        return None
    if _INT_RE.match(s):
        return int(s)
    if _FLOAT_RE.match(s):
        return float(s)
    return s


def _split_kv(body, lineno):
    if ":" not in body:
        raise GameConfigError("第 %d 行：缺少冒号，无法解析 → %s" % (lineno, body))
    key, _, val = body.partition(":")
    key = key.strip()
    if not key:
        raise GameConfigError("第 %d 行：键为空 → %s" % (lineno, body))
    return key, val


def parse_subset(text):
    """把受限 YAML 子集解析成 dict。行号从 1 开始，报错直接可定位。"""
    root = {}
    section = None          # 当前顶层 key
    is_list = False         # 当前顶层是否为列表
    item = None             # 当前列表项

    lines = text.splitlines()
    for lineno, raw in enumerate(lines, 1):
        line = _strip_comment(raw)
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()

        if indent == 0:
            if not body.endswith(":"):
                raise GameConfigError(
                    "第 %d 行：顶层必须是 `key:` 形式 → %s" % (lineno, body)
                )
            section = body[:-1].strip()
            if not section:
                raise GameConfigError("第 %d 行：顶层键为空" % lineno)
            if section in root:
                raise GameConfigError("第 %d 行：顶层键重复 `%s`" % (lineno, section))

            # 预读下一条有效行，判断是列表还是映射
            nxt = None
            for probe in lines[lineno:]:
                p = _strip_comment(probe).strip()
                if p:
                    nxt = p
                    break
            is_list = bool(nxt and nxt.startswith("- "))
            root[section] = [] if is_list else {}
            item = None
            continue

        if section is None:
            raise GameConfigError("第 %d 行：缩进内容出现在任何顶层键之前" % lineno)

        if is_list:
            if body.startswith("- "):
                item = {}
                root[section].append(item)
                rest = body[2:].strip()
                if rest:
                    k, v = _split_kv(rest, lineno)
                    item[k] = _parse_scalar(v, lineno)
                continue
            if item is None:
                raise GameConfigError("第 %d 行：列表项内的键出现在 `- ` 之前" % lineno)
            k, v = _split_kv(body, lineno)
            if k in item:
                raise GameConfigError("第 %d 行：同一列表项内键重复 `%s`" % (lineno, k))
            item[k] = _parse_scalar(v, lineno)
        else:
            k, v = _split_kv(body, lineno)
            if k in root[section]:
                raise GameConfigError(
                    "第 %d 行：`%s` 下的键重复 `%s`" % (lineno, section, k)
                )
            root[section][k] = _parse_scalar(v, lineno)

    return root


# --------------------------------------------------------------------------
# 校验与规范化
# --------------------------------------------------------------------------

def _normalize(raw, source):
    defaults = dict(DEFAULT_DEFAULTS)
    for k, v in (raw.get("defaults") or {}).items():
        if k not in DEFAULT_DEFAULTS:
            raise GameConfigError("%s: defaults 中存在未知字段 `%s`" % (source, k))
        if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
            raise GameConfigError("%s: defaults.%s 必须是正整数，实际 %r" % (source, k, v))
        defaults[k] = v

    games_raw = raw.get("games")
    if not isinstance(games_raw, list) or not games_raw:
        raise GameConfigError("%s: games 缺失或为空，至少需要一款游戏" % source)

    seen_appid, seen_slug, games = {}, {}, []
    for idx, g in enumerate(games_raw, 1):
        if not isinstance(g, dict):
            raise GameConfigError("%s: games 第 %d 项不是映射" % (source, idx))
        where = "%s: games[%d]" % (source, idx)

        appid = g.get("appid")
        if not isinstance(appid, int) or isinstance(appid, bool) or appid <= 0:
            raise GameConfigError("%s 的 appid 必须是正整数，实际 %r" % (where, appid))
        if appid in seen_appid:
            raise GameConfigError("%s 的 appid %d 与第 %d 项重复" % (where, appid, seen_appid[appid]))
        seen_appid[appid] = idx

        slug = g.get("slug")
        if not isinstance(slug, str) or not SLUG_RE.match(slug):
            raise GameConfigError(
                "%s 的 slug 必须是小写字母/数字/连字符（1–32 位），实际 %r" % (where, slug)
            )
        if slug in seen_slug:
            raise GameConfigError("%s 的 slug `%s` 与第 %d 项重复" % (where, slug, seen_slug[slug]))
        seen_slug[slug] = idx

        name = g.get("name")
        if not isinstance(name, str) or not name.strip():
            raise GameConfigError("%s 的 name 不能为空" % where)

        color = g.get("color") or PALETTE[(idx - 1) % len(PALETTE)]
        if not isinstance(color, str) or not re.match(r"^#[0-9A-Fa-f]{6}$", color):
            raise GameConfigError("%s 的 color 必须是 #RRGGBB，实际 %r" % (where, color))

        enabled = g.get("enabled", True)
        if not isinstance(enabled, bool):
            raise GameConfigError("%s 的 enabled 必须是 true/false，实际 %r" % (where, enabled))

        interval = g.get("interval_minutes", defaults["interval_minutes"])
        if not isinstance(interval, int) or isinstance(interval, bool) or interval <= 0:
            raise GameConfigError("%s 的 interval_minutes 必须是正整数，实际 %r" % (where, interval))

        tags = g.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise GameConfigError("%s 的 tags 必须是字符串列表，实际 %r" % (where, tags))

        unknown = set(g) - {"appid", "slug", "name", "color", "enabled", "interval_minutes", "tags"}
        if unknown:
            raise GameConfigError("%s 存在未知字段：%s" % (where, "、".join(sorted(unknown))))

        games.append({
            "appid": appid,
            "slug": slug,
            "name": name.strip(),
            "color": color,
            "enabled": enabled,
            "interval_minutes": interval,
            "tags": tags,
        })

    if not any(g["enabled"] for g in games):
        raise GameConfigError("%s: 至少需要一款 enabled: true 的游戏，否则采集无事可做" % source)

    return {"defaults": defaults, "games": games, "source": source}


def load_games(path=None):
    """读取并校验配置，返回 {defaults, games, source}。失败抛 GameConfigError。"""
    path = path or os.environ.get("GAMES_CONFIG") or DEFAULT_CONFIG
    if not os.path.exists(path):
        raise GameConfigError("找不到游戏配置：%s" % path)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return _normalize(parse_subset(text), os.path.relpath(path, ROOT_DIR))


def enabled_games(path=None):
    """只需要采集目标时用这个：按配置顺序返回 enabled 的游戏。"""
    return [g for g in load_games(path)["games"] if g["enabled"]]


def find_game(appid, path=None):
    for g in load_games(path)["games"]:
        if g["appid"] == int(appid):
            return g
    return None


def public_payload(cfg=None):
    """给前端/API 用的精简结构：不含 internal 字段，只保留展示所需。"""
    cfg = cfg or load_games()
    return {
        "source": cfg["source"],
        "defaults": cfg["defaults"],
        "games": [
            {
                "appid": g["appid"],
                "slug": g["slug"],
                "name": g["name"],
                "color": g["color"],
                "enabled": g["enabled"],
                "interval_minutes": g["interval_minutes"],
                "tags": g["tags"],
            }
            for g in cfg["games"]
        ],
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv

    try:
        cfg = load_games()
    except GameConfigError as e:
        print("配置错误：%s" % e, file=sys.stderr)
        return 2

    if as_json:
        print(json.dumps(public_payload(cfg), ensure_ascii=False, indent=2))
        return 0

    print("配置：%s" % cfg["source"])
    print("缺省：%s" % json.dumps(cfg["defaults"], ensure_ascii=False))
    print("")
    print("%-9s %-7s %-16s %-9s %-6s %s" % ("appid", "slug", "name", "color", "启用", "间隔"))
    for g in cfg["games"]:
        print("%-9d %-7s %-16s %-9s %-6s %d 分钟"
              % (g["appid"], g["slug"], g["name"], g["color"],
                 "是" if g["enabled"] else "否", g["interval_minutes"]))
    print("\n共 %d 款，启用 %d 款" % (len(cfg["games"]), sum(1 for g in cfg["games"] if g["enabled"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
