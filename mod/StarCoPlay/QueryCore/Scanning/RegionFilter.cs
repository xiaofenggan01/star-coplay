using System;
using StardewValley;
using StarCoPlay.Protocol.V245;

namespace StarCoPlay.V2.Query.Scanning;

public static class RegionFilter
{
    /// <summary>
    /// Returns true if (tileX, tileY) falls within the requested region.
    /// Priority: Rectangle region > Center+Radius > player tile + radius/default radius.
    /// </summary>
    public static bool Contains(QueryWorldRegionParams request, int tileX, int tileY)
    {
        if (request.Region is { Width: > 0 } rect)
        {
            return tileX >= rect.X
                && tileX < rect.X + rect.Width
                && tileY >= rect.Y
                && tileY < rect.Y + rect.Height;
        }

        if (request.Center is not null && request.Radius > 0)
        {
            int dx = Math.Abs(tileX - request.Center.X);
            int dy = Math.Abs(tileY - request.Center.Y);
            return Math.Max(dx, dy) <= request.Radius; // Chebyshev distance
        }

        if (Game1.player != null)
        {
            int radius = request.Radius > 0 ? request.Radius : 8;
            int dx = Math.Abs(tileX - (int)Game1.player.Tile.X);
            int dy = Math.Abs(tileY - (int)Game1.player.Tile.Y);
            return Math.Max(dx, dy) <= radius; // Chebyshev distance
        }

        return true; // no player loaded yet — accept all rather than hiding facts
    }

    /// <summary>
    /// Returns true if entityType should be included based on WorldEntityTypes filter.
    /// Empty filter means accept all.
    /// </summary>
    public static bool IsEntityTypeIncluded(QueryWorldRegionParams request, string entityType)
    {
        if (request.WorldEntityTypes.Count == 0)
            return true;

        return request.WorldEntityTypes.Contains(entityType);
    }
}
