using System;
using StardewModdingAPI;

namespace StarCoPlay.GameServices.SimulatedInput;

/// <summary>输入模拟的四种模式。</summary>
public enum InputMode
{
    /// <summary>单次点按（只在第 0 帧 Press 一次）。</summary>
    Tap,

    /// <summary>持续按住直到 StopCondition 为 true。</summary>
    Hold,

    /// <summary>持续按住固定帧数后自动释放。</summary>
    HoldFor,

    /// <summary>持续按住直到 ReleaseCondition 为 true，释放后完成。</summary>
    HoldAndRelease,
}

/// <summary>
/// 表示一个待执行的输入动作，由 InputBridge 在每帧驱动。
/// 通过静态工厂方法创建，避免构造参数歧义。
/// </summary>
public class InputAction
{
    /// <summary>要模拟的按键。</summary>
    public SButton Button { get; }

    /// <summary>输入模式。</summary>
    public InputMode Mode { get; }

    /// <summary>HoldFor 模式的持续帧数。</summary>
    public int DurationTicks { get; }

    /// <summary>Tap 模式的完成条件；为 null 时按完即完成。</summary>
    public Func<bool>? CompletionCheck { get; }

    /// <summary>Hold 模式的停止条件。</summary>
    public Func<bool>? StopCondition { get; }

    /// <summary>HoldAndRelease 模式的释放条件。</summary>
    public Func<bool>? ReleaseCondition { get; }

    /// <summary>超时帧数，超过后强制结束。</summary>
    public int TimeoutTicks { get; }

    /// <summary>已经过的帧数，由 InputBridge 递增。</summary>
    public int ElapsedTicks { get; set; }

    /// <summary>完成回调：参数 true=正常完成，false=超时。</summary>
    public Action<bool>? OnComplete { get; set; }

    private InputAction(SButton button, InputMode mode, int durationTicks,
        Func<bool>? completionCheck, Func<bool>? stopCondition,
        Func<bool>? releaseCondition, int timeoutTicks)
    {
        if (mode == InputMode.Hold && stopCondition == null)
            throw new ArgumentNullException(nameof(stopCondition), "Hold 模式必须提供 stopCondition");
        if (mode == InputMode.HoldAndRelease && releaseCondition == null)
            throw new ArgumentNullException(nameof(releaseCondition), "HoldAndRelease 模式必须提供 releaseCondition");

        Button = button;
        Mode = mode;
        DurationTicks = durationTicks;
        CompletionCheck = completionCheck;
        StopCondition = stopCondition;
        ReleaseCondition = releaseCondition;
        TimeoutTicks = timeoutTicks;
    }

    /// <summary>根据 Mode 决定当前帧是否应该按键。</summary>
    public bool ShouldPress()
    {
        return Mode switch
        {
            // Tap: 只在第 0 帧按一次
            InputMode.Tap => ElapsedTicks == 0,

            // Hold: 每帧持续按住，直到 StopCondition 满足
            InputMode.Hold => StopCondition == null || !StopCondition(),

            // HoldFor: 在 DurationTicks 内每帧按住
            InputMode.HoldFor => ElapsedTicks < DurationTicks,

            // HoldAndRelease: 每帧按住，直到 ReleaseCondition 满足
            InputMode.HoldAndRelease => ReleaseCondition == null || !ReleaseCondition(),

            _ => false,
        };
    }

    /// <summary>根据 Mode 检查动作是否已完成（含超时判定）。</summary>
    public bool IsComplete()
    {
        // 超时保护：所有模式通用
        if (TimeoutTicks > 0 && ElapsedTicks >= TimeoutTicks)
            return true;

        return Mode switch
        {
            // Tap: 按完后等待 CompletionCheck（为 null 则按完即完成）
            InputMode.Tap => ElapsedTicks > 0 && (CompletionCheck == null || CompletionCheck()),

            // Hold: StopCondition 返回 true 时完成
            InputMode.Hold => StopCondition != null && StopCondition(),

            // HoldFor: 按够指定帧数后完成
            InputMode.HoldFor => ElapsedTicks >= DurationTicks,

            // HoldAndRelease: ReleaseCondition 满足后完成
            InputMode.HoldAndRelease => ReleaseCondition != null && ReleaseCondition(),

            _ => true,
        };
    }

    /// <summary>是否因超时而结束（用于区分正常完成和超时）。</summary>
    public bool IsTimedOut => TimeoutTicks > 0 && ElapsedTicks >= TimeoutTicks;

    // ── 静态工厂方法 ──

    /// <summary>单次点按。</summary>
    /// <param name="button">按键</param>
    /// <param name="completion">完成条件，null 表示按完即完成</param>
    /// <param name="timeout">超时帧数（默认 120 帧 ≈ 2 秒）</param>
    public static InputAction CreateTap(SButton button, Func<bool>? completion = null, int timeout = 120)
    {
        return new InputAction(button, InputMode.Tap, 0, completion, null, null, timeout);
    }

    /// <summary>持续按住直到条件满足。</summary>
    /// <param name="button">按键</param>
    /// <param name="stopCondition">停止条件</param>
    /// <param name="timeout">超时帧数（默认 600 帧 ≈ 10 秒）</param>
    public static InputAction CreateHold(SButton button, Func<bool> stopCondition, int timeout = 600)
    {
        return new InputAction(button, InputMode.Hold, 0, null, stopCondition, null, timeout);
    }

    /// <summary>持续按住固定帧数。</summary>
    /// <param name="button">按键</param>
    /// <param name="durationTicks">持续帧数</param>
    public static InputAction CreateHoldFor(SButton button, int durationTicks)
    {
        // HoldFor 不需要超时，duration 本身就是终止条件
        return new InputAction(button, InputMode.HoldFor, durationTicks, null, null, null, 0);
    }

    /// <summary>持续按住直到释放条件满足。</summary>
    /// <param name="button">按键</param>
    /// <param name="releaseCondition">释放条件</param>
    /// <param name="timeout">超时帧数（默认 300 帧 ≈ 5 秒）</param>
    public static InputAction CreateHoldAndRelease(SButton button, Func<bool> releaseCondition, int timeout = 300)
    {
        return new InputAction(button, InputMode.HoldAndRelease, 0, null, null, releaseCondition, timeout);
    }
}
