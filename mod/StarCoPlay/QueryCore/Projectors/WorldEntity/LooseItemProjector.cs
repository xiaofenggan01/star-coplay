using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;
using SObject = StardewValley.Object;

namespace StarCoPlay.V2.Query.Projectors.WorldEntity;

public static class LooseItemProjector
{
    public static WorldEntityFact Project(SObject obj, string mapNameOrUniqueName, int tileX, int tileY)
    {
        var qualifiedItemId = obj.QualifiedItemId ?? "";
        var itemId = obj.ItemId ?? "";
        var internalName = obj.Name ?? "";
        var displayName = obj.DisplayName ?? "";
        var stack = obj.Stack;
        var quality = obj.Quality;
        var canBeGrabbed = obj.CanBeGrabbed;
        var isSpawned = obj.IsSpawnedObject;

        var refId = RefIdBuilder.BuildLooseItem(mapNameOrUniqueName, tileX, tileY, qualifiedItemId);

        var position = new TilePosition
        {
            MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
            X = tileX,
            Y = tileY,
        };

        var itemFact = new LooseItemFact
        {
            QualifiedItemId = qualifiedItemId,
            ItemId          = itemId,
            InternalName    = internalName,
            DisplayName     = displayName,
            Stack           = stack,
            Quality         = quality,
            CanBeGrabbed    = canBeGrabbed,
            IsSpawnedObject = isSpawned,
            Category        = obj.Category,
        };

        var parts = new System.Collections.Generic.List<string>();
        if (stack > 1) parts.Add($"x{stack}");
        if (quality > 0) parts.Add($"q{quality}");
        if (canBeGrabbed) parts.Add("grabbable");

        var suffix = parts.Count > 0 ? $" ({string.Join(", ", parts)})" : "";
        var summary = $"{displayName}{suffix}";

        return new WorldEntityFact
        {
            Ref = refId,
            Type = "loose_item",
            FactKind = "loose_item",
            Position = position,
            Summary = summary,
            LooseItem = itemFact,
        };
    }
}
