using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.WorldEntity;

public static class WarpProjector
{
    public static WorldEntityFact Project(Warp warp, string mapNameOrUniqueName)
    {
        var tileX = warp.X;
        var tileY = warp.Y;
        var targetMap = warp.TargetName ?? "";
        var targetX = warp.TargetX;
        var targetY = warp.TargetY;
        var npcOnly = warp.npcOnly.Value;
        var warpKey = $"{mapNameOrUniqueName}_{tileX}_{tileY}";

        var refId = RefIdBuilder.BuildWarp(mapNameOrUniqueName, tileX, tileY, targetMap);

        var position = new TilePosition
        {
            MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
            X = tileX,
            Y = tileY,
        };

        var warpFact = new WarpFact
        {
            WarpKey = warpKey,
            TargetMap = targetMap,
            TargetX = targetX,
            TargetY = targetY,
            NpcOnly = npcOnly,
        };

        var suffix = npcOnly ? " (NPC only)" : "";
        var summary = $"Warp → {targetMap} ({targetX},{targetY}){suffix}";

        return new WorldEntityFact
        {
            Ref = refId,
            Type = "warp",
            FactKind = "warp",
            Position = position,
            Summary = summary,
            Warp = warpFact,
        };
    }
}
