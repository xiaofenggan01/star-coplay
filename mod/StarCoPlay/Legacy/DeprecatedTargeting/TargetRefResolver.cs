using System;
using System.Globalization;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Buildings;

namespace StarCoPlay.V2;

/// <summary>
/// 通用 TargetRef 解析工具。
///
/// TargetRef 格式: "LocationName:entityKind_descriptor"
/// 示例:
///   "Farm:animal_White Cow"        — 动物（descriptor = 内部 Name）
///   "Town:npc_Abigail"             — NPC（descriptor = 内部 Name）
///   "Farm:crop_12_15"              — 坐标静态实体（descriptor = X_Y）
///   "Farm:tile_5_8"                — 坐标 tile（descriptor = X_Y）
///
/// 支持的 entityKind（11 类）：
///   动态实体层（实时查找，返回 IsDynamic=true）：
///     animal — 搜索 Farm.animals + 所有 AnimalHouse.animals
///     npc    — Game1.getCharacterFromName
///   坐标静态层（解析 X_Y 坐标，返回 IsDynamic=false）：
///     crop / tree / rock / debris / object / obj / water / tile /
///     furniture / chest / fridge / shipping_bin / mini（兼容旧别名）
///   兜底：未知 kind → 返回 null（调用方可决定 fallback 或 fail-fast）
/// </summary>
public static class TargetRefResolver
{
    /// <summary>
    /// 解析结果。
    /// IsDynamic=true 表示动态实体（animal/npc），调用方可按需重 Resolve 追踪最新位置。
    /// Entity 对于动态实体为 FarmAnimal/NPC，对于坐标静态实体为 null。
    /// </summary>
    public record ResolveResult(
        GameLocation Location,
        Vector2 CurrentPosition,
        object? Entity,   // FarmAnimal / NPC / null
        string Kind,      // 原始 entityKind 小写，如 "animal"/"npc"/"crop"/"tile"...
        bool IsDynamic    // true=动态实体（需重 Resolve），false=坐标静态
    )
    {
        // 向后兼容旧调用方（只用三参数构造）
        [Obsolete("请使用五参数构造，明确指定 Kind 和 IsDynamic")]
        public ResolveResult(GameLocation Location, Vector2 CurrentPosition, object? Entity)
            : this(Location, CurrentPosition, Entity, "", false) { }
    }

    /// <summary>
    /// 解析 TargetRef 字符串，返回位置信息。
    /// 支持 11 类 entityKind（animal/npc/crop/tree/rock/debris/object/water/tile/furniture + 兼容别名）。
    /// </summary>
    /// <param name="targetRef">格式 "LocationName:entityKind_descriptor"</param>
    /// <param name="monitor">日志监视器（可为 null）</param>
    /// <returns>解析结果；未知 kind 或解析失败返回 null</returns>
    public static ResolveResult? Resolve(string targetRef, IMonitor? monitor = null)
    {
        if (string.IsNullOrWhiteSpace(targetRef))
            return null;

        // 解析格式: "LocationName:entityKind_descriptor"
        var colonIndex = targetRef.IndexOf(':');
        if (colonIndex < 0)
            return null;

        var locationHint = targetRef[..colonIndex];
        var entityPart = targetRef[(colonIndex + 1)..];

        // 解析 entityKind（第一个下划线之前）
        var underscoreIndex = entityPart.IndexOf('_');
        if (underscoreIndex < 0)
            return null;

        var entityKind = entityPart[..underscoreIndex].ToLowerInvariant();
        var descriptor = entityPart[(underscoreIndex + 1)..];

        return entityKind switch
        {
            // ── 动态实体层 ─────────────────────────────────────
            "animal" => ResolveAnimal(descriptor, locationHint, monitor),
            "npc"    => ResolveNpc(descriptor, monitor),

            // ── 坐标静态层 ─────────────────────────────────────
            // 核心 11 类 + 常见别名（obj/chest/fridge/shipping_bin/mini）保持向后兼容
            "crop"
            or "tree"
            or "rock"
            or "debris"
            or "object"
            or "obj"          // 旧别名
            or "water"
            or "tile"
            or "furniture"
            or "chest"        // container 别名
            or "fridge"       // container 别名
            or "shipping_bin" // container 别名
            or "mini"         // mini_shipping_bin 旧前缀
                => ResolveCoordEntity(entityKind, descriptor, locationHint, monitor),

            // ── 兜底 ───────────────────────────────────────────
            _ => HandleUnknownKind(entityKind, targetRef, monitor)
        };
    }

    /// <summary>
    /// 判断给定字符串是否是 TargetRef 格式（包含冒号且冒号后有下划线）。
    /// </summary>
    public static bool IsTargetRef(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return false;

        var colonIndex = value.IndexOf(':');
        if (colonIndex < 0 || colonIndex >= value.Length - 1)
            return false;

        return value.IndexOf('_', colonIndex + 1) > colonIndex;
    }

    // ══════════════════════════════════════════════════════════════════
    // 动态实体层：animal / npc
    // ══════════════════════════════════════════════════════════════════

    /// <summary>
    /// 搜索农场动物。动物可能在 Farm 户外或在 Barn/Coop 建筑内的 AnimalHouse 中。
    /// </summary>
    private static ResolveResult? ResolveAnimal(string animalName, string locationHint, IMonitor? monitor)
    {
        var farm = Game1.getFarm();
        if (farm == null) return null;

        // 1. 优先搜索 locationHint 指定的位置
        if (!string.IsNullOrWhiteSpace(locationHint))
        {
            var hintResult = SearchAnimalInLocation(animalName, locationHint, farm, monitor);
            if (hintResult != null) return hintResult;
        }

        // 2. Fallback: 搜索 Farm 户外 + 所有 AnimalHouse
        foreach (var animal in farm.animals.Values)
        {
            if (MatchAnimalName(animal, animalName))
            {
                monitor?.Log(
                    $"[V2] TargetRefResolver: 找到动物 {animalName} 在 Farm ({(int)animal.Tile.X},{(int)animal.Tile.Y})",
                    LogLevel.Debug);
                return new ResolveResult(farm, animal.Tile, animal, "animal", IsDynamic: true);
            }
        }

        foreach (var building in farm.buildings)
        {
            if (building.indoors.Value is AnimalHouse animalHouse)
            {
                foreach (var animal in animalHouse.animals.Values)
                {
                    if (MatchAnimalName(animal, animalName))
                    {
                        monitor?.Log(
                            $"[V2] TargetRefResolver: 找到动物 {animalName} 在 {animalHouse.Name} ({(int)animal.Tile.X},{(int)animal.Tile.Y})",
                            LogLevel.Debug);
                        return new ResolveResult(animalHouse, animal.Tile, animal, "animal", IsDynamic: true);
                    }
                }
            }
        }

        monitor?.Log($"[V2] TargetRefResolver: 未找到动物 {animalName}", LogLevel.Debug);
        return null;
    }

    /// <summary>在指定 locationHint 对应的位置搜索动物。</summary>
    private static ResolveResult? SearchAnimalInLocation(string animalName, string locationHint, Farm farm, IMonitor? monitor)
    {
        // locationHint == "Farm" → 只搜户外
        if (string.Equals(locationHint, "Farm", StringComparison.OrdinalIgnoreCase))
        {
            foreach (var animal in farm.animals.Values)
            {
                if (MatchAnimalName(animal, animalName))
                    return new ResolveResult(farm, animal.Tile, animal, "animal", IsDynamic: true);
            }
            return null;
        }

        // locationHint 匹配建筑名：支持短名（"Coop"）和完整唯一名（"Coop82b38ef3-..."）
        foreach (var building in farm.buildings)
        {
            if (building.indoors.Value is AnimalHouse animalHouse &&
                (string.Equals(animalHouse.Name, locationHint, StringComparison.OrdinalIgnoreCase) ||
                 string.Equals(animalHouse.NameOrUniqueName, locationHint, StringComparison.OrdinalIgnoreCase)))
            {
                foreach (var animal in animalHouse.animals.Values)
                {
                    if (MatchAnimalName(animal, animalName))
                        return new ResolveResult(animalHouse, animal.Tile, animal, "animal", IsDynamic: true);
                }
            }
        }

        return null;
    }

    private static bool MatchAnimalName(FarmAnimal animal, string name)
    {
        return string.Equals(animal.Name, name, StringComparison.OrdinalIgnoreCase) ||
               string.Equals(animal.displayName, name, StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>
    /// 通过 Game1.getCharacterFromName 解析 NPC。
    /// </summary>
    private static ResolveResult? ResolveNpc(string npcName, IMonitor? monitor)
    {
        var npc = Game1.getCharacterFromName(npcName);
        if (npc == null)
        {
            monitor?.Log($"[V2] TargetRefResolver: 未找到 NPC {npcName}", LogLevel.Debug);
            return null;
        }

        var location = npc.currentLocation;
        if (location == null)
        {
            monitor?.Log($"[V2] TargetRefResolver: NPC {npcName} 没有当前地点", LogLevel.Debug);
            return null;
        }

        monitor?.Log(
            $"[V2] TargetRefResolver: 找到 NPC {npcName} 在 {location.Name} ({(int)npc.Tile.X},{(int)npc.Tile.Y})",
            LogLevel.Debug);
        return new ResolveResult(location, npc.Tile, npc, "npc", IsDynamic: true);
    }

    // ══════════════════════════════════════════════════════════════════
    // 坐标静态层：crop/tree/rock/debris/object/water/tile/furniture + 别名
    // descriptor 格式："X_Y"（可能有额外后缀，仅取前两段）
    // ══════════════════════════════════════════════════════════════════

    /// <summary>
    /// 解析坐标型 TargetRef（descriptor = "X_Y"）。
    /// 通过 locationHint 定位 GameLocation（支持短名 + GUID 双轨制）。
    /// </summary>
    private static ResolveResult? ResolveCoordEntity(string kind, string descriptor, string locationHint, IMonitor? monitor)
    {
        // 解析坐标
        var parts = descriptor.Split('_');
        if (parts.Length < 2)
        {
            monitor?.Log($"[V2] TargetRefResolver: 坐标解析失败（需要 X_Y），descriptor={descriptor}", LogLevel.Debug);
            return null;
        }

        if (!int.TryParse(parts[0], NumberStyles.Integer, CultureInfo.InvariantCulture, out var x) ||
            !int.TryParse(parts[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out var y))
        {
            monitor?.Log($"[V2] TargetRefResolver: 坐标非整数，descriptor={descriptor}", LogLevel.Debug);
            return null;
        }

        // 定位 GameLocation（支持短名 + 完整唯一名）
        var location = FindLocation(locationHint, monitor);
        if (location == null)
        {
            monitor?.Log($"[V2] TargetRefResolver: 未找到地图 {locationHint}", LogLevel.Debug);
            return null;
        }

        var tile = new Vector2(x, y);
        monitor?.Log(
            $"[V2] TargetRefResolver: 解析坐标实体 kind={kind} tile=({x},{y}) location={location.NameOrUniqueName}",
            LogLevel.Debug);
        return new ResolveResult(location, tile, Entity: null, kind, IsDynamic: false);
    }

    /// <summary>
    /// 按地图名定位 GameLocation。支持短名（"Farm"）和完整唯一名（含 GUID）双轨制。
    /// </summary>
    private static GameLocation? FindLocation(string locationHint, IMonitor? monitor)
    {
        if (string.IsNullOrWhiteSpace(locationHint))
            return Game1.currentLocation;

        // 先遍历所有顶层地图（短名和完整名都匹配）
        foreach (var loc in Game1.locations)
        {
            if (string.Equals(loc.Name, locationHint, StringComparison.OrdinalIgnoreCase) ||
                string.Equals(loc.NameOrUniqueName, locationHint, StringComparison.OrdinalIgnoreCase))
                return loc;
        }

        // 再搜索建筑内部地图（indoors，如 Coop/Barn/Cabin 等含 GUID 的唯一名）
        foreach (var loc in Game1.locations)
        {
            foreach (var building in loc.buildings)
            {
                var indoors = building.indoors.Value;
                if (indoors == null) continue;
                if (string.Equals(indoors.Name, locationHint, StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(indoors.NameOrUniqueName, locationHint, StringComparison.OrdinalIgnoreCase))
                    return indoors;
            }
        }

        return null;
    }

    // ══════════════════════════════════════════════════════════════════
    // 兜底：未知 kind
    // ══════════════════════════════════════════════════════════════════

    private static ResolveResult? HandleUnknownKind(string kind, string targetRef, IMonitor? monitor)
    {
        monitor?.Log($"[V2] TargetRefResolver: 未知 entityKind='{kind}'，targetRef={targetRef}，返回 null（调用方应 fail-fast 或 fallback）", LogLevel.Debug);
        return null;
    }
}
