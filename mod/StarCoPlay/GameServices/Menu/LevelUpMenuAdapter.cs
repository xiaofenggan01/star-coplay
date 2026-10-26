using System.Collections.Generic;
using System.Reflection;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StarCoPlay.GameServices.SimulatedInput;

namespace StarCoPlay.GameServices.Menu;

/// <summary>
/// LevelUpMenu 专用适配器。
/// SDV 的 LevelUpMenu.receiveLeftClick 是空方法，职业选择在 update() 中通过原始鼠标状态轮询处理。
/// 此 adapter 绕过 receiveLeftClick，直接操作菜单内部状态完成语义投影和点击。
/// </summary>
internal static class LevelUpMenuAdapter
{
    private const int IdOkButton = 101;
    private const int IdLeftProfession = 102;
    private const int IdRightProfession = 103;

    private static readonly FieldInfo? ProfessionsField =
        typeof(LevelUpMenu).GetField("professionsToChoose", BindingFlags.NonPublic | BindingFlags.Instance);
    private static readonly FieldInfo? LeftDescField =
        typeof(LevelUpMenu).GetField("leftProfessionDescription", BindingFlags.NonPublic | BindingFlags.Instance);
    private static readonly FieldInfo? RightDescField =
        typeof(LevelUpMenu).GetField("rightProfessionDescription", BindingFlags.NonPublic | BindingFlags.Instance);
    private static readonly FieldInfo? TitleField =
        typeof(LevelUpMenu).GetField("title", BindingFlags.NonPublic | BindingFlags.Instance);
    private static readonly FieldInfo? CurrentLevelField =
        typeof(LevelUpMenu).GetField("currentLevel", BindingFlags.NonPublic | BindingFlags.Instance);

    // ═══════════════════════════════════════════
    //  Project — 语义投影
    // ═══════════════════════════════════════════

    public static void Project(LevelUpMenu menu, List<ComponentInfo> result, IMonitor monitor)
    {
        if (menu.isProfessionChooser)
            ProjectProfessionChooser(menu, result, monitor);
        else
            ProjectSkillLevelUp(menu, result, monitor);
    }

    private static void ProjectProfessionChooser(LevelUpMenu menu, List<ComponentInfo> result, IMonitor monitor)
    {
        var leftDesc = LeftDescField?.GetValue(menu) as List<string>;
        var rightDesc = RightDescField?.GetValue(menu) as List<string>;
        var title = TitleField?.GetValue(menu) as string ?? "";
        var level = CurrentLevelField?.GetValue(menu) as int? ?? 0;

        string leftLabel = BuildProfessionLabel(leftDesc);
        string rightLabel = BuildProfessionLabel(rightDesc);

        result.Add(new ComponentInfo
        {
            Id = IdLeftProfession,
            Name = "leftProfession",
            Label = leftLabel,
            Visible = true,
            Source = MenuNavigator.SourcePage,
            Neighbors = new Dictionary<string, int>
            {
                ["left"] = -1, ["right"] = IdRightProfession,
                ["up"] = -1, ["down"] = -1,
            },
        });

        result.Add(new ComponentInfo
        {
            Id = IdRightProfession,
            Name = "rightProfession",
            Label = rightLabel,
            Visible = true,
            Source = MenuNavigator.SourcePage,
            Neighbors = new Dictionary<string, int>
            {
                ["left"] = IdLeftProfession, ["right"] = -1,
                ["up"] = -1, ["down"] = -1,
            },
        });

        monitor.Log($"[LevelUpMenuAdapter] Profession chooser: {title} Lv{level} — left='{leftLabel}', right='{rightLabel}'", LogLevel.Debug);
    }

    private static void ProjectSkillLevelUp(LevelUpMenu menu, List<ComponentInfo> result, IMonitor monitor)
    {
        var title = TitleField?.GetValue(menu) as string ?? "";
        var level = CurrentLevelField?.GetValue(menu) as int? ?? 0;

        result.Add(new ComponentInfo
        {
            Id = IdOkButton,
            Name = "okButton",
            Label = $"OK — {title} Lv{level}",
            Visible = true,
            Source = MenuNavigator.SourcePage,
            Neighbors = new Dictionary<string, int>
            {
                ["left"] = -1, ["right"] = -1,
                ["up"] = -1, ["down"] = -1,
            },
        });

        monitor.Log($"[LevelUpMenuAdapter] Skill level-up: {title} Lv{level}", LogLevel.Debug);
    }

    private static string BuildProfessionLabel(List<string>? desc)
    {
        if (desc == null || desc.Count == 0)
            return "(unknown)";
        if (desc.Count == 1)
            return desc[0];
        return desc[0] + " — " + string.Join("; ", desc.GetRange(1, desc.Count - 1));
    }

    // ═══════════════════════════════════════════
    //  Click — 直接操作菜单状态
    // ═══════════════════════════════════════════

    public static UiClickResult Click(LevelUpMenu menu, int componentId, IMonitor monitor)
    {
        const string menuTypeBefore = "LevelUpMenu";

        if (!menu.isActive)
            return UiClickResult.Failed("LevelUpMenu is not active");

        if (!menu.CanReceiveInput())
            return UiClickResult.Failed("LevelUpMenu not ready (timerBeforeStart or !informationUp)");

        if (componentId == IdOkButton && !menu.isProfessionChooser)
            return ClickOkButton(menu, monitor, menuTypeBefore);

        if ((componentId == IdLeftProfession || componentId == IdRightProfession) && menu.isProfessionChooser)
            return ClickProfession(menu, componentId, monitor, menuTypeBefore);

        monitor.Log($"[LevelUpMenuAdapter] Invalid click: id={componentId}, isProfessionChooser={menu.isProfessionChooser}", LogLevel.Warn);
        return UiClickResult.Failed($"invalid component id={componentId} for LevelUpMenu (isProfessionChooser={menu.isProfessionChooser})");
    }

    private static UiClickResult ClickOkButton(LevelUpMenu menu, IMonitor monitor, string menuTypeBefore)
    {
        menu.okButtonClicked();

        var menuTypeAfter = Game1.activeClickableMenu?.GetType().Name ?? "None";
        var effect = menuTypeAfter != menuTypeBefore ? "menu_closed" : "level_up_dismissed";

        monitor.Log($"[LevelUpMenuAdapter] okButtonClicked → {menuTypeAfter} ({effect})", LogLevel.Debug);

        return new UiClickResult
        {
            Success = true,
            Method = "level_up_ok_button",
            Effect = effect,
            ComponentId = IdOkButton,
            Source = MenuNavigator.SourcePage,
            MenuTypeBefore = menuTypeBefore,
            MenuTypeAfter = menuTypeAfter,
        };
    }

    private static UiClickResult ClickProfession(LevelUpMenu menu, int componentId, IMonitor monitor, string menuTypeBefore)
    {
        var professions = ProfessionsField?.GetValue(menu) as List<int>;
        if (professions == null || professions.Count < 2)
        {
            monitor.Log("[LevelUpMenuAdapter] Cannot read professionsToChoose or <2 entries", LogLevel.Warn);
            return UiClickResult.Failed("cannot read professionsToChoose");
        }

        int index = componentId == IdLeftProfession ? 0 : 1;
        int professionId = professions[index];
        string side = componentId == IdLeftProfession ? "left" : "right";

        Game1.player.professions.Add(professionId);
        menu.getImmediateProfessionPerk(professionId);
        menu.isActive = false;
        menu.informationUp = false;
        menu.isProfessionChooser = false;
        menu.RemoveLevelFromLevelList();

        var menuTypeAfter = Game1.activeClickableMenu?.GetType().Name ?? "None";
        var effect = menuTypeAfter != menuTypeBefore ? "menu_closed" : "profession_selected";

        monitor.Log($"[LevelUpMenuAdapter] Profession selected: {side} (professionId={professionId}) → {menuTypeAfter} ({effect})", LogLevel.Info);

        return new UiClickResult
        {
            Success = true,
            Method = "level_up_profession_select",
            Effect = effect,
            ComponentId = componentId,
            Source = MenuNavigator.SourcePage,
            MenuTypeBefore = menuTypeBefore,
            MenuTypeAfter = menuTypeAfter,
        };
    }
}
