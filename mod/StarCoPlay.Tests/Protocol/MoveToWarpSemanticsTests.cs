using System.Collections.Generic;
using NUnit.Framework;
using StarCoPlay.ProtocolCore.Handlers;

namespace StarCoPlay.Tests.Protocol;

[TestFixture]
public sealed class MoveToWarpSemanticsTests
{
    [Test]
    public void ExactTargetRecognizesWalkThroughWarpTile()
    {
        var map = new MapEntry
        {
            Exits = new List<MapExit>
            {
                new()
                {
                    Kind = WarpKind.WalkThrough,
                    WarpTiles = new[] { new TilePoint(3, 12) },
                },
            },
        };

        Assert.That(MoveToProtoHandler.IsWalkThroughWarpTile(map, 3, 12), Is.True);
        Assert.That(MoveToProtoHandler.IsWalkThroughWarpTile(map, 3, 11), Is.False);
    }

    [Test]
    public void ExactTargetDoesNotTreatInteractDoorAsWalkThroughWarp()
    {
        var map = new MapEntry
        {
            Exits = new List<MapExit>
            {
                new()
                {
                    Kind = WarpKind.InteractDoor,
                    DoorTiles = new[] { new TilePoint(3, 12) },
                },
            },
        };

        Assert.That(MoveToProtoHandler.IsWalkThroughWarpTile(map, 3, 12), Is.False);
        Assert.That(MoveToProtoHandler.IsWalkThroughWarpTile(null, 3, 12), Is.False);
    }
}
