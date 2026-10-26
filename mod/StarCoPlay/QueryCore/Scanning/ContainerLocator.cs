using Microsoft.Xna.Framework;
using StardewValley;
using StardewValley.Locations;
using StardewValley.Objects;

namespace StarCoPlay.V2.Query.Scanning;

public static class ContainerLocator
{
    /// <summary>
    /// Resolve a container by kind and locator string.
    /// For "chest": locator = "map_token:x:y"
    /// For "fridge": locator = "map_token"
    /// </summary>
    public static Chest? Resolve(string containerKind, string containerLocator)
    {
        if (string.IsNullOrEmpty(containerLocator))
            return null;

        return containerKind switch
        {
            "chest" => ResolveChest(containerLocator),
            "fridge" => ResolveFridge(containerLocator),
            _ => null,
        };
    }

    private static Chest? ResolveChest(string locator)
    {
        // Format: "map_token:x:y"
        var parts = locator.Split(':');
        if (parts.Length < 3)
            return null;

        // Rejoin all but last two parts as map token (in case map token itself contains colons)
        var mapToken = string.Join(":", parts, 0, parts.Length - 2);
        if (!int.TryParse(parts[^2], out int x) || !int.TryParse(parts[^1], out int y))
            return null;

        var location = LocationScanner.ResolveMapToken(mapToken);
        if (location == null)
            return null;

        var tile = new Vector2(x, y);
        if (location.Objects.TryGetValue(tile, out var obj) && obj is Chest chest)
            return chest;

        return null;
    }

    private static Chest? ResolveFridge(string locator)
    {
        // Format: "map_token"
        var location = LocationScanner.ResolveMapToken(locator);
        if (location == null)
            return null;

        return location switch
        {
            FarmHouse farmHouse => farmHouse.fridge.Value,
            IslandFarmHouse islandFarmHouse => islandFarmHouse.fridge.Value,
            _ => null,
        };
    }
}
