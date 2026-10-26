using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using StarCoPlay.Bridge;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;

namespace StarCoPlay.GameServices.SimulatedInput;

/// <summary>
/// AutoDismissHandler — automatically handles blocking UI menus during day transitions.
/// Called every frame from ModEntry.OnUpdateTicked (NOT from CommandProcessor).
/// Detects and dismisses: ShippingMenu, LevelUpMenu, SaveGameMenu.
/// Extracts key info before dismissing and emits events via EventEmitter.
/// </summary>
public class AutoDismissHandler
{
    private readonly EventEmitter _emitter;
    private readonly IMonitor _monitor;

    // ── State machine ────────────────────────────────────────
    private enum Phase
    {
        Idle,
        WaitingShippingAnimation,
        DismissingShipping,
        DismissingLevelUp,
        WaitingSave,
    }

    private Phase _phase = Phase.Idle;

    // ── Timing ───────────────────────────────────────────────
    private int _waitTicks;

    /// <summary>Cooldown ticks between dismiss attempts (avoid clicking every frame).</summary>
    private const int DismissCooldownTicks = 10;

    /// <summary>How long to wait for ShippingMenu animation before dismissing (~3 seconds).</summary>
    private const int ShippingAnimationWaitTicks = 180;

    /// <summary>Force-close ShippingMenu if okButton click doesn't work after this many ticks.</summary>
    private const int ShippingForceCloseTimeoutTicks = 120;

    /// <summary>Force-close SaveGameMenu if stuck for this many ticks (~10 seconds).</summary>
    private const int SaveForceCloseTimeoutTicks = 600;

    /// <summary>Cooldown counter — decrements each tick, skip dismiss when > 0.</summary>
    private int _cooldown;

    /// <summary>Ticks spent trying to dismiss the current menu (for force-close timeout).</summary>
    private int _dismissAttemptTicks;
    private bool _levelUpEmitted; // 防止重复发射 level_up 事件

    // ── Reflection cache for LevelUpMenu private fields ──────
    private static readonly FieldInfo? CurrentSkillField =
        typeof(LevelUpMenu).GetField("currentSkill", BindingFlags.NonPublic | BindingFlags.Instance);
    private static readonly FieldInfo? NewLevelField =
        typeof(LevelUpMenu).GetField("newLevel", BindingFlags.NonPublic | BindingFlags.Instance);

    // ── Skill ID → name mapping (Stardew Valley skill IDs) ──
    private static readonly Dictionary<int, string> SkillNames = new()
    {
        [0] = "Farming",
        [1] = "Fishing",
        [2] = "Foraging",
        [3] = "Mining",
        [4] = "Combat",
        [5] = "Luck",
    };

    public AutoDismissHandler(EventEmitter emitter, IMonitor monitor)
    {
        _emitter = emitter;
        _monitor = monitor;
    }

    /// <summary>
    /// Called every frame from ModEntry.OnUpdateTicked.
    /// Detects blocking day-transition menus and handles them.
    /// NOTE: Global Tick() call has been removed from ModEntry as of go_to_bed implementation.
    /// Cross-day menu handling is now owned by GoToBedProtoHandler + EndOfDayUiDriver.
    /// </summary>
    public void Tick()
    {
        // Cooldown — skip this tick
        if (_cooldown > 0)
        {
            _cooldown--;
            return;
        }

        var menu = Game1.activeClickableMenu;

        switch (_phase)
        {
            case Phase.Idle:
                DetectMenu(menu);
                break;
            case Phase.WaitingShippingAnimation:
                TickWaitingShippingAnimation(menu);
                break;
            case Phase.DismissingShipping:
                TickDismissingShipping(menu);
                break;
            case Phase.DismissingLevelUp:
                TickDismissingLevelUp(menu);
                break;
            case Phase.WaitingSave:
                TickWaitingSave(menu);
                break;
        }
    }

    // ════════════════════════════════════════════════════════
    //  Detection — identify which menu is active
    // ════════════════════════════════════════════════════════

    private void DetectMenu(IClickableMenu? menu)
    {
        if (menu == null) return;

        if (menu is ShippingMenu)
        {
            _monitor.Log("[AutoDismiss] ShippingMenu detected, waiting for animation...", LogLevel.Info);
            _phase = Phase.WaitingShippingAnimation;
            _waitTicks = 0;
            _dismissAttemptTicks = 0;
        }
        else if (menu is LevelUpMenu)
        {
            _monitor.Log("[AutoDismiss] LevelUpMenu detected", LogLevel.Info);
            _phase = Phase.DismissingLevelUp;
            _waitTicks = 0;
            _dismissAttemptTicks = 0;
        }
        else if (menu is SaveGameMenu)
        {
            _monitor.Log("[AutoDismiss] SaveGameMenu detected, waiting for save to complete...", LogLevel.Info);
            _phase = Phase.WaitingSave;
            _waitTicks = 0;
        }
    }

    // ════════════════════════════════════════════════════════
    //  ShippingMenu handling
    // ════════════════════════════════════════════════════════

    private void TickWaitingShippingAnimation(IClickableMenu? menu)
    {
        _waitTicks++;

        // Menu was closed externally
        if (menu is not ShippingMenu)
        {
            _monitor.Log("[AutoDismiss] ShippingMenu disappeared during animation wait", LogLevel.Debug);
            Reset();
            return;
        }

        // Wait for animation to complete
        if (_waitTicks < ShippingAnimationWaitTicks)
            return;

        _monitor.Log($"[AutoDismiss] ShippingMenu animation wait complete ({_waitTicks} ticks), extracting data and dismissing", LogLevel.Info);

        // Extract shipping data before dismissing
        ExtractAndEmitShippingData((ShippingMenu)menu);

        _phase = Phase.DismissingShipping;
        _dismissAttemptTicks = 0;
    }

    private void TickDismissingShipping(IClickableMenu? menu)
    {
        // Menu already gone — success
        if (menu is not ShippingMenu shippingMenu)
        {
            _monitor.Log("[AutoDismiss] ShippingMenu dismissed successfully", LogLevel.Info);
            Reset();
            // Check if a new menu appeared immediately (LevelUpMenu often follows)
            DetectMenu(Game1.activeClickableMenu);
            return;
        }

        _dismissAttemptTicks++;

        // Try clicking the OK button
        try
        {
            if (shippingMenu.okButton != null)
            {
                var bounds = shippingMenu.okButton.bounds;
                int cx = bounds.Center.X;
                int cy = bounds.Center.Y;
                shippingMenu.receiveLeftClick(cx, cy);
                _monitor.Log($"[AutoDismiss] Clicked ShippingMenu okButton at ({cx},{cy})", LogLevel.Debug);
                _cooldown = DismissCooldownTicks;
            }
        }
        catch (Exception ex)
        {
            _monitor.Log($"[AutoDismiss] ShippingMenu click exception: {ex.Message}", LogLevel.Warn);
        }

        // Force close if okButton click doesn't work after timeout
        if (_dismissAttemptTicks >= ShippingForceCloseTimeoutTicks)
        {
            _monitor.Log("[AutoDismiss] ShippingMenu force-closing after timeout", LogLevel.Warn);
            Game1.activeClickableMenu = null;
            Reset();
            DetectMenu(Game1.activeClickableMenu);
        }
    }

    private void ExtractAndEmitShippingData(ShippingMenu shippingMenu)
    {
        try
        {
            // Extract total earnings from the day
            int totalEarnings = 0;
            var shippedItems = new List<Dictionary<string, object>>();

            // Try to access categoryItems (List<List<Item>>)
            try
            {
                var categoryItemsField = typeof(ShippingMenu).GetField("categoryItems",
                    BindingFlags.NonPublic | BindingFlags.Instance);
                if (categoryItemsField?.GetValue(shippingMenu) is List<List<Item>> categories)
                {
                    foreach (var category in categories)
                    {
                        if (category == null) continue;
                        foreach (var item in category)
                        {
                            if (item == null) continue;
                            int sellPrice = item.sellToStorePrice() * item.Stack;
                            totalEarnings += sellPrice;
                            shippedItems.Add(new Dictionary<string, object>
                            {
                                ["name"] = item.DisplayName,
                                ["id"] = item.QualifiedItemId ?? "",
                                ["stack"] = item.Stack,
                                ["sell_price"] = sellPrice,
                            });
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                _monitor.Log($"[AutoDismiss] Failed to extract categoryItems: {ex.Message}", LogLevel.Debug);
            }

            // Fallback: try dayProfits field
            if (totalEarnings == 0)
            {
                try
                {
                    var dayProfitField = typeof(ShippingMenu).GetField("dayProfits",
                        BindingFlags.NonPublic | BindingFlags.Instance);
                    if (dayProfitField?.GetValue(shippingMenu) is int profits)
                    {
                        totalEarnings = profits;
                    }
                }
                catch
                {
                    // ignore
                }
            }

            var data = new Dictionary<string, object>
            {
                ["earnings"] = totalEarnings,
                ["items"] = shippedItems,
                ["item_count"] = shippedItems.Count,
            };

            _emitter.EmitDayTransition("shipping_complete", data);
            _monitor.Log($"[AutoDismiss] Shipping data: {shippedItems.Count} items, {totalEarnings}g earnings", LogLevel.Info);
        }
        catch (Exception ex)
        {
            _monitor.Log($"[AutoDismiss] Failed to extract shipping data: {ex.Message}", LogLevel.Warn);
            // Emit event anyway with empty data
            _emitter.EmitDayTransition("shipping_complete", new Dictionary<string, object>
            {
                ["earnings"] = 0,
                ["items"] = new List<Dictionary<string, object>>(),
                ["error"] = ex.Message,
            });
        }
    }

    // ════════════════════════════════════════════════════════
    //  LevelUpMenu handling
    // ════════════════════════════════════════════════════════

    private void TickDismissingLevelUp(IClickableMenu? menu)
    {
        // Menu gone — check for consecutive LevelUpMenus
        if (menu is not LevelUpMenu levelUpMenu)
        {
            _monitor.Log("[AutoDismiss] LevelUpMenu dismissed", LogLevel.Debug);
            Reset();
            // Another LevelUpMenu may appear immediately (multi-skill level up)
            DetectMenu(Game1.activeClickableMenu);
            return;
        }

        _dismissAttemptTicks++;

        // Wait a small amount before first dismiss attempt (let menu render)
        if (_dismissAttemptTicks < 30)
            return;

        // Extract level-up data (only once per menu instance)
        if (!_levelUpEmitted)
        {
            _levelUpEmitted = true;
            try
            {
                int currentSkill = -1;
                int newLevel = -1;

                if (CurrentSkillField != null)
                    currentSkill = (int)(CurrentSkillField.GetValue(levelUpMenu) ?? -1);
                if (NewLevelField != null)
                    newLevel = (int)(NewLevelField.GetValue(levelUpMenu) ?? -1);

                string skillName = currentSkill >= 0 && SkillNames.TryGetValue(currentSkill, out var name)
                    ? name
                    : $"Unknown({currentSkill})";

                _monitor.Log($"[AutoDismiss] LevelUp: {skillName} -> level {newLevel}", LogLevel.Info);
                _emitter.EmitLevelUp(skillName, newLevel);
            }
            catch (Exception ex)
            {
                _monitor.Log($"[AutoDismiss] Failed to extract LevelUp data: {ex.Message}", LogLevel.Warn);
            }
        }

        // 职业选择菜单（等级 5/10）— 不自动关闭，需要玩家或 Agent 选择
        if (levelUpMenu.isProfessionChooser)
        {
            _monitor.Log("[AutoDismiss] LevelUpMenu is profession chooser — skipping auto-dismiss, Agent needs to choose", LogLevel.Info);
            // 不关闭，不重置。Agent 侧应通过 query_ui_snapshot 发现并用 answer 选择职业。
            return;
        }

        // 普通升级通知 — 点击 okButton 关闭
        try
        {
            if (levelUpMenu.okButton != null)
            {
                var bounds = levelUpMenu.okButton.bounds;
                int cx = bounds.Center.X;
                int cy = bounds.Center.Y;
                levelUpMenu.receiveLeftClick(cx, cy);
                _monitor.Log($"[AutoDismiss] Clicked LevelUpMenu okButton at ({cx},{cy})", LogLevel.Debug);
                _cooldown = DismissCooldownTicks;
            }
            else
            {
                levelUpMenu.receiveLeftClick(0, 0);
                _cooldown = DismissCooldownTicks;
            }
        }
        catch (Exception ex)
        {
            _monitor.Log($"[AutoDismiss] LevelUpMenu click exception: {ex.Message}", LogLevel.Warn);
        }

        // Force close after extended timeout (仅非职业选择菜单)
        if (_dismissAttemptTicks >= ShippingForceCloseTimeoutTicks)
        {
            _monitor.Log("[AutoDismiss] LevelUpMenu force-closing after timeout", LogLevel.Warn);
            Game1.activeClickableMenu = null;
            Reset();
            DetectMenu(Game1.activeClickableMenu);
        }
    }

    // ════════════════════════════════════════════════════════
    //  SaveGameMenu handling
    // ════════════════════════════════════════════════════════

    private void TickWaitingSave(IClickableMenu? menu)
    {
        // Save completed — menu is gone
        if (menu is not SaveGameMenu)
        {
            _monitor.Log("[AutoDismiss] SaveGameMenu completed", LogLevel.Info);
            _emitter.EmitDayTransition("save_complete", new Dictionary<string, object>
            {
                ["wait_ticks"] = _waitTicks,
            });
            Reset();
            // Check for any post-save menus
            DetectMenu(Game1.activeClickableMenu);
            return;
        }

        _waitTicks++;

        // Force close only if stuck for a very long time
        if (_waitTicks >= SaveForceCloseTimeoutTicks)
        {
            _monitor.Log($"[AutoDismiss] SaveGameMenu stuck for {_waitTicks} ticks, force-closing", LogLevel.Warn);
            Game1.activeClickableMenu = null;
            _emitter.EmitDayTransition("save_complete", new Dictionary<string, object>
            {
                ["wait_ticks"] = _waitTicks,
                ["force_closed"] = true,
            });
            Reset();
        }
    }

    // ════════════════════════════════════════════════════════
    //  Reset
    // ════════════════════════════════════════════════════════

    private void Reset()
    {
        _phase = Phase.Idle;
        _waitTicks = 0;
        _cooldown = 0;
        _dismissAttemptTicks = 0;
        _levelUpEmitted = false;
    }
}
