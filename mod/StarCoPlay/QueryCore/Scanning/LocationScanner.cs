using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Monsters;
using StardewValley.Characters;
using StardewValley.Objects;
using StardewValley.TerrainFeatures;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.Query.Classification;
using StarCoPlay.V2.Query.Projectors;
using StarCoPlay.V2.Query.Projectors.WorldEntity;
using StarCoPlay.V2.Query.Projectors.Character;
using StarCoPlay.V2.RefId.Encoding;
using ProtoRefId = StarCoPlay.Protocol.V245.RefId;

namespace StarCoPlay.V2.Query.Scanning;

public static class LocationScanner
{
    public static List<WorldEntityFact> ScanEntities(GameLocation location, QueryWorldRegionParams request)
    {
        var results = new List<WorldEntityFact>();
        var mapName = location.NameOrUniqueName;

        // Terrain features: Tree, FruitTree, Crop
        foreach (var pair in location.terrainFeatures.Pairs)
        {
            int tileX = (int)pair.Key.X;
            int tileY = (int)pair.Key.Y;

            if (!RegionFilter.Contains(request, tileX, tileY))
                continue;

            var entityType = EntityClassifier.ClassifyTerrainFeature(pair.Value);
            if (entityType == WorldEntityType.Unknown)
                continue;

            if (!RegionFilter.IsEntityTypeIncluded(request, EntityTypeName(entityType)))
                continue;

            WorldEntityFact? fact = (entityType, pair.Value) switch
            {
                (WorldEntityType.Tree, Tree tree) => TreeProjector.Project(tree, mapName, tileX, tileY),
                (WorldEntityType.FruitTree, FruitTree ft) => FruitTreeProjector.Project(ft, mapName, tileX, tileY),
                (WorldEntityType.Crop, HoeDirt dirt) => CropProjector.Project(dirt, mapName, tileX, tileY),
                _ => null,
            };

            if (fact != null)
                results.Add(fact);
        }

        // Objects: Machine, Container, ResourceNode, LooseItem
        foreach (var pair in location.Objects.Pairs)
        {
            int tileX = (int)pair.Key.X;
            int tileY = (int)pair.Key.Y;

            if (!RegionFilter.Contains(request, tileX, tileY))
                continue;

            var entityType = EntityClassifier.ClassifyObject(pair.Value);
            if (!RegionFilter.IsEntityTypeIncluded(request, EntityTypeName(entityType)))
                continue;

            WorldEntityFact? fact = (entityType, pair.Value) switch
            {
                (WorldEntityType.Machine, _) => MachineProjector.Project(pair.Value, mapName, tileX, tileY),
                (WorldEntityType.Container, Chest chest) => ContainerProjector.Project(chest, mapName, tileX, tileY),
                (WorldEntityType.ResourceNode, _) => ResourceNodeProjector.Project(pair.Value, mapName, tileX, tileY),
                (WorldEntityType.LooseItem, _) => LooseItemProjector.Project(pair.Value, mapName, tileX, tileY),
                _ => null,
            };

            if (fact != null)
                results.Add(fact);
        }

        // Furniture (including beds)
        foreach (var furniture in location.furniture)
        {
            int tileX = (int)furniture.TileLocation.X;
            int tileY = (int)furniture.TileLocation.Y;

            if (!RegionFilter.Contains(request, tileX, tileY))
                continue;

            var entityType = EntityClassifier.ClassifyFurniture(furniture);
            if (!RegionFilter.IsEntityTypeIncluded(request, EntityTypeName(entityType)))
                continue;

            WorldEntityFact? fact = entityType switch
            {
                WorldEntityType.Bed when furniture is BedFurniture bed => BedProjector.Project(bed, mapName, tileX, tileY),
                WorldEntityType.Furniture => FurnitureProjector.Project(furniture, mapName, tileX, tileY),
                _ => null,
            };

            if (fact != null)
                results.Add(fact);
        }

        // Resource clumps
        foreach (var clump in location.resourceClumps)
        {
            int tileX = (int)clump.Tile.X;
            int tileY = (int)clump.Tile.Y;

            if (!RegionFilter.Contains(request, tileX, tileY))
                continue;

            if (!RegionFilter.IsEntityTypeIncluded(request, "resource_clump"))
                continue;

            results.Add(ResourceClumpProjector.Project(clump, mapName, tileX, tileY));
        }

        // Warps
        if (RegionFilter.IsEntityTypeIncluded(request, "warp"))
        {
            foreach (var warp in location.warps)
            {
                if (!RegionFilter.Contains(request, warp.X, warp.Y))
                    continue;

                results.Add(WarpProjector.Project(warp, mapName));
            }
        }

        // Doors
        if (RegionFilter.IsEntityTypeIncluded(request, "door"))
        {
            foreach (var pair in location.doors.Pairs)
            {
                int tileX = (int)pair.Key.X;
                int tileY = (int)pair.Key.Y;

                if (!RegionFilter.Contains(request, tileX, tileY))
                    continue;

                results.Add(DoorProjector.Project(pair.Key, pair.Value, mapName, location));
            }
        }

        return results;
    }

    public static List<CharacterFact> ScanCharacters(GameLocation location, QueryWorldRegionParams request)
    {
        var results = new List<CharacterFact>();
        var mapName = location.NameOrUniqueName;

        foreach (var npc in location.characters)
        {
            int tileX = (int)npc.Tile.X;
            int tileY = (int)npc.Tile.Y;

            if (!RegionFilter.Contains(request, tileX, tileY))
                continue;

            CharacterFact? fact = npc switch
            {
                Monster monster => MonsterProjector.Project(monster),
                Horse horse => HorseProjector.Project(horse),
                Pet pet => PetProjector.Project(pet),
                _ when !npc.IsMonster => NpcProjector.Project(npc),
                _ => null,
            };

            if (fact != null)
                results.Add(fact);
        }

        // Farm animals (only in AnimalHouse or Farm)
        IEnumerable<FarmAnimal>? animals = location switch
        {
            AnimalHouse ah => ah.animals.Values,
            Farm farm => farm.animals.Values,
            _ => null,
        };

        if (animals != null)
        {
            foreach (var animal in animals)
            {
                int tileX = (int)animal.Tile.X;
                int tileY = (int)animal.Tile.Y;

                if (!RegionFilter.Contains(request, tileX, tileY))
                    continue;

                results.Add(FarmAnimalProjector.Project(animal));
            }
        }

        return results;
    }

    /// <summary>
    /// Resolve a MapToken to a GameLocation by comparing encoded tokens.
    /// This avoids the bug of passing an encoded token directly to getLocationFromName.
    /// </summary>
    public static GameLocation? ResolveMapToken(string mapToken)
    {
        if (string.IsNullOrEmpty(mapToken))
            return Game1.player?.currentLocation;

        var current = Game1.player?.currentLocation;
        if (MapTokenMatches(current, mapToken))
            return current;

        foreach (var loc in Game1.locations)
        {
            try
            {
                if (loc == null) continue;
                if (MapTokenMatches(loc, mapToken))
                    return loc;
            }
            catch
            {
                // skip locations whose NameOrUniqueName throws (e.g. BadImageFormatException)
            }
        }

        // Fallback: try direct name lookup (handles unencoded names)
        try { return Game1.getLocationFromName(mapToken); }
        catch { return null; }
    }

    private static bool MapTokenMatches(GameLocation? location, string mapToken)
    {
        if (location == null)
            return false;

        return string.Equals(
            RefIdTokenEncoder.EncodeMapToken(location.NameOrUniqueName),
            mapToken,
            StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>
    /// Build a lookup from tile coordinates to entity refs.
    /// Only entities with non-null Ref are included.
    /// </summary>
    public static Dictionary<(int x, int y), List<ProtoRefId>> BuildRefsByTile(
        IEnumerable<WorldEntityFact> entities)
    {
        var map = new Dictionary<(int x, int y), List<ProtoRefId>>();

        foreach (var entity in entities)
        {
            if (entity.Ref == null)
                continue;

            var pos = entity.Position;
            if (pos == null)
                continue;

            var key = (pos.X, pos.Y);
            if (!map.TryGetValue(key, out var list))
            {
                list = new List<ProtoRefId>();
                map[key] = list;
            }
            list.Add(entity.Ref);
        }

        return map;
    }

    /// <summary>
    /// Scan tiles in the requested region, projecting each to a TileFact.
    /// Falls back to player position radius-8 when no region/center is specified.
    /// </summary>
    public static List<TileFact> ScanTiles(
        GameLocation location,
        QueryWorldRegionParams request,
        IReadOnlyDictionary<(int x, int y), List<ProtoRefId>> refsByTile,
        IMonitor? monitor = null)
    {
        var results = new List<TileFact>();
        var mapId = location.NameOrUniqueName;

        // Determine map bounds
        var layer = location.Map?.Layers?.Count > 0 ? location.Map.Layers[0] : null;
        if (layer == null)
        {
            monitor?.Log($"[ScanTiles] Map '{mapId}' has no layers — skipping tile scan.", LogLevel.Warn);
            return results;
        }

        int mapW = layer.LayerWidth;
        int mapH = layer.LayerHeight;

        // Determine scan bounds — same priority as RegionFilter
        int x0, y0, x1, y1;

        if (request.Region is { Width: > 0, Height: > 0 } rect)
        {
            x0 = rect.X;
            y0 = rect.Y;
            x1 = rect.X + rect.Width - 1;
            y1 = rect.Y + rect.Height - 1;
        }
        else if (request.Center != null && request.Radius > 0)
        {
            x0 = request.Center.X - request.Radius;
            y0 = request.Center.Y - request.Radius;
            x1 = request.Center.X + request.Radius;
            y1 = request.Center.Y + request.Radius;
        }
        else if (request.Radius > 0)
        {
            // radius without center — use player tile as implicit center
            int px = (int)(Game1.player?.Tile.X ?? 0);
            int py = (int)(Game1.player?.Tile.Y ?? 0);
            x0 = px - request.Radius; y0 = py - request.Radius;
            x1 = px + request.Radius; y1 = py + request.Radius;
        }
        else
        {
            // no filter at all — default player position ± 8
            int px = (int)(Game1.player?.Tile.X ?? 0);
            int py = (int)(Game1.player?.Tile.Y ?? 0);
            x0 = px - 8; y0 = py - 8;
            x1 = px + 8; y1 = py + 8;
        }

        // Clamp to map bounds
        x0 = Math.Max(0, x0); y0 = Math.Max(0, y0);
        x1 = Math.Min(mapW - 1, x1); y1 = Math.Min(mapH - 1, y1);

        for (int y = y0; y <= y1; y++)
        {
            for (int x = x0; x <= x1; x++)
            {
                results.Add(TileProjector.Project(location, mapId, x, y, refsByTile, monitor));
            }
        }

        return results;
    }

    public static string EntityTypeName(WorldEntityType type) => type switch
    {
        WorldEntityType.Tree => "tree",
        WorldEntityType.FruitTree => "fruit_tree",
        WorldEntityType.Crop => "crop",
        WorldEntityType.Machine => "machine",
        WorldEntityType.Container => "container",
        WorldEntityType.ResourceNode => "resource_node",
        WorldEntityType.LooseItem => "loose_item",
        WorldEntityType.ResourceClump => "resource_clump",
        WorldEntityType.Bed => "bed",
        WorldEntityType.Furniture => "furniture",
        WorldEntityType.Door => "door",
        WorldEntityType.Warp => "warp",
        _ => "unknown",
    };
}
