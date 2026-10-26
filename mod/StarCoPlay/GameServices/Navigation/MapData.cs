using System;
using System.Collections.Generic;
using System.Linq;
using StardewModdingAPI;
using StardewValley;

namespace StarCoPlay
{
    // ════════════════════════════════════════════════════════════════════════
    //  公开类型（新设计 — 一次性重设计，封装内部细节）
    // ════════════════════════════════════════════════════════════════════════

    /// <summary>瓦片坐标（地图整数 tile 坐标系）。</summary>
    public readonly record struct TilePoint(int X, int Y);

    /// <summary>四方向罗盘（用于 WalkThrough 的 PressDirection）。</summary>
    public enum CompassDirection { North, South, East, West }

    /// <summary>地图出口的两类语义。</summary>
    public enum WarpKind
    {
        /// <summary>玩家走到 trigger tile 旁/上 + interact 触发。典型：建筑门、矿洞梯子。</summary>
        InteractDoor,
        /// <summary>玩家走到 warp tile 上 + 持续按方向键触发 SDV 自然 warp。典型：地图边界、农场山洞入口。</summary>
        WalkThrough
    }

    /// <summary>
    /// 地图基本信息 + 出口列表。对外只读视图，内部 BuildFromGameData 期间填充。
    /// 兼容旧调用：保留 SafeX/SafeY/Width/Height/Exits 字段。
    /// </summary>
    public sealed class MapEntry
    {
        public string Name { get; internal set; } = "";
        public string DisplayName { get; internal set; } = "";
        public int SafeX { get; internal set; }
        public int SafeY { get; internal set; }
        public int Width { get; internal set; }
        public int Height { get; internal set; }
        public List<MapExit> Exits { get; internal set; } = new();

        /// <summary>SafePoint 计算属性（新接口）。</summary>
        public TilePoint SafePoint => new(SafeX, SafeY);
    }

    /// <summary>地图出口定义。一条 MapExit 代表"从某地图前往某目标地图的一条逻辑出口"。</summary>
    public sealed class MapExit
    {
        // ── 共通字段 ────────────────────────────────────────────────
        public WarpKind Kind { get; init; }
        public string TargetMap { get; init; } = "";
        public int TargetX { get; init; }
        public int TargetY { get; init; }
        public bool IsAccessible { get; init; } = true;
        public string DisplayName { get; init; } = "";

        // ── InteractDoor 专属：多个候选 trigger tile（普通门 1 个，梯子可能多个）────
        public IReadOnlyList<TilePoint> DoorTiles { get; init; } = Array.Empty<TilePoint>();

        // ── WalkThrough 专属：多个候选 warp tile + 按压方向 ──────────────────
        public IReadOnlyList<TilePoint> WarpTiles { get; init; } = Array.Empty<TilePoint>();
        public CompassDirection? PressDirection { get; init; }

        // ── 兼容字段（V1 MoveToHandler / StateReader / TargetNameResolver 仍使用）──
        // 一律从新字段派生：取第一个候选 tile 作为代表（bug #8 之后多候选已聚合到 *Tiles 列表）
        [Obsolete("Use WarpTiles / DoorTiles depending on Kind")]
        public int SourceX => RepresentativeTile().X;
        [Obsolete("Use WarpTiles / DoorTiles depending on Kind")]
        public int SourceY => RepresentativeTile().Y;
        [Obsolete("Use WarpTiles / DoorTiles depending on Kind")]
        public int OriginalWarpX => RepresentativeTile().X;
        [Obsolete("Use WarpTiles / DoorTiles depending on Kind")]
        public int OriginalWarpY => RepresentativeTile().Y;
        [Obsolete("Use DisplayName")]
        public string Direction => DisplayName;

        private TilePoint RepresentativeTile()
        {
            if (Kind == WarpKind.InteractDoor && DoorTiles.Count > 0) return DoorTiles[0];
            if (Kind == WarpKind.WalkThrough && WarpTiles.Count > 0) return WarpTiles[0];
            return new TilePoint(0, 0);
        }
    }

    /// <summary>
    /// 地图连通表（公开 facade）。
    ///
    /// 数据来源：SDV 公开 API
    ///   - Game1.locations[*].warps        → WalkThrough（玩家走过 tile + 按方向键）
    ///   - Game1.locations[*].doors        → InteractDoor（玩家走到旁 + interact）
    ///   - Game1.locations[*].buildings[*].indoors.warps  → WalkThrough（建筑内向外）
    ///   - Game1.locations[*].buildings[*].humanDoor      → InteractDoor（外向建筑入口）
    ///
    /// 触发时机：
    ///   - SaveLoaded 时全量 Rebuild（ModEntry.OnSaveLoaded → BuildFromGameData）
    ///   - EnsureKnown 帧级防抖按需重扫
    ///
    /// PressDirection 三级查找链（仅 WalkThrough 适用）：
    ///   1) 配置覆盖（ModConfig.WarpDirectionOverrides，最高优先）
    ///   2) 内置白名单（_builtinDirectionWhitelist）
    ///   3) 启发式推断（基于 warp tiles 在地图中的位置）
    /// </summary>
    public static class MapData
    {
        // ════════════════════════════════════════════════════════════════
        //  公开 facade（外部仅依赖以下 API + MapExit/MapInfo 类型）
        // ════════════════════════════════════════════════════════════════

        /// <summary>查询地图基本信息（含 Exits）。</summary>
        public static MapEntry? GetMap(string name)
        {
            return _maps.TryGetValue(name, out var entry) ? entry : null;
        }

        /// <summary>
        /// Resolve a runtime map by its internal name or a unique display name.
        /// Display names are intentionally exact: save-specific buildings may share
        /// labels, and guessing in that case would move the player to the wrong map.
        /// </summary>
        public static string? ResolveMapName(string requestedName)
        {
            return ResolveMapNameFromEntries(_maps.Values, requestedName);
        }

        internal static string? ResolveMapNameFromEntries(
            IEnumerable<MapEntry> entries,
            string requestedName)
        {
            if (string.IsNullOrWhiteSpace(requestedName))
                return null;

            string requested = requestedName.Trim();
            var snapshot = entries.ToList();
            var exact = snapshot.FirstOrDefault(entry =>
                string.Equals(entry.Name, requested, StringComparison.OrdinalIgnoreCase));
            if (exact != null)
                return exact.Name;

            var displayMatches = snapshot
                .Where(entry => string.Equals(
                    entry.DisplayName, requested, StringComparison.OrdinalIgnoreCase))
                .Select(entry => entry.Name)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .Take(2)
                .ToList();
            return displayMatches.Count == 1 ? displayMatches[0] : null;
        }

        /// <summary>列出某地图所有出口（仅 IsAccessible=true）。</summary>
        public static IReadOnlyList<MapExit> GetExits(string fromMap)
        {
            if (!_maps.TryGetValue(fromMap, out var entry))
                return Array.Empty<MapExit>();
            return entry.Exits.Where(e => e.IsAccessible).ToList();
        }

        /// <summary>查找两地图间所有直连出口（含不同 Kind）。</summary>
        public static IReadOnlyList<MapExit> FindExits(string fromMap, string toMap)
        {
            if (!_maps.TryGetValue(fromMap, out var entry))
                return Array.Empty<MapExit>();
            return entry.Exits.Where(e => e.TargetMap == toMap && e.IsAccessible).ToList();
        }

        /// <summary>获取目标地图的安全落点。</summary>
        public static (int x, int y)? GetSafeWarpTarget(string targetMap)
        {
            if (_maps.TryGetValue(targetMap, out var entry))
                return (entry.SafeX, entry.SafeY);
            return null;
        }

        /// <summary>BFS 跨地图路由，返回 MapExit 序列。空列表表示同地图或不可达。</summary>
        public static List<MapExit> FindRoute(string currentMap, string targetMap)
        {
            if (currentMap == targetMap || !_maps.ContainsKey(currentMap) || !_maps.ContainsKey(targetMap))
                return new List<MapExit>();

            var visited = new HashSet<string> { currentMap };
            var queue = new Queue<(string map, List<MapExit> path)>();
            queue.Enqueue((currentMap, new List<MapExit>()));

            while (queue.Count > 0)
            {
                var (map, path) = queue.Dequeue();
                if (!_maps.TryGetValue(map, out var entry)) continue;

                foreach (var exit in entry.Exits)
                {
                    if (!exit.IsAccessible) continue;
                    if (visited.Contains(exit.TargetMap)) continue;
                    visited.Add(exit.TargetMap);

                    var newPath = new List<MapExit>(path) { exit };
                    if (exit.TargetMap == targetMap)
                        return newPath;

                    queue.Enqueue((exit.TargetMap, newPath));
                }
            }

            return new List<MapExit>();
        }

        /// <summary>获取所有已知地图名（NameOrUniqueName）。</summary>
        public static IReadOnlyCollection<string> GetAllMapNames() => _maps.Keys;

        /// <summary>地图是否已注册。</summary>
        public static bool IsKnownMap(string name) => _maps.ContainsKey(name);

        /// <summary>
        /// 确保目标地图已知，否则触发一次帧级防抖重扫。
        /// 用于动态变化（玩家建造新建筑后路径中包含未知地图）。
        /// </summary>
        public static bool EnsureKnown(string name, IMonitor? monitor = null)
        {
            if (_maps.ContainsKey(name)) return true;

            int currentTick = Game1.ticks;
            if (currentTick == _lastEnsureFrame) return false;

            monitor?.Log($"[MapData] EnsureKnown 触发重扫: {name}", LogLevel.Info);
            try
            {
                BuildFromGameData(monitor);
                _lastEnsureFrame = currentTick;
            }
            catch (Exception ex)
            {
                monitor?.Log($"[MapData] EnsureKnown 异常: {ex.Message}", LogLevel.Error);
                return false;
            }

            bool found = _maps.ContainsKey(name);
            if (!found)
                monitor?.Log($"[MapData] 重扫后仍未知: {name}", LogLevel.Warn);
            return found;
        }

        /// <summary>注入配置层 PressDirection 覆盖（在 SaveLoaded 后由 ModEntry 调）。</summary>
        public static void RegisterDirectionOverrides(IDictionary<string, string>? overrides)
        {
            _configOverrides.Clear();
            if (overrides == null) return;
            foreach (var kv in overrides)
            {
                // key 形如 "FarmHouse→Farm" 或 "FarmHouse->Farm"
                var sep = kv.Key.IndexOfAny(new[] { '→', '>' });
                if (sep <= 0) continue;
                int from_end = sep;
                if (kv.Key[sep] == '>' && sep > 0 && kv.Key[sep - 1] == '-') from_end = sep - 1;
                var from = kv.Key.Substring(0, from_end).Trim();
                var to = kv.Key.Substring(sep + 1).Trim();
                if (TryParseDirection(kv.Value, out var dir))
                    _configOverrides[(from, to)] = dir;
            }
        }

        // ════════════════════════════════════════════════════════════════
        //  内部数据结构（不对外）
        // ════════════════════════════════════════════════════════════════

        private static readonly Dictionary<string, MapEntry> _maps = new();
        private static int _lastEnsureFrame = -1;

        /// <summary>静态 DisplayName 映射（动态扫描没有显示名时回填）。</summary>
        private static readonly Dictionary<string, string> _displayNames = new()
        {
            ["FarmHouse"] = "农舍", ["Farm"] = "农场", ["BusStop"] = "巴士站",
            ["Backwoods"] = "山路", ["Town"] = "鹈鹕镇", ["Beach"] = "海滩",
            ["Mountain"] = "山顶", ["Forest"] = "煤矿森林", ["Mine"] = "矿洞入口",
            ["Railroad"] = "铁路", ["SeedShop"] = "皮埃尔商店", ["Saloon"] = "星之果酒吧",
            ["ScienceHouse"] = "木匠铺", ["Hospital"] = "诊所", ["Blacksmith"] = "铁匠铺",
            ["FishShop"] = "鱼店", ["AnimalShop"] = "玛尼牧场", ["WizardHouse"] = "巫师塔",
            ["ArchaeologyHouse"] = "博物馆", ["CommunityCenter"] = "社区中心",
            ["AdventureGuild"] = "冒险公会", ["Sewer"] = "下水道", ["Desert"] = "沙漠",
            ["JoshHouse"] = "乔治家", ["HaleyHouse"] = "海莉家", ["SamHouse"] = "乔迪家",
            ["ManorHouse"] = "市长家", ["Trailer"] = "潘妮家", ["Trailer_Big"] = "潘妮家(升级)",
            ["ElliottHouse"] = "艾利欧特小屋", ["LeahHouse"] = "莉亚小屋",
            ["JojaMart"] = "Joja超市", ["Woods"] = "秘密森林", ["Tunnel"] = "隧道",
            ["Greenhouse"] = "温室", ["BathHouse_Entry"] = "澡堂入口",
            ["BathHouse_MensLocker"] = "男更衣室", ["BathHouse_WomensLocker"] = "女更衣室",
            ["BathHouse_Pool"] = "澡堂泳池",
        };

        /// <summary>
        /// 内置 PressDirection 白名单（覆盖启发式推断不准的特殊地图对）。
        /// 关键场景：地图中部 walk-through warp（如农场山洞入口、BusStop ↔ Town 道路）。
        /// </summary>
        private static readonly Dictionary<(string from, string to), CompassDirection> _builtinDirectionWhitelist = new()
        {
            // BusStop → Town：道路 walk-through 在 X=44/65 Y=22-25（横跨道路），
            // 玩家从西端 (11,23) 朝 east 走过 X=44/65 触发自然 warp。
            [("BusStop", "Town")] = CompassDirection.East,
            // Town → BusStop：walk-through tile 在 Town X=-1（西边界外），
            // 玩家朝 west 走过 X=-1 触发 warp。
            [("Town", "BusStop")] = CompassDirection.West,
            // 农场山洞（FarmCave）↔ Farm：山洞入口在农场内陆
            [("Farm", "FarmCave")] = CompassDirection.North,
            [("FarmCave", "Farm")] = CompassDirection.South,
        };

        /// <summary>从 ModConfig 注入的覆盖（SaveLoaded 后由 ModEntry 调 RegisterDirectionOverrides 填充）。</summary>
        private static readonly Dictionary<(string from, string to), CompassDirection> _configOverrides = new();

        // ════════════════════════════════════════════════════════════════
        //  动态构建：BuildFromGameData
        // ════════════════════════════════════════════════════════════════

        /// <summary>
        /// 从 SDV 运行时数据全量重建地图连通表。
        /// 应在 SaveLoaded 事件、或玩家建造新建筑/解锁新区域时调用。
        /// </summary>
        public static int BuildFromGameData(IMonitor? monitor = null)
        {
            _maps.Clear();
            int totalExits = 0;

            // ─── Pass 1: 顶层地图扫描 ───────────────────────────────────
            foreach (var location in Game1.locations)
            {
                if (location?.Name == null) continue;
                var entry = EnsureEntry(location);
                totalExits += ScanLocationWarps(location, entry);
                totalExits += ScanLocationDoors(location, entry);
            }

            // ─── Pass 2: 建筑内部地图 + 双向连通 ─────────────────────────
            foreach (var location in Game1.locations)
            {
                if (location?.buildings == null) continue;

                var parentName = !string.IsNullOrEmpty(location.NameOrUniqueName)
                    ? location.NameOrUniqueName
                    : location.Name;
                if (!_maps.TryGetValue(parentName, out var parentEntry)) continue;

                foreach (var building in location.buildings)
                {
                    var interior = building.indoors?.Value;
                    if (interior?.Name == null) continue;

                    var interiorName = !string.IsNullOrEmpty(interior.NameOrUniqueName)
                        ? interior.NameOrUniqueName
                        : (!string.IsNullOrEmpty(interior.uniqueName?.Value)
                            ? interior.uniqueName.Value
                            : interior.Name);

                    var interiorEntry = EnsureBuildingEntry(interior, interiorName, building.buildingType.Value);
                    totalExits += ScanLocationWarps(interior, interiorEntry);

                    // 外部 → 建筑入口：humanDoor + 站位偏移 = InteractDoor
                    if (!parentEntry.Exits.Any(e => e.TargetMap == interiorName))
                    {
                        var humanDoor = building.humanDoor.Value;
                        int doorX = building.tileX.Value + humanDoor.X;
                        int doorY = building.tileY.Value + humanDoor.Y; // 门 tile 本身
                        int targetX = interior.warps?.Count > 0 ? interior.warps[0].X : interiorEntry.Width / 2;
                        int targetY = interior.warps?.Count > 0
                            ? Math.Max(interior.warps[0].Y - 1, 0)
                            : interiorEntry.Height / 2;
                        string displayName = _displayNames.TryGetValue(interiorName, out var dn)
                            ? dn
                            : (building.buildingType.Value ?? interiorName);

                        parentEntry.Exits.Add(new MapExit
                        {
                            Kind = WarpKind.InteractDoor,
                            TargetMap = interiorName,
                            TargetX = targetX,
                            TargetY = targetY,
                            IsAccessible = true,
                            DisplayName = displayName,
                            DoorTiles = new[] { new TilePoint(doorX, doorY) },
                        });
                        totalExits++;
                    }
                }
            }

            // ─── Pass 3: PostProcess（修补 doors 0,0 落点 + SafePoint）─────
            PostProcess();

            monitor?.Log($"[MapData] 动态构建完成: {_maps.Count} 地图, {totalExits} 出口", LogLevel.Info);
            DumpExits(monitor);
            return totalExits;
        }

        // ════════════════════════════════════════════════════════════════
        //  内部辅助：扫描各 SDV 来源
        // ════════════════════════════════════════════════════════════════

        private static MapEntry EnsureEntry(GameLocation location)
        {
            var mapName = !string.IsNullOrEmpty(location.NameOrUniqueName)
                ? location.NameOrUniqueName
                : location.Name;
            var mapLayer = location.Map?.Layers?.FirstOrDefault();
            int width = mapLayer?.LayerWidth ?? 0;
            int height = mapLayer?.LayerHeight ?? 0;

            if (!_maps.TryGetValue(mapName, out var entry))
            {
                entry = new MapEntry
                {
                    Name = mapName,
                    DisplayName = _displayNames.TryGetValue(mapName, out var dn)
                        ? dn
                        : (_displayNames.TryGetValue(location.Name, out var dn2) ? dn2 : mapName),
                    Width = width,
                    Height = height,
                };
                _maps[mapName] = entry;
            }
            return entry;
        }

        private static MapEntry EnsureBuildingEntry(GameLocation interior, string interiorName, string? buildingType)
        {
            var mapLayer = interior.Map?.Layers?.FirstOrDefault();
            int width = mapLayer?.LayerWidth ?? 0;
            int height = mapLayer?.LayerHeight ?? 0;

            if (!_maps.TryGetValue(interiorName, out var entry))
            {
                entry = new MapEntry
                {
                    Name = interiorName,
                    DisplayName = _displayNames.TryGetValue(interiorName, out var dn)
                        ? dn
                        : (buildingType ?? interiorName),
                    Width = width,
                    Height = height,
                };
                _maps[interiorName] = entry;
            }
            return entry;
        }

        /// <summary>扫描 location.warps → 聚合为 WalkThrough MapExit（按 TargetMap 聚合多 tile）。</summary>
        private static int ScanLocationWarps(GameLocation location, MapEntry entry)
        {
            if (location.warps == null) return 0;

            // 按 TargetMap 聚合所有 warp tile，去重
            var grouped = new Dictionary<string, List<TilePoint>>();
            var firstTargetCoord = new Dictionary<string, (int tx, int ty)>();
            foreach (var warp in location.warps)
            {
                if (string.IsNullOrEmpty(warp.TargetName)) continue;
                if (!grouped.TryGetValue(warp.TargetName, out var list))
                {
                    list = new List<TilePoint>();
                    grouped[warp.TargetName] = list;
                    firstTargetCoord[warp.TargetName] = (warp.TargetX, warp.TargetY);
                }
                var pt = new TilePoint(warp.X, warp.Y);
                if (!list.Contains(pt)) list.Add(pt);
            }

            int added = 0;
            foreach (var kv in grouped)
            {
                var targetMap = kv.Key;
                var tiles = kv.Value;

                // 跳过本地图已有同 TargetMap 的出口（避免与 doors 分支重复）
                if (entry.Exits.Any(e => e.TargetMap == targetMap)) continue;

                var (tx, ty) = firstTargetCoord[targetMap];
                string displayName = _displayNames.TryGetValue(targetMap, out var dn) ? dn : targetMap;
                var pressDir = ResolveWalkThroughDirection(entry.Name, targetMap, tiles, entry);

                entry.Exits.Add(new MapExit
                {
                    Kind = WarpKind.WalkThrough,
                    TargetMap = targetMap,
                    TargetX = tx,
                    TargetY = ty,
                    IsAccessible = true,
                    DisplayName = displayName,
                    WarpTiles = tiles,
                    PressDirection = pressDir,
                });
                added++;
            }
            return added;
        }

        /// <summary>扫描 location.doors → InteractDoor MapExit（按 TargetMap 聚合多 door tile，如梯子）。</summary>
        private static int ScanLocationDoors(GameLocation location, MapEntry entry)
        {
            if (location.doors == null || !location.doors.Pairs.Any()) return 0;

            var grouped = new Dictionary<string, (List<TilePoint> tiles, int tx, int ty)>();
            foreach (var door in location.doors.Pairs)
            {
                var doorPoint = door.Key;
                var doorTarget = door.Value;
                if (string.IsNullOrEmpty(doorTarget)) continue;

                var parts = doorTarget.Split(' ');
                string targetMap = parts[0];
                int targetX = 0, targetY = 0;
                if (parts.Length >= 3)
                {
                    int.TryParse(parts[1], out targetX);
                    int.TryParse(parts[2], out targetY);
                }

                if (!grouped.TryGetValue(targetMap, out var slot))
                {
                    slot = (new List<TilePoint>(), targetX, targetY);
                    grouped[targetMap] = slot;
                }
                slot.tiles.Add(new TilePoint(doorPoint.X, doorPoint.Y));
                grouped[targetMap] = slot;
            }

            int added = 0;
            foreach (var kv in grouped)
            {
                var targetMap = kv.Key;
                if (entry.Exits.Any(e => e.TargetMap == targetMap)) continue;

                string displayName = _displayNames.TryGetValue(targetMap, out var dn) ? dn : targetMap;
                entry.Exits.Add(new MapExit
                {
                    Kind = WarpKind.InteractDoor,
                    TargetMap = targetMap,
                    TargetX = kv.Value.tx,
                    TargetY = kv.Value.ty,
                    IsAccessible = true,
                    DisplayName = displayName,
                    DoorTiles = kv.Value.tiles,
                });
                added++;
            }
            return added;
        }

        // ════════════════════════════════════════════════════════════════
        //  PressDirection 三级查找链
        // ════════════════════════════════════════════════════════════════

        private static CompassDirection? ResolveWalkThroughDirection(
            string fromMap, string toMap, IReadOnlyList<TilePoint> warpTiles, MapEntry mapEntry)
        {
            // 1. 配置覆盖（最高优先）
            if (_configOverrides.TryGetValue((fromMap, toMap), out var configDir))
                return configDir;

            // 2. 内置白名单
            if (_builtinDirectionWhitelist.TryGetValue((fromMap, toMap), out var whitelistDir))
                return whitelistDir;

            // 3. 启发式推断：用 warp tiles 在地图中的位置
            return InferDirectionFromTiles(warpTiles, mapEntry);
        }

        private static CompassDirection? InferDirectionFromTiles(
            IReadOnlyList<TilePoint> tiles, MapEntry mapEntry)
        {
            if (tiles.Count == 0 || mapEntry.Width <= 0 || mapEntry.Height <= 0) return null;

            // 全部贴某条边 → 该方向
            bool allTop = tiles.All(t => t.Y <= 1);
            bool allBottom = tiles.All(t => t.Y >= mapEntry.Height - 2);
            bool allLeft = tiles.All(t => t.X <= 1);
            bool allRight = tiles.All(t => t.X >= mapEntry.Width - 2);
            if (allTop) return CompassDirection.North;
            if (allBottom) return CompassDirection.South;
            if (allLeft) return CompassDirection.West;
            if (allRight) return CompassDirection.East;

            // Fallback：取重心 vs 地图中心，最大偏移轴方向
            double avgX = tiles.Average(t => (double)t.X);
            double avgY = tiles.Average(t => (double)t.Y);
            double cx = mapEntry.Width / 2.0;
            double cy = mapEntry.Height / 2.0;
            double dx = avgX - cx;
            double dy = avgY - cy;
            if (Math.Abs(dx) > Math.Abs(dy))
                return dx > 0 ? CompassDirection.East : CompassDirection.West;
            return dy > 0 ? CompassDirection.South : CompassDirection.North;
        }

        private static bool TryParseDirection(string raw, out CompassDirection dir)
        {
            switch (raw?.Trim().ToLowerInvariant())
            {
                case "north": case "up":    dir = CompassDirection.North; return true;
                case "south": case "down":  dir = CompassDirection.South; return true;
                case "east":  case "right": dir = CompassDirection.East; return true;
                case "west":  case "left":  dir = CompassDirection.West; return true;
                default: dir = CompassDirection.North; return false;
            }
        }

        // ════════════════════════════════════════════════════════════════
        //  PostProcess：修补 doors 0,0 落点 + SafePoint
        // ════════════════════════════════════════════════════════════════

        private static void PostProcess()
        {
            // Phase 1: doors 注册时 TargetX/Y=0 的出口，从对侧返回 warp 修复。
            // 先收集 fix 任务，再统一 apply（避免 foreach 期间修改 List 引发 InvalidOperationException）。
            var fixTasks = new List<(MapEntry entry, MapExit oldExit, int tx, int ty)>();
            foreach (var entry in _maps.Values)
            {
                foreach (var exit in entry.Exits)
                {
                    if (exit.TargetX != 0 || exit.TargetY != 0) continue;
                    if (!_maps.TryGetValue(exit.TargetMap, out var targetEntry)) continue;

                    var returnExit = targetEntry.Exits.FirstOrDefault(e => e.TargetMap == entry.Name);
                    if (returnExit != null)
                    {
                        var rep = returnExit.Kind == WarpKind.WalkThrough && returnExit.WarpTiles.Count > 0
                            ? returnExit.WarpTiles[0]
                            : (returnExit.DoorTiles.Count > 0
                                ? returnExit.DoorTiles[0]
                                : new TilePoint(targetEntry.Width / 2, targetEntry.Height / 2));
                        fixTasks.Add((entry, exit, rep.X, rep.Y));
                    }
                    else
                    {
                        fixTasks.Add((entry, exit,
                            Math.Max(targetEntry.Width / 2, 1),
                            Math.Max(targetEntry.Height / 2, 1)));
                    }
                }
            }
            foreach (var (entry, oldExit, tx, ty) in fixTasks)
                ReplaceExitWithFixedTarget(entry, oldExit, tx, ty);

            // Phase 2: SafePoint = 第一个入站 warp 的落点 / 地图中心
            var inbound = new Dictionary<string, (int x, int y)>();
            foreach (var entry in _maps.Values)
            {
                foreach (var exit in entry.Exits)
                {
                    if (exit.TargetX == 0 && exit.TargetY == 0) continue;
                    if (!inbound.ContainsKey(exit.TargetMap))
                        inbound[exit.TargetMap] = (exit.TargetX, exit.TargetY);
                }
            }
            foreach (var entry in _maps.Values)
            {
                if (entry.SafeX != 0 || entry.SafeY != 0) continue;
                if (inbound.TryGetValue(entry.Name, out var pt))
                {
                    entry.SafeX = pt.x;
                    entry.SafeY = pt.y;
                }
                else
                {
                    entry.SafeX = Math.Max(entry.Width / 2, 1);
                    entry.SafeY = Math.Max(entry.Height / 2, 1);
                }
            }
        }

        private static void ReplaceExitWithFixedTarget(MapEntry entry, MapExit oldExit, int tx, int ty)
        {
            int idx = entry.Exits.IndexOf(oldExit);
            if (idx < 0) return;
            entry.Exits[idx] = new MapExit
            {
                Kind = oldExit.Kind,
                TargetMap = oldExit.TargetMap,
                TargetX = tx,
                TargetY = ty,
                IsAccessible = oldExit.IsAccessible,
                DisplayName = oldExit.DisplayName,
                DoorTiles = oldExit.DoorTiles,
                WarpTiles = oldExit.WarpTiles,
                PressDirection = oldExit.PressDirection,
            };
        }

        // ════════════════════════════════════════════════════════════════
        //  诊断输出
        // ════════════════════════════════════════════════════════════════

        private static void DumpExits(IMonitor? monitor)
        {
            if (monitor == null) return;
            foreach (var entry in _maps.Values.OrderBy(e => e.Name))
            {
                if (entry.Exits.Count == 0) continue;
                foreach (var exit in entry.Exits)
                {
                    string kindStr = exit.Kind == WarpKind.InteractDoor ? "Door" : "Walk";
                    string tilesStr = exit.Kind == WarpKind.InteractDoor
                        ? $"[{string.Join(",", exit.DoorTiles.Select(t => $"({t.X},{t.Y})"))}]"
                        : $"[{string.Join(",", exit.WarpTiles.Select(t => $"({t.X},{t.Y})"))}]press={exit.PressDirection}";
                    monitor.Log(
                        $"[MapData] {entry.Name} -> {exit.TargetMap} {kindStr} tiles={tilesStr} land=({exit.TargetX},{exit.TargetY})",
                        LogLevel.Trace);
                }
            }
        }
    }
}
