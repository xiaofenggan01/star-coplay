using System;
using Microsoft.Xna.Framework;
using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;
using ProtoTilePoint = StarCoPlay.Protocol.V245.TilePoint;

namespace StarCoPlay.V2.Query.Projectors.WorldEntity;

public static class DoorProjector
{
    public static WorldEntityFact Project(Point doorTile, string targetMap, string mapNameOrUniqueName, GameLocation location)
    {
        var tileX = doorTile.X;
        var tileY = doorTile.Y;
        var doorKey = $"{mapNameOrUniqueName}_{tileX}_{tileY}";

        var refId = RefIdBuilder.BuildDoor(mapNameOrUniqueName, tileX, tileY, targetMap);

        var position = new TilePosition
        {
            MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
            X = tileX,
            Y = tileY,
        };

        var (doorKind, locked, targetTile) = ClassifyDoor(location, doorTile);

        var doorFact = new DoorFact
        {
            DoorKey    = doorKey,
            TargetMap  = targetMap,
            Locked     = locked,
            DoorKind   = doorKind,
        };

        if (targetTile.HasValue)
        {
            doorFact.TargetTile = new ProtoTilePoint
            {
                X = targetTile.Value.X,
                Y = targetTile.Value.Y,
            };
        }

        var summary = $"Door → {targetMap}";

        return new WorldEntityFact
        {
            Ref = refId,
            Type = "door",
            FactKind = "door",
            Position = position,
            Summary = summary,
            Door = doorFact,
        };
    }

    private static (string doorKind, bool locked, Point? targetTile) ClassifyDoor(GameLocation location, Point doorTile)
    {
        // getWarpFromDoor can throw in edge cases (e.g. building without interior); treat as no warp info.
        Warp? warp = null;
        try { warp = location.getWarpFromDoor(doorTile); }
        catch (Exception) { /* target_tile left null; warp info unavailable */ }

        Point? targetTile = warp != null ? new Point(warp.TargetX, warp.TargetY) : null;

        // Determine door kind from tile Action property
        var actionSplit = location.GetTilePropertySplitBySpaces("Action", "Buildings", doorTile.X, doorTile.Y);
        var action = actionSplit.Length > 0 ? actionSplit[0] : "";

        // locked is time/friendship/festival/key-dependent — not a stable objective state.
        // C3 is conservative: door_kind reflects the lock type, but locked is always false here.
        // A future View/ActionCandidate layer can compute the actual runtime lock state.
        bool locked = false;

        string doorKind = action switch
        {
            "LockedDoorWarp" => "locked_door",
            "Warp" or "WarpMensLocker" or "WarpWomensLocker"
                or "WarpCommunityCenter" or "Warp_Sunroom_Door" or "WarpBoatTunnel" => "warp",
            "" when warp != null => "door",    // building door (no explicit tile action)
            _ => "door",
        };

        return (doorKind, locked, targetTile);
    }
}
