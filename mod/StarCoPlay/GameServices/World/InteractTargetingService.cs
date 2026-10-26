using System;
using Microsoft.Xna.Framework;
using StardewValley;

namespace StarCoPlay.GameServices.World;

public class InteractTargetingService
{
    internal static Vector2 ComputeGrabTile(Rectangle bbox, int facingDirection)
    {
        return facingDirection switch
        {
            0 => new Vector2((bbox.X + bbox.Width / 2) / 64, (bbox.Y - 5) / 64),
            1 => new Vector2((bbox.X + bbox.Width + 5) / 64, (bbox.Y + bbox.Height / 2) / 64),
            2 => new Vector2((bbox.X + bbox.Width / 2) / 64, (bbox.Y + bbox.Height + 5) / 64),
            3 => new Vector2((bbox.X - 5) / 64, (bbox.Y + bbox.Height / 2) / 64),
            _ => Vector2.Zero
        };
    }

    public bool IsGrabTileAligned(Vector2 targetTile)
    {
        var grab = Game1.player.GetGrabTile();
        return (int)grab.X == (int)targetTile.X && (int)grab.Y == (int)targetTile.Y;
    }

    public static int ComputeFacingDirection(Vector2 playerTile, Vector2 targetTile)
    {
        int dx = (int)targetTile.X - (int)playerTile.X;
        int dy = (int)targetTile.Y - (int)playerTile.Y;

        if (Math.Abs(dx) + Math.Abs(dy) != 1)
            return -1;

        if (dy == -1) return 0;
        if (dx == 1) return 1;
        if (dy == 1) return 2;
        return 3;
    }

    public Func<bool> CreateAlignmentStopCondition(Vector2 targetTile)
    {
        int tx = (int)targetTile.X;
        int ty = (int)targetTile.Y;
        return () =>
        {
            var grab = Game1.player.GetGrabTile();
            return (int)grab.X == tx && (int)grab.Y == ty;
        };
    }

    public static Func<bool> CreateTileBoundaryCondition(Vector2 startTile)
    {
        int sx = (int)startTile.X;
        int sy = (int)startTile.Y;
        return () =>
        {
            var tile = Game1.player.Tile;
            return (int)tile.X != sx || (int)tile.Y != sy;
        };
    }
}
