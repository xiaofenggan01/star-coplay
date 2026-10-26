using System;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2;
using StarCoPlay.V2.Models;

namespace StarCoPlay.GameServices.SimulatedInput;

/// <summary>
/// Waits for tool/eat/harvest animations to complete before writing the result.
/// After triggering a game action (DoFunction, eatObject, etc.), the caller
/// registers a "pending" state via Begin(). Each frame, Tick() checks whether
/// the player has finished the animation (UsingTool==false, canMove==true,
/// no single-frame pause). Only then does it return the deferred V2Result.
///
/// While waiting, CommandProcessor should block new action commands but allow
/// observe/query through.
/// </summary>
public class AnimationAwaiter
{
    private readonly IMonitor _monitor;
    private readonly InputBridge? _inputBridge;

    private V2Command? _pendingCmd;
    private string _intent = "";
    private float _energyBefore;
    private int _startTick;
    private int _timeoutTicks;

    /// <summary>Whether an animation wait is currently active.</summary>
    public bool IsWaiting => _pendingCmd != null;

    /// <summary>
    /// 构造 AnimationAwaiter。
    /// </summary>
    /// <param name="monitor">SMAPI monitor for logging.</param>
    /// <param name="inputBridge">
    /// 可选的 InputBridge 引用。传入后 Tick 会优先检查 InputBridge.IsBusy，
    /// 在按键队列还没消化完时不会判定动画完成。
    /// V1 旧 Handler 不需要感知 InputBridge 队列，可不传（向后兼容）。
    /// </param>
    public AnimationAwaiter(IMonitor monitor, InputBridge? inputBridge = null)
    {
        _monitor = monitor;
        _inputBridge = inputBridge;
    }

    /// <summary>
    /// Begin waiting for an animation to complete.
    /// </summary>
    /// <param name="cmd">The V2 command that triggered the animation.</param>
    /// <param name="intent">Descriptive intent string (e.g. "water", "harvest", "use_tool", "consume").</param>
    /// <param name="energyBefore">Player stamina before the action, for side-effect tracking.</param>
    /// <param name="timeoutTicks">Maximum ticks to wait before forcing completion (default ~3 seconds).</param>
    public void Begin(V2Command cmd, string intent, float energyBefore, int timeoutTicks = 180)
    {
        if (_pendingCmd != null)
            throw new InvalidOperationException($"AnimationAwaiter already waiting for {_intent}");

        _pendingCmd = cmd;
        _intent = intent;
        _energyBefore = energyBefore;
        _startTick = Game1.ticks;
        _timeoutTicks = timeoutTicks;
        _monitor.Log($"[AnimWait] Begin: intent={intent}, seq={cmd.Seq}, timeout={timeoutTicks}", LogLevel.Debug);
    }

    /// <summary>
    /// Called each frame. Returns a V2Result when the animation has completed
    /// (or timed out), or null if still waiting.
    /// </summary>
    public V2Result? Tick()
    {
        if (_pendingCmd == null) return null;

        var player = Game1.player;
        var elapsed = Game1.ticks - _startTick;

        // Skip trigger frames — ensure Farmer.Update ran at least once
        if (elapsed < 2) return null;

        // 优先检查 InputBridge 队列是否还在执行：
        // V2 Handler（face/interact/use_tool/menu_click 等）通过 InputBridge 入队按键，
        // 实际按键注入会跨多个 tick 消化。如果队列没消化完就判定完成，会返回 success 但
        // 玩家其实还没动作 —— 必须等 InputBridge 空才能继续走 player 状态检测。
        // （旧 Handler 不传 InputBridge，_inputBridge 为 null 时跳过此检查，行为不变。）
        if (_inputBridge != null && _inputBridge.IsBusy)
        {
            // 不更新超时计时（仍由总 elapsed 兜底）；继续等待。
            return null;
        }

        // Animation complete: player can move again and is not in tool/eat animation
        if (!player.UsingTool && player.canMove
            && !player.FarmerSprite.PauseForSingleAnimation)
        {
            _monitor.Log($"[AnimWait] Complete: intent={_intent}, elapsed={elapsed} ticks", LogLevel.Debug);
            var result = BuildSuccess();
            Reset();
            return result;
        }

        // Timeout protection
        if (elapsed > _timeoutTicks)
        {
            _monitor.Log($"[AnimWait] Timeout ({elapsed} ticks), intent={_intent}", LogLevel.Warn);
            ForceResetPlayer(player);
            var result = BuildTimeoutFailure();
            Reset();
            return result;
        }

        return null;
    }

    /// <summary>
    /// Force-cancel any pending wait (e.g. when stuck recovery triggers).
    /// </summary>
    public void Cancel()
    {
        if (_pendingCmd != null)
        {
            _monitor.Log($"[AnimWait] Cancelled: intent={_intent}", LogLevel.Debug);
            Reset();
        }
    }

    private V2Result BuildSuccess()
    {
        var cmd = _pendingCmd!;
        return ResultBuilder.Success(cmd.Seq, cmd.ActionId, new { intent = _intent })
            .WithObservation($"已完成 {_intent}")
            .WithSideEffectIfEnergyChanged(_energyBefore);
    }

    private V2Result BuildTimeoutFailure()
    {
        var cmd = _pendingCmd!;
        return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
            ResultBuilder.Errors.AnimationTimeout,
            $"{_intent} 动画等待超时（{_timeoutTicks} ticks）");
    }

    private void ForceResetPlayer(Farmer player)
    {
        player.UsingTool = false;
        player.canMove = true;
        player.FarmerSprite.StopAnimation();
        player.completelyStopAnimatingOrDoingAction();
    }

    private void Reset()
    {
        _pendingCmd = null;
        _intent = "";
    }
}
