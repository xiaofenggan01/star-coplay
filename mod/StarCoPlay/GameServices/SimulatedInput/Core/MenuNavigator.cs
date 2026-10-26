using System;
using System.Collections.Generic;
using System.Linq;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StarCoPlay.GameServices.Menu;

namespace StarCoPlay.GameServices.SimulatedInput;

/// <summary>
/// 菜单 BFS 导航系统 — 基于 ClickableComponent 的邻居图进行寻路，
/// 通过 InputBridge DPad 模拟逐步跳转到目标组件。
/// </summary>
/// <remarks>
/// 节点 key 为 <c>"{source}:{id}"</c> 字符串（例如 <c>"tab:0"</c> / <c>"page:35"</c>），
/// 用于隔离 GameMenu 顶部 tabs 与子 page 内组件可能重复的 myID。
/// </remarks>
public class MenuNavigator
{
    private readonly InputBridge _bridge;
    private readonly IMonitor _monitor;
    private readonly UiComponentClicker _clicker;

    public UiClickResult? LastClickResult { get; private set; }

    // ── 特殊 neighborID 常量（跳过不处理）──
    private const int NoNeighbor = -1;
    private const int AutoSnap = -99999;
    private const int CustomSnap = -99998;
    private const int RegionJump = -7777;

    // ── 方向常量 ──
    private const int DirUp = 0;
    private const int DirRight = 1;
    private const int DirDown = 2;
    private const int DirLeft = 3;

    // ── Source 命名空间常量 ──
    public const string SourceTab = "tab";    // GameMenu 顶部页签
    public const string SourcePage = "page";  // 普通菜单或当前 active page 内组件

    // ── 方向 → SButton 映射 ──
    private static readonly SButton[] DPadButtons =
    {
        SButton.DPadUp,    // 0
        SButton.DPadRight, // 1
        SButton.DPadDown,  // 2
        SButton.DPadLeft,  // 3
    };

    public MenuNavigator(InputBridge bridge, IMonitor monitor)
    {
        _bridge = bridge;
        _monitor = monitor;
        _clicker = new UiComponentClicker(monitor);
    }

    // ════════════════════════════════════════════════════════
    //  QueryMenu — 获取当前菜单的快照
    // ════════════════════════════════════════════════════════

    /// <summary>获取当前菜单的快照。无菜单时返回 MenuSnapshot.Empty。</summary>
    public MenuSnapshot QueryMenu()
    {
        var menu = Game1.activeClickableMenu;
        if (menu == null)
            return MenuSnapshot.Empty;

        var menuType = menu.GetType().Name;
        string? activeTab = null;
        var componentList = new List<ComponentInfo>();

        // ShopMenu 价格表 — best-effort 提取（仅顶层菜单为 ShopMenu 时生效）
        Dictionary<ISalable, ItemStockInformation>? priceMap = null;
        if (menu is ShopMenu shop)
        {
            try
            {
                priceMap = shop.itemPriceAndStock;
            }
            catch (Exception ex)
            {
                _monitor.Log($"[MenuNav] ShopMenu 价格提取失败: {ex.Message}", LogLevel.Debug);
            }
        }

        // ── Composite menu 处理：GameMenu 等容器菜单 ──
        if (menu is GameMenu gameMenu)
        {
            // 1. 顶部 tabs（切换页签按钮）— SDV 1.6 GameMenu 顶层 allClickableComponents 为 null，
            //    实际 tabs 列表在 gameMenu.tabs 字段（dev_eval 实测确认）
            AddComponentsFrom(componentList, gameMenu.tabs, priceMap, SourceTab);

            // 2. 当前 active page 的 components（标记 source=page）
            if (gameMenu.pages != null
                && gameMenu.currentTab >= 0
                && gameMenu.currentTab < gameMenu.pages.Count)
            {
                var activePage = gameMenu.pages[gameMenu.currentTab];
                if (activePage != null)
                {
                    activeTab = activePage.GetType().Name; // e.g. "InventoryPage"
                    AddComponentsFrom(componentList, activePage.allClickableComponents, priceMap, SourcePage);
                    EnrichInventoryItems(componentList, activePage);
                }
            }
            // MenuType 保持 "GameMenu"（不带斜杠后缀），通过 ActiveTab 区分子 page，
            // 与 query_ui_snapshot handler 保持一致。
        }
        else if (menu is DialogueBox dialogueBox)
        {
            AddDialogueResponses(componentList, dialogueBox);
        }
        else if (menu is LevelUpMenu levelUpMenu)
        {
            LevelUpMenuAdapter.Project(levelUpMenu, componentList, _monitor);
        }
        else
        {
            // 普通菜单（ShopMenu / ItemGrabMenu / DialogueBox 等），全部归 page 命名空间
            AddComponentsFrom(componentList, menu.allClickableComponents, priceMap, SourcePage);
        }

        var snapshot = new MenuSnapshot
        {
            MenuType = menuType,
            ActiveTab = activeTab,
            CurrentSnappedId = menu.currentlySnappedComponent?.myID ?? -1,
            Components = componentList,
        };

        if (componentList.Count == 0)
        {
            _monitor.Log($"[MenuNav] {menuType}: 无可用组件", LogLevel.Debug);
        }

        _monitor.Log($"[MenuNav] QueryMenu: {snapshot.MenuType}{(activeTab != null ? "/" + activeTab : "")}, {snapshot.Components.Count} 组件, snapped={snapshot.CurrentSnappedId}", LogLevel.Trace);
        return snapshot;
    }

    /// <summary>
    /// 将 ClickableComponent 列表转为 ComponentInfo 追加到结果。
    /// priceMap 不为 null 时尝试为可售物品填充 Price 字段。
    /// </summary>
    private void AddComponentsFrom(
        List<ComponentInfo> result,
        List<ClickableComponent>? source,
        Dictionary<ISalable, ItemStockInformation>? priceMap,
        string sourceNamespace)
    {
        if (source == null || source.Count == 0)
            return;

        foreach (var c in source)
        {
            if (c == null) continue;

            var info = new ComponentInfo
            {
                Id = c.myID,
                Name = c.name ?? "",
                Label = c.label,
                Visible = c.visible,
                Source = sourceNamespace,
                Neighbors = new Dictionary<string, int>
                {
                    ["left"] = c.leftNeighborID,
                    ["right"] = c.rightNeighborID,
                    ["up"] = c.upNeighborID,
                    ["down"] = c.downNeighborID,
                },
            };

            // 关联物品信息
            if (c.item is Item item)
            {
                info.ItemName = item.DisplayName;
                info.ItemStack = item.Stack;
            }

            // ShopMenu 价格提取
            if (priceMap != null && c.item is ISalable salable)
            {
                try
                {
                    if (priceMap.TryGetValue(salable, out var stockInfo))
                    {
                        info.Price = stockInfo.Price > 0 ? stockInfo.Price : salable.salePrice();
                    }
                }
                catch
                {
                    // 提取失败不影响整体
                }
            }

            result.Add(info);
        }
    }

    /// <summary>
    /// DialogueBox does not expose question responses through allClickableComponents
    /// until its internal responseCC list is populated. That list is the executable
    /// component source for menu_click(component_id, source=page).
    /// </summary>
    private void AddDialogueResponses(List<ComponentInfo> result, DialogueBox dialogueBox)
    {
        if (!dialogueBox.isQuestion || dialogueBox.responseCC == null || dialogueBox.responseCC.Count == 0)
        {
            AddComponentsFrom(result, dialogueBox.allClickableComponents, null, SourcePage);
            return;
        }

        for (int i = 0; i < dialogueBox.responseCC.Count; i++)
        {
            var c = dialogueBox.responseCC[i];
            if (c == null) continue;

            var response = dialogueBox.responses != null && i < dialogueBox.responses.Length
                ? dialogueBox.responses[i]
                : null;

            result.Add(new ComponentInfo
            {
                Id = c.myID,
                Name = response?.responseKey ?? c.name ?? "",
                Label = response?.responseText ?? c.label,
                Visible = c.visible,
                Source = SourcePage,
                Neighbors = new Dictionary<string, int>
                {
                    ["left"] = c.leftNeighborID,
                    ["right"] = c.rightNeighborID,
                    ["up"] = c.upNeighborID,
                    ["down"] = c.downNeighborID,
                },
            });
        }
    }

    // ════════════════════════════════════════════════════════
    //  FindPath — BFS 最短路径搜索
    // ════════════════════════════════════════════════════════

    /// <summary>
    /// BFS 搜索从 (currentSource, currentId) 到 (targetSource, targetId) 的最短路径。
    /// 节点用 <c>"{source}:{id}"</c> 字符串隔离 GameMenu tabs 与 page 内组件。
    /// </summary>
    /// <returns>方向序列（0=上 1=右 2=下 3=左），不可达返回 null。</returns>
    public List<int>? FindPath(string currentSource, int currentId, string targetSource, int targetId, IClickableMenu menu)
    {
        var currentKey = MakeKey(currentSource, currentId);
        var targetKey = MakeKey(targetSource, targetId);

        if (currentKey == targetKey)
            return new List<int>();

        var adjacency = BuildAdjacency(menu);

        if (adjacency.Count == 0)
            return null;

        if (!adjacency.ContainsKey(currentKey) || !adjacency.ContainsKey(targetKey))
            return null;

        // BFS
        var visited = new HashSet<string> { currentKey };
        var queue = new Queue<(string nodeKey, List<int> path)>();
        queue.Enqueue((currentKey, new List<int>()));

        while (queue.Count > 0)
        {
            var (nodeKey, path) = queue.Dequeue();

            if (!adjacency.TryGetValue(nodeKey, out var edges))
                continue;

            foreach (var (neighborKey, dir) in edges)
            {
                if (visited.Contains(neighborKey))
                    continue;

                var newPath = new List<int>(path) { dir };

                if (neighborKey == targetKey)
                    return newPath;

                visited.Add(neighborKey);
                queue.Enqueue((neighborKey, newPath));
            }
        }

        _monitor.Log($"[MenuNav] FindPath: {currentKey} → {targetKey} 不可达", LogLevel.Debug);
        return null;
    }

    // ════════════════════════════════════════════════════════
    //  NavigateTo — DPad 逐步导航到目标组件
    // ════════════════════════════════════════════════════════

    /// <summary>导航到指定组件（通过 DPad 逐步跳转）。</summary>
    /// <param name="targetSource">目标命名空间（"tab" / "page"），默认 "page" 兼容旧调用。</param>
    /// <param name="targetId">目标组件 myID。</param>
    /// <returns>true=已入队导航序列，false=不可达或无菜单。</returns>
    public bool NavigateTo(int targetId, string targetSource = SourcePage)
    {
        var menu = Game1.activeClickableMenu;
        if (menu == null)
        {
            _monitor.Log("[MenuNav] NavigateTo: 无活动菜单", LogLevel.Debug);
            return false;
        }

        int currentId = menu.currentlySnappedComponent?.myID ?? -1;
        if (currentId < 0)
        {
            // SDV 在键盘模式下打开 GameMenu 默认无 snapped 组件，
            // 调用 SDV 内置 snapToDefaultClickableComponent() 让游戏自动选择默认组件
            // （和玩家手柄首次按 DPad 时 SDV 自动选默认的行为一致）
            try
            {
                menu.snapToDefaultClickableComponent();
                currentId = menu.currentlySnappedComponent?.myID ?? -1;
                _monitor.Log($"[MenuNav] NavigateTo: 自动 snap 到默认组件 id={currentId}", LogLevel.Trace);
            }
            catch (Exception ex)
            {
                _monitor.Log($"[MenuNav] NavigateTo: snapToDefaultClickableComponent 失败: {ex.Message}", LogLevel.Warn);
            }

            // 仍无 snapped → 直接强制 snap 到目标组件（fallback，跳过 BFS）
            if (currentId < 0)
            {
                try
                {
                    menu.setCurrentlySnappedComponentTo(targetId);
                    menu.snapCursorToCurrentSnappedComponent();
                    _monitor.Log($"[MenuNav] NavigateTo: 强制 snap 到目标 {targetSource}:{targetId}", LogLevel.Debug);
                    return true;
                }
                catch (Exception ex)
                {
                    _monitor.Log($"[MenuNav] NavigateTo: setCurrentlySnappedComponentTo 失败: {ex.Message}", LogLevel.Warn);
                    return false;
                }
            }
        }

        // 推断当前 snapped 组件所属命名空间（GameMenu 时通过 currentTab 判断）
        var currentSource = ResolveCurrentSnappedSource(menu, currentId);

        if (currentSource == targetSource && currentId == targetId)
        {
            _monitor.Log($"[MenuNav] NavigateTo: 已在目标 {targetSource}:{targetId}", LogLevel.Trace);
            return true;
        }

        var path = FindPath(currentSource, currentId, targetSource, targetId, menu);
        if (path == null)
        {
            _monitor.Log($"[MenuNav] NavigateTo: {currentSource}:{currentId} → {targetSource}:{targetId} 不可达", LogLevel.Debug);
            return false;
        }

        _monitor.Log($"[MenuNav] NavigateTo: {currentSource}:{currentId} → {targetSource}:{targetId}, 路径长度 {path.Count}", LogLevel.Debug);

        // 逐步入队 DPad 按键
        foreach (int dir in path)
        {
            _bridge.Tap(DPadButtons[dir]);
        }

        return true;
    }

    // ════════════════════════════════════════════════════════
    //  Click — 导航到目标并点击确认
    // ════════════════════════════════════════════════════════

    /// <summary>
    /// 导航到目标组件并点击 A 键确认。
    /// 兼容旧签名：默认 source="page"（普通菜单或 GameMenu 子 page 内组件）。
    /// </summary>
    /// <returns>true=已入队导航+点击序列，false=不可达。</returns>
    public bool Click(int targetId)
    {
        return Click(targetId, SourcePage);
    }

    /// <summary>
    /// 点击当前菜单的默认区域（component_id=-1 专用路径）。
    /// 仅支持 isQuestion=false 的 DialogueBox，调用 receiveLeftClick(0,0) 推进对话文本。
    /// 不做"找不到组件就自动 fallback"；只在调用方显式传 -1 时触发。
    /// </summary>
    /// <returns>true=成功点击，false=不支持的菜单类型或前置条件不满足。</returns>
    public bool ClickDefault()
    {
        var menu = Game1.activeClickableMenu;
        if (menu is DialogueBox db && !db.isQuestion)
        {
            var menuTypeBefore = "DialogueBox";
            db.receiveLeftClick(0, 0);
            var menuTypeAfter = Game1.activeClickableMenu?.GetType().Name ?? "None";
            var effect = menuTypeAfter == "None" ? "dialogue_closed" : "dialogue_advanced";
            LastClickResult = new UiClickResult
            {
                Success = true,
                Method = "default_click",
                Effect = effect,
                ComponentId = -1,
                Source = "page",
                MenuTypeBefore = menuTypeBefore,
                MenuTypeAfter = menuTypeAfter,
            };
            _monitor.Log($"[MenuNav] ClickDefault: DialogueBox → {menuTypeAfter} ({effect})", LogLevel.Debug);
            return true;
        }

        _monitor.Log($"[MenuNav] ClickDefault: unsupported menu {menu?.GetType().Name ?? "none"}", LogLevel.Debug);
        LastClickResult = UiClickResult.Failed(
            menu == null ? "no active menu"
            : menu is DialogueBox ? "default click only supported when isQuestion=false"
            : $"default click not supported for {menu.GetType().Name}");
        return false;
    }

    /// <summary>导航到目标组件并点击 A 键确认。</summary>
    /// <param name="targetId">目标组件 myID。</param>
    /// <param name="targetSource">目标命名空间（"tab" / "page"）。</param>
    /// <returns>true=已入队导航+点击序列，false=不可达。</returns>
    /// <remarks>
    /// 特殊路径：tab 切换直接调用 GameMenu.changeTab API。
    /// 原因：实测 LB/RB 按键不真正切换 tab（按了但 currentTab 没变）。
    /// SDV 内部 tab 切换通过 GameMenu.changeTab(int whichTab, bool playSound = true) 方法，
    /// 直接 API 调用绕过所有按键路径和 hit-test 问题，行为最稳定。
    /// </remarks>
    public bool Click(int targetId, string targetSource)
    {
        var menu = Game1.activeClickableMenu;
        if (menu == null) return false;

        // ── tab 切换：直接调用 GameMenu.changeTab API（仅当目标命名空间是 tab 且当前是 GameMenu）──
        if (targetSource == SourceTab && menu is GameMenu gameMenu)
        {
            // QueryMenu 中 tab.myID 即来自 ClickableComponent.myID，SDV 1.6 中 GameMenu tabs
            // 的 myID 通常为 12340 + index（见 GameMenu 构造函数中对各 tab 的 myID 赋值）。
            // 但为了不依赖魔数，优先在 gameMenu.tabs 中按 myID 查 index。
            int targetTabIndex = -1;
            if (gameMenu.tabs != null)
            {
                for (int i = 0; i < gameMenu.tabs.Count; i++)
                {
                    if (gameMenu.tabs[i] != null && gameMenu.tabs[i].myID == targetId)
                    {
                        targetTabIndex = i;
                        break;
                    }
                }
            }

            // 兜底：按 12340 偏移推断（与 SDV 1.6 GameMenu 默认 myID 约定一致）
            if (targetTabIndex < 0)
            {
                int inferred = targetId - 12340;
                if (inferred >= 0 && gameMenu.pages != null && inferred < gameMenu.pages.Count)
                {
                    targetTabIndex = inferred;
                }
            }

            if (targetTabIndex < 0 || gameMenu.pages == null || targetTabIndex >= gameMenu.pages.Count)
            {
                _monitor.Log($"[MenuNav] Click: tab id={targetId} 解析失败（pages.Count={gameMenu.pages?.Count ?? 0}）", LogLevel.Debug);
                LastClickResult = UiClickResult.Failed($"tab id={targetId} resolve failed");
                return false;
            }

            if (targetTabIndex == gameMenu.currentTab)
            {
                _monitor.Log($"[MenuNav] Click: 已在目标 tab index={targetTabIndex}", LogLevel.Trace);
                LastClickResult = new UiClickResult
                {
                    Success = true,
                    Method = "direct_change_tab",
                    Effect = "no_op",
                    ComponentId = targetId,
                    Source = SourceTab,
                    MenuTypeBefore = "GameMenu",
                    MenuTypeAfter = "GameMenu",
                };
                return true;
            }

            // 实测：LB/RB 按键不真正切换 tab。
            // SDV 内部 tab 切换通过 GameMenu.changeTab(int whichTab, bool playSound = true) 方法，
            // 直接 API 调用最稳定。
            try
            {
                gameMenu.changeTab(targetTabIndex);
                _monitor.Log($"[MenuNav] Click: tab 切换 {gameMenu.currentTab} → {targetTabIndex}（直接 API GameMenu.changeTab）", LogLevel.Debug);
                LastClickResult = new UiClickResult
                {
                    Success = true,
                    Method = "direct_change_tab",
                    Effect = "tab_changed",
                    ComponentId = targetId,
                    Source = SourceTab,
                    MenuTypeBefore = "GameMenu",
                    MenuTypeAfter = "GameMenu",
                };
                return true;
            }
            catch (Exception ex)
            {
                _monitor.Log($"[MenuNav] Click: GameMenu.changeTab({targetTabIndex}) 异常: {ex.Message}", LogLevel.Warn);
                LastClickResult = UiClickResult.Failed($"changeTab exception: {ex.Message}");
                return false;
            }
        }

        // ── LevelUpMenu：专用 adapter（receiveLeftClick 是空方法）──
        if (menu is LevelUpMenu levelUpMenu)
        {
            LastClickResult = LevelUpMenuAdapter.Click(levelUpMenu, targetId, _monitor);
            return LastClickResult.Success;
        }

        // ── 普通组件：委派给 UiComponentClicker 执行可观测点击 ──
        var targetComponent = FindComponentById(menu, targetId, targetSource);
        if (targetComponent == null)
        {
            _monitor.Log($"[MenuNav] Click: component id={targetId} source={targetSource} 未找到", LogLevel.Debug);
            LastClickResult = UiClickResult.Failed($"component id={targetId} source={targetSource} not found");
            return false;
        }

        LastClickResult = _clicker.LeftClick(menu, targetComponent, targetSource);
        return LastClickResult.Success;
    }

    // ════════════════════════════════════════════════════════
    //  内部方法
    // ════════════════════════════════════════════════════════

    /// <summary>
    /// InventoryPage 特殊处理：通过 UiInventorySlotResolver 按 ClickableComponent 解析物品。
    /// </summary>
    private void EnrichInventoryItems(List<ComponentInfo> components, IClickableMenu page)
    {
        if (page is not InventoryPage) return;

        var activeMenu = Game1.activeClickableMenu;
        if (activeMenu == null) return;

        foreach (var info in components)
        {
            if (info.Source != SourcePage || info.ItemName != null) continue;
            if (string.IsNullOrEmpty(info.Name)) continue;

            var component = FindComponentById(activeMenu, info.Id, SourcePage);
            if (component == null) continue;

            var item = UiInventorySlotResolver.ResolveItem(activeMenu, component);
            if (item != null)
            {
                info.ItemName = item.DisplayName;
                info.ItemStack = item.Stack;
            }
        }
    }

    /// <summary>在菜单的组件列表中按 myID 和 source 查找 ClickableComponent。</summary>
    private ClickableComponent? FindComponentById(IClickableMenu menu, int targetId, string targetSource)
    {
        if (targetSource == SourceTab && menu is GameMenu gm && gm.tabs != null)
        {
            foreach (var c in gm.tabs)
                if (c != null && c.myID == targetId) return c;
        }

        if (targetSource == SourcePage && menu is DialogueBox db && db.responseCC != null)
        {
            foreach (var c in db.responseCC)
                if (c != null && c.myID == targetId) return c;
        }

        List<ClickableComponent>? components = null;
        if (menu is GameMenu gameMenu)
        {
            if (gameMenu.pages != null && gameMenu.currentTab >= 0 && gameMenu.currentTab < gameMenu.pages.Count)
                components = gameMenu.pages[gameMenu.currentTab]?.allClickableComponents;
        }
        else
        {
            components = menu.allClickableComponents;
        }

        if (components != null)
        {
            foreach (var c in components)
                if (c != null && c.myID == targetId) return c;
        }
        return null;
    }

    /// <summary>
    /// 构建邻接图：节点 key 为 <c>"{source}:{id}"</c> 字符串，
    /// 隔离 GameMenu 顶部 tabs 与 page 内组件可能重复的 myID。
    /// </summary>
    /// <remarks>
    /// 假设：邻居跳转默认在<b>同一命名空间</b>内（tab→tab，page→page）。
    /// SDV 原生 ClickableComponent 邻接表确实如此，跨 page 切换走 tab 点击触发。
    /// </remarks>
    private Dictionary<string, List<(string neighborKey, int dir)>> BuildAdjacency(IClickableMenu menu)
    {
        var adjacency = new Dictionary<string, List<(string, int)>>();

        if (menu == null)
            return adjacency;

        // 1. 顶部 tabs（GameMenu 才有，普通菜单的 allClickableComponents 也归 page）
        if (menu is GameMenu gameMenu)
        {
            // SDV 1.6: GameMenu 顶层 allClickableComponents 为 null，tabs 在 gameMenu.tabs
            AppendEdgesFrom(adjacency, gameMenu.tabs, SourceTab);

            if (gameMenu.pages != null
                && gameMenu.currentTab >= 0
                && gameMenu.currentTab < gameMenu.pages.Count)
            {
                var activePage = gameMenu.pages[gameMenu.currentTab];
                if (activePage?.allClickableComponents != null)
                    AppendEdgesFrom(adjacency, activePage.allClickableComponents, SourcePage);
            }
        }
        else
        {
            AppendEdgesFrom(adjacency, menu.allClickableComponents, SourcePage);
        }

        return adjacency;
    }

    /// <summary>将 ClickableComponent 列表的邻接边追加到邻接图。</summary>
    private static void AppendEdgesFrom(
        Dictionary<string, List<(string, int)>> adjacency,
        List<ClickableComponent>? source,
        string sourceNamespace)
    {
        if (source == null) return;

        foreach (var c in source)
        {
            if (c == null) continue;

            var key = MakeKey(sourceNamespace, c.myID);
            if (!adjacency.ContainsKey(key))
                adjacency[key] = new List<(string, int)>();

            TryAddEdge(adjacency[key], sourceNamespace, c.upNeighborID, DirUp);
            TryAddEdge(adjacency[key], sourceNamespace, c.rightNeighborID, DirRight);
            TryAddEdge(adjacency[key], sourceNamespace, c.downNeighborID, DirDown);
            TryAddEdge(adjacency[key], sourceNamespace, c.leftNeighborID, DirLeft);
        }
    }

    /// <summary>
    /// 推断当前 snapped 组件所属命名空间。GameMenu 时优先匹配 active page，
    /// 匹配不到则回退到 tab；非 GameMenu 一律视为 page。
    /// </summary>
    private static string ResolveCurrentSnappedSource(IClickableMenu menu, int snappedId)
    {
        if (menu is not GameMenu gameMenu)
            return SourcePage;

        // 优先在 active page 中查找
        if (gameMenu.pages != null
            && gameMenu.currentTab >= 0
            && gameMenu.currentTab < gameMenu.pages.Count)
        {
            var activePage = gameMenu.pages[gameMenu.currentTab];
            if (activePage?.allClickableComponents != null
                && activePage.allClickableComponents.Any(c => c != null && c.myID == snappedId))
            {
                return SourcePage;
            }
        }

        // 回退：tabs 中查找
        if (gameMenu.allClickableComponents != null
            && gameMenu.allClickableComponents.Any(c => c != null && c.myID == snappedId))
        {
            return SourceTab;
        }

        // 默认 page（保守兜底）
        return SourcePage;
    }

    /// <summary>将有效邻居加入边列表，排除特殊 ID。</summary>
    private static void TryAddEdge(List<(string, int)> edges, string sourceNamespace, int neighborId, int dir)
    {
        if (!IsSpecialNeighborId(neighborId))
        {
            edges.Add((MakeKey(sourceNamespace, neighborId), dir));
        }
    }

    /// <summary>构造 (source, id) → key 字符串。</summary>
    private static string MakeKey(string source, int id) => $"{source}:{id}";

    /// <summary>判断 neighborID 是否为 SDV 特殊值（不表示实际组件连接）。</summary>
    private static bool IsSpecialNeighborId(int id)
    {
        return id == NoNeighbor      // -1: 无邻居
            || id == AutoSnap        // -99999: 自动搜索
            || id == CustomSnap      // -99998: 自定义
            || id == RegionJump;     // -7777: 区域跳转
    }
}
