using System;
using System.Collections.Generic;
using StardewValley;
using StardewValley.Objects;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;

namespace StarCoPlay.V2.Query.Projectors
{
    public static class InventoryProjector
    {
        private const string PlayerContainerKind = "player";
        private const string PlayerContainerLocator = "local";

        public static InventorySnapshotFact ProjectPlayerInventory(bool includeEmptySlots = false)
        {
            var fact = new InventorySnapshotFact
            {
                ContainerKind = PlayerContainerKind,
                SlotsTotal = Game1.player?.MaxItems ?? 0,
            };

            if (Game1.player == null)
                return fact;

            var items = new List<InventoryItemFact>();
            int nonNull = 0;

            for (int slot = 0; slot < Game1.player.Items.Count; slot++)
            {
                var item = Game1.player.Items[slot];
                if (item != null) nonNull++;
                else if (!includeEmptySlots) continue;

                items.Add(ProjectItem(item, PlayerContainerKind, PlayerContainerLocator, slot));
            }

            fact.Items.AddRange(items);
            fact.SlotsUsed = nonNull;

            return fact;
        }

        public static InventorySnapshotFact ProjectContainer(Chest chest, string containerKind, string containerLocator, bool includeEmptySlots)
        {
            var fact = new InventorySnapshotFact
            {
                ContainerKind = containerKind,
                ContainerRef = new Protocol.V245.RefId { Value = containerLocator },
                SlotsTotal = chest.GetActualCapacity(),
            };

            var playerItems = chest.GetItemsForPlayer();
            var items = new List<InventoryItemFact>();
            int nonNull = 0;

            for (int slot = 0; slot < playerItems.Count; slot++)
            {
                var item = playerItems[slot];
                if (item != null)
                    nonNull++;
                else if (!includeEmptySlots)
                    continue;

                items.Add(ProjectItem(item, containerKind, containerLocator, slot));
            }

            fact.Items.AddRange(items);
            fact.SlotsUsed = nonNull;

            return fact;
        }

        public static InventoryItemFact ProjectItem(Item? item, string containerKind, string containerLocator, int slot)
        {
            var fact = new InventoryItemFact
            {
                Slot = slot + 1,   // 对外 1-based，与 equip.slot 协议一致；内部索引仍用 slot
                ContainerKind = containerKind,
                ContainerLocator = containerLocator,
            };

            if (item == null)
                return fact;

            fact.Ref = containerKind == "player"
                ? RefIdBuilder.BuildPlayerInventoryItem(slot, item.QualifiedItemId)
                : BuildContainerRef(containerKind, containerLocator, slot, item.QualifiedItemId);
            fact.QualifiedItemId = item.QualifiedItemId ?? string.Empty;
            fact.ItemId = item.ItemId ?? string.Empty;
            fact.InternalName = item.Name ?? string.Empty;
            fact.DisplayName = item.DisplayName ?? string.Empty;
            fact.Stack = item.Stack;
            fact.Quality = item.Quality;
            fact.Category = item.Category;
            fact.CanStack = item.maximumStackSize() > 1;

            // upgrade_level: only meaningful for Tool subclasses (Pickaxe, Axe, FishingRod, etc.)
            fact.UpgradeLevel = item is Tool tool ? tool.UpgradeLevel : 0;

            // is_active: only meaningful for the player's inventory; true when this slot is currently held
            fact.IsActive = containerKind == PlayerContainerKind
                && Game1.player != null
                && slot == Game1.player.CurrentToolIndex;

            // is_recipe: recipe books share display_name with regular items but are consumed on use
            fact.IsRecipe = item.IsRecipe;

            // base_sell_price: static base price from Object.Price; -1 for non-Object items (tools, etc.)
            fact.BaseSellPrice = item is StardewValley.Object obj ? obj.Price : -1;

            // category_name: localized display name for the category int; empty string for non-Object items
            fact.CategoryName = item is StardewValley.Object catObj ? catObj.getCategoryName() : string.Empty;

            return fact;
        }

        /// <summary>
        /// Parse containerLocator (chest="map_token:x:y", fridge="map_token") and build a self-contained ref.
        /// </summary>
        private static Protocol.V245.RefId BuildContainerRef(string containerKind, string containerLocator, int slot, string qualifiedItemId)
        {
            switch (containerKind)
            {
                case "chest":
                {
                    // containerLocator format: "map_token:x:y" (map_token may contain colons)
                    var parts = containerLocator.Split(':');
                    if (parts.Length >= 3
                        && int.TryParse(parts[^1], out int y)
                        && int.TryParse(parts[^2], out int x))
                    {
                        var mapToken = string.Join(":", parts, 0, parts.Length - 2);
                        return RefIdBuilder.BuildContainerInventoryItem("chest", mapToken, x, y, slot, qualifiedItemId);
                    }
                    break;
                }
                case "fridge":
                {
                    // containerLocator format: "map_token"
                    if (!string.IsNullOrEmpty(containerLocator))
                        return RefIdBuilder.BuildContainerInventoryItem("fridge", containerLocator, null, null, slot, qualifiedItemId);
                    break;
                }
            }

            // Fallback: legacy 2-part locator (should not happen for well-formed calls)
            return RefIdBuilder.BuildInventoryItem(containerKind, slot, qualifiedItemId);
        }
    }
}
