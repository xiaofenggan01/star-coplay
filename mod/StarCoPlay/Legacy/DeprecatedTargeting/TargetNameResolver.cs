using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Buildings;
using StarCoPlay.GameServices.Naming;

namespace StarCoPlay.V2;

/// <summary>
/// 语义名称解析工具。
///
/// 将 NPC 名、动物名、地图名、建筑名等语义字符串解析为游戏引擎坐标或 TargetRef，
/// 供 Agent 指令系统将自然语言目标转化为可执行的游戏坐标/追踪引用。
///
/// 解析优先级（R1-R6）：
///   R1 "player" 字面量
///   R2 ObserveId（[npc:Name] / [animal:Name] / [building:Type]）
///   R3 地图精确名 / DisplayName 反查
///   R4 NPC 跨地图（getCharacterFromName）
///   R5 动物跨地图（Farm.animals + AnimalHouse.animals）
///   R6 建筑名（短名 / 中文别名 / GUID 精确匹配 / 建筑类型）
/// </summary>
public static class TargetNameResolver
{
    /// <summary>解析结果类型。</summary>
    public enum ResolveType
    {
        /// <summary>可追踪实体（NPC/动物），返回 TargetRef。</summary>
        TrackingEntity,
        /// <summary>静态地图坐标。</summary>
        StaticLocation,
        /// <summary>多个同名建筑，无法自动消歧。返回 Candidates 候选清单。</summary>
        AmbiguousTarget,
        /// <summary>解析失败。</summary>
        Unknown
    }

    /// <summary>多实例建筑候选条目（K1：fail + 候选清单）。</summary>
    public record BuildingCandidate(
        string LocationName,   // 建筑内部地图的 NameOrUniqueName（唯一 GUID 名）
        string BuildingType,   // 英文 buildingType（如 "Coop"）
        int Distance,          // 玩家当前位置到建筑入口的曼哈顿距离
        int X,                 // 建筑门口 X（Farm 地图坐标）
        int Y                  // 建筑门口 Y（Farm 地图坐标）
    );

    /// <summary>解析结果。</summary>
    public record ResolveResult(
        ResolveType Type,
        string? TargetRef,                         // TrackingEntity 时有效
        string? LocationName,                      // StaticLocation 时有效（NameOrUniqueName）
        int X,                                     // StaticLocation 时有效
        int Y,                                     // StaticLocation 时有效
        string ResolvedBy,                         // "player"/"observe_id"/"npc"/"animal"/"map_name"/"map_display"/"building"/"building_guid"
        string? ErrorMessage,                      // AmbiguousTarget/Unknown 时有效
        List<BuildingCandidate>? Candidates = null // AmbiguousTarget 时有效（K1）
    );

    /// <summary>建筑类型中文别名映射（中文名 → 英文 buildingType，K9 扩展到 ~10 项）。</summary>
    private static readonly Dictionary<string, string> _buildingAliases = new(StringComparer.OrdinalIgnoreCase)
    {
        // 原有 4 项
        ["鸡舍"] = "Coop",
        ["畜棚"] = "Barn",
        ["温室"] = "Greenhouse",
        ["小屋"] = "Cabin",
        // K9 扩展：农场建筑中文别名
        ["筒仓"] = "Silo",
        ["磨坊"] = "Mill",
        ["鱼缸"] = "FishPond",
        ["鱼塘"] = "FishPond",
        ["史莱姆屋"] = "SlimeHutch",
        ["黏液屋"] = "SlimeHutch",
        ["果冻精小屋"] = "Junimo Hut",
        ["精灵小屋"] = "Junimo Hut",
        ["水塔"] = "Well",
        ["蜂箱"] = "Bee House",  // 注：SDV 中 Bee House 一般是物品不是建筑，保留兜底
        ["联机小屋"] = "Cabin",
        ["大鸡舍"] = "Big Coop",
        ["豪华鸡舍"] = "Deluxe Coop",
        ["大畜棚"] = "Big Barn",
        ["豪华畜棚"] = "Deluxe Barn",
        ["工具棚"] = "Shed",
        ["大工具棚"] = "Big Shed",
    };

    /// <summary>
    /// 解析语义名称为追踪引用或静态坐标。
    /// 支持 R1-R6 六条解析路径，永不返回 null。
    /// </summary>
    /// <param name="target">语义目标名</param>
    /// <param name="monitor">日志监视器（可选）</param>
    /// <returns>解析结果，Type 区分 TrackingEntity/StaticLocation/AmbiguousTarget/Unknown</returns>
    public static ResolveResult Resolve(string target, IMonitor? monitor = null)
    {
        if (string.IsNullOrWhiteSpace(target))
        {
            return new ResolveResult(
                ResolveType.Unknown,
                null, null, 0, 0,
                "none",
                "目标名称不能为空");
        }

        // R1：player 字面量
        var playerResult = TryResolvePlayer(target, monitor);
        if (playerResult != null) return playerResult;

        // R2：ObserveId 格式（[npc:Name] / [animal:Name] / [building:Type]）
        var observeResult = TryResolveObserveId(target, monitor);
        if (observeResult != null) return observeResult;

        // R3a：地图精确名匹配
        var mapExactResult = TryResolveMapByName(target, monitor);
        if (mapExactResult != null) return mapExactResult;

        // R3b：地图 DisplayName 反查
        var mapDisplayResult = TryResolveMapByDisplayName(target, monitor);
        if (mapDisplayResult != null) return mapDisplayResult;

        // R4：NPC 跨地图（getCharacterFromName）
        var npcResult = TryResolveNpc(target, monitor);
        if (npcResult != null) return npcResult;

        // R5：动物跨地图（Farm.animals + AnimalHouse.animals）
        var animalResult = TryResolveAnimal(target, monitor);
        if (animalResult != null) return animalResult;

        // R6a：建筑 GUID 精确匹配（含 GUID 特征：长度 > 20）
        if (target.Length > 20)
        {
            var guidResult = TryResolveBuildingByGuid(target, monitor);
            if (guidResult != null) return guidResult;
        }

        // R6b：建筑类型匹配（短名 / 中文别名）→ 可能返回 AmbiguousTarget（K1）
        var buildingResult = TryResolveBuildingByType(target, monitor);
        if (buildingResult != null) return buildingResult;

        // 全部未命中 — 尝试 SuggestSimilar 提示用户
        var suggestions = BuildSuggestions(target);
        var hint = suggestions.Count > 0
            ? $" 是否想输入：{string.Join(", ", suggestions)}？"
            : string.Empty;

        monitor?.Log($"[V2] TargetNameResolver: 无法解析目标 '{target}'{hint}", LogLevel.Warn);
        return new ResolveResult(
            ResolveType.Unknown,
            null, null, 0, 0,
            "none",
            $"无法解析目标: {target}{hint}");
    }

    // ════════════════════════════════════════════════════════
    //  私有解析方法（R1-R6）
    // ════════════════════════════════════════════════════════

    /// <summary>R1：解析 "player" 字面量，返回玩家当前位置的 StaticLocation。</summary>
    private static ResolveResult? TryResolvePlayer(string target, IMonitor? monitor)
    {
        if (!string.Equals(target, "player", StringComparison.OrdinalIgnoreCase))
            return null;

        var player = Game1.player;
        if (player == null) return null;

        var loc = player.currentLocation;
        var locationName = loc?.NameOrUniqueName ?? "Farm";
        int x = (int)player.Tile.X;
        int y = (int)player.Tile.Y;

        monitor?.Log(
            $"[V2] TargetNameResolver: R1 player → {locationName} ({x},{y})",
            LogLevel.Debug);

        return new ResolveResult(
            ResolveType.StaticLocation,
            null,
            locationName, x, y,
            "player",
            null);
    }

    /// <summary>
    /// R2：解析 ObserveId 格式（[npc:Name] / [animal:Name] / [building:Type]）。
    /// 剥离方括号和类型前缀，转为普通名称后递归走 R4/R5/R6 路径。
    /// </summary>
    private static ResolveResult? TryResolveObserveId(string target, IMonitor? monitor)
    {
        // 格式检查：必须以 [ 开头、] 结尾
        if (!target.StartsWith("[") || !target.EndsWith("]"))
            return null;

        // 剥离方括号
        var inner = target[1..^1]; // "[npc:Lewis]" → "npc:Lewis"
        var colonIdx = inner.IndexOf(':');
        if (colonIdx < 0) return null;

        var kind = inner[..colonIdx].Trim().ToLowerInvariant();
        var name = inner[(colonIdx + 1)..].Trim();

        if (string.IsNullOrEmpty(name)) return null;

        monitor?.Log(
            $"[V2] TargetNameResolver: R2 ObserveId [{kind}:{name}]",
            LogLevel.Debug);

        return kind switch
        {
            "npc"      => TryResolveNpc(name, monitor),
            "animal"   => TryResolveAnimal(name, monitor),
            "building" => TryResolveBuildingByType(name, monitor),
            _          => null
        };
    }

    /// <summary>R4：尝试将目标解析为 NPC（跨地图 getCharacterFromName）。</summary>
    private static ResolveResult? TryResolveNpc(string target, IMonitor? monitor)
    {
        var npc = Game1.getCharacterFromName(target);
        if (npc == null) return null;

        var location = npc.currentLocation;
        if (location == null)
        {
            monitor?.Log($"[V2] TargetNameResolver: NPC '{target}' 当前没有地点", LogLevel.Debug);
            return null;
        }

        var locationName = location.NameOrUniqueName;
        var targetRef = $"{locationName}:npc_{target}";

        monitor?.Log(
            $"[V2] TargetNameResolver: NPC '{target}' → {targetRef} ({(int)npc.Tile.X},{(int)npc.Tile.Y})",
            LogLevel.Debug);

        return new ResolveResult(
            ResolveType.TrackingEntity,
            targetRef,
            null, 0, 0,
            "npc",
            null);
    }

    /// <summary>R5：尝试将目标解析为农场动物（Farm 户外 + 所有 AnimalHouse）。</summary>
    private static ResolveResult? TryResolveAnimal(string target, IMonitor? monitor)
    {
        var farm = Game1.getFarm();
        if (farm == null) return null;

        // 搜索 Farm 户外动物
        foreach (var animal in farm.animals.Values)
        {
            if (MatchAnimalName(animal, target))
            {
                var locationName = farm.NameOrUniqueName;
                var displayName = animal.displayName ?? animal.Name;
                var targetRef = $"{locationName}:animal_{displayName}";

                monitor?.Log(
                    $"[V2] TargetNameResolver: 动物 '{target}' → {targetRef} (Farm 户外)",
                    LogLevel.Debug);

                return new ResolveResult(
                    ResolveType.TrackingEntity,
                    targetRef,
                    null, 0, 0,
                    "animal",
                    null);
            }
        }

        // 搜索所有 Game1.locations 中的 AnimalHouse
        foreach (var loc in Game1.locations)
        {
            if (loc?.buildings == null) continue;

            foreach (var building in loc.buildings)
            {
                if (building.indoors.Value is not AnimalHouse animalHouse) continue;

                foreach (var animal in animalHouse.animals.Values)
                {
                    if (!MatchAnimalName(animal, target)) continue;

                    var locationName = animalHouse.NameOrUniqueName;
                    var displayName = animal.displayName ?? animal.Name;
                    var targetRef = $"{locationName}:animal_{displayName}";

                    monitor?.Log(
                        $"[V2] TargetNameResolver: 动物 '{target}' → {targetRef} ({animalHouse.Name})",
                        LogLevel.Debug);

                    return new ResolveResult(
                        ResolveType.TrackingEntity,
                        targetRef,
                        null, 0, 0,
                        "animal",
                        null);
                }
            }
        }

        return null;
    }

    /// <summary>R3a：尝试通过地图内部名精确匹配（MapData.GetMap）。</summary>
    private static ResolveResult? TryResolveMapByName(string target, IMonitor? monitor)
    {
        var entry = MapData.GetMap(target);
        if (entry == null) return null;

        var safePoint = MapData.GetSafeWarpTarget(target);
        int x = safePoint?.x ?? 0;
        int y = safePoint?.y ?? 0;

        monitor?.Log(
            $"[V2] TargetNameResolver: 地图名 '{target}' → StaticLocation ({x},{y})",
            LogLevel.Debug);

        return new ResolveResult(
            ResolveType.StaticLocation,
            null,
            target, x, y,
            "map_name",
            null);
    }

    /// <summary>R3b：尝试通过地图 DisplayName 反查（遍历所有已知地图）。
    /// 多个匹配（如多个 Cabin 都 DisplayName="Cabin"）→ 返回 null 让 R6b 接管处理 K1。</summary>
    private static ResolveResult? TryResolveMapByDisplayName(string target, IMonitor? monitor)
    {
        var matches = new List<string>();
        foreach (var name in MapData.GetAllMapNames())
        {
            var entry = MapData.GetMap(name);
            if (entry == null) continue;
            if (string.Equals(entry.DisplayName, target, StringComparison.OrdinalIgnoreCase))
                matches.Add(name);
        }

        if (matches.Count == 0) return null;
        if (matches.Count > 1)
        {
            // 多实例（如多个 Cabin/Barn）→ 不在 R3b 解决，让流程继续到 R6b（K1 AmbiguousBuilding）
            monitor?.Log(
                $"[V2] TargetNameResolver R3b: DisplayName '{target}' 多实例匹配 ({matches.Count})，跳过让 R6b 处理",
                LogLevel.Debug);
            return null;
        }

        var pickName = matches[0];
        var safePoint = MapData.GetSafeWarpTarget(pickName);
        int x = safePoint?.x ?? 0;
        int y = safePoint?.y ?? 0;

        monitor?.Log(
            $"[V2] TargetNameResolver: DisplayName '{target}' → {pickName} StaticLocation ({x},{y})",
            LogLevel.Debug);

        return new ResolveResult(
            ResolveType.StaticLocation,
            null,
            pickName, x, y,
            "map_display",
            null);
    }

    /// <summary>R6a：尝试通过建筑 GUID 精确匹配（target 长度 > 20 时调用）。</summary>
    private static ResolveResult? TryResolveBuildingByGuid(string target, IMonitor? monitor)
    {
        var farm = Game1.getFarm();
        if (farm?.buildings == null) return null;

        foreach (var building in farm.buildings)
        {
            var interior = building.indoors?.Value;
            if (interior == null) continue;

            var uniqueName = interior.uniqueName?.Value;
            var nameOrUnique = interior.NameOrUniqueName;

            bool matched =
                (!string.IsNullOrEmpty(uniqueName) &&
                 string.Equals(uniqueName, target, StringComparison.OrdinalIgnoreCase)) ||
                string.Equals(nameOrUnique, target, StringComparison.OrdinalIgnoreCase);

            if (!matched) continue;

            GetBuildingDoorCoords(building, out int doorX, out int doorY);

            monitor?.Log(
                $"[V2] TargetNameResolver: 建筑 GUID '{target}' → Farm ({doorX},{doorY})",
                LogLevel.Debug);

            return new ResolveResult(
                ResolveType.StaticLocation,
                null,
                "Farm", doorX, doorY,
                "building_guid",
                null);
        }

        return null;
    }

    /// <summary>
    /// R6b：尝试通过建筑类型匹配（短名 / 中文别名）。
    /// 单实例 → StaticLocation；多实例 → AmbiguousTarget + Candidates（K1：fail + 候选清单）。
    /// </summary>
    private static ResolveResult? TryResolveBuildingByType(string target, IMonitor? monitor)
    {
        var farm = Game1.getFarm();
        if (farm?.buildings == null) return null;

        // 中文别名 → 英文 buildingType
        string resolveTarget = _buildingAliases.TryGetValue(target, out var alias) ? alias : target;

        var matched = farm.buildings
            .Where(b => string.Equals(b.buildingType.Value, resolveTarget, StringComparison.OrdinalIgnoreCase))
            .ToList();

        if (matched.Count == 0) return null;

        if (matched.Count == 1)
        {
            var building = matched[0];
            GetBuildingDoorCoords(building, out int doorX, out int doorY);

            monitor?.Log(
                $"[V2] TargetNameResolver: R6b 建筑类型 '{target}' → Farm ({doorX},{doorY})",
                LogLevel.Debug);

            return new ResolveResult(
                ResolveType.StaticLocation,
                null,
                "Farm", doorX, doorY,
                "building",
                null);
        }

        // 多个同类型建筑 → K1：fail + 候选清单（错误码 AMBIGUOUS_BUILDING）
        var playerTile = Game1.player?.Tile ?? Vector2.Zero;
        int playerX = (int)playerTile.X;
        int playerY = (int)playerTile.Y;

        var candidates = matched.Select(b =>
        {
            GetBuildingDoorCoords(b, out int dx, out int dy);
            int distance = Math.Abs(dx - playerX) + Math.Abs(dy - playerY);
            var locName = b.indoors?.Value?.NameOrUniqueName ?? b.buildingType.Value;
            return new BuildingCandidate(locName, b.buildingType.Value, distance, dx, dy);
        })
        .OrderBy(c => c.Distance)
        .ToList();

        var errorMsg = $"找到 {matched.Count} 个 '{target}' 建筑（AMBIGUOUS_BUILDING），请用 GUID 完整名指定目标";

        monitor?.Log(
            $"[V2] TargetNameResolver: R6b 建筑类型 '{target}' 歧义 — {matched.Count} 个实例",
            LogLevel.Warn);

        return new ResolveResult(
            ResolveType.AmbiguousTarget,
            null, null, 0, 0,
            "building",
            errorMsg,
            candidates);
    }

    // ════════════════════════════════════════════════════════
    //  辅助方法
    // ════════════════════════════════════════════════════════

    /// <summary>计算建筑入口坐标（站在门前）。</summary>
    private static void GetBuildingDoorCoords(Building building, out int doorX, out int doorY)
    {
        var humanDoor = building.humanDoor.Value;
        doorX = building.tileX.Value + humanDoor.X;
        doorY = building.tileY.Value + humanDoor.Y + 1; // +1 站在门前
    }

    /// <summary>按名称匹配动物（Name 或 displayName，忽略大小写）。</summary>
    private static bool MatchAnimalName(FarmAnimal animal, string name)
    {
        return string.Equals(animal.Name, name, StringComparison.OrdinalIgnoreCase) ||
               string.Equals(animal.displayName, name, StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>
    /// N7：使用 NameSimilarity 从已知候选集中生成相似建议（解析失败时的 hint）。
    /// </summary>
    private static List<string> BuildSuggestions(string input)
    {
        // 候选集：建筑别名 key + MapData 已知地图名
        var candidates = _buildingAliases.Keys
            .Concat(_buildingAliases.Values)
            .Concat(MapData.GetAllMapNames())
            .Distinct(StringComparer.OrdinalIgnoreCase);

        return NameSimilarity.SuggestSimilar(input, candidates, maxDistance: 2, maxResults: 3);
    }
}
