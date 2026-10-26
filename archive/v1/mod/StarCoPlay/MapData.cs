using System.Collections.Generic;
using System.Linq;

namespace StarCoPlay
{
    /// <summary>
    /// 静态地图连通表 — 硬编码 SDV 各地图的安全出生点和合法传送目标。
    /// AI Agent 跨地图传送时，只能传送到此表中已登记的安全坐标。
    /// </summary>
    public static class MapData
    {
        /// <summary>地图信息条目。</summary>
        public class MapEntry
        {
            /// <summary>内部地图名。</summary>
            public string Name { get; set; } = "";
            /// <summary>显示名称（便于 AI 理解）。</summary>
            public string DisplayName { get; set; } = "";
            /// <summary>安全出生点 X（保证可行走且可达）。</summary>
            public int SafeX { get; set; }
            /// <summary>安全出生点 Y。</summary>
            public int SafeY { get; set; }
            /// <summary>地图宽度（瓦片）。</summary>
            public int Width { get; set; }
            /// <summary>地图高度（瓦片）。</summary>
            public int Height { get; set; }
            /// <summary>连通的相邻地图出口列表。</summary>
            public List<MapExit> Exits { get; set; } = new();
        }

        /// <summary>地图出口/入口定义。</summary>
        public class MapExit
        {
            /// <summary>出口方向描述（如"南"、"东"、"入口"）。</summary>
            public string Direction { get; set; } = "";
            /// <summary>在本地图中触发传送的坐标 X。</summary>
            public int SourceX { get; set; }
            /// <summary>在本地图中触发传送的坐标 Y。</summary>
            public int SourceY { get; set; }
            /// <summary>目标地图内部名。</summary>
            public string TargetMap { get; set; } = "";
            /// <summary>目标地图中的到达坐标 X。</summary>
            public int TargetX { get; set; }
            /// <summary>目标地图中的到达坐标 Y。</summary>
            public int TargetY { get; set; }
        }

        /// <summary>全部已知地图数据（内部名 → MapEntry）。</summary>
        private static readonly Dictionary<string, MapEntry> _maps = new();

        /// <summary>静态构造：初始化地图数据表。</summary>
        static MapData()
        {
            InitializeMaps();
        }

        /// <summary>查询地图信息。</summary>
        public static MapEntry? GetMap(string name)
        {
            return _maps.TryGetValue(name, out var entry) ? entry : null;
        }

        /// <summary>获取从当前地图到目标地图的安全传送坐标。返回 null 表示不允许直接传送。</summary>
        public static (int x, int y)? GetSafeWarpTarget(string targetMap)
        {
            if (_maps.TryGetValue(targetMap, out var entry))
                return (entry.SafeX, entry.SafeY);
            return null;
        }

        /// <summary>获取从当前地图前往目标地图的出口信息。</summary>
        public static MapExit? FindExit(string currentMap, string targetMap)
        {
            if (!_maps.TryGetValue(currentMap, out var entry)) return null;
            return entry.Exits.FirstOrDefault(e => e.TargetMap == targetMap);
        }

        /// <summary>获取所有已知地图名称。</summary>
        public static IReadOnlyCollection<string> GetAllMapNames() => _maps.Keys;

        /// <summary>检查目标地图是否在已知列表中。</summary>
        public static bool IsKnownMap(string name) => _maps.ContainsKey(name);

        /*────────────────────────────────────────────
         *  地图数据初始化
         *  SafeX/SafeY 来源：SDV 源码 Utility.getDefaultWarpLocation()
         *  出口坐标基于 SDV 1.6 标准农场布局，部分需实测校准
         *────────────────────────────────────────────*/

        private static void InitializeMaps()
        {
            // ═══════════════════════════════════════
            //  核心区域
            // ═══════════════════════════════════════

            Add(new MapEntry
            {
                Name = "FarmHouse", DisplayName = "农舍",
                SafeX = 9, SafeY = 9, Width = 29, Height = 14,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 3, SourceY = 12, TargetMap = "Farm", TargetX = 64, TargetY = 15 }
                }
            });

            Add(new MapEntry
            {
                Name = "Farm", DisplayName = "农场",
                SafeX = 64, SafeY = 15, Width = 80, Height = 65,
                Exits = new()
                {
                    new() { Direction = "南", SourceX = 40, SourceY = 64, TargetMap = "Forest", TargetX = 68, TargetY = 0 },
                    new() { Direction = "东", SourceX = 79, SourceY = 16, TargetMap = "BusStop", TargetX = 0, TargetY = 23 },
                    new() { Direction = "北", SourceX = 40, SourceY = 0, TargetMap = "Backwoods", TargetX = 14, TargetY = 39 },
                    new() { Direction = "农舍入口", SourceX = 64, SourceY = 14, TargetMap = "FarmHouse", TargetX = 9, TargetY = 9 }
                }
            });

            Add(new MapEntry
            {
                Name = "BusStop", DisplayName = "巴士站",
                SafeX = 14, SafeY = 23, Width = 35, Height = 29,
                Exits = new()
                {
                    new() { Direction = "西（农场）", SourceX = 0, SourceY = 23, TargetMap = "Farm", TargetX = 79, TargetY = 16 },
                    new() { Direction = "东（小镇）", SourceX = 34, SourceY = 23, TargetMap = "Town", TargetX = 0, TargetY = 53 },
                    new() { Direction = "北（山路）", SourceX = 18, SourceY = 0, TargetMap = "Backwoods", TargetX = 40, TargetY = 39 }
                }
            });

            Add(new MapEntry
            {
                Name = "Backwoods", DisplayName = "山路",
                SafeX = 18, SafeY = 18, Width = 50, Height = 40,
                Exits = new()
                {
                    new() { Direction = "南（农场）", SourceX = 14, SourceY = 39, TargetMap = "Farm", TargetX = 40, TargetY = 0 },
                    new() { Direction = "东（山顶）", SourceX = 49, SourceY = 15, TargetMap = "Mountain", TargetX = 0, TargetY = 12 },
                    new() { Direction = "南（巴士站）", SourceX = 40, SourceY = 39, TargetMap = "BusStop", TargetX = 18, TargetY = 0 }
                }
            });

            Add(new MapEntry
            {
                Name = "Town", DisplayName = "鹈鹕镇",
                SafeX = 29, SafeY = 67, Width = 120, Height = 120,
                Exits = new()
                {
                    new() { Direction = "西（巴士站）", SourceX = 0, SourceY = 53, TargetMap = "BusStop", TargetX = 34, TargetY = 23 },
                    new() { Direction = "南（海滩）", SourceX = 52, SourceY = 109, TargetMap = "Beach", TargetX = 20, TargetY = 0 },
                    new() { Direction = "北（山顶）", SourceX = 110, SourceY = 0, TargetMap = "Mountain", TargetX = 31, TargetY = 20 },
                    new() { Direction = "东南（森林）", SourceX = 0, SourceY = 89, TargetMap = "Forest", TargetX = 117, TargetY = 18 }
                }
            });

            Add(new MapEntry
            {
                Name = "Beach", DisplayName = "海滩",
                SafeX = 39, SafeY = 1, Width = 64, Height = 41,
                Exits = new()
                {
                    new() { Direction = "北（小镇）", SourceX = 20, SourceY = 0, TargetMap = "Town", TargetX = 52, TargetY = 109 }
                }
            });

            Add(new MapEntry
            {
                Name = "Mountain", DisplayName = "山顶",
                SafeX = 40, SafeY = 13, Width = 135, Height = 41,
                Exits = new()
                {
                    new() { Direction = "南（小镇）", SourceX = 31, SourceY = 20, TargetMap = "Town", TargetX = 110, TargetY = 0 },
                    new() { Direction = "西（山路）", SourceX = 0, SourceY = 12, TargetMap = "Backwoods", TargetX = 49, TargetY = 15 },
                    new() { Direction = "矿洞入口", SourceX = 54, SourceY = 4, TargetMap = "Mine", TargetX = 17, TargetY = 16 },
                    new() { Direction = "北（铁路）", SourceX = 10, SourceY = 0, TargetMap = "Railroad", TargetX = 10, TargetY = 55 },
                    new() { Direction = "木匠铺", SourceX = 12, SourceY = 26, TargetMap = "ScienceHouse", TargetX = 6, TargetY = 24 },
                    new() { Direction = "冒险公会", SourceX = 76, SourceY = 9, TargetMap = "AdventureGuild", TargetX = 3, TargetY = 12 }
                }
            });

            Add(new MapEntry
            {
                Name = "Forest", DisplayName = "煤矿森林",
                SafeX = 27, SafeY = 12, Width = 120, Height = 120,
                Exits = new()
                {
                    new() { Direction = "北（农场）", SourceX = 68, SourceY = 0, TargetMap = "Farm", TargetX = 40, TargetY = 64 },
                    new() { Direction = "东（小镇）", SourceX = 117, SourceY = 18, TargetMap = "Town", TargetX = 0, TargetY = 89 },
                    new() { Direction = "巫师塔", SourceX = 5, SourceY = 27, TargetMap = "WizardHouse", TargetX = 2, TargetY = 12 },
                    new() { Direction = "牧场", SourceX = 90, SourceY = 16, TargetMap = "AnimalShop", TargetX = 4, TargetY = 19 }
                }
            });

            Add(new MapEntry
            {
                Name = "Mine", DisplayName = "矿洞入口",
                SafeX = 13, SafeY = 10, Width = 34, Height = 22,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 17, SourceY = 16, TargetMap = "Mountain", TargetX = 54, TargetY = 5 }
                }
            });

            Add(new MapEntry
            {
                Name = "Railroad", DisplayName = "铁路",
                SafeX = 29, SafeY = 58, Width = 57, Height = 56,
                Exits = new()
                {
                    new() { Direction = "南（山顶）", SourceX = 10, SourceY = 55, TargetMap = "Mountain", TargetX = 10, TargetY = 0 }
                }
            });

            // ═══════════════════════════════════════
            //  建筑/商店
            // ═══════════════════════════════════════

            Add(new MapEntry
            {
                Name = "SeedShop", DisplayName = "皮埃尔商店",
                SafeX = 4, SafeY = 19, Width = 28, Height = 28,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 5, SourceY = 19, TargetMap = "Town", TargetX = 43, TargetY = 57 }
                }
            });

            Add(new MapEntry
            {
                Name = "Saloon", DisplayName = "星之果酒吧",
                SafeX = 18, SafeY = 20, Width = 25, Height = 23,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 13, SourceY = 20, TargetMap = "Town", TargetX = 45, TargetY = 71 }
                }
            });

            Add(new MapEntry
            {
                Name = "ScienceHouse", DisplayName = "木匠铺",
                SafeX = 8, SafeY = 20, Width = 30, Height = 26,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 6, SourceY = 24, TargetMap = "Mountain", TargetX = 12, TargetY = 26 }
                }
            });

            Add(new MapEntry
            {
                Name = "Hospital", DisplayName = "诊所",
                SafeX = 10, SafeY = 18, Width = 15, Height = 20,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 5, SourceY = 10, TargetMap = "Town", TargetX = 36, TargetY = 57 }
                }
            });

            Add(new MapEntry
            {
                Name = "Blacksmith", DisplayName = "铁匠铺",
                SafeX = 3, SafeY = 15, Width = 19, Height = 18,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 4, SourceY = 14, TargetMap = "Town", TargetX = 94, TargetY = 82 }
                }
            });

            Add(new MapEntry
            {
                Name = "FishShop", DisplayName = "鱼店",
                SafeX = 6, SafeY = 6, Width = 12, Height = 12,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 5, SourceY = 9, TargetMap = "Beach", TargetX = 30, TargetY = 34 }
                }
            });

            Add(new MapEntry
            {
                Name = "AnimalShop", DisplayName = "玛尼牧场",
                SafeX = 12, SafeY = 16, Width = 20, Height = 20,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 4, SourceY = 19, TargetMap = "Forest", TargetX = 90, TargetY = 16 }
                }
            });

            Add(new MapEntry
            {
                Name = "WizardHouse", DisplayName = "巫师塔",
                SafeX = 6, SafeY = 18, Width = 20, Height = 20,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 2, SourceY = 12, TargetMap = "Forest", TargetX = 5, TargetY = 27 }
                }
            });

            Add(new MapEntry
            {
                Name = "ArchaeologyHouse", DisplayName = "博物馆",
                SafeX = 3, SafeY = 10, Width = 40, Height = 16,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 3, SourceY = 14, TargetMap = "Town", TargetX = 101, TargetY = 90 }
                }
            });

            Add(new MapEntry
            {
                Name = "CommunityCenter", DisplayName = "社区中心",
                SafeX = 32, SafeY = 13, Width = 47, Height = 23,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 32, SourceY = 22, TargetMap = "Town", TargetX = 52, TargetY = 20 }
                }
            });

            Add(new MapEntry
            {
                Name = "AdventureGuild", DisplayName = "冒险公会",
                SafeX = 4, SafeY = 13, Width = 13, Height = 16,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 3, SourceY = 12, TargetMap = "Mountain", TargetX = 76, TargetY = 9 }
                }
            });

            Add(new MapEntry
            {
                Name = "Sewer", DisplayName = "下水道",
                SafeX = 31, SafeY = 18, Width = 35, Height = 22,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 16, SourceY = 21, TargetMap = "Town", TargetX = 35, TargetY = 97 }
                }
            });

            Add(new MapEntry
            {
                Name = "Desert", DisplayName = "沙漠",
                SafeX = 35, SafeY = 43, Width = 59, Height = 60,
                Exits = new()
                {
                    // 沙漠通过巴士到达，没有步行出口
                }
            });

            // ═══════════════════════════════════════
            //  村民住宅
            // ═══════════════════════════════════════

            Add(new MapEntry
            {
                Name = "JoshHouse", DisplayName = "乔治家（Alex/Evelyn）",
                SafeX = 9, SafeY = 20, Width = 25, Height = 23,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 9, SourceY = 21, TargetMap = "Town", TargetX = 57, TargetY = 64 }
                }
            });

            Add(new MapEntry
            {
                Name = "HaleyHouse", DisplayName = "海莉/艾米丽家",
                SafeX = 2, SafeY = 23, Width = 18, Height = 25,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 6, SourceY = 8, TargetMap = "Town", TargetX = 20, TargetY = 89 }
                }
            });

            Add(new MapEntry
            {
                Name = "SamHouse", DisplayName = "乔迪家（Sam/Vincent）",
                SafeX = 4, SafeY = 15, Width = 25, Height = 20,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 5, SourceY = 10, TargetMap = "Town", TargetX = 10, TargetY = 86 }
                }
            });

            Add(new MapEntry
            {
                Name = "ManorHouse", DisplayName = "市长家",
                SafeX = 4, SafeY = 10, Width = 14, Height = 12,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 4, SourceY = 11, TargetMap = "Town", TargetX = 58, TargetY = 86 }
                }
            });

            Add(new MapEntry
            {
                Name = "Trailer", DisplayName = "潘妮家",
                SafeX = 12, SafeY = 9, Width = 14, Height = 12,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 4, SourceY = 10, TargetMap = "Town", TargetX = 72, TargetY = 69 }
                }
            });

            Add(new MapEntry
            {
                Name = "ElliottHouse", DisplayName = "艾利欧特小屋",
                SafeX = 3, SafeY = 9, Width = 12, Height = 10,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 3, SourceY = 9, TargetMap = "Beach", TargetX = 49, TargetY = 11 }
                }
            });

            Add(new MapEntry
            {
                Name = "LeahHouse", DisplayName = "莉亚小屋",
                SafeX = 7, SafeY = 9, Width = 12, Height = 11,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 5, SourceY = 10, TargetMap = "Forest", TargetX = 104, TargetY = 33 }
                }
            });

            // ═══════════════════════════════════════
            //  其他地图
            // ═══════════════════════════════════════

            Add(new MapEntry
            {
                Name = "JojaMart", DisplayName = "Joja超市",
                SafeX = 13, SafeY = 28, Width = 30, Height = 30,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 13, SourceY = 29, TargetMap = "Town", TargetX = 95, TargetY = 51 }
                }
            });

            Add(new MapEntry
            {
                Name = "Woods", DisplayName = "秘密森林",
                SafeX = 8, SafeY = 9, Width = 30, Height = 30,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 8, SourceY = 0, TargetMap = "Forest", TargetX = 28, TargetY = 29 }
                }
            });

            Add(new MapEntry
            {
                Name = "Tunnel", DisplayName = "隧道",
                SafeX = 17, SafeY = 7, Width = 20, Height = 10,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 0, SourceY = 7, TargetMap = "BusStop", TargetX = 4, TargetY = 5 }
                }
            });

            Add(new MapEntry
            {
                Name = "Greenhouse", DisplayName = "温室",
                SafeX = 9, SafeY = 8, Width = 20, Height = 20,
                Exits = new()
                {
                    new() { Direction = "出口", SourceX = 9, SourceY = 19, TargetMap = "Farm", TargetX = 28, TargetY = 16 }
                }
            });
        }

        private static void Add(MapEntry entry)
        {
            _maps[entry.Name] = entry;
        }
    }
}
