using System;
using System.Collections.Generic;
using System.Reflection;
using StardewModdingAPI;
using StardewValley;

namespace StarCoPlay.GameServices.SimulatedInput;

/// <summary>
/// L0 按键模拟引擎。每帧驱动一个 <see cref="InputAction"/>，支持 4 种交互模式
/// （Tap / Hold / HoldFor / HoldAndRelease）。必须在 UpdateTicking 事件中调用 <see cref="OnUpdateTicking"/>。
///
/// 按键路径：所有按键统一走 SMAPI <see cref="IInputHelper.Press"/>。
/// 失焦支持依赖 <see cref="InputBridgeSimulator"/> 作为"总开关"——SDV 1.6 一旦
/// <c>Game1.inputSimulator</c> 非空，<c>UpdateControlInput</c> 会在失焦下仍被调用，
/// SDV 内部读 <c>currentKBState</c> 时拿到 SMAPI Press 注入的所有按键。
///
/// 失焦下 sticky 释放：SMAPI <c>SInputState.TrueUpdate</c> 仅在 <c>Game1.IsActive</c>
/// 为 true 时被调用（<c>SCore.cs:719-724</c>），TrueUpdate 末尾的
/// <c>CustomPressedKeys.Clear()</c>（<c>SInputState.cs:114</c>）就是聚焦下 Press 单帧
/// 自动失效的清理来源。失焦下 TrueUpdate 不跑 → CustomPressedKeys 不清 →
/// SDV 通过 inputSimulator 总开关仍调 UpdateControlInput → 读到 sticky 按键。
///
/// 修复路径：每帧 ShouldPress() 由 true → false 那一帧（边沿）通过反射调
/// <c>SInputState.OverrideButton(button, false)</c>（<c>SInputState.cs:163-171</c>）
/// 把按键从 CustomPressedKeys 移除并加入 CustomReleasedKeys；再调
/// <c>SInputState.ApplyOverrides()</c>（<c>SInputState.cs:181-200</c>）立即把
/// 释放语义同步到 SInputState 缓存的 KeyboardState 字段。
/// </summary>
public class InputBridge
{
    private readonly IInputHelper _input;
    private readonly IMonitor _monitor;
    private readonly InputBridgeSimulator _simulator;
    private InputAction? _current;
    private readonly Queue<InputAction> _queue = new();

    /// <summary>当前 _current 是否在上一帧执行了 Press（用于 ShouldPress=false 边沿检测）。</summary>
    private bool _wasPressedLastTick;

    /// <summary>当前 tick 已注入 override 的按键（用于完成/取消时统一释放）。</summary>
    private readonly HashSet<SButton> _pressedOverrides = new();

    // ── SInputState 反射缓存（用于失焦下 sticky 释放） ──
    private static bool _reflectionInited;
    private static object? _sInputState;
    private static MethodInfo? _overrideButtonMethod;
    private static MethodInfo? _applyOverridesMethod;

    public InputBridge(IInputHelper input, IMonitor monitor)
    {
        _input = input;
        _monitor = monitor;
        _simulator = new InputBridgeSimulator(monitor);
    }

    /// <summary>暴露 simulator 实例，供 ModEntry 在 SaveLoaded 时 Install。</summary>
    public InputBridgeSimulator Simulator => _simulator;

    // ── 状态查询 ──

    /// <summary>是否有正在执行或排队中的动作。</summary>
    public bool IsBusy => _current != null || _queue.Count > 0;

    /// <summary>玩家是否处于可操作状态（可移动、未使用工具、无菜单）。</summary>
    public bool IsPlayerFree =>
        Context.CanPlayerMove
        && !Game1.player.UsingTool
        && Game1.activeClickableMenu == null;

    // ── 公开方法：4 种交互模式 ──

    /// <summary>单次点按。</summary>
    public void Tap(SButton button, Func<bool>? completion = null, int timeout = 120)
    {
        Enqueue(InputAction.CreateTap(button, completion, timeout));
    }

    /// <summary>单次点按，带完成回调（true=正常完成，false=超时）。</summary>
    public void TapTracked(SButton button, Func<bool>? completion, int timeout, Action<bool> onComplete)
    {
        var action = InputAction.CreateTap(button, completion, timeout);
        action.OnComplete = onComplete;
        Enqueue(action);
    }

    /// <summary>持续按住直到条件满足。</summary>
    public void Hold(SButton button, Func<bool> stopCondition, int timeout = 600)
    {
        Enqueue(InputAction.CreateHold(button, stopCondition, timeout));
    }

    /// <summary>持续按住固定帧数。</summary>
    public void HoldFor(SButton button, int durationTicks)
    {
        Enqueue(InputAction.CreateHoldFor(button, durationTicks));
    }

    /// <summary>持续按住直到释放条件满足。</summary>
    public void HoldAndRelease(SButton button, Func<bool> releaseCondition, int timeout = 300)
    {
        Enqueue(InputAction.CreateHoldAndRelease(button, releaseCondition, timeout));
    }

    // ── 控制方法 ──

    /// <summary>取消当前动作（回调 false），继续队列中下一个。</summary>
    public void Cancel()
    {
        if (_current == null)
            return;

        _monitor.Log($"[InputBridge] 取消动作: {_current.Button} ({_current.Mode})", LogLevel.Debug);
        ReleaseAllOverrides();
        // 与 Clear() 路径保持一致：吞掉回调异常，避免阻塞队列推进。
        try { _current.OnComplete?.Invoke(false); } catch { /* 回调异常不阻塞取消 */ }
        AdvanceQueue();
    }

    /// <summary>取消当前动作并清空队列。</summary>
    public void Clear()
    {
        if (_current != null)
        {
            _monitor.Log($"[InputBridge] 清空: 取消当前 {_current.Button} ({_current.Mode})，队列 {_queue.Count} 个待执行", LogLevel.Debug);
            ReleaseAllOverrides();
            try { _current.OnComplete?.Invoke(false); } catch { /* 回调异常不阻塞清空 */ }
            _current = null;
            _wasPressedLastTick = false;
        }

        // 队列中的动作也触发失败回调
        while (_queue.Count > 0)
        {
            var action = _queue.Dequeue();
            try { action.OnComplete?.Invoke(false); } catch { /* 回调异常不阻塞清空 */ }
        }
    }

    // ── 帧驱动 ──

    /// <summary>
    /// 每帧调用一次（在 UpdateTicking 事件中）。驱动当前 InputAction 的按键模拟和完成检测，
    /// 同时驱动 simulator 的 ESC 自动重 install 检测。
    /// </summary>
    public void OnUpdateTicking()
    {
        EnsureInputReflection();

        // 0. 维持 simulator 安装状态。SDV 会因 Escape 卸载 inputSimulator；这是游戏内部行为，
        //    不代表 AI cancel。InputBridge 仅重装 simulator，命令取消统一走 protocol cancel。
        if (_simulator.WasInstalled && !_simulator.IsInstalled && Context.IsWorldReady)
        {
            _monitor.Log("[InputBridge] inputSimulator 被 SDV 卸载，自动重 install；不清空输入队列", LogLevel.Trace);
        }
        _simulator.OnUpdateTicking();

        // 1. 如果没有当前动作，尝试从队列取
        if (_current == null)
        {
            if (_queue.Count == 0)
                return;

            _current = _queue.Dequeue();
            _wasPressedLastTick = false;
            _monitor.Log($"[InputBridge] 开始动作: {_current.Button} ({_current.Mode})", LogLevel.Trace);
        }

        // 2. 决定本帧的 Press / Release 状态
        //    实测：SMAPI IInputHelper.Press 走的不是 SInputState.OverrideButton(SButton,bool) 重载
        //    （InputDiagnosticPatch.Cam1 拦截 OverrideButton(SButton,bool) 收不到 Press 触发的 down=true 调用），
        //    失焦下走 IInputHelper.Press 路径不能让 SDV 读到按键。改为直接反射调 OverrideButton(btn, true) 保证
        //    与 Release 路径对称、统一走"按键加入 CustomPressedKeys"的入口。
        bool shouldPress = _current.ShouldPress();
        if (shouldPress)
        {
            PressOverride(_current.Button);
            _wasPressedLastTick = true;
        }
        else if (_wasPressedLastTick)
        {
            // 边沿：从"按"切换到"不按"——立刻释放 override 防止失焦下 sticky
            ReleaseOverride(_current.Button);
            _wasPressedLastTick = false;
        }

        // 3. 递增帧计数
        _current.ElapsedTicks++;

        // 4. 检查是否完成
        if (_current.IsComplete())
        {
            bool success = !_current.IsTimedOut;

            if (success)
                _monitor.Log($"[InputBridge] 动作完成: {_current.Button} ({_current.Mode})，耗时 {_current.ElapsedTicks} 帧", LogLevel.Trace);
            else
                _monitor.Log($"[InputBridge] 动作超时: {_current.Button} ({_current.Mode})，超时 {_current.TimeoutTicks} 帧", LogLevel.Warn);

            // 兜底：完成时确保按键已释放（防止 HoldFor 末帧 ShouldPress=true 后立即 IsComplete 跳过边沿释放）
            ReleaseAllOverrides();

            try
            {
                _current.OnComplete?.Invoke(success);
            }
            finally
            {
                AdvanceQueue();
            }
        }
    }

    // ── 内部方法 ──

    private void Enqueue(InputAction action)
    {
        _queue.Enqueue(action);
        _monitor.Log($"[InputBridge] 入队: {action.Button} ({action.Mode})，队列深度 {_queue.Count}", LogLevel.Trace);
    }

    /// <summary>从队列取下一个动作，或置为 null。</summary>
    private void AdvanceQueue()
    {
        _wasPressedLastTick = false;
        if (_queue.Count > 0)
        {
            _current = _queue.Dequeue();
            _monitor.Log($"[InputBridge] 下一个动作: {_current.Button} ({_current.Mode})", LogLevel.Trace);
        }
        else
        {
            _current = null;
        }
    }

    // ── SMAPI SInputState 反射工具（用于失焦下显式 Unpress） ──

    /// <summary>
    /// Lazy 初始化反射 SInputState 实例 + OverrideButton(SButton, bool) 方法 + ApplyOverrides() 方法。
    /// SMAPI 内部 InputState 实例就是 Game1.input（SMAPI 启动时把 Game1.input 替换为 SInputState）。
    /// 失败时所有 Release* 退化为 noop（聚焦下仍正常，因为 TrueUpdate 末尾自动清 CustomPressedKeys）。
    /// </summary>
    private void EnsureInputReflection()
    {
        if (_reflectionInited) return;
        _reflectionInited = true;

        try
        {
            var inputField = typeof(Game1).GetField("input",
                BindingFlags.Public | BindingFlags.Static | BindingFlags.NonPublic);
            _sInputState = inputField?.GetValue(null);
            if (_sInputState == null)
            {
                _monitor.Log("[InputBridge] 找不到 Game1.input 字段，失焦 sticky 释放将退化为 noop", LogLevel.Warn);
                return;
            }

            var t = _sInputState.GetType();
            _overrideButtonMethod = t.GetMethod("OverrideButton",
                BindingFlags.Public | BindingFlags.Instance,
                binder: null,
                types: new[] { typeof(SButton), typeof(bool) },
                modifiers: null);
            _applyOverridesMethod = t.GetMethod("ApplyOverrides",
                BindingFlags.Public | BindingFlags.Instance,
                binder: null,
                types: Type.EmptyTypes,
                modifiers: null);

            if (_overrideButtonMethod == null)
                _monitor.Log("[InputBridge] SInputState.OverrideButton(SButton,bool) 反射失败，失焦 sticky 释放将退化为 noop", LogLevel.Warn);
            else
                _monitor.Log("[InputBridge] SInputState 反射就绪：OverrideButton + ApplyOverrides 可用", LogLevel.Info);
        }
        catch (Exception ex)
        {
            _monitor.Log($"[InputBridge] SInputState 反射异常: {ex.Message}", LogLevel.Warn);
        }
    }

    /// <summary>
    /// 反射调 SInputState.OverrideButton(button, true) 把按键加入 CustomPressedKeys。
    /// 失败时 fallback 到 IInputHelper.Press（聚焦下仍可工作）。立即 ApplyOverrides 同步 KeyboardState 字段。
    /// </summary>
    private void PressOverride(SButton button)
    {
        _pressedOverrides.Add(button);
        if (_overrideButtonMethod != null && _sInputState != null)
        {
            try
            {
                _overrideButtonMethod.Invoke(_sInputState, new object[] { button, true });
            }
            catch (Exception ex)
            {
                _monitor.Log($"[InputBridge] OverrideButton({button},true) 反射调用失败，回退 IInputHelper.Press: {ex.Message}", LogLevel.Warn);
                _input.Press(button);
            }
        }
        else
        {
            _input.Press(button);
        }
        ApplyOverrides();
    }

    /// <summary>把单个按键从 SMAPI override 中清掉（setDown=false 走 CustomReleasedKeys 路径）。</summary>
    private void ReleaseOverride(SButton button)
    {
        _pressedOverrides.Remove(button);
        if (_overrideButtonMethod == null || _sInputState == null) return;

        try
        {
            _overrideButtonMethod.Invoke(_sInputState, new object[] { button, false });
        }
        catch (Exception ex)
        {
            _monitor.Log($"[InputBridge] OverrideButton({button},false) 反射调用失败: {ex.Message}", LogLevel.Warn);
        }
        ApplyOverrides();
    }

    /// <summary>调 SInputState.ApplyOverrides() 立即把 CustomPressed/Released Keys 写入 KeyboardState 字段。</summary>
    private void ApplyOverrides()
    {
        if (_applyOverridesMethod == null || _sInputState == null) return;
        try { _applyOverridesMethod.Invoke(_sInputState, null); }
        catch (Exception ex) { _monitor.Log($"[InputBridge] ApplyOverrides 调用失败: {ex.Message}", LogLevel.Warn); }
    }

    /// <summary>释放本动作期间所有注入过的按键（完成/取消/清空时调用，兜底防 stuck）。</summary>
    private void ReleaseAllOverrides()
    {
        if (_pressedOverrides.Count == 0) return;
        // 拷贝避免迭代过程中修改集合
        var snapshot = new SButton[_pressedOverrides.Count];
        _pressedOverrides.CopyTo(snapshot);
        foreach (var btn in snapshot)
            ReleaseOverride(btn);
    }
}
