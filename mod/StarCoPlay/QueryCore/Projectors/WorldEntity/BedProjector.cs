using Microsoft.Xna.Framework;
using StardewValley.Objects;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;
using ProtoTilePoint = StarCoPlay.Protocol.V245.TilePoint;

namespace StarCoPlay.V2.Query.Projectors.WorldEntity;

public static class BedProjector
{
    public static WorldEntityFact Project(BedFurniture bed, string mapNameOrUniqueName, int tileX, int tileY)
    {
        var qualifiedItemId = bed.QualifiedItemId ?? "";
        var bedType = bed.bedType.ToString();
        var rotation = bed.currentRotation.Value;

        var refId = RefIdBuilder.BuildBed(mapNameOrUniqueName, tileX, tileY, qualifiedItemId);

        var position = new TilePosition
        {
            MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
            X = tileX,
            Y = tileY,
        };

        var bedSpot = bed.GetBedSpot();

        var bedFact = new BedFact
        {
            QualifiedItemId  = qualifiedItemId,
            BedType          = bedType,
            CanSleep         = bed.bedType != BedFurniture.BedType.Child,
            IsBeingSleptIn   = bed.IsBeingSleptIn(),
            DisplayName      = bed.DisplayName ?? "",
            Rotation         = rotation,
            BedSpot          = new ProtoTilePoint { X = bedSpot.X, Y = bedSpot.Y },
        };

        var boundingBox = bed.GetBoundingBox();
        var tileWidth = boundingBox.Width / 64;
        var tileHeight = boundingBox.Height / 64;
        for (var dx = 0; dx < tileWidth; dx++)
        {
            for (var dy = 0; dy < tileHeight; dy++)
            {
                bedFact.BoundingTiles.Add(new TilePosition
                {
                    MapId = RefIdTokenEncoder.EncodeMapToken(mapNameOrUniqueName),
                    X = tileX + dx,
                    Y = tileY + dy,
                });
            }
        }

        var summary = $"Bed({bedType}) at ({tileX},{tileY})";

        return new WorldEntityFact
        {
            Ref = refId,
            Type = "bed",
            FactKind = "bed",
            Position = position,
            Summary = summary,
            Bed = bedFact,
        };
    }
}
