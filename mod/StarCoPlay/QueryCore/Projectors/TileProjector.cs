using System;
using System.Collections.Generic;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.TerrainFeatures;
using StarCoPlay.Protocol.V245;
using ProtoRefId = StarCoPlay.Protocol.V245.RefId;

namespace StarCoPlay.V2.Query.Projectors;

public static class TileProjector
{
    public static TileFact Project(
        GameLocation location,
        string mapId,
        int tileX,
        int tileY,
        IReadOnlyDictionary<(int x, int y), List<ProtoRefId>> refsByTile,
        IMonitor? monitor = null)
    {
        var fact = new TileFact
        {
            Position = new TilePosition { MapId = mapId, X = tileX, Y = tileY },
        };

        var tile = new Vector2(tileX, tileY);

        try { fact.Passable = location.isTilePassable(tile); }
        catch (Exception ex)
        {
            fact.Passable = false;
            monitor?.Log($"[TileProjector] passable read error at ({tileX},{tileY}): {ex.Message}", LogLevel.Debug);
        }

        try { fact.Occupied = location.IsTileOccupiedBy(tile); }
        catch (Exception ex)
        {
            fact.Occupied = false;
            monitor?.Log($"[TileProjector] occupied read error at ({tileX},{tileY}): {ex.Message}", LogLevel.Debug);
        }

        try { fact.Diggable = location.doesTileHaveProperty(tileX, tileY, "Diggable", "Back") != null; }
        catch { fact.Diggable = false; }

        try { fact.Water = location.isWaterTile(tileX, tileY); }
        catch { fact.Water = false; }

        try { fact.OpenWater = location.isOpenWater(tileX, tileY); }
        catch { fact.OpenWater = false; }

        try
        {
            fact.FloorType = location.doesTileHaveProperty(tileX, tileY, "Type", "Back") ?? "";
        }
        catch { fact.FloorType = ""; }

        try
        {
            fact.TerrainFeatureType = ResolveTerrainFeatureType(location, tile);
        }
        catch { fact.TerrainFeatureType = ""; }

        if (refsByTile.TryGetValue((tileX, tileY), out var refs))
            fact.Refs.AddRange(refs);

        return fact;
    }

    private static string ResolveTerrainFeatureType(GameLocation location, Vector2 tile)
    {
        if (!location.terrainFeatures.TryGetValue(tile, out var feature))
            return "";

        return feature switch
        {
            HoeDirt => "hoe_dirt",
            Tree    => "tree",
            FruitTree => "fruit_tree",
            Grass   => "grass",
            Flooring => "flooring",
            _       => "unknown",
        };
    }
}
