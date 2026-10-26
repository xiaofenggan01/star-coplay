using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Buildings;
using StardewValley.Locations;
using StardewValley.Objects;

namespace StarCoPlay.GameServices.Inventory;

// ── Data Models ─────────────────────────────────────────────────────────────

/// <summary>
/// Describes a discovered container with its location, type, and capacity info.
/// </summary>
public class ContainerInfo
{
    public string Id { get; set; } = "";
    public string Type { get; set; } = "";
    public string Location { get; set; } = "";
    public int TileX { get; set; }
    public int TileY { get; set; }
    public string DisplayName { get; set; } = "";
    public int ItemCount { get; set; }
    public int Capacity { get; set; }
    public bool CanTakeItems { get; set; }
    public bool CanStoreItems { get; set; }
    public string? PlayerColor { get; set; }
}

/// <summary>
/// Describes a single item inside a container.
/// </summary>
public class ContainerItemInfo
{
    public string Name { get; set; } = "";
    public string ItemId { get; set; } = "";
    public int Stack { get; set; }
    public int Quality { get; set; }
    public string Category { get; set; } = "";
    public int SellPrice { get; set; }
}

/// <summary>
/// Result of a take/store operation on a container.
/// </summary>
public class ContainerOpResult
{
    public bool Success { get; set; }
    public string? ErrorCode { get; set; }
    public int Transferred { get; set; }
    public int RemainingCount { get; set; }

    public static ContainerOpResult Ok(int transferred, int remaining)
    {
        return new ContainerOpResult
        {
            Success = true,
            Transferred = transferred,
            RemainingCount = remaining,
        };
    }

    public static ContainerOpResult Fail(string errorCode)
    {
        return new ContainerOpResult
        {
            Success = false,
            ErrorCode = errorCode,
        };
    }
}

// ── Container Manager ───────────────────────────────────────────────────────

/// <summary>
/// Core container management: discovery, resolution, take/store operations.
/// Phase 1 supports Chest, Fridge, and ShippingBin.
/// </summary>
public class ContainerManager
{
    private readonly IMonitor? _monitor;

    public ContainerManager(IMonitor? monitor = null)
    {
        _monitor = monitor;
    }

    private void Log(string message, LogLevel level = LogLevel.Debug)
    {
        _monitor?.Log(message, level);
    }

    // ── Discovery ───────────────────────────────────────────────────────

    /// <summary>
    /// Enumerate all containers in a specific location.
    /// </summary>
    public List<ContainerInfo> DiscoverContainers(GameLocation location)
    {
        var containers = new List<ContainerInfo>();
        var locationName = location.NameOrUniqueName;

        // 1. Iterate placed objects — find Chest instances
        foreach (var pair in location.objects.Pairs)
        {
            if (pair.Value is not Chest chest)
                continue;

            var tileX = (int)pair.Key.X;
            var tileY = (int)pair.Key.Y;

            string type;
            if (chest.SpecialChestType == Chest.SpecialChestTypes.MiniShippingBin)
            {
                type = "mini_shipping_bin";
            }
            else if (chest.fridge.Value)
            {
                type = "fridge"; // Mini-Fridge placed as object
            }
            else
            {
                type = "chest";
            }

            var id = $"{locationName}:{type}_{tileX}_{tileY}";
            var info = BuildChestInfo(id, type, locationName, tileX, tileY, chest);
            containers.Add(info);
        }

        // 2. Built-in fridge for FarmHouse / IslandFarmHouse
        if (location is FarmHouse farmHouse && farmHouse.fridge.Value is Chest fridgeChest)
        {
            var fridgePos = farmHouse.fridgePosition;
            var id = $"{locationName}:fridge_{fridgePos.X}_{fridgePos.Y}";
            var info = BuildChestInfo(id, "fridge", locationName, fridgePos.X, fridgePos.Y, fridgeChest);
            containers.Add(info);
        }

        // 3. Shipping bin on Farm
        if (location is Farm)
        {
            var binX = 71;
            var binY = 14;
            var id = $"{locationName}:shipping_bin_{binX}_{binY}";
            containers.Add(new ContainerInfo
            {
                Id = id,
                Type = "shipping_bin",
                Location = locationName,
                TileX = binX,
                TileY = binY,
                DisplayName = "Shipping Bin",
                ItemCount = Game1.getFarm().getShippingBin(Game1.player).Count,
                Capacity = -1,
                CanTakeItems = false,
                CanStoreItems = true,
            });
        }

        return containers;
    }

    /// <summary>
    /// Discover containers across all game locations, including building interiors.
    /// </summary>
    public List<ContainerInfo> GetAllContainers()
    {
        var all = new List<ContainerInfo>();

        foreach (var location in Game1.locations)
        {
            all.AddRange(DiscoverContainers(location));

            if (location.buildings == null)
                continue;

            foreach (var building in location.buildings)
            {
                if (building.indoors.Value is GameLocation indoors)
                {
                    all.AddRange(DiscoverContainers(indoors));
                }
            }
        }

        Log($"[ContainerManager] Discovered {all.Count} containers across all locations.", LogLevel.Trace);
        return all;
    }

    // ── Resolution ──────────────────────────────────────────────────────

    /// <summary>
    /// Resolve a container ID to its inventory, chest reference, and location.
    /// Returns (inventory, chest, location, error). On failure only error is set.
    /// </summary>
    public (IList<Item>? inventory, Chest? chest, GameLocation? location, string? error) ResolveContainer(string containerId)
    {
        // Parse format: Location:type_x_y
        var colonIdx = containerId.IndexOf(':');
        if (colonIdx < 0)
        {
            return (null, null, null, "INVALID_CONTAINER_ID");
        }

        var locationName = containerId[..colonIdx];
        var descriptor = containerId[(colonIdx + 1)..];

        // Find location (including building interiors)
        var location = Game1.locations
            .SelectMany(loc => new[] { loc }
                .Concat(loc.buildings?
                    .Where(b => b.indoors.Value != null)
                    .Select(b => b.indoors.Value!) ?? Enumerable.Empty<GameLocation>()))
            .FirstOrDefault(loc => string.Equals(loc.NameOrUniqueName, locationName, StringComparison.OrdinalIgnoreCase)
                                || string.Equals(loc.Name, locationName, StringComparison.OrdinalIgnoreCase));

        if (location == null)
        {
            return (null, null, null, "LOCATION_NOT_FOUND");
        }

        // Parse descriptor: split from the right to extract _y and _x, remainder is type
        if (!TryParseDescriptor(descriptor, out var type, out var tileX, out var tileY))
        {
            return (null, null, null, "INVALID_CONTAINER_ID");
        }

        // Resolve by type
        switch (type)
        {
            case "fridge":
            {
                if (location is FarmHouse farmHouse && farmHouse.fridge.Value is Chest fridge)
                {
                    return (fridge.Items, fridge, location, null);
                }

                // Could be a Mini-Fridge placed as object
                var tile = new Vector2(tileX, tileY);
                if (location.objects.TryGetValue(tile, out var obj) && obj is Chest miniFridge && miniFridge.fridge.Value)
                {
                    return (miniFridge.Items, miniFridge, location, null);
                }

                return (null, null, location, "CONTAINER_NOT_FOUND");
            }

            case "shipping_bin":
            {
                if (location is Farm farm)
                {
                    var bin = farm.getShippingBin(Game1.player);
                    return (bin, null, location, null);
                }

                return (null, null, location, "CONTAINER_NOT_FOUND");
            }

            case "mini_shipping_bin":
            case "chest":
            {
                var tile = new Vector2(tileX, tileY);
                if (location.objects.TryGetValue(tile, out var obj) && obj is Chest chest)
                {
                    return (chest.Items, chest, location, null);
                }

                return (null, null, location, "CONTAINER_NOT_FOUND");
            }

            default:
                return (null, null, location, "INVALID_CONTAINER_ID");
        }
    }

    // ── Take / Store Operations ─────────────────────────────────────────

    /// <summary>
    /// Take items from a container into the player's inventory.
    /// </summary>
    public ContainerOpResult TakeItem(string containerId, string itemName, int count)
    {
        var (inventory, chest, location, error) = ResolveContainer(containerId);
        if (error != null)
        {
            Log($"[ContainerManager] TakeItem failed: {error} for {containerId}", LogLevel.Warn);
            return ContainerOpResult.Fail(error);
        }

        if (inventory == null)
        {
            return ContainerOpResult.Fail("CONTAINER_NOT_FOUND");
        }

        // Shipping bin does not allow taking
        if (chest == null && containerId.Contains(":shipping_bin_"))
        {
            return ContainerOpResult.Fail("OPERATION_NOT_ALLOWED");
        }

        // Find matching item
        var sourceItem = inventory.FirstOrDefault(item =>
            item != null &&
            (item.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
             item.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)));

        if (sourceItem == null)
        {
            return ContainerOpResult.Fail("ITEM_NOT_FOUND");
        }

        var actualCount = (count <= 0) ? sourceItem.Stack : Math.Min(count, sourceItem.Stack);

        // Always create a copy for transfer — never hand the original to addItemToInventoryBool
        // which may mutate it. This ensures atomic rollback on failure.
        var toTake = sourceItem.getOne();
        toTake.Stack = actualCount;

        // Try adding to player inventory
        var added = Game1.player.addItemToInventoryBool(toTake);
        if (!added)
        {
            Log($"[ContainerManager] Player inventory full, cannot take {itemName}.", LogLevel.Warn);
            return ContainerOpResult.Fail("INVENTORY_FULL");
        }

        // Success — now remove from container (safe: toTake is a copy, no duplication)
        if (actualCount >= sourceItem.Stack)
        {
            var idx = inventory.IndexOf(sourceItem);
            if (idx >= 0) inventory[idx] = null!;
        }
        else
        {
            sourceItem.Stack -= actualCount;
        }

        var remaining = inventory.Where(i => i != null &&
            (i.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
             i.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)))
            .Sum(i => i.Stack);

        Log($"[ContainerManager] Took {actualCount}x {itemName} from {containerId}. Remaining: {remaining}.", LogLevel.Info);
        return ContainerOpResult.Ok(actualCount, remaining);
    }

    /// <summary>
    /// Store items from the player's inventory into a container.
    /// </summary>
    public ContainerOpResult StoreItem(string containerId, string itemName, int count)
    {
        var (inventory, chest, location, error) = ResolveContainer(containerId);
        if (error != null)
        {
            Log($"[ContainerManager] StoreItem failed: {error} for {containerId}", LogLevel.Warn);
            return ContainerOpResult.Fail(error);
        }

        if (inventory == null)
        {
            return ContainerOpResult.Fail("CONTAINER_NOT_FOUND");
        }

        // Find matching item in player inventory
        var playerItem = Game1.player.Items.FirstOrDefault(item =>
            item != null &&
            (item.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
             item.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)));

        if (playerItem == null)
        {
            return ContainerOpResult.Fail("ITEM_NOT_FOUND");
        }

        var actualCount = (count <= 0) ? playerItem.Stack : Math.Min(count, playerItem.Stack);

        // Always create a copy for transfer — ensures atomic rollback on failure
        var toStore = playerItem.getOne();
        toStore.Stack = actualCount;

        // Store into container
        bool stored;
        if (chest != null)
        {
            // Chest supports auto-stacking via addItem
            var leftover = chest.addItem(toStore);
            stored = leftover == null;
        }
        else
        {
            // Shipping bin — just add to the list
            inventory.Add(toStore);
            stored = true;
        }

        if (!stored)
        {
            Log($"[ContainerManager] Container full, cannot store {itemName}.", LogLevel.Warn);
            return ContainerOpResult.Fail("CONTAINER_FULL");
        }

        // Success — now remove from player inventory (safe: toStore is a copy)
        if (actualCount >= playerItem.Stack)
        {
            var idx = Game1.player.Items.IndexOf(playerItem);
            if (idx >= 0) Game1.player.Items[idx] = null!;
        }
        else
        {
            playerItem.Stack -= actualCount;
        }

        var remainingInPlayer = Game1.player.Items
            .Where(i => i != null &&
                (i.DisplayName.Equals(itemName, StringComparison.OrdinalIgnoreCase) ||
                 i.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)))
            .Sum(i => i.Stack);

        Log($"[ContainerManager] Stored {actualCount}x {itemName} into {containerId}. Player remaining: {remainingInPlayer}.", LogLevel.Info);
        return ContainerOpResult.Ok(actualCount, remainingInPlayer);
    }

    // ── Query ───────────────────────────────────────────────────────────

    /// <summary>
    /// Get the list of items in a container.
    /// </summary>
    public List<ContainerItemInfo>? GetContainerItems(string containerId)
    {
        var (inventory, _, _, error) = ResolveContainer(containerId);
        if (error != null || inventory == null)
        {
            Log($"[ContainerManager] GetContainerItems failed: {error ?? "null inventory"} for {containerId}", LogLevel.Warn);
            return null;
        }

        var items = new List<ContainerItemInfo>();
        foreach (var item in inventory)
        {
            if (item == null)
                continue;

            items.Add(new ContainerItemInfo
            {
                Name = item.DisplayName,
                ItemId = item.ItemId,
                Stack = item.Stack,
                Quality = item is StardewValley.Object obj ? obj.Quality : 0,
                Category = item.getCategoryName(),
                SellPrice = item is StardewValley.Object sellObj ? sellObj.sellToStorePrice() : 0,
            });
        }

        return items;
    }

    // ── Helpers ──────────────────────────────────────────────────────────

    private ContainerInfo BuildChestInfo(string id, string type, string locationName, int tileX, int tileY, Chest chest)
    {
        var isMiniShippingBin = chest.SpecialChestType == Chest.SpecialChestTypes.MiniShippingBin;

        string? colorHex = null;
        if (chest.playerChoiceColor.Value != Color.Black && chest.playerChoiceColor.Value != default)
        {
            var c = chest.playerChoiceColor.Value;
            colorHex = $"#{c.R:X2}{c.G:X2}{c.B:X2}";
        }

        return new ContainerInfo
        {
            Id = id,
            Type = type,
            Location = locationName,
            TileX = tileX,
            TileY = tileY,
            DisplayName = chest.DisplayName ?? type,
            ItemCount = chest.Items.Count(i => i != null),
            Capacity = chest.GetActualCapacity(),
            CanTakeItems = !isMiniShippingBin,
            CanStoreItems = true,
            PlayerColor = colorHex,
        };
    }

    /// <summary>
    /// Parse a descriptor like "chest_5_10" into (type, x, y).
    /// Splits from the right so type names with underscores are preserved.
    /// </summary>
    private static bool TryParseDescriptor(string descriptor, out string type, out int tileX, out int tileY)
    {
        type = "";
        tileX = 0;
        tileY = 0;

        // Find last underscore → tileY
        var lastUnderscore = descriptor.LastIndexOf('_');
        if (lastUnderscore < 0)
            return false;

        if (!int.TryParse(descriptor[(lastUnderscore + 1)..], out tileY))
            return false;

        // Find second-to-last underscore → tileX
        var secondLast = descriptor.LastIndexOf('_', lastUnderscore - 1);
        if (secondLast < 0)
            return false;

        if (!int.TryParse(descriptor[(secondLast + 1)..lastUnderscore], out tileX))
            return false;

        type = descriptor[..secondLast];
        return type.Length > 0;
    }
}
