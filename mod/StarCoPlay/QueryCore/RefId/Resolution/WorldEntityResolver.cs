using System;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewValley;
using StardewValley.Objects;
using StardewValley.TerrainFeatures;
using StarCoPlay.V2.Query.Classification;
using StarCoPlay.V2.Query.Projectors.WorldEntity;
using StarCoPlay.V2.RefId.Encoding;
using StarCoPlay.V2.RefId.Protocol;

namespace StarCoPlay.V2.RefId.Resolution;

public sealed class WorldEntityResolver : IRefIdResolver
{
    public RefIdResolutionResult Resolve(ParsedRefId parsedRefId)
    {
        var factKind = RefIdProtocolMapper.InferFactKind(parsedRefId);

        // Validate locator: [map_token, x, y]
        if (parsedRefId.LocatorParts.Count != 3)
            return Malformed(parsedRefId, factKind, "world_entity locator must have 3 parts: map_token, x, y.");

        if (!int.TryParse(parsedRefId.LocatorParts[1], out var x) ||
            !int.TryParse(parsedRefId.LocatorParts[2], out var y))
            return Malformed(parsedRefId, factKind, "x/y must be integers.");

        // Map lookup
        var location = MapLookup.Find(parsedRefId.LocatorParts[0]);
        if (location == null)
            return Result(RefIdStatus.Missing, parsedRefId, factKind, null, "Map not found.");

        var tile = new Vector2(x, y);

        return parsedRefId.Type switch
        {
            "tree" => ResolveTerrainFeature<Tree>(location, tile, parsedRefId, factKind,
                tree => RefIdTokenEncoder.EncodeWildTreeType(tree.treeType.Value)),
            "fruit_tree" => ResolveTerrainFeature<FruitTree>(location, tile, parsedRefId, factKind,
                ft =>
                {
                    var treeId = ft.treeId.Value ?? "";
                    var qualifiedItemId = string.IsNullOrEmpty(treeId) ? "" : $"(O){treeId}";
                    return RefIdTokenEncoder.EncodeQualifiedItemId(qualifiedItemId);
                }),
            "crop" => ResolveCrop(location, tile, parsedRefId, factKind),
            "machine" or "container" or "resource_node" or "loose_item" =>
                ResolveObject(location, tile, parsedRefId, factKind),
            "bed" => ResolveBed(location, tile, parsedRefId, factKind),
            "furniture" => ResolveFurniture(location, tile, parsedRefId, factKind),
            "resource_clump" => ResolveResourceClump(location, tile, parsedRefId, factKind),
            "door" => ResolveDoor(location, tile, parsedRefId, factKind),
            "warp" => ResolveWarp(location, tile, parsedRefId, factKind),
            _ => new RefIdResolutionResult(RefIdStatus.Unsupported, parsedRefId, factKind,
                $"world_entity type '{parsedRefId.Type}' not supported.", null),
        };
    }

    private RefIdResolutionResult ResolveTerrainFeature<T>(
        GameLocation location, Vector2 tile, ParsedRefId parsed, string factKind,
        Func<T, string> guardBuilder) where T : TerrainFeature
    {
        if (!location.terrainFeatures.TryGetValue(tile, out var tf))
            return Result(RefIdStatus.Missing, parsed, factKind, null, "No terrain feature at tile.");

        if (tf is not T typed)
            return Result(RefIdStatus.StaleTypeMismatch, parsed, factKind, null,
                $"Expected {typeof(T).Name}, found {tf.GetType().Name}.");

        var expectedGuard = guardBuilder(typed);
        if (expectedGuard != parsed.IdentityGuard)
            return Result(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                $"Guard mismatch: expected '{expectedGuard}', got '{parsed.IdentityGuard}'.");

        var source = new ResolvedRefSource(typed, location, tile);

        // Tree stump detection
        if (typed is Tree tree && tree.stump.Value)
            return Result(RefIdStatus.ResolvedDegraded, parsed, factKind, "Tree is a stump.", null, source);

        return Result(RefIdStatus.Resolved, parsed, factKind, null, null, source);
    }

    private RefIdResolutionResult ResolveCrop(
        GameLocation location, Vector2 tile, ParsedRefId parsed, string factKind)
    {
        if (!location.terrainFeatures.TryGetValue(tile, out var tf))
            return Result(RefIdStatus.Missing, parsed, factKind, null, "No terrain feature at tile.");

        if (tf is not HoeDirt dirt || dirt.crop == null)
            return Result(RefIdStatus.StaleTypeMismatch, parsed, factKind, null, "No crop at tile.");

        var harvestItemId = dirt.crop.indexOfHarvest.Value ?? "";
        var expectedGuard = harvestItemId.StartsWith("(") && harvestItemId.Contains(')')
            ? RefIdTokenEncoder.EncodeQualifiedItemId(harvestItemId)
            : $"o_{RefIdTokenEncoder.EncodeProtocolKey(harvestItemId)}";
        if (expectedGuard != parsed.IdentityGuard)
            return Result(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                $"Guard mismatch: expected '{expectedGuard}', got '{parsed.IdentityGuard}'.");

        // Source is HoeDirt (CropProjector.Project signature takes HoeDirt)
        var source = new ResolvedRefSource(dirt, location, tile);
        return Result(RefIdStatus.Resolved, parsed, factKind, null, null, source);
    }

    private RefIdResolutionResult ResolveObject(
        GameLocation location, Vector2 tile, ParsedRefId parsed, string factKind)
    {
        if (!location.Objects.TryGetValue(tile, out var obj))
            return Result(RefIdStatus.Missing, parsed, factKind, null, "No object at tile.");

        var classified = EntityClassifier.ClassifyObject(obj);
        var expectedType = classified switch
        {
            WorldEntityType.Machine => "machine",
            WorldEntityType.Container => "container",
            WorldEntityType.ResourceNode => "resource_node",
            WorldEntityType.LooseItem => "loose_item",
            _ => "unknown",
        };

        if (expectedType != parsed.Type)
            return Result(RefIdStatus.StaleTypeMismatch, parsed, factKind, null,
                $"Expected type '{parsed.Type}', found '{expectedType}'.");

        var expectedGuard = RefIdTokenEncoder.EncodeQualifiedItemId(obj.QualifiedItemId);

        if (expectedGuard != parsed.IdentityGuard)
            return Result(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                $"Guard mismatch: expected '{expectedGuard}', got '{parsed.IdentityGuard}'.");

        var source = new ResolvedRefSource(obj, location, tile);
        return Result(RefIdStatus.Resolved, parsed, factKind, null, null, source);
    }

    private RefIdResolutionResult ResolveBed(
        GameLocation location, Vector2 tile, ParsedRefId parsed, string factKind)
    {
        foreach (var furniture in location.furniture)
        {
            if ((int)furniture.TileLocation.X == (int)tile.X &&
                (int)furniture.TileLocation.Y == (int)tile.Y)
            {
                if (furniture is not BedFurniture bed)
                    return Result(RefIdStatus.StaleTypeMismatch, parsed, factKind, null,
                        "Furniture at tile is not a bed.");

                var expectedGuard = RefIdTokenEncoder.EncodeQualifiedItemId(furniture.QualifiedItemId);
                if (expectedGuard != parsed.IdentityGuard)
                    return Result(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                        $"Guard mismatch: expected '{expectedGuard}', got '{parsed.IdentityGuard}'.");

                var source = new ResolvedRefSource(bed, location, tile);
                return Result(RefIdStatus.Resolved, parsed, factKind, null, null, source);
            }
        }

        return Result(RefIdStatus.Missing, parsed, factKind, null, "No furniture at tile.");
    }

    private RefIdResolutionResult ResolveFurniture(
        GameLocation location, Vector2 tile, ParsedRefId parsed, string factKind)
    {
        foreach (var furniture in location.furniture)
        {
            if ((int)furniture.TileLocation.X == (int)tile.X &&
                (int)furniture.TileLocation.Y == (int)tile.Y)
            {
                // Exclude BedFurniture (handled by "bed" type)
                if (furniture is BedFurniture)
                    return Result(RefIdStatus.StaleTypeMismatch, parsed, factKind, null,
                        "Furniture at tile is a bed; use type 'bed' instead.");

                var expectedGuard = RefIdTokenEncoder.EncodeQualifiedItemId(furniture.QualifiedItemId);
                if (expectedGuard != parsed.IdentityGuard)
                    return Result(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                        $"Guard mismatch: expected '{expectedGuard}', got '{parsed.IdentityGuard}'.");

                var source = new ResolvedRefSource(furniture, location, tile);
                return Result(RefIdStatus.Resolved, parsed, factKind, null, null, source);
            }
        }

        return Result(RefIdStatus.Missing, parsed, factKind, null, "No furniture at tile.");
    }

    private RefIdResolutionResult ResolveResourceClump(
        GameLocation location, Vector2 tile, ParsedRefId parsed, string factKind)
    {
        var targetX = (int)tile.X;
        var targetY = (int)tile.Y;

        foreach (var clump in location.resourceClumps)
        {
            // clump.Tile is the origin tile of the clump
            if ((int)clump.Tile.X == targetX && (int)clump.Tile.Y == targetY)
            {
                var clumpKind = ResourceClumpProjector.ClassifyClumpKind(clump.parentSheetIndex.Value);
                var expectedGuard = RefIdTokenEncoder.EncodeProtocolKey(clumpKind);
                if (expectedGuard != parsed.IdentityGuard)
                    return Result(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                        $"Guard mismatch: expected '{expectedGuard}', got '{parsed.IdentityGuard}'.");

                var source = new ResolvedRefSource(clump, location, tile);
                return Result(RefIdStatus.Resolved, parsed, factKind, null, null, source);
            }
        }

        return Result(RefIdStatus.Missing, parsed, factKind, null, "No resource clump at tile.");
    }

    private RefIdResolutionResult ResolveDoor(
        GameLocation location, Vector2 tile, ParsedRefId parsed, string factKind)
    {
        var point = new Point((int)tile.X, (int)tile.Y);

        if (!location.doors.TryGetValue(point, out var targetMap))
            return Result(RefIdStatus.Missing, parsed, factKind, null, "No door at tile.");

        var expectedGuard = RefIdTokenEncoder.EncodeMapToken(targetMap);
        if (expectedGuard != parsed.IdentityGuard)
            return Result(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                $"Guard mismatch: expected '{expectedGuard}', got '{parsed.IdentityGuard}'.");

        var source = new ResolvedRefSource(new DoorResolvedData(point, targetMap), location, tile);
        return Result(RefIdStatus.Resolved, parsed, factKind, null, null, source);
    }

    private RefIdResolutionResult ResolveWarp(
        GameLocation location, Vector2 tile, ParsedRefId parsed, string factKind)
    {
        var targetX = (int)tile.X;
        var targetY = (int)tile.Y;

        var warp = location.warps.FirstOrDefault(w => w.X == targetX && w.Y == targetY);
        if (warp == null)
            return Result(RefIdStatus.Missing, parsed, factKind, null, "No warp at tile.");

        var expectedGuard = RefIdTokenEncoder.EncodeMapToken(warp.TargetName);
        if (expectedGuard != parsed.IdentityGuard)
            return Result(RefIdStatus.StaleGuardMismatch, parsed, factKind, null,
                $"Guard mismatch: expected '{expectedGuard}', got '{parsed.IdentityGuard}'.");

        var source = new ResolvedRefSource(warp, location, tile);
        return Result(RefIdStatus.Resolved, parsed, factKind, null, null, source);
    }

    private static RefIdResolutionResult Result(
        RefIdStatus status, ParsedRefId parsed, string factKind, string? summary, string? debugReason,
        ResolvedRefSource? source = null)
    {
        return new RefIdResolutionResult(status, parsed, factKind, summary, debugReason, source);
    }

    private static RefIdResolutionResult Malformed(ParsedRefId parsed, string factKind, string reason)
    {
        return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsed, factKind, null, reason);
    }
}
