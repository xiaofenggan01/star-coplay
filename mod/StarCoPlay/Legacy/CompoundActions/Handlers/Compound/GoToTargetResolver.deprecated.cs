// ============================================================================
// DEPRECATED — go_to L2 重设计旧方案（已弃用，2026-05-03）
//
// 状态：暂停使用，等待新版 go_to L2 实现替代
// 排除：通过 StarCoPlay.csproj 中 `<Compile Remove="**\*.deprecated.cs" />` 排除编译
// 原因：方案重新设计；保留源码作为旧实现的历史快照与设计参考
//
// MUST NOT：在新代码中引用本文件中的任何类型；如需复用思路请整段重写
// ============================================================================

using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Buildings;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// 8 级语义解析，将 LLM 目标字符串解析为 ResolvedTarget。
/// 幂等只读：任意时刻可重复调用，结果反映当前帧 game state（不缓存）。
/// </summary>
public static class GoToTargetResolver
{
    private static readonly Regex ObserveIdRegex = new Regex(
        @"^\[(\w+):(.+)\]$",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    private static readonly Dictionary<string, string> BuildingAliases =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["鸡舍"] = "Coop",
            ["畜棚"] = "Barn",
            ["温室"] = "Greenhouse",
            ["小屋"] = "Cabin",
        };

    public static ResolvedTarget Resolve(
        string target,
        IReadOnlyDictionary<string, string>? aliasMap = null,
        IMonitor? monitor = null)
    {
        if (string.IsNullOrWhiteSpace(target))
        {
            return Fail(target, ResolveErrorCode.TargetNotFound, "目标名称不能为空", null);
        }

        // 1. "player" 字面量
        if (string.Equals(target, "player", StringComparison.OrdinalIgnoreCase))
        {
            var loc = Game1.currentLocation?.NameOrUniqueName ?? "";
            monitor?.Log($"[GoToTargetResolver] player → Kind=Player map={loc}", LogLevel.Debug);
            return new ResolvedTarget
            {
                Kind = TargetKind.Player,
                MapName = loc,
                HasPreciseCoord = false,
                IsTrackingEntity = false,
                RawInput = target,
                ResolvedBy = "player",
            };
        }

        // 2. ObserveId 格式 [type:xxx]
        var observeMatch = ObserveIdRegex.Match(target);
        if (observeMatch.Success)
        {
            var observeType = observeMatch.Groups[1].Value.ToLowerInvariant();
            var qualifier = observeMatch.Groups[2].Value;

            switch (observeType)
            {
                case "npc":
                    return ResolveNpcByName(qualifier, target, monitor);

                case "animal":
                {
                    var parts = qualifier.Split('#');
                    string animalName = parts[0];
                    string? shortUid = parts.Length > 1 ? parts[1] : null;
                    return ResolveAnimalByName(animalName, shortUid, target, monitor);
                }

                case "building":
                    return ResolveBuildingAlias(qualifier, aliasMap, target, monitor);

                case "exit":
                {
                    string mapName = char.ToUpperInvariant(qualifier[0]) + qualifier.Substring(1);
                    return ResolveMapByName(mapName, target, monitor)
                           ?? Fail(target, ResolveErrorCode.TargetNotFound,
                               $"ObserveId exit '{qualifier}' 未找到对应地图", BuildFuzzySuggestions(qualifier));
                }
            }
        }

        // 3. 地图名精确匹配（W5 裁决：地图优先于 NPC）
        var mapResult = ResolveMapByName(target, target, monitor);
        if (mapResult != null) return mapResult;

        // 4. NPC 搜索
        var npcResult = ResolveNpcByName(target, target, monitor);
        if (npcResult.IsSuccess) return npcResult;

        // 5. 动物搜索
        var animalResult = ResolveAnimalByName(target, null, target, monitor);
        if (animalResult.IsSuccess) return animalResult;

        // 6. 建筑别名（aliasMap 精确查找）
        if (aliasMap != null && aliasMap.TryGetValue(target, out var guidMapName))
        {
            monitor?.Log($"[GoToTargetResolver] 建筑别名 '{target}' → {guidMapName}", LogLevel.Debug);
            return ResolveMapByName(guidMapName, target, monitor)
                   ?? BuildBuildingEntryResult(guidMapName, target, monitor);
        }

        // 7. 建筑类型匹配（含中文别名）
        var buildingResult = ResolveBuildingByType(target, aliasMap, target, monitor);
        if (buildingResult != null) return buildingResult;

        // 8. 全部未命中 → fuzzy match
        var suggestions = BuildFuzzySuggestions(target);
        monitor?.Log($"[GoToTargetResolver] 无法解析 '{target}'，fuzzy 推荐: {string.Join(", ", suggestions)}", LogLevel.Warn);
        // bug #6 修复：suggestions 为空时不要输出"你是否想找：?"的尴尬文案
        string message = suggestions.Count > 0
            ? $"无法找到目标 '{target}'。你是否想找：{string.Join("、", suggestions)}？"
            : $"无法找到目标 '{target}'，请检查名称拼写";
        return Fail(target, ResolveErrorCode.TargetNotFound, message, suggestions);
    }

    // ── 分支 3：地图名精确匹配 ──────────────────────────────────────────────

    private static ResolvedTarget? ResolveMapByName(string mapName, string rawInput, IMonitor? monitor)
    {
        var entry = MapData.GetMap(mapName);
        if (entry == null)
        {
            // 尝试 NameOrUniqueName 方式：遍历地图寻 NameOrUniqueName 匹配
            foreach (var loc in Game1.locations)
            {
                if (loc == null) continue;
                if (string.Equals(loc.NameOrUniqueName, mapName, StringComparison.OrdinalIgnoreCase) ||
                    string.Equals(loc.Name, mapName, StringComparison.OrdinalIgnoreCase))
                {
                    string uniqueName = loc.NameOrUniqueName;
                    var safePoint = MapData.GetSafeWarpTarget(uniqueName);
                    int sx = safePoint?.x ?? (int)Game1.player.Tile.X;
                    int sy = safePoint?.y ?? (int)Game1.player.Tile.Y;
                    monitor?.Log($"[GoToTargetResolver] 地图 '{mapName}' → {uniqueName} ({sx},{sy})", LogLevel.Debug);
                    return new ResolvedTarget
                    {
                        Kind = TargetKind.Map,
                        MapName = uniqueName,
                        TileX = sx,
                        TileY = sy,
                        HasPreciseCoord = false,
                        IsTrackingEntity = false,
                        RawInput = rawInput,
                        ResolvedBy = "map_name",
                    };
                }
            }
            return null;
        }

        var safe = MapData.GetSafeWarpTarget(mapName);
        int x = safe?.x ?? 0;
        int y = safe?.y ?? 0;
        monitor?.Log($"[GoToTargetResolver] 地图 '{mapName}' → ({x},{y})", LogLevel.Debug);
        return new ResolvedTarget
        {
            Kind = TargetKind.Map,
            MapName = mapName,
            TileX = x,
            TileY = y,
            HasPreciseCoord = false,
            IsTrackingEntity = false,
            RawInput = rawInput,
            ResolvedBy = "map_name",
        };
    }

    // ── 分支 4：NPC 搜索 ────────────────────────────────────────────────────

    private static ResolvedTarget ResolveNpcByName(string npcName, string rawInput, IMonitor? monitor)
    {
        var npc = Game1.getCharacterFromName(npcName);
        if (npc == null)
        {
            return Fail(rawInput, ResolveErrorCode.TargetNotFound,
                $"NPC '{npcName}' 未找到", null);
        }

        var location = npc.currentLocation;
        if (location == null)
        {
            return Fail(rawInput, ResolveErrorCode.TargetNotFound,
                $"NPC '{npcName}' 当前没有所在地图", null);
        }

        string mapName = location.NameOrUniqueName;
        string trackingRef = $"{mapName}:npc_{npcName}";
        monitor?.Log($"[GoToTargetResolver] NPC '{npcName}' → {trackingRef}", LogLevel.Debug);

        return new ResolvedTarget
        {
            Kind = TargetKind.Npc,
            MapName = mapName,
            TileX = (int)npc.Tile.X,
            TileY = (int)npc.Tile.Y,
            HasPreciseCoord = true,
            IsTrackingEntity = true,
            TrackingRef = trackingRef,
            PreferredSide = null,
            RawInput = rawInput,
            ResolvedBy = "npc",
        };
    }

    // ── 分支 5：动物搜索 ────────────────────────────────────────────────────

    private static ResolvedTarget ResolveAnimalByName(
        string animalName, string? shortUid, string rawInput, IMonitor? monitor)
    {
        var candidates = new List<(FarmAnimal animal, GameLocation location)>();

        var farm = Game1.getFarm();
        if (farm != null)
        {
            foreach (var animal in farm.animals.Values)
            {
                if (MatchAnimalName(animal, animalName))
                    candidates.Add((animal, farm));
            }
        }

        foreach (var loc in Game1.locations)
        {
            if (loc?.buildings == null) continue;
            foreach (var building in loc.buildings)
            {
                if (building.indoors?.Value is not AnimalHouse animalHouse) continue;
                foreach (var animal in animalHouse.animals.Values)
                {
                    if (MatchAnimalName(animal, animalName))
                        candidates.Add((animal, animalHouse));
                }
            }
        }

        if (candidates.Count == 0)
        {
            return Fail(rawInput, ResolveErrorCode.TargetNotFound,
                $"动物 '{animalName}' 未找到", null);
        }

        if (candidates.Count == 1 || shortUid == null)
        {
            if (candidates.Count > 1 && shortUid == null)
            {
                var suggList = candidates
                    .Select(c =>
                    {
                        string uid = c.animal.myID.Value.ToString("x8").Substring(0, 6);
                        return $"{animalName}#{uid}";
                    })
                    .ToList();
                return new ResolvedTarget
                {
                    Kind = TargetKind.Failure,
                    ErrorCode = ResolveErrorCode.AmbiguousAnimal,
                    ErrorMessage = $"找到 {candidates.Count} 只名为 '{animalName}' 的动物，请用 go_to('{animalName}#uid') 区分",
                    Suggestions = suggList,
                    RawInput = rawInput,
                };
            }

            var (chosen, chosenLoc) = candidates[0];
            return BuildAnimalResult(chosen, chosenLoc, rawInput, monitor);
        }

        // 用 shortUid 消歧
        foreach (var (animal, loc) in candidates)
        {
            string uid = animal.myID.Value.ToString("x8").Substring(0, 6);
            if (uid.StartsWith(shortUid, StringComparison.OrdinalIgnoreCase))
                return BuildAnimalResult(animal, loc, rawInput, monitor);
        }

        return Fail(rawInput, ResolveErrorCode.TargetNotFound,
            $"动物 '{animalName}' 未找到匹配 uid '{shortUid}' 的个体", null);
    }

    private static ResolvedTarget BuildAnimalResult(FarmAnimal animal, GameLocation location, string rawInput, IMonitor? monitor)
    {
        string mapName = location.NameOrUniqueName;
        string displayName = animal.displayName ?? animal.Name;
        string uid = animal.myID.Value.ToString("x8").Substring(0, 6);
        string trackingRef = $"{mapName}:animal_{displayName}#{uid}";
        monitor?.Log($"[GoToTargetResolver] 动物 '{displayName}' → {trackingRef}", LogLevel.Debug);

        return new ResolvedTarget
        {
            Kind = TargetKind.Animal,
            MapName = mapName,
            TileX = (int)animal.Tile.X,
            TileY = (int)animal.Tile.Y,
            HasPreciseCoord = true,
            IsTrackingEntity = true,
            TrackingRef = trackingRef,
            PreferredSide = null,
            RawInput = rawInput,
            ResolvedBy = "animal",
        };
    }

    // ── 分支 6：建筑别名（aliasMap 精确查找） ────────────────────────────────

    private static ResolvedTarget ResolveBuildingAlias(
        string qualifier, IReadOnlyDictionary<string, string>? aliasMap,
        string rawInput, IMonitor? monitor)
    {
        if (aliasMap != null && aliasMap.TryGetValue(qualifier, out var guidName))
        {
            monitor?.Log($"[GoToTargetResolver] 建筑别名 '{qualifier}' → {guidName}", LogLevel.Debug);
            return ResolveMapByName(guidName, rawInput, monitor)
                   ?? BuildBuildingEntryResult(guidName, rawInput, monitor);
        }
        return Fail(rawInput, ResolveErrorCode.TargetNotFound,
            $"建筑别名 '{qualifier}' 未找到", null);
    }

    // ── 分支 7：建筑类型匹配 ─────────────────────────────────────────────────

    private static ResolvedTarget? ResolveBuildingByType(
        string target, IReadOnlyDictionary<string, string>? aliasMap,
        string rawInput, IMonitor? monitor)
    {
        string resolveTarget = BuildingAliases.TryGetValue(target, out var alias) ? alias : target;

        var farm = Game1.getFarm();
        if (farm?.buildings == null) return null;

        var matched = farm.buildings
            .Where(b => string.Equals(b.buildingType.Value, resolveTarget, StringComparison.OrdinalIgnoreCase))
            .ToList();

        if (matched.Count == 0) return null;

        if (matched.Count == 1)
        {
            var building = matched[0];
            GetBuildingDoorCoords(building, out int doorX, out int doorY);
            string mapName = farm.NameOrUniqueName;
            monitor?.Log($"[GoToTargetResolver] 建筑类型 '{target}' → {mapName} ({doorX},{doorY})", LogLevel.Debug);
            return new ResolvedTarget
            {
                Kind = TargetKind.Map,
                MapName = mapName,
                TileX = doorX,
                TileY = doorY,
                HasPreciseCoord = true,
                IsTrackingEntity = false,
                RawInput = rawInput,
                ResolvedBy = "building",
            };
        }

        // 严格命名：suggestions 直接返回 NameOrUniqueName（含 GUID 的内部地图名），
        // LLM 复读传回即可走分支 3 ResolveMapByName 命中。不再编造 "coop1/coop2"
        // 这种假别名（既无 aliasMap 注入路径解析，又违反 stardew-location-naming.md
        // "用 NameOrUniqueName 做地图查找/比较" 的规范）。
        var suggList = matched
            .Select(b => b.indoors?.Value?.NameOrUniqueName ?? b.buildingType.Value)
            .ToList();

        return new ResolvedTarget
        {
            Kind = TargetKind.Failure,
            ErrorCode = ResolveErrorCode.AmbiguousBuilding,
            ErrorMessage = $"找到 {matched.Count} 个 '{target}'，请用以下完整名称之一：{string.Join(", ", suggList)}",
            Suggestions = suggList,
            RawInput = rawInput,
        };
    }

    // ── 工具方法 ────────────────────────────────────────────────────────────

    private static ResolvedTarget BuildBuildingEntryResult(string guidMapName, string rawInput, IMonitor? monitor)
    {
        // 找建筑门前坐标（通过 GUID 唯一名）
        var farm = Game1.getFarm();
        if (farm?.buildings != null)
        {
            foreach (var building in farm.buildings)
            {
                var interior = building.indoors?.Value;
                if (interior == null) continue;
                if (string.Equals(interior.NameOrUniqueName, guidMapName, StringComparison.OrdinalIgnoreCase))
                {
                    GetBuildingDoorCoords(building, out int doorX, out int doorY);
                    string mapName = farm.NameOrUniqueName;
                    monitor?.Log($"[GoToTargetResolver] 建筑 GUID '{guidMapName}' 门前 → {mapName} ({doorX},{doorY})", LogLevel.Debug);
                    return new ResolvedTarget
                    {
                        Kind = TargetKind.Map,
                        MapName = mapName,
                        TileX = doorX,
                        TileY = doorY,
                        HasPreciseCoord = true,
                        IsTrackingEntity = false,
                        RawInput = rawInput,
                        ResolvedBy = "building_guid",
                    };
                }
            }
        }
        return Fail(rawInput, ResolveErrorCode.TargetNotFound, $"未找到建筑 '{guidMapName}'", null);
    }

    private static void GetBuildingDoorCoords(Building building, out int doorX, out int doorY)
    {
        var humanDoor = building.humanDoor.Value;
        doorX = building.tileX.Value + humanDoor.X;
        doorY = building.tileY.Value + humanDoor.Y + 1;
    }

    private static bool MatchAnimalName(FarmAnimal animal, string name)
    {
        return string.Equals(animal.Name, name, StringComparison.OrdinalIgnoreCase) ||
               string.Equals(animal.displayName, name, StringComparison.OrdinalIgnoreCase);
    }

    private static ResolvedTarget Fail(
        string rawInput, ResolveErrorCode code, string message, List<string>? suggestions)
    {
        return new ResolvedTarget
        {
            Kind = TargetKind.Failure,
            ErrorCode = code,
            ErrorMessage = message,
            Suggestions = suggestions,
            RawInput = rawInput,
        };
    }

    // ── Fuzzy Match（三级：前缀 → 包含 → Levenshtein） ─────────────────────

    public static List<string> BuildFuzzySuggestions(string target)
    {
        var candidates = new List<string>();

        foreach (var name in MapData.GetAllMapNames())
            candidates.Add(name);

        foreach (var loc in Game1.locations)
        {
            if (loc == null) continue;
            var entry = MapData.GetMap(loc.Name);
            if (entry != null && !string.IsNullOrEmpty(entry.DisplayName))
                candidates.Add(entry.DisplayName);
        }

        var town = Game1.getLocationFromName("Town");
        if (town != null)
            foreach (var npc in town.characters)
                candidates.Add(npc.Name);

        // NPC 全局搜索
        foreach (var loc in Game1.locations)
        {
            if (loc == null) continue;
            foreach (var c in loc.characters)
                candidates.Add(c.Name);
        }

        var scored = new List<(string candidate, int score)>();
        foreach (var c in candidates.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            int score = ScoreCandidate(target, c);
            if (score > 0)
                scored.Add((c, score));
        }

        return scored
            .OrderByDescending(x => x.score)
            .Take(5)
            .Select(x => x.candidate)
            .ToList();
    }

    private static int ScoreCandidate(string target, string candidate)
    {
        if (string.IsNullOrEmpty(candidate)) return 0;

        if (candidate.StartsWith(target, StringComparison.OrdinalIgnoreCase))
            return 100 - candidate.Length;

        if (candidate.IndexOf(target, StringComparison.OrdinalIgnoreCase) >= 0)
            return 50;

        int threshold = target.Length <= 4 ? 1 : 2;
        int dist = LevenshteinDistance(target.ToLowerInvariant(), candidate.ToLowerInvariant());
        if (dist <= threshold)
            return threshold - dist + 1;

        return 0;
    }

    private static int LevenshteinDistance(string s, string t)
    {
        int m = s.Length, n = t.Length;
        if (m == 0) return n;
        if (n == 0) return m;

        var d = new int[m + 1, n + 1];
        for (int i = 0; i <= m; i++) d[i, 0] = i;
        for (int j = 0; j <= n; j++) d[0, j] = j;

        for (int i = 1; i <= m; i++)
        for (int j = 1; j <= n; j++)
        {
            int cost = s[i - 1] == t[j - 1] ? 0 : 1;
            d[i, j] = Math.Min(
                Math.Min(d[i - 1, j] + 1, d[i, j - 1] + 1),
                d[i - 1, j - 1] + cost);
        }

        return d[m, n];
    }
}
