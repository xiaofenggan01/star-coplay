using StardewValley;

namespace StarCoPlay.GameServices.Sleep;

/// <summary>
/// 一张可睡的床候选。
/// MapId 是与 MapData 路由表一致的"可路由 ID"（建筑内部含 GUID 后缀）。
/// </summary>
internal record SleepBedCandidate(
    string MapId,           // 可路由地图 ID（与 MapData 注册 key 一致）
    int BedTileX,           // BedFurniture.TileLocation.X
    int BedTileY,           // BedFurniture.TileLocation.Y
    int BedSpotX,           // GetBedSpot().X — 触发 TouchAction 的点
    int BedSpotY,           // GetBedSpot().Y
    bool IsPlayerHome,      // 是否为 getHomeOfFarmer 的床
    bool IsCurrentMap       // 扫描时床是否在玩家当前所在地图
);
