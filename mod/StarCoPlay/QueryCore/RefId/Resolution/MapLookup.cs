using StardewValley;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.RefId.Resolution;

public static class MapLookup
{
    public static GameLocation? Find(string mapToken)
    {
        if (string.IsNullOrEmpty(mapToken))
            return null;

        var current = Game1.player?.currentLocation;
        if (TokenMatches(current, mapToken))
            return current;

        foreach (var loc in Game1.locations)
        {
            if (TokenMatches(loc, mapToken))
                return loc;
        }

        return null;
    }

    private static bool TokenMatches(GameLocation? location, string mapToken)
    {
        if (location == null)
            return false;

        return string.Equals(
            RefIdTokenEncoder.EncodeMapToken(location.NameOrUniqueName),
            mapToken,
            System.StringComparison.OrdinalIgnoreCase);
    }
}
