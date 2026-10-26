"""Semantic Stardew place names resolved without duplicating the runtime map graph."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlaceDestination:
    place_id: str
    map_id: str
    x: int
    y: int
    stand_side: str
    aliases: tuple[str, ...]

    @property
    def uses_safe_point(self) -> bool:
        return self.x == -1 and self.y == -1


class PlaceResolutionError(ValueError):
    def __init__(self, code: str, place: str) -> None:
        self.code = code
        self.place = place
        super().__init__(code)


_PLACES = (
    PlaceDestination("SeedShop", "SeedShop", 4, 19, "down", (
        "商店", "种子店", "皮埃尔商店", "皮埃尔的商店", "seed shop", "pierre's", "pierre",
    )),
    PlaceDestination("FishShop", "FishShop", 5, 6, "down", (
        "鱼店", "鱼具店", "威利商店", "fish shop", "willy's", "willy",
    )),
    PlaceDestination("Saloon", "Saloon", 10, 18, "down", (
        "酒吧", "酒馆", "星之果酒吧", "saloon", "stardrop saloon", "gus",
    )),
    PlaceDestination("Blacksmith", "Blacksmith", 3, 13, "down", (
        "铁匠铺", "铁匠", "blacksmith", "clint",
    )),
    PlaceDestination("AnimalShop", "AnimalShop", 12, 16, "down", (
        "动物店", "玛妮牧场", "animal shop", "marnie", "marnie's ranch",
    )),
    PlaceDestination("Carpenter", "ScienceHouse", 8, 18, "down", (
        "木匠铺", "木匠店", "罗宾家", "carpenter", "robin", "carpenter's shop",
    )),
    PlaceDestination("Hospital", "Hospital", 8, 19, "down", (
        "诊所", "医院", "哈维诊所", "hospital", "clinic", "harvey",
    )),
    PlaceDestination("JojaMart", "JojaMart", 11, 25, "down", (
        "joja", "joja超市", "joja商店", "jojamart",
    )),
)


_STANDARD_MAPS = (
    ("FarmHouse", "农舍", ("家", "回家", "farmhouse", "farm house")),
    ("Farm", "农场", ("farm",)),
    ("BusStop", "巴士站", ("公交站", "bus stop")),
    ("Backwoods", "山路", ("backwoods",)),
    ("Town", "鹈鹕镇", ("镇上", "城镇", "小镇", "pelican town", "town")),
    ("Beach", "海滩", ("沙滩", "beach")),
    ("Mountain", "山顶", ("山区", "山上", "mountain")),
    ("Forest", "煤矿森林", ("森林", "cindersap forest", "forest")),
    ("Mine", "矿洞入口", ("矿洞", "矿井", "mine")),
    ("Railroad", "铁路", ("火车站", "railroad")),
    ("WizardHouse", "巫师塔", ("巫师家", "wizard tower", "wizard's tower")),
    ("ArchaeologyHouse", "博物馆", ("图书馆", "museum", "library")),
    ("CommunityCenter", "社区中心", ("community center",)),
    ("AdventureGuild", "冒险公会", ("探险家公会", "adventurer's guild", "adventure guild")),
    ("Sewer", "下水道", ("sewers", "sewer")),
    ("Desert", "沙漠", ("calico desert", "desert")),
    ("JoshHouse", "乔治家", ("亚历克斯家", "george's house", "alex's house")),
    ("HaleyHouse", "海莉家", ("艾米丽家", "haley's house", "emily's house")),
    ("SamHouse", "乔迪家", ("山姆家", "jodi's house", "sam's house")),
    ("ManorHouse", "市长家", ("刘易斯家", "mayor's manor", "manor house")),
    ("Trailer", "潘妮家", ("拖车", "penny's trailer", "trailer")),
    ("Trailer_Big", "潘妮家(升级)", ("升级后的潘妮家", "trailer big")),
    ("ElliottHouse", "艾利欧特小屋", ("艾利欧特家", "elliott's cabin")),
    ("LeahHouse", "莉亚小屋", ("莉亚家", "leah's cottage")),
    ("Woods", "秘密森林", ("secret woods", "woods")),
    ("Tunnel", "隧道", ("tunnel",)),
    ("Greenhouse", "温室", ("greenhouse",)),
    ("BathHouse_Entry", "澡堂入口", ("温泉入口", "bathhouse entrance")),
    ("BathHouse_MensLocker", "男更衣室", ("男浴室", "men's locker room")),
    ("BathHouse_WomensLocker", "女更衣室", ("女浴室", "women's locker room")),
    ("BathHouse_Pool", "澡堂泳池", ("温泉", "浴场", "bathhouse pool", "spa")),
    ("FarmCave", "农场山洞", ("农场洞穴", "farm cave")),
    ("Cellar", "地窖", ("酒窖", "cellar")),
    ("SkullCave", "骷髅洞穴", ("头骨山洞", "骷髅矿洞", "skull cavern", "skull cave")),
    ("Club", "赌场", ("齐先生赌场", "casino", "desert casino")),
    ("SandyHouse", "绿洲", ("桑迪商店", "沙漠商店", "oasis", "sandy's shop")),
    ("Summit", "山顶峰", ("山峰", "summit")),
    ("MovieTheater", "电影院", ("电影剧院", "movie theater", "cinema")),
    ("AbandonedJojaMart", "废弃的Joja超市", ("废弃超市", "abandoned jojamart")),
    ("Sunroom", "日光室", ("卡洛琳的日光室", "sunroom")),
    ("HarveyRoom", "哈维房间", ("哈维的房间", "harvey's room")),
    ("WizardHouseBasement", "巫师地下室", ("幻觉神龛", "wizard basement")),
    ("BugLand", "突变虫穴", ("虫穴", "mutant bug lair", "bug land")),
    ("WitchSwamp", "女巫沼泽", ("沼泽", "witch's swamp", "witch swamp")),
    ("WitchHut", "女巫小屋", ("女巫 hut", "witch's hut", "witch hut")),
    ("WitchWarpCave", "女巫传送洞穴", ("女巫洞穴", "witch warp cave")),
    ("BoatTunnel", "威利的船舱", ("船舱", "boat tunnel", "willy's boat")),
    ("IslandSouth", "姜岛南部", ("姜岛码头", "ginger island", "island south")),
    ("IslandSouthEast", "姜岛东南部", ("海盗湾外", "island southeast")),
    ("IslandSouthEastCave", "海盗湾", ("海盗洞穴", "pirate cove")),
    ("IslandWest", "姜岛西部", ("姜岛农场", "island west")),
    ("IslandNorth", "姜岛北部", ("姜岛火山入口", "island north")),
    ("IslandEast", "姜岛东部", ("雷欧树屋", "island east")),
    ("IslandShrine", "宝石鸟神龛", ("姜岛神龛", "island shrine")),
    ("IslandFarmHouse", "姜岛农舍", ("姜岛房子", "island farmhouse")),
    ("IslandHut", "雷欧小屋", ("雷欧的家", "leo's hut", "island hut")),
    ("IslandFieldOffice", "姜岛办事处", ("岛屿办事处", "field office")),
    ("IslandTrader", "姜岛商人", ("岛屿商人", "island trader")),
    ("CaptainRoom", "船长室", ("威利船长室", "captain room")),
    ("VolcanoDungeon0", "火山地牢入口", ("火山入口", "volcano dungeon", "volcano")),
    ("Caldera", "火山口", ("火山锻造台", "forge", "caldera")),
    ("QiNutRoom", "齐先生的核桃房", ("齐先生房间", "核桃房", "qi's walnut room", "qi nut room")),
)

_SAFE_POINT_PLACES = tuple(
    PlaceDestination(map_id, map_id, -1, -1, "", (display_name, *aliases))
    for map_id, display_name, aliases in _STANDARD_MAPS
)


def _normalize(value: str) -> str:
    return " ".join(value.strip().casefold().split())


_BY_NAME = {
    _normalize(name): place
    for place in (*_PLACES, *_SAFE_POINT_PLACES)
    for name in (place.place_id, place.map_id, *place.aliases)
}


def resolve_place(place: str) -> PlaceDestination:
    normalized = _normalize(place)
    if not normalized:
        raise PlaceResolutionError("unknown_place", place)
    destination = _BY_NAME.get(normalized)
    if destination is not None:
        return destination

    # Runtime MapData owns the complete graph, including save-specific building
    # interiors and modded maps. Preserve unknown names for runtime resolution.
    passthrough = place.strip()
    return PlaceDestination(passthrough, passthrough, -1, -1, "", ())


__all__ = ["PlaceDestination", "PlaceResolutionError", "resolve_place"]
