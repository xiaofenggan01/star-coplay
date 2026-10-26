using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2.Compound;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.V2;
using StarCoPlay.V2.Models;

namespace StarCoPlay.Capabilities.ExecutionAtoms;

/// <summary>
/// L1 V2 — use_tool 命令：按 C 键对面前 tile 使用当前手持工具。
///
/// 与旧 <see cref="UseToolHandler"/> 的区别：
/// - 旧版通过 ActionExecutor.UseToolAt 直接调用 DoFunction/BeginUsingTool，绕过键位输入。
/// - V2 版改为通过 InputCombo 入队 face + tap C（或 hold_for C 蓄力），
///   让游戏自身根据当前手持工具决定执行效果，与真实玩家操作一致。
///
/// 蓄力支持：
/// - charge_level=0（默认）：点按一次 C（无蓄力，等价于普通工具使用）
/// - charge_level=1..4：长按 C，每级约 36 ticks（600ms @60fps），
///   对应升级后的锄头/水壶范围操作（铜=1、钢=2、金=3、铱=4）
///
/// 入参契约：
///   x: int            目标 tile X
///   y: int            目标 tile Y
///   charge_level: int 可选，默认 0，范围 0-5
///
/// 注意：本 Handler 不做工具切换、不解析 tool_category；
/// 调用方应通过 equip 命令先装备好工具，再调用 use_tool。
/// </summary>
public class UseToolHandler
{
    private readonly InputCombo _combo;
    private readonly AnimationAwaiter _animAwaiter;
    private readonly IMonitor _monitor;

    /// <summary>
    /// 每级蓄力 ticks（SDV 1.6 实测约 600ms/级 = 36 ticks @60fps）。
    /// 与 <see cref="InputCombo.UseToolCharged"/> 内的常量一致。
    /// </summary>
    private const int TicksPerChargeLevel = 36;

    // 注：本 Handler 不直接持有 InputBridge —— 入队由 InputCombo 完成，
    // InputBridge 队列状态感知已下沉到 AnimationAwaiter（构造时持有 InputBridge 引用）。
    public UseToolHandler(
        InputCombo combo,
        AnimationAwaiter animAwaiter,
        IMonitor monitor)
    {
        _combo = combo;
        _animAwaiter = animAwaiter;
        _monitor = monitor;
    }

    public V2Result? Handle(V2Command cmd)
    {
        // 0. cmd.Params 防御
        var p = cmd.Params ?? new System.Collections.Generic.Dictionary<string, object>();

        // 注：原"失焦防呆（IsActive）"已在 Stage A 切换到 IInputSimulator 后移除。
        // simulator 路径直接覆写 SDV 内部 ref bool，失焦也能工作；防呆变成 anti-feature。

        // M7：工具未装备防御 —— use_tool 假设 player.CurrentTool 不为空。
        if (Game1.player.CurrentTool == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.WrongTool,
                "use_tool 失败：当前未装备工具，请先 equip");
        }

        // 2. 解析坐标参数
        if (!CompoundUtils.TryGetInt(p, "x", out var x) || !CompoundUtils.TryGetInt(p, "y", out var y))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "use_tool 需要 x, y 参数");
        }

        // 3. 解析可选的蓄力参数（默认 0 = 无蓄力）
        int chargeLevel = CompoundUtils.TryGetInt(p, "charge_level", out var cl) ? cl : 0;

        // 4. 验证 charge_level 范围（0=无蓄力，1-4=工具升级蓄力，5 留作冗余）
        if (chargeLevel < 0 || chargeLevel > 5)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                $"charge_level 必须在 0-5 之间，实际: {chargeLevel}");
        }

        var player = Game1.player;
        var toolName = player.CurrentTool?.DisplayName ?? player.CurrentTool?.Name ?? "无工具";
        var target = new Vector2(x, y);

        // 5. 入队对应模式
        if (chargeLevel == 0)
        {
            _monitor.Log(
                $"[ExecutionAtom] use_tool: tap C at ({x},{y}) tool={toolName}",
                LogLevel.Debug);
            _combo.UseTool(target);
        }
        else
        {
            _monitor.Log(
                $"[ExecutionAtom] use_tool: hold C at ({x},{y}) tool={toolName} charge={chargeLevel}",
                LogLevel.Debug);
            _combo.UseToolCharged(target, chargeLevel);
        }

        // 6. 注册异步等待
        // 蓄力模式下需要更长的超时：基础动画窗口 (120 ticks) + 蓄力按住时长。
        // 蓄力等级越高，HoldFor 入队的按住 ticks 越多，需相应延长 AnimationAwaiter 超时，
        // 否则蓄力还没结束就触发 ANIMATION_TIMEOUT。
        int timeoutTicks = chargeLevel == 0
            ? 120
            : 120 + chargeLevel * TicksPerChargeLevel;

        _animAwaiter.Begin(
            cmd,
            chargeLevel == 0 ? $"use_tool({toolName})" : $"use_tool({toolName},charge={chargeLevel})",
            player.Stamina,
            timeoutTicks: timeoutTicks);

        return null;
    }

    // 参数解析下沉到 CompoundUtils.TryGetInt（C1 修复，避免 5 处独立实现漂移）。
}
