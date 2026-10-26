using StardewValley;
using StardewValley.Menus;
using StardewModdingAPI;

namespace StarCoPlay.GameServices.Menu;

/// <summary>
/// 可观测的 UI 组件点击执行器。
/// 捕获点击前后状态（CursorSlotItem、槽位物品、菜单类型），
/// 分类点击效果（picked_up / placed_down / swapped / menu_closed / no_op）。
/// </summary>
public sealed class UiComponentClicker
{
    private readonly IMonitor _monitor;

    public UiComponentClicker(IMonitor monitor)
    {
        _monitor = monitor;
    }

    public UiClickResult LeftClick(IClickableMenu menu, ClickableComponent component, string source)
    {
        var menuTypeBefore = menu.GetType().Name;
        var cursorBefore = Game1.player?.CursorSlotItem;
        var slotBefore = ResolveSlotItem(menu, component);

        var cx = component.bounds.Center.X;
        var cy = component.bounds.Center.Y;
        menu.receiveLeftClick(cx, cy);

        var activeAfter = Game1.activeClickableMenu;
        var menuTypeAfter = activeAfter?.GetType().Name ?? "None";
        var cursorAfter = Game1.player?.CursorSlotItem;
        var slotAfter = ResolveSlotItem(activeAfter, component);

        var effect = ClassifyEffect(
            cursorBefore, cursorAfter,
            slotBefore, slotAfter,
            menuTypeBefore, menuTypeAfter);

        _monitor.Log($"[UiClicker] LeftClick id={component.myID} name={component.name} " +
            $"effect={effect} cursor=[{ItemName(cursorBefore)}→{ItemName(cursorAfter)}] " +
            $"slot=[{ItemName(slotBefore)}→{ItemName(slotAfter)}]", LogLevel.Debug);

        return new UiClickResult
        {
            Success = true,
            Method = "receive_left_click",
            Effect = effect,
            ComponentId = component.myID,
            ComponentName = component.name ?? "",
            Source = source,
            X = cx,
            Y = cy,
            CursorItemBefore = ItemName(cursorBefore),
            CursorItemAfter = ItemName(cursorAfter),
            SlotItemBefore = ItemName(slotBefore),
            SlotItemAfter = ItemName(slotAfter),
            MenuTypeBefore = menuTypeBefore,
            MenuTypeAfter = menuTypeAfter,
        };
    }

    public static string ClassifyEffect(
        Item? cursorBefore, Item? cursorAfter,
        Item? slotBefore, Item? slotAfter,
        string menuBefore, string menuAfter)
    {
        if (menuAfter == "None" && menuBefore != "None")
            return "menu_closed";

        var cursorIdBefore = cursorBefore?.QualifiedItemId;
        var cursorIdAfter = cursorAfter?.QualifiedItemId;
        var slotIdBefore = slotBefore?.QualifiedItemId;
        var slotIdAfter = slotAfter?.QualifiedItemId;

        if (cursorIdBefore == null && cursorIdAfter != null)
            return "picked_up";
        if (cursorIdBefore != null && cursorIdAfter == null)
            return "placed_down";
        if (cursorIdBefore != null && cursorIdAfter != null
            && (cursorIdBefore != cursorIdAfter || cursorBefore?.Stack != cursorAfter?.Stack))
            return "swapped";

        if (slotIdBefore != slotIdAfter
            || slotBefore?.Stack != slotAfter?.Stack)
            return "slot_changed";

        return "no_op";
    }

    private Item? ResolveSlotItem(IClickableMenu? menu, ClickableComponent component)
    {
        if (menu == null) return null;
        return UiInventorySlotResolver.ResolveItem(menu, component);
    }

    private static string ItemName(Item? item) =>
        item?.DisplayName ?? "";
}
