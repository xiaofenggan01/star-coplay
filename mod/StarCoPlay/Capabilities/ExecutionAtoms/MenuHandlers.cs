using System.Collections.Generic;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.V2;
using StarCoPlay.V2.Compound;
using StarCoPlay.V2.Models;

namespace StarCoPlay.Capabilities.ExecutionAtoms;

/// <summary>
/// L1 V2 — open_menu 命令：按 E 键打开 GameMenu。
///
/// 入参契约：（无）
///
/// 行为：
/// - 已有菜单时返回 INVALID_PARAMS（避免与 close→open 链路歧义）
/// - 入队 InputCombo.OpenMenu（按 E + 等待 activeClickableMenu != null）
///
/// 注意：本 Handler 仅入队按键，不阻塞等待菜单实际打开；
/// 调用方需要后续 query_ui_snapshot / observe 验证菜单已就绪。
/// 注：原"失焦防呆"已在 Stage A 切换到 IInputSimulator 后移除（simulator 路径失焦也能工作）。
/// </summary>
public class OpenMenuHandler
{
    private readonly InputCombo _combo;
    private readonly AnimationAwaiter _animAwaiter;
    private readonly IMonitor _monitor;

    public OpenMenuHandler(InputCombo combo, AnimationAwaiter animAwaiter, IMonitor monitor)
    {
        _combo = combo;
        _animAwaiter = animAwaiter;
        _monitor = monitor;
    }

    public V2Result? Handle(V2Command cmd)
    {
        // 0. cmd.Params 防御（open_menu 无参，但保留对称模板，未来扩展不易踩坑）
        _ = cmd.Params ?? new Dictionary<string, object>();

        // 注：原"失焦防呆（IsActive）"已在 Stage A 切换到 IInputSimulator 后移除。

        // 1. 已有菜单时拒绝（避免按 E 后产生关闭/切换的歧义行为）
        if (Game1.activeClickableMenu != null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                $"已有菜单打开: {Game1.activeClickableMenu.GetType().Name}");
        }

        _monitor.Log("[ExecutionAtom] open_menu: tap E", LogLevel.Debug);
        _combo.OpenMenu();

        // 异步等待：InputBridge.IsBusy 消化完按键后再返回 success。
        // 菜单操作不消耗 stamina，timeout 设为 180 ticks (~3s)，覆盖按 E + 等待 activeClickableMenu 出现。
        _animAwaiter.Begin(cmd, "open_menu", Game1.player.Stamina, timeoutTicks: 180);
        return null;
    }
}

/// <summary>
/// L1 V2 — menu_click 命令：DPad 导航到指定组件 + A 键确认。
///
/// 入参契约：
///   component_id: int    目标组件 myID
///   source: string       可选，命名空间 "page"/"tab"，默认 "page"
///                        （GameMenu 顶部 tab 按钮用 "tab"，子 page 内组件用 "page"）
///
/// 行为：
/// - 无菜单时返回 INVALID_PARAMS
/// - MenuNavigator.Click 失败（不可达）时返回 NOT_SUPPORTED
/// 注：原"失焦防呆"已在 Stage A 切换到 IInputSimulator 后移除（simulator 路径失焦也能工作）。
/// </summary>
public class MenuClickHandler
{
    private readonly MenuNavigator _navigator;
    private readonly AnimationAwaiter _animAwaiter;
    private readonly IMonitor _monitor;

    public MenuClickHandler(MenuNavigator navigator, AnimationAwaiter animAwaiter, IMonitor monitor)
    {
        _navigator = navigator;
        _animAwaiter = animAwaiter;
        _monitor = monitor;
    }

    public V2Result? Handle(V2Command cmd)
    {
        // 0. cmd.Params 防御
        var p = cmd.Params ?? new Dictionary<string, object>();

        // 注：原"失焦防呆（IsActive）"已在 Stage A 切换到 IInputSimulator 后移除。

        // 1. 必须先有活动菜单
        if (Game1.activeClickableMenu == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "menu_click 需要先打开菜单");
        }

        // 3. 解析 component_id（必填）
        if (!CompoundUtils.TryGetInt(p, "component_id", out var componentId))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "menu_click 需要 component_id 参数");
        }

        // 4. 解析 source（可选，默认 "page"）
        string source = MenuNavigator.SourcePage;
        if (CompoundUtils.TryGetString(p, "source", out var s) && !string.IsNullOrEmpty(s))
        {
            source = s;
        }

        _monitor.Log($"[ExecutionAtom] menu_click: target={source}:{componentId}", LogLevel.Debug);

        // 5. 委托 MenuNavigator 执行 BFS 导航 + A 键确认
        bool ok = _navigator.Click(componentId, source);
        if (!ok)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.NotSupported,
                $"menu_click 失败：组件 {source}:{componentId} 不可达");
        }

        // 异步等待 InputBridge 消化完 DPad 序列 + A 键。
        // 菜单导航可能是多步 DPad 移动，timeout 设为 180 ticks (~3s)。
        _animAwaiter.Begin(cmd, $"menu_click({source}:{componentId})", Game1.player.Stamina, timeoutTicks: 180);
        return null;
    }
}

/// <summary>
/// L1 V2 — menu_close 命令：按 ESC 关闭当前菜单。
///
/// 入参契约：（无）
///
/// 行为：
/// - 无菜单时返回 success + already_closed=true（幂等语义）
/// - 入队 InputCombo.CloseMenu（按 ESC + 等待 activeClickableMenu == null）
/// 注：原"失焦防呆"已在 Stage A 切换到 IInputSimulator 后移除（simulator 路径失焦也能工作）。
/// </summary>
public class MenuCloseHandler
{
    private readonly InputCombo _combo;
    private readonly AnimationAwaiter _animAwaiter;
    private readonly IMonitor _monitor;

    public MenuCloseHandler(InputCombo combo, AnimationAwaiter animAwaiter, IMonitor monitor)
    {
        _combo = combo;
        _animAwaiter = animAwaiter;
        _monitor = monitor;
    }

    public V2Result? Handle(V2Command cmd)
    {
        // 0. cmd.Params 防御（menu_close 无参，对称模板）
        _ = cmd.Params ?? new Dictionary<string, object>();

        // 注：原"失焦防呆（IsActive）"已在 Stage A 切换到 IInputSimulator 后移除。

        // 1. 无菜单视为幂等成功（避免上层重复关闭报错）
        if (Game1.activeClickableMenu == null)
        {
            _monitor.Log("[ExecutionAtom] menu_close: 无菜单，跳过", LogLevel.Trace);
            return ResultBuilder.Success(cmd.Seq, cmd.ActionId, new { already_closed = true })
                .WithObservation("无菜单打开，无需关闭");
        }

        _monitor.Log("[ExecutionAtom] menu_close: tap ESC", LogLevel.Debug);
        _combo.CloseMenu();

        // 异步等待：InputBridge 消化按键 + 等 activeClickableMenu 实际关闭。
        _animAwaiter.Begin(cmd, "menu_close", Game1.player.Stamina, timeoutTicks: 180);
        return null;
    }
}

// 参数解析下沉到 CompoundUtils.TryGetInt / TryGetString（C1 修复，避免 5 处独立实现漂移）。
// 原 MenuHandlerUtilsV2 静态类已删除。
