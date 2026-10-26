using StardewValley.Objects;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.WorldEntity;

public static class FurnitureProjector
{
    public static WorldEntityFact Project(Furniture furniture, string mapNameOrUniqueName, int tileX, int tileY)
    {
        var qualifiedItemId = furniture.QualifiedItemId ?? "";
        var rotation = furniture.currentRotation.Value;
        var canSit = furniture.GetSeatPositions().Count > 0;

        var refId = RefIdBuilder.BuildFurniture(mapNameOrUniqueName, tileX, tileY, qualifiedItemId);

        var position = new TilePosition
        {
            MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
            X = tileX,
            Y = tileY,
        };

        var furnitureFact = new FurnitureFact
        {
            QualifiedItemId = qualifiedItemId,
            FurnitureType   = MapFurnitureType(furniture.furniture_type.Value),
            Rotation        = rotation,
            CanSit          = canSit,
            DisplayName     = furniture.DisplayName ?? "",
            InternalName    = furniture.Name ?? "",
            IsOn            = furniture.isOn.Value,
            MaxRotations    = furniture.rotations.Value,
        };

        var boundingBox = furniture.GetBoundingBox();
        var tileWidth = boundingBox.Width / 64;
        var tileHeight = boundingBox.Height / 64;
        for (var dx = 0; dx < tileWidth; dx++)
        {
            for (var dy = 0; dy < tileHeight; dy++)
            {
                furnitureFact.BoundingTiles.Add(new TilePosition
                {
                    MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
                    X = tileX + dx,
                    Y = tileY + dy,
                });
            }
        }

        var displayName = furniture.DisplayName ?? qualifiedItemId;
        var parts = new System.Collections.Generic.List<string>();
        if (canSit) parts.Add("sittable");
        if (rotation > 0) parts.Add($"rot={rotation}");

        var suffix = parts.Count > 0 ? $" ({string.Join(", ", parts)})" : "";
        var summary = $"{displayName}{suffix}";

        return new WorldEntityFact
        {
            Ref = refId,
            Type = "furniture",
            FactKind = "furniture",
            Position = position,
            Summary = summary,
            Furniture = furnitureFact,
        };
    }

    private static string MapFurnitureType(int typeInt) => typeInt switch
    {
        0  => "chair",
        1  => "bench",
        2  => "couch",
        3  => "armchair",
        4  => "dresser",
        5  => "long_table",
        6  => "painting",
        7  => "lamp",
        8  => "decor",
        10 => "bookcase",
        11 => "table",
        12 => "rug",
        13 => "window",
        14 => "fireplace",
        15 => "bed",
        16 => "torch",
        17 => "sconce",
        _  => "other",
    };
}
