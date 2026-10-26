using System;
using System.Reflection;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Util;

namespace StarCoPlay.GameServices.SimulatedInput;

/// <summary>
/// SDV 1.6 <see cref="IInputSimulator"/> 的"总开关"实现。
///
/// 设计动机：SMAPI <c>IInputHelper.Press</c> 仅在窗口聚焦时被 SDV 消费
/// （<c>IsActiveNoOverlay=false</c> 时整个 <c>UpdateControlInput</c> 被跳过）。
/// 但 PoC 实测 + SMAPI 反编译确认：一旦 <c>Game1.inputSimulator</c> 非空，
/// SDV 在失焦下仍会调用 <c>UpdateControlInput</c>，并在其内部读 <c>currentKBState</c>
/// 拿到 SMAPI Press 注入的所有按键（参见 Game1.cs:3898 + Game1.cs:12425）。
///
/// 因此本类的职责退化为：**装上去就够了**。<see cref="SimulateInput"/> 不写任何 ref bool，
/// 让 SDV 完全走 OS 输入解析（聚焦下）+ SMAPI Press 注入（失焦下也被消费）的常规路径。
/// 所有按键模拟历史代码（HoldFor 蓄力 / 边缘触发 / D1-D9 工具栏切换 / 鼠标 / 手柄）
/// 在聚焦下已经全量验证通过，叠加 simulator 总开关后失焦下同样工作。
///
/// SDV 会因 Escape 卸载 inputSimulator（`Game1.cs:12427` 检测到
/// `currentKBState.IsKeyDown(Keys.Escape)` 时清空 inputSimulator）。
/// 这是游戏内部行为，不代表 AI cancel。命令取消统一走 protocol cancel。
///
/// 我们每帧检查若 inputSimulator==null 且世界就绪，则自动重新 install——
/// 保证 AI 命令模拟的按键（包括 Escape Tap）不会因 SDV 卸载 simulator 而丢失。
///
/// SDV 1.5 / 更早版本反射查不到 inputSimulator 字段时全部 noop，等价于退回纯 SMAPI Press
/// 路径（聚焦下正常，失焦下不工作），不阻断 Mod 加载。
/// </summary>
public class InputBridgeSimulator : IInputSimulator
{
    private readonly IMonitor _monitor;
    private readonly FieldInfo? _simulatorField;

    // ── 诊断 ──
    public int TotalCalls { get; private set; }
    public int LastCallTick { get; private set; }

    public InputBridgeSimulator(IMonitor monitor)
    {
        _monitor = monitor ?? throw new ArgumentNullException(nameof(monitor));
        _simulatorField = typeof(Game1).GetField("inputSimulator",
            BindingFlags.NonPublic | BindingFlags.Static);
        if (_simulatorField == null)
        {
            _monitor.Log(
                "[InputBridgeSimulator] 找不到 Game1.inputSimulator 字段（SDV < 1.6？）—— 降级为 noop，将退回纯 SMAPI Press 路径（聚焦下正常，失焦下不工作）",
                LogLevel.Warn);
        }
    }

    /// <summary>当前是否已 install 到 Game1.inputSimulator。SDV 1.5 降级时恒为 false。</summary>
    public bool IsInstalled => _simulatorField != null && ReferenceEquals(_simulatorField.GetValue(null), this);

    /// <summary>上一次 OnUpdateTicking 时的 IsInstalled 值（用于 InputBridge 检测 ESC 卸载边沿）。</summary>
    public bool WasInstalled { get; private set; }

    /// <summary>把自己设到 Game1.inputSimulator。重复调用幂等；SDV 1.5 降级时 noop。</summary>
    public void Install()
    {
        if (_simulatorField == null) return; // 1.5 noop
        if (IsInstalled) return;
        _simulatorField.SetValue(null, this);
        _monitor.Log("[InputBridgeSimulator] 已 install 到 Game1.inputSimulator（总开关已开，失焦下 SMAPI Press 也被消费）", LogLevel.Info);
    }

    /// <summary>从 Game1.inputSimulator 移除（若当前指向自己）；SDV 1.5 降级时 noop。</summary>
    public void Uninstall()
    {
        if (_simulatorField == null) return; // 1.5 noop
        if (!IsInstalled) return;
        _simulatorField.SetValue(null, null);
        _monitor.Log("[InputBridgeSimulator] 已 uninstall", LogLevel.Debug);
    }

    /// <summary>
    /// 每帧调用：检查 SDV 是否因 Escape 把 inputSimulator 清空了，若是则在世界就绪时自动重 install。
    /// SDV 卸载 simulator 是游戏内部行为，不代表 AI cancel；命令取消统一走 protocol cancel。
    /// SDV 1.5 降级时 noop。
    /// </summary>
    public void OnUpdateTicking()
    {
        if (_simulatorField == null) return; // 1.5 noop
        var current = _simulatorField.GetValue(null);
        if (current == null && Context.IsWorldReady)
        {
            _simulatorField.SetValue(null, this);
            _monitor.Log("[InputBridgeSimulator] 检测到 inputSimulator==null（SDV 内部卸载），已自动重 install", LogLevel.Trace);
        }
        WasInstalled = IsInstalled;
    }

    // ── IInputSimulator 实现：空体（"总开关"语义） ──

    /// <summary>
    /// 不写任何 ref bool。装上 simulator 的唯一目的是让 SDV 在失焦下仍调用
    /// <c>UpdateControlInput</c>，让 SMAPI Press 注入的 currentKBState 被消费。
    /// 所有按键模拟由 <see cref="InputBridge"/> 通过 SMAPI <see cref="IInputHelper.Press"/> 完成。
    /// </summary>
    public void SimulateInput(
        ref bool actionButtonPressed, ref bool switchToolButtonPressed,
        ref bool useToolButtonPressed, ref bool useToolButtonReleased,
        ref bool addItemToInventoryButtonPressed, ref bool cancelButtonPressed,
        ref bool moveUpPressed, ref bool moveRightPressed, ref bool moveLeftPressed, ref bool moveDownPressed,
        ref bool moveUpReleased, ref bool moveRightReleased, ref bool moveLeftReleased, ref bool moveDownReleased,
        ref bool moveUpHeld, ref bool moveRightHeld, ref bool moveLeftHeld, ref bool moveDownHeld)
    {
        TotalCalls++;
        LastCallTick = Game1.ticks;
    }
}
