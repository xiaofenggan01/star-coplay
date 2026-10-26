using Microsoft.Xna.Framework;
using StardewValley;
using StardewValley.Locations;
using StardewValley.Objects;
using StarCoPlay.V2.Query.Scanning;
using StarCoPlay.V2.RefId.Encoding;
using StarCoPlay.V2.RefId.Protocol;

namespace StarCoPlay.V2.RefId.Resolution;

public sealed class InventoryResolver : IRefIdResolver
{
    public RefIdResolutionResult Resolve(ParsedRefId parsedRefId)
    {
        var factKind = RefIdProtocolMapper.InferFactKind(parsedRefId);

        if (parsedRefId.Type != "item")
            return new RefIdResolutionResult(RefIdStatus.Unsupported, parsedRefId, factKind,
                $"Inventory type '{parsedRefId.Type}' not supported.", null);

        if (parsedRefId.LocatorParts.Count < 2)
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsedRefId, factKind,
                null, "Inventory item locator must have at least 2 parts.");

        var owner = parsedRefId.LocatorParts[0];

        return owner switch
        {
            "player" => ResolvePlayerItem(parsedRefId, factKind),
            "chest" => ResolveChestItem(parsedRefId, factKind),
            "fridge" => ResolveFridgeItem(parsedRefId, factKind),
            _ => new RefIdResolutionResult(RefIdStatus.Unsupported, parsedRefId, factKind,
                $"Owner '{owner}' not supported.", null),
        };
    }

    /// <summary>Locator: [player, slot]</summary>
    private static RefIdResolutionResult ResolvePlayerItem(ParsedRefId parsedRefId, string factKind)
    {
        if (parsedRefId.LocatorParts.Count != 2)
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsedRefId, factKind,
                null, "Player inventory locator must have 2 parts: player, slot.");

        if (!int.TryParse(parsedRefId.LocatorParts[1], out var slot))
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsedRefId, factKind,
                null, "Slot must be a valid integer.");

        var player = Game1.player;
        if (player == null)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsedRefId, factKind,
                null, "Game not loaded.");

        if (slot < 0 || slot >= player.Items.Count)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsedRefId, factKind,
                null, "Slot index out of bounds.");

        var item = player.Items[slot];
        if (item == null)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsedRefId, factKind,
                null, "Slot is empty.");

        if (!CheckGuard(parsedRefId, item, factKind, out var mismatch))
            return mismatch!;

        var source = new ResolvedRefSource(item, ContainerKind: "player", ContainerLocator: "local", InventorySlot: slot);
        return new RefIdResolutionResult(RefIdStatus.Resolved, parsedRefId, factKind, null, null, source);
    }

    /// <summary>Locator: [chest, map_token, x, y, slot]</summary>
    private static RefIdResolutionResult ResolveChestItem(ParsedRefId parsedRefId, string factKind)
    {
        if (parsedRefId.LocatorParts.Count != 5)
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsedRefId, factKind,
                null, "Chest inventory locator must have 5 parts: chest, map_token, x, y, slot.");

        var mapToken = parsedRefId.LocatorParts[1];
        if (!int.TryParse(parsedRefId.LocatorParts[2], out var x) ||
            !int.TryParse(parsedRefId.LocatorParts[3], out var y))
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsedRefId, factKind,
                null, "Chest tile coordinates must be valid integers.");

        if (!int.TryParse(parsedRefId.LocatorParts[4], out var slot))
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsedRefId, factKind,
                null, "Slot must be a valid integer.");

        var location = LocationScanner.ResolveMapToken(mapToken);
        if (location == null)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsedRefId, factKind,
                null, $"Location '{mapToken}' not found.");

        var tile = new Vector2(x, y);
        if (!location.Objects.TryGetValue(tile, out var obj) || obj is not Chest chest)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsedRefId, factKind,
                null, $"No chest at {mapToken}:{x},{y}.");

        var items = chest.GetItemsForPlayer();
        if (slot < 0 || slot >= items.Count)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsedRefId, factKind,
                null, "Slot index out of bounds.");

        var item = items[slot];
        if (item == null)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsedRefId, factKind,
                null, "Slot is empty.");

        if (!CheckGuard(parsedRefId, item, factKind, out var mismatch))
            return mismatch!;

        var containerLocator = $"{mapToken}:{x}:{y}";
        var source = new ResolvedRefSource(item, location, tile, ContainerKind: "chest", ContainerLocator: containerLocator, InventorySlot: slot);
        return new RefIdResolutionResult(RefIdStatus.Resolved, parsedRefId, factKind, null, null, source);
    }

    /// <summary>Locator: [fridge, map_token, slot]</summary>
    private static RefIdResolutionResult ResolveFridgeItem(ParsedRefId parsedRefId, string factKind)
    {
        if (parsedRefId.LocatorParts.Count != 3)
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsedRefId, factKind,
                null, "Fridge inventory locator must have 3 parts: fridge, map_token, slot.");

        var mapToken = parsedRefId.LocatorParts[1];
        if (!int.TryParse(parsedRefId.LocatorParts[2], out var slot))
            return new RefIdResolutionResult(RefIdStatus.MalformedRef, parsedRefId, factKind,
                null, "Slot must be a valid integer.");

        var location = LocationScanner.ResolveMapToken(mapToken);
        if (location == null)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsedRefId, factKind,
                null, $"Location '{mapToken}' not found.");

        var fridge = location switch
        {
            FarmHouse farmHouse => farmHouse.fridge.Value,
            IslandFarmHouse islandFarmHouse => islandFarmHouse.fridge.Value,
            _ => null,
        };

        if (fridge == null)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsedRefId, factKind,
                null, $"No fridge in location '{mapToken}'.");

        var items = fridge.GetItemsForPlayer();
        if (slot < 0 || slot >= items.Count)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsedRefId, factKind,
                null, "Slot index out of bounds.");

        var item = items[slot];
        if (item == null)
            return new RefIdResolutionResult(RefIdStatus.Missing, parsedRefId, factKind,
                null, "Slot is empty.");

        if (!CheckGuard(parsedRefId, item, factKind, out var mismatch))
            return mismatch!;

        var source = new ResolvedRefSource(item, location, ContainerKind: "fridge", ContainerLocator: mapToken, InventorySlot: slot);
        return new RefIdResolutionResult(RefIdStatus.Resolved, parsedRefId, factKind, null, null, source);
    }

    private static bool CheckGuard(ParsedRefId parsedRefId, Item item, string factKind, out RefIdResolutionResult? mismatch)
    {
        var expectedGuard = RefIdTokenEncoder.EncodeQualifiedItemId(item.QualifiedItemId);
        if (expectedGuard != parsedRefId.IdentityGuard)
        {
            mismatch = new RefIdResolutionResult(RefIdStatus.StaleGuardMismatch, parsedRefId, factKind, null,
                $"Guard mismatch: expected '{expectedGuard}', got '{parsedRefId.IdentityGuard}'.");
            return false;
        }

        mismatch = null;
        return true;
    }
}
