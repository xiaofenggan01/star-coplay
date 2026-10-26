"""
observe Skill — ObserveModule 抽象基础设施

实现规范来源：
  observe-l3-skill-spec.md §3（ObserveModule 抽象）、§12（工具函数）

设计选择：
  dataclass 而非 ABC/Mixin，原因：
  - 模块是数据驱动的（queries/render/visibility 都是配置项），不是行为层级
  - dataclass 支持直接序列化检查，便于测试断言
  - 可通过 field 提供合理默认值，避免每个模块都要写 __init__
  - 注册表只需 list[ObserveModule]，无需复杂工厂
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


# ── QuerySpec 数据类 ──────────────────────────────────────────────────

@dataclass
class QuerySpec:
    """
    描述一次 query_* 调用所需的参数。
    用于 ObserveModule.queries 声明，采集时由编排层统一调用。
    """
    capability: str
    """Mod 侧 capability name，如 "find_targets"、"query_containers"。
    注意：game_state 从 Blackboard 同步读取，不走 IPC，不需要 QuerySpec。
    """

    params: dict = field(default_factory=dict)
    """额外参数，如 {"target_type": "crop"}。
    空字典表示无额外参数（如 "query_inventory"）。
    """

    result_key: str = ""
    """合并后 data dict 中的 key，如 "crops_data"、"inventory"。
    编排层在 data[result_key] = query_result 存放结果。
    """


# ── ObserveModule 数据类 ──────────────────────────────────────────────

@dataclass
class ObserveModule:
    """
    observe 的最小信息单元。
    每个模块负责一类信息的采集规格声明 + 渲染。

    设计参考：observe-l3-skill-spec.md §3.1
    """

    name: str
    """唯一标识符，如 "player_status"、"farm_summary_crops"。"""

    layer: int
    """出现在哪一层：0=场景速览, 1=类别列表, 2=单实例详情。"""

    section_title: str
    """
    渲染时的章节标题。
    空字符串表示嵌入上文不加标题（如 player_status、world_time）。
    含 {observe_id} 等占位符时，由渲染函数在运行时替换。
    """

    queries: list[QuerySpec]
    """
    声明依赖哪些 query_* 调用（采集时用）。
    层 2 的动态参数在运行时计算，queries 为空列表。
    game_state 从 Blackboard 读取，不在 queries 中声明。
    """

    render: Callable[[dict], str]
    """
    渲染函数：data dict → str（纯函数，无副作用）。
    data 包含所有 QuerySpec.result_key 对应的查询结果，以及 alias_map 等元数据。
    返回空字符串时，组装层跳过该模块（不留空行）。
    """

    visibility: Callable[[dict], bool] = field(
        default_factory=lambda: (lambda data: True)
    )
    """
    可见性条件：data dict → bool。
    默认始终显示（True）。
    层 0 的农场/NPC/容器模块在数据为空时返回 False。
    层 1/2 模块通常无条件渲染（显式调用时始终渲染）。
    """

    def applicable(self, data: dict) -> bool:
        """
        是否在当前数据上下文下渲染该模块。
        可见性判断出错时降级为显示（避免模块静默消失）。
        """
        try:
            return self.visibility(data)
        except Exception:
            return True  # 可见性判断出错时降级为显示

    def safe_render(self, data: dict) -> str:
        """
        调用 render，任何异常均降级为占位字符串（不阻塞整体渲染）。
        空字符串返回值是合法的（表示该模块当前无内容）。
        """
        try:
            result = self.render(data)
            return result if result is not None else ""
        except Exception as e:
            return f"（{self.name} 渲染失败：{e}）"


# ── 工具函数 ──────────────────────────────────────────────────────────
# 来源：observe-l3-skill-spec.md §12

def _parse_loc_xy(rest: str) -> tuple[str, str, str]:
    """
    解析 'farm_12_15' → ('farm', '12', '15')。
    使用 rsplit 保证前缀可含下划线（如 "coop1_4_6"）。

    参数:
        rest: locshort_x_y 格式字符串

    返回:
        (locshort, x_str, y_str)

    异常:
        ValueError: 格式不符合期望（不含两个下划线分隔符）
    """
    parts = rest.rsplit("_", 2)
    if len(parts) != 3:
        raise ValueError(f"无法解析 loc_x_y: {rest!r}，期望格式 'locshort_x_y'")
    return parts[0], parts[1], parts[2]


def _resolve_loc(locshort: str, alias_map: dict) -> str:
    """
    locshort → NameOrUniqueName（先查 alias_map 逆向，再查静态映射表）。

    alias_map: {NameOrUniqueName → locshort}，如 {"Farm": "farm", "Coop82b3...": "coop1"}
    逆向查找: {locshort → NameOrUniqueName}

    参数:
        locshort: 短名，如 "farm", "farmhouse", "coop1"
        alias_map: 由 Skill 执行时从 query_buildings 构建

    返回:
        NameOrUniqueName 字符串
    """
    # 先查 alias_map 的逆向
    reverse = {v: k for k, v in alias_map.items()}
    if locshort in reverse:
        return reverse[locshort]

    # 静态映射表（兜底）
    _special = {
        "farmhouse": "FarmHouse",
        "farm": "Farm",
        "town": "Town",
        "beach": "Beach",
        "mine": "Mine",
        "desert": "Desert",
        "railroad": "Railroad",
        "forest": "Forest",
        "mountain": "Mountain",
        "backwoods": "Backwoods",
        "busstop": "BusStop",
        "woods": "Woods",
        "sewer": "Sewer",
        "saloon": "Saloon",
        "hospital": "Hospital",
        "manorhouse": "ManorHouse",
    }
    return _special.get(locshort.lower(), locshort.capitalize())


def _truncate(text: str, max_tokens: int) -> str:
    """
    按 token 估算截断文本。

    CJK 字符 ~1.5 token/char（GPT-4 分词），ASCII ~0.25 token/char。
    超限时在末尾添加截断提示，引导 LLM 使用更具体的 detail 参数。

    参数:
        text: 待截断文本
        max_tokens: token 预算上限

    返回:
        截断后的文本（含截断提示）或原文（未超限时）
    """
    estimate = sum(1.5 if '一' <= c <= '鿿' else 0.25 for c in text)
    if estimate <= max_tokens:
        return text
    ratio = max_tokens / estimate
    cutoff = int(len(text) * ratio * 0.9)
    return text[:cutoff] + "\n…（内容过多，已截断。请用更具体的 detail 参数查询）"


# ── 截断阈值 ─────────────────────────────────────────────────────────

_MAX_TOKENS_LAYER0: int = 200
"""层 0 场景速览的 token 预算上限。"""

_MAX_TOKENS_LAYER1: int = 600
"""层 1 类别列表的 token 预算上限。"""

_MAX_TOKENS_LAYER2: int = 400
"""层 2 单实例详情的 token 预算上限。"""


# ── 地图中文名映射表 ──────────────────────────────────────────────────

LOCATION_DISPLAY_NAMES: dict[str, str] = {
    "Farm": "农场",
    "FarmHouse": "农舍",
    "Town": "镇上",
    "Beach": "海滩",
    "Mine": "矿洞",
    "Desert": "卡利科沙漠",
    "Railroad": "铁路",
    "Forest": "森林",
    "Mountain": "山区",
    "Backwoods": "后山",
    "BusStop": "公交站",
    "Woods": "神秘树林",
    "Sewer": "下水道",
    "Saloon": "酒馆",
    "Hospital": "诊所",
    "ManorHouse": "市长官邸",
    # 建筑内部（NameOrUniqueName 含 GUID，短名通过 alias_map 解析）
    # 以下是无 GUID 的特殊地图
    "AnimalShop": "玛妮牧场",
    "ScienceHouse": "洛宾的木匠铺",
    "Blacksmith": "克林特铁匠铺",
    "JoshHouse": "亚历克斯和伊芙琳家",
    "HaleyHouse": "海莉和艾蜜莉家",
    "SamHouse": "萨姆家",
    "Trailer": "潘尼家",
    "Hospital": "诊所",
    "ElliottHouse": "艾略特家",
    "WizardHouse": "巫师塔",
    "MaryHouse": "玛丽家",
    "LeoTreeHouse": "利欧树屋",
    "AdventureGuild": "冒险者公会",
    "Club": "俱乐部",
    "FishShop": "威利渔具店",
    "Greenhouse": "温室",
    "LibraryMuseum": "图书馆兼博物馆",
}

# 天气中文映射
WEATHER_DISPLAY_NAMES: dict[str, str] = {
    "sunny": "晴",
    "rainy": "雨天",
    "stormy": "雷暴",
    "snowy": "雪天",
    "windy": "大风",
    "cloudy": "多云",
}

# 季节中文映射
SEASON_DISPLAY_NAMES: dict[str, str] = {
    "spring": "春",
    "summer": "夏",
    "fall": "秋",
    "winter": "冬",
}

# 星期中文映射
DAY_OF_WEEK_DISPLAY_NAMES: dict[str, str] = {
    "Monday": "星期一",
    "Tuesday": "星期二",
    "Wednesday": "星期三",
    "Thursday": "星期四",
    "Friday": "星期五",
    "Saturday": "星期六",
    "Sunday": "星期日",
}

# 品质显示映射
QUALITY_DISPLAY_NAMES: dict[int, str] = {
    0: "",           # 普通，不显示
    1: "（银★）",
    2: "（金★）",
    4: "（铱★）",
}


def format_date(world_state: dict) -> str:
    """
    格式化游戏日期为中文显示。

    参数:
        world_state: game_state["world"] 子字典

    返回:
        如 "春 12 日，星期三"
    """
    season = world_state.get("season", "spring")
    day = world_state.get("day", 1)
    day_of_week = world_state.get("day_of_week", "")

    season_cn = SEASON_DISPLAY_NAMES.get(season, season)
    dow_cn = DAY_OF_WEEK_DISPLAY_NAMES.get(day_of_week, day_of_week)

    if dow_cn:
        return f"{season_cn} {day} 日，{dow_cn}"
    return f"{season_cn} {day} 日"


def format_time(game_time: int) -> str:
    """
    格式化游戏时间整数为 HH:MM 格式。

    参数:
        game_time: 如 630=06:30, 1200=12:00, 2400=00:00

    返回:
        如 "06:30", "12:00", "22:10"
    """
    hours = game_time // 100
    minutes = game_time % 100
    # SDV 时间超过 2400 时回绕（次日凌晨）
    hours = hours % 24
    return f"{hours:02d}:{minutes:02d}"


def format_weather(weather_str: str) -> str:
    """
    格式化天气字符串为中文。

    参数:
        weather_str: 如 "sunny", "rainy"

    返回:
        如 "晴", "雨天"
    """
    return WEATHER_DISPLAY_NAMES.get(weather_str, weather_str)


def get_location_display(location_name: str, alias_map: Optional[dict] = None) -> str:
    """
    获取地图的中文显示名。

    优先查静态映射表，未命中时：
    1. 查 alias_map（{NameOrUniqueName → locshort}）的逆向（locshort → display）
    2. 截断 GUID 后缀（如 "Coop82b38ef3..." → "Coop"）并查映射
    3. 直接返回 location_name

    参数:
        location_name: NameOrUniqueName，如 "Farm", "Coop82b38ef3-ac7b-..."
        alias_map: 可选，{NameOrUniqueName → locshort}

    返回:
        中文显示名
    """
    # 1. 直接查静态映射
    if location_name in LOCATION_DISPLAY_NAMES:
        return LOCATION_DISPLAY_NAMES[location_name]

    # 2. 截取前缀（去掉 GUID 部分）匹配
    for known_name, display in LOCATION_DISPLAY_NAMES.items():
        if location_name.startswith(known_name):
            return display

    # 3. 用 alias_map 显示 locshort
    if alias_map and location_name in alias_map:
        return alias_map[location_name]

    # 4. 降级返回原值
    return location_name


def _items_truncate(items: list, max_n: int) -> tuple[list, int]:
    """
    截断列表，返回 (前 max_n 个元素, 隐藏的数量)。

    参数:
        items: 原始列表
        max_n: 最多显示数量

    返回:
        (shown_items, hidden_count) tuple
        hidden_count=0 表示未截断
    """
    if len(items) <= max_n:
        return items, 0
    return items[:max_n], len(items) - max_n
