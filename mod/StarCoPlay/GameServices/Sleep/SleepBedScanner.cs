using System;
using System.Collections.Generic;
using System.Linq;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Objects;

namespace StarCoPlay.GameServices.Sleep;

internal class SleepBedScanner
{
    private readonly IMonitor _monitor;

    public SleepBedScanner(IMonitor monitor)
    {
        _monitor = monitor;
    }

    /// <summary>
    /// 扫描全存档可睡床，优先级：当前地图 > 玩家家 > 其他地图。
    /// 排除 BedType.Child 和正在被占用的床。
    /// MapId 输出"可路由 ID"（与 MapData 注册一致），而非 location 自报名。
    /// </summary>
    public List<SleepBedCandidate> Scan()
    {
        var currentBeds = new List<SleepBedCandidate>();
        var homeBeds = new List<SleepBedCandidate>();
        var otherBeds = new List<SleepBedCandidate>();

        var currentLocation = Game1.currentLocation;
        var homeLocation = Utility.getHomeOfFarmer(Game1.player);
        string currentRawMapId = currentLocation?.NameOrUniqueName ?? "";

        string homeName = homeLocation != null
            ? ResolveRoutableMapId(homeLocation, currentRawMapId)
            : "";

        Utility.ForEachLocation(location =>
        {
            string routeMapId = ResolveRoutableMapId(location, currentRawMapId);

            AddBedsFromLocation(location, routeMapId, homeName,
                currentBeds, homeBeds, otherBeds);
            return true;
        });

        var results = new List<SleepBedCandidate>();
        results.AddRange(currentBeds);
        results.AddRange(homeBeds);
        results.AddRange(otherBeds);

        _monitor.Log($"[SleepBedScanner] Found {results.Count} sleepable bed(s)", LogLevel.Debug);
        return results;
    }

    /// <summary>
    /// 解析 location 的可路由 MapId（与 MapData 注册 key 一致）。
    /// 规则：当前地图直接返回 raw；否则 raw 可达则用 raw；
    /// 否则 prefix 匹配 GUID 版本中可达的唯一候选。
    /// </summary>
    private string ResolveRoutableMapId(GameLocation location, string currentRawMapId)
    {
        string raw = location.NameOrUniqueName;

        if (location == Game1.currentLocation)
            return raw;

        if (MapData.IsKnownMap(raw) && MapData.FindRoute(currentRawMapId, raw).Count > 0)
            return raw;

        var candidates = MapData.GetAllMapNames()
            .Where(n => n.StartsWith(raw, StringComparison.OrdinalIgnoreCase) && n.Length > raw.Length)
            .Where(n => MapData.FindRoute(currentRawMapId, n).Count > 0)
            .ToList();

        if (candidates.Count == 1)
        {
            _monitor.Log($"[SleepBedScanner] Resolved '{raw}' → '{candidates[0]}' (routable prefix match)", LogLevel.Debug);
            return candidates[0];
        }

        if (candidates.Count > 1)
        {
            _monitor.Log($"[SleepBedScanner] WARNING: '{raw}' has {candidates.Count} routable GUID candidates, falling back to raw", LogLevel.Warn);
        }

        return raw;
    }

    private void AddBedsFromLocation(
        GameLocation location,
        string routeMapId,
        string homeName,
        List<SleepBedCandidate> currentBeds,
        List<SleepBedCandidate> homeBeds,
        List<SleepBedCandidate> otherBeds)
    {
        foreach (var furniture in location.furniture)
        {
            if (furniture is not BedFurniture bed) continue;
            if (bed.bedType == BedFurniture.BedType.Child) continue;
            if (bed.IsBeingSleptIn()) continue;

            bool isCurrentMap = location == Game1.currentLocation;
            var bedSpot = bed.GetBedSpot();
            var candidate = new SleepBedCandidate(
                MapId: routeMapId,
                BedTileX: (int)bed.TileLocation.X,
                BedTileY: (int)bed.TileLocation.Y,
                BedSpotX: bedSpot.X,
                BedSpotY: bedSpot.Y,
                IsPlayerHome: routeMapId == homeName,
                IsCurrentMap: isCurrentMap
            );

            if (isCurrentMap)
                currentBeds.Add(candidate);
            else if (routeMapId == homeName)
                homeBeds.Add(candidate);
            else
                otherBeds.Add(candidate);
        }
    }
}
