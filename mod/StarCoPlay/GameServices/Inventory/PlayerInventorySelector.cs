using System;
using StardewValley;
using StardewValley.Tools;
using StarCoPlay.Protocol.V245;

namespace StarCoPlay.GameServices.Inventory;

public static class PlayerInventorySelector
{
    public static Item? GetItemAtSlot(int zeroBasedSlot)
    {
        var items = Game1.player.Items;
        if (zeroBasedSlot < 0 || zeroBasedSlot >= items.Count)
            return null;
        return items[zeroBasedSlot];
    }

    public static InventorySelectionResult? FindByName(string itemName, bool toolbarFirst = true)
    {
        var items = Game1.player.Items;
        int count = items.Count;

        if (toolbarFirst)
        {
            int toolbarEnd = Math.Min(count, 12);
            for (int i = 0; i < toolbarEnd; i++)
            {
                if (items[i] != null && MatchesName(items[i], itemName))
                    return new InventorySelectionResult { Slot = i, Item = items[i] };
            }
            for (int i = 12; i < count; i++)
            {
                if (items[i] != null && MatchesName(items[i], itemName))
                    return new InventorySelectionResult { Slot = i, Item = items[i] };
            }
        }
        else
        {
            for (int i = 0; i < count; i++)
            {
                if (items[i] != null && MatchesName(items[i], itemName))
                    return new InventorySelectionResult { Slot = i, Item = items[i] };
            }
        }

        return null;
    }

    public static InventorySelectionResult? FindByCategory(ItemCategory category, bool toolbarFirst = true)
    {
        var items = Game1.player.Items;
        int count = items.Count;

        if (toolbarFirst)
        {
            int toolbarEnd = Math.Min(count, 12);
            for (int i = 0; i < toolbarEnd; i++)
            {
                if (items[i] != null && MatchesCategory(items[i], category))
                    return new InventorySelectionResult { Slot = i, Item = items[i] };
            }
            for (int i = 12; i < count; i++)
            {
                if (items[i] != null && MatchesCategory(items[i], category))
                    return new InventorySelectionResult { Slot = i, Item = items[i] };
            }
        }
        else
        {
            for (int i = 0; i < count; i++)
            {
                if (items[i] != null && MatchesCategory(items[i], category))
                    return new InventorySelectionResult { Slot = i, Item = items[i] };
            }
        }

        return null;
    }

    public static bool MatchesName(Item item, string itemName)
    {
        return item.DisplayName.IndexOf(itemName, StringComparison.OrdinalIgnoreCase) >= 0 ||
               item.Name.IndexOf(itemName, StringComparison.OrdinalIgnoreCase) >= 0 ||
               item.ItemId.Equals(itemName, StringComparison.OrdinalIgnoreCase);
    }

    public static bool MatchesCategory(Item item, ItemCategory category) => category switch
    {
        ItemCategory.Axe => item is Axe,
        ItemCategory.Pickaxe => item is Pickaxe,
        ItemCategory.Hoe => item is Hoe,
        ItemCategory.WateringCan => item is WateringCan,
        ItemCategory.Scythe => item is MeleeWeapon w && w.isScythe(),
        ItemCategory.FishingRod => item is FishingRod,
        ItemCategory.Weapon => item is MeleeWeapon w2 && !w2.isScythe(),
        _ => false
    };
}
