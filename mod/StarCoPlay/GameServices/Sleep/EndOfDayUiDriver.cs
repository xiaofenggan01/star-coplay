using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StarCoPlay.GameServices.Menu;

namespace StarCoPlay.GameServices.Sleep;

internal enum UiDriveResult
{
    NothingToDo,       // 没有活跃菜单
    Progressed,        // 成功推进了一步
    DecisionRequired,  // 遇到需要 LLM 决策的菜单
    Unknown            // 未知菜单，不敢推进
}

internal class EndOfDayUiDriver
{
    private readonly IMonitor _monitor;

    public EndOfDayUiDriver(IMonitor monitor)
    {
        _monitor = monitor;
    }

    /// <summary>
    /// 尝试推进当前活跃的日结 UI 一步。
    /// 严格白名单模式：只推进明确已知的菜单，未知菜单不做任何操作。
    /// </summary>
    public UiDriveResult TryAdvance()
    {
        var menu = Game1.activeClickableMenu;
        if (menu == null)
            return UiDriveResult.NothingToDo;

        // ShippingMenu：点击 okButton 推进
        if (menu is ShippingMenu shippingMenu)
        {
            return AdvanceShippingMenu(shippingMenu);
        }

        // DialogueBox：非 question 直接点击推进，question 需要上层决策
        if (menu is DialogueBox dlg)
        {
            return AdvanceDialogueBox(dlg);
        }

        // SaveGameMenu：自动进行，不需要交互
        if (menu is SaveGameMenu)
        {
            return UiDriveResult.NothingToDo;
        }

        // LevelUpMenu：区分普通升级和职业选择
        if (menu is LevelUpMenu levelUp)
        {
            return AdvanceLevelUpMenu(levelUp);
        }

        // 未知菜单：不做任何操作
        string typeName = menu.GetType().Name;
        _monitor.Log($"[EndOfDayUiDriver] Unknown menu '{typeName}', not progressing", LogLevel.Warn);
        return UiDriveResult.Unknown;
    }

    private int _shippingClickCooldown;

    private UiDriveResult AdvanceShippingMenu(ShippingMenu menu)
    {
        // Cooldown to avoid spamming clicks during intro animation (~3.5s)
        if (_shippingClickCooldown > 0)
        {
            _shippingClickCooldown--;
            return UiDriveResult.NothingToDo;
        }

        var okButton = menu.okButton;
        if (okButton == null)
        {
            _monitor.Log("[EndOfDayUiDriver] ShippingMenu has no okButton, cannot advance", LogLevel.Warn);
            return UiDriveResult.Unknown;
        }

        int x = okButton.bounds.Center.X;
        int y = okButton.bounds.Center.Y;
        menu.receiveLeftClick(x, y);

        // Check if click actually closed the menu
        if (Game1.activeClickableMenu is ShippingMenu)
        {
            // Still showing — intro animation not done yet, wait before retrying
            _shippingClickCooldown = 60; // ~1 second between attempts
            _monitor.Log($"[EndOfDayUiDriver] ShippingMenu okButton click had no effect (intro animation?), waiting", LogLevel.Debug);
            return UiDriveResult.NothingToDo;
        }

        _monitor.Log($"[EndOfDayUiDriver] ShippingMenu dismissed via okButton at ({x}, {y})", LogLevel.Debug);
        _shippingClickCooldown = 0;
        return UiDriveResult.Progressed;
    }

    private UiDriveResult AdvanceDialogueBox(DialogueBox dlg)
    {
        if (dlg.isQuestion)
        {
            _monitor.Log("[EndOfDayUiDriver] DialogueBox is question, decision required", LogLevel.Info);
            return UiDriveResult.DecisionRequired;
        }

        // Info dialogue — click to dismiss/advance
        _monitor.Log("[EndOfDayUiDriver] Advancing DialogueBox (info)", LogLevel.Debug);
        dlg.receiveLeftClick(0, 0);
        return UiDriveResult.Progressed;
    }

    private UiDriveResult AdvanceLevelUpMenu(LevelUpMenu menu)
    {
        if (menu.isProfessionChooser)
        {
            _monitor.Log("[EndOfDayUiDriver] LevelUpMenu is profession chooser, decision required", LogLevel.Info);
            return UiDriveResult.DecisionRequired;
        }

        if (!menu.CanReceiveInput())
            return UiDriveResult.NothingToDo;

        var result = LevelUpMenuAdapter.Click(menu, 101, _monitor);
        if (result.Success)
        {
            _monitor.Log($"[EndOfDayUiDriver] LevelUpMenu advanced via adapter: {result.Effect}", LogLevel.Debug);
            return UiDriveResult.Progressed;
        }

        _monitor.Log($"[EndOfDayUiDriver] LevelUpMenu adapter failed: {result.Error}", LogLevel.Warn);
        return UiDriveResult.Unknown;
    }

    /// <summary>判断当前菜单是否需要 LLM 决策</summary>
    public bool IsDecisionMenu()
    {
        var menu = Game1.activeClickableMenu;
        if (menu == null) return false;
        return menu is LevelUpMenu levelUp && levelUp.isProfessionChooser;
    }

    /// <summary>获取当前阻塞菜单的类型名</summary>
    public string? GetBlockingMenuType()
    {
        var menu = Game1.activeClickableMenu;
        if (menu == null) return null;

        if (menu is LevelUpMenu levelUp && levelUp.isProfessionChooser)
            return "LevelUpMenu(ProfessionChooser)";

        // 未知菜单也视为阻塞
        if (menu is not ShippingMenu && menu is not SaveGameMenu && menu is not LevelUpMenu)
            return menu.GetType().Name;

        return null;
    }
}
