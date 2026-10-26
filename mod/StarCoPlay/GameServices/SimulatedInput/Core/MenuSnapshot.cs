using System.Collections.Generic;

namespace StarCoPlay.GameServices.SimulatedInput;

/// <summary>菜单中单个可交互组件的信息。</summary>
public class ComponentInfo
{
    public int Id { get; set; }                                // ClickableComponent.myID
    public string Name { get; set; } = "";                     // ClickableComponent.name（语义标识，如 "purchase"）
    public string? Label { get; set; }                         // ClickableComponent.label（显示文本）
    public string? ItemName { get; set; }                      // 关联物品的 DisplayName（如果有）
    public int? ItemStack { get; set; }                        // 物品堆叠数量
    public int? Price { get; set; }                            // 价格（商店场景）
    public bool Visible { get; set; }                          // 是否可见
    public Dictionary<string, int> Neighbors { get; set; } = new(); // left/right/up/down → neighborID

    /// <summary>
    /// 组件来源命名空间，用于区分 GameMenu 顶部 tab 与子 page 内组件。
    /// 取值："tab"（GameMenu 顶部页签按钮）/ "page"（普通菜单或当前 active page 内的组件）。
    /// </summary>
    /// <remarks>
    /// GameMenu 的 tabs 与子 page（如 InventoryPage）的 inventory slots 都用 myID 0~N，
    /// 邻接图按 (Source, Id) 二元组隔离，避免命名空间冲突。
    /// </remarks>
    public string Source { get; set; } = "page";              // "tab" | "page"
}

/// <summary>当前菜单的完整快照。</summary>
public class MenuSnapshot
{
    public string MenuType { get; set; } = "None";             // 菜单类名（如 "ShopMenu"、"GameMenu"）
    public string? ActiveTab { get; set; }                     // GameMenu 当前 active page 类名（如 "InventoryPage"），其他菜单为 null
    public int CurrentSnappedId { get; set; } = -1;            // 当前聚焦的组件 ID
    public List<ComponentInfo> Components { get; set; } = new();

    public static MenuSnapshot Empty => new()
    {
        MenuType = "None",
        ActiveTab = null,
        CurrentSnappedId = -1,
        Components = new(),
    };
}
