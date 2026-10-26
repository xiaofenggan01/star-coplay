using StardewValley;
using StardewValley.Menus;

namespace StarCoPlay.GameServices.Menu;

/// <summary>
/// UI 槽位物品解析器。输入 ClickableComponent（含 myID + name），
/// 输出该槽位当前持有的 Item。
/// 背包槽用 InventoryMenu.getItemFromClickableComponent；
/// 装备槽按 component.name switch；Trinket 用 myID - 120。
/// </summary>
public static class UiInventorySlotResolver
{
    public static Item? ResolveItem(IClickableMenu activeMenu, ClickableComponent component)
    {
        if (activeMenu is GameMenu gameMenu)
        {
            if (gameMenu.pages == null || gameMenu.currentTab < 0
                || gameMenu.currentTab >= gameMenu.pages.Count)
                return null;

            var page = gameMenu.pages[gameMenu.currentTab];
            if (page is InventoryPage inventoryPage)
                return ResolveInventoryPageItem(inventoryPage, component);
        }
        else if (activeMenu is InventoryPage directPage)
        {
            return ResolveInventoryPageItem(directPage, component);
        }
        return null;
    }

    public static Item? ResolveInventoryPageItem(InventoryPage page, ClickableComponent component)
    {
        if (int.TryParse(component.name, out _))
        {
            var fromInventory = page.inventory?.getItemFromClickableComponent(component);
            if (fromInventory != null) return fromInventory;
        }

        return ResolveEquipmentItem(component);
    }

    public static Item? ResolveTrinket(int componentId)
    {
        if (Game1.player?.trinketItems == null) return null;
        int index = componentId - 120;
        if (index < 0 || index >= Game1.player.trinketItems.Count) return null;
        return Game1.player.trinketItems[index];
    }

    public static string ItemDisplayName(Item? item) =>
        item?.DisplayName ?? "";

    private static Item? ResolveEquipmentItem(ClickableComponent component)
    {
        if (Game1.player == null) return null;

        return component.name switch
        {
            "Hat" => Game1.player.hat.Value,
            "Left Ring" => Game1.player.leftRing.Value,
            "Right Ring" => Game1.player.rightRing.Value,
            "Boots" => Game1.player.boots.Value,
            "Shirt" => Game1.player.shirtItem.Value,
            "Pants" => Game1.player.pantsItem.Value,
            "Trinket" => ResolveTrinket(component.myID),
            _ => null
        };
    }
}
