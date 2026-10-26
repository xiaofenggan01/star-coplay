using System;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;

namespace StarCoPlay.GameServices.SimulatedInput;

/// <summary>
/// 组合动作语法糖：将常见的"面朝 + 按键"模式封装为一步调用。
/// 所有方法都是入队操作（向 InputBridge 队列追加 InputAction），不会立即执行。
/// L1 Handler 直接调用，无需关心底层按键时序。
/// </summary>
/// <remarks>
/// 按键映射设计：使用键盘按键（W/A/S/D/C/X/E/Escape/数字键）。
/// 历史：曾尝试切换到手柄按键（Controller* / DPad* / Shoulder*），实测发现：
/// - menu_click 中 LB/RB 按键不真正切换 GameMenu tab（按了但 currentTab 没变）
/// - menu_close 中 ControllerB 按键不能关闭菜单
/// 因此回滚到键盘按键。tab 切换改为直接调用 GameMenu.changeTab API（见 MenuNavigator.cs）。
///
/// 注：EquipSlot（工具栏栏位）保留键盘数字键 D1-D9 / D0 / OemMinus / OemPlus，
/// 与其他按键保持一致。
/// </remarks>
public class InputCombo
{
    private readonly InputBridge _bridge;
    private readonly IMonitor _monitor;

    public InputCombo(InputBridge bridge, IMonitor monitor)
    {
        _bridge = bridge;
        _monitor = monitor;
    }

    // ── 方向与面朝 ──

    /// <summary>面朝指定方向（点按一次方向键）。</summary>
    /// <param name="direction">0=上 1=右 2=下 3=左</param>
    public void Face(int direction)
    {
        var button = DirectionToButton(direction);
        _monitor.Log($"[InputCombo] Face: direction={direction} button={button}", LogLevel.Trace);
        _bridge.Tap(button, () => Game1.player.FacingDirection == direction, timeout: 10);
    }

    // ── 工具使用 ──

    /// <summary>
    /// 对目标 tile 使用当前装备工具（面朝 + 按使用工具键）。
    /// 调用方应确保在入队时玩家已在目标 tile 旁边（L1 Handler 先 move_to 到位，再 use_tool）。
    /// </summary>
    /// <param name="targetTile">目标 tile 坐标（应与玩家相邻）</param>
    public void UseTool(Vector2 targetTile)
    {
        int direction = GetFacingDirection(Game1.player.Tile, targetTile);
        _monitor.Log($"[InputCombo] UseTool: target=({targetTile.X},{targetTile.Y}) dir={direction}", LogLevel.Trace);

        Face(direction);
        _bridge.HoldFor(SButton.C, 2);
    }

    /// <summary>
    /// 蓄力工具使用（面朝 + 长按使用工具键）。
    /// 每级蓄力约 600ms ≈ 36 ticks @60fps。
    /// 注意：释放后的动画等待由调用方负责（通过 AnimationAwaiter 或轮询 IsPlayerFree）。
    /// 调用方应确保在入队时玩家已在目标 tile 旁边。
    /// </summary>
    /// <param name="targetTile">目标 tile 坐标（应与玩家相邻）</param>
    /// <param name="chargeLevel">蓄力等级（1=铜, 2=钢, 3=金, 4=铱）</param>
    public void UseToolCharged(Vector2 targetTile, int chargeLevel)
    {
        int direction = GetFacingDirection(Game1.player.Tile, targetTile);
        int timeout = chargeLevel * 70 + 30;
        _monitor.Log($"[InputCombo] UseToolCharged: target=({targetTile.X},{targetTile.Y}) dir={direction} charge={chargeLevel} timeout={timeout}f", LogLevel.Trace);

        Face(direction);
        _bridge.Hold(SButton.C, () =>
        {
            var p = Game1.player;
            if (!p.UsingTool || !p.canReleaseTool)
                return false;
            return p.toolPower.Value >= chargeLevel;
        }, timeout);
    }

    // ── 交互 ──

    /// <summary>
    /// 对目标 tile 执行交互（面朝 + 按动作键）。
    /// 调用方应确保在入队时玩家已在目标 tile 旁边（L1 Handler 先 move_to 到位，再 interact）。
    /// </summary>
    /// <param name="targetTile">目标 tile 坐标（应与玩家相邻）</param>
    public void Interact(Vector2 targetTile)
    {
        int direction = GetFacingDirection(Game1.player.Tile, targetTile);
        _monitor.Log($"[InputCombo] Interact: target=({targetTile.X},{targetTile.Y}) dir={direction}", LogLevel.Trace);

        Face(direction);
        _bridge.HoldFor(SButton.X, 2);
    }

    /// <summary>按一次 action button（X 键），固定 HoldFor 2 帧。</summary>
    public void PressActionButton()
    {
        _bridge.HoldFor(SButton.X, 2);
    }

    /// <summary>
    /// 微移：在当前 tile 内沿指定方向移动，直到 stopCondition 为 true 或超时。
    /// 使用 Hold 模式，由 InputBridge 每帧检查 stopCondition。
    /// </summary>
    /// <param name="direction">0=上 1=右 2=下 3=左</param>
    /// <param name="stopCondition">每帧检查的停止条件</param>
    /// <param name="timeoutTicks">最大持续帧数</param>
    public void MicroMove(int direction, Func<bool> stopCondition, int timeoutTicks)
    {
        _monitor.Log($"[InputCombo] MicroMove: dir={direction} timeout={timeoutTicks}", LogLevel.Trace);
        var button = DirectionToButton(direction);
        _bridge.Hold(button, stopCondition, timeoutTicks);
    }

    // ── 装备切换 ──

    /// <summary>
    /// 按数字键切换到指定工具栏栏位。
    /// 使用键盘数字键 D1-D9 / D0 / OemMinus / OemPlus。
    /// </summary>
    /// <param name="slot">栏位编号 1-12</param>
    public void EquipSlot(int slot)
    {
        var button = SlotToButton(slot);
        _monitor.Log($"[InputCombo] EquipSlot: slot={slot} button={button}", LogLevel.Trace);
        _bridge.Tap(button, timeout: 10);
    }

    // ── 菜单 ──

    /// <summary>打开游戏菜单（按键盘 E 键，对应 SDV menuButton）。</summary>
    public void OpenMenu()
    {
        _monitor.Log("[InputCombo] OpenMenu: button=E", LogLevel.Trace);
        _bridge.Tap(SButton.E, () => Game1.activeClickableMenu != null, timeout: 30);
    }

    /// <summary>打开任务日志（按键盘 F 键，对应 SDV journalButton）。</summary>
    public void OpenQuestLog()
    {
        _monitor.Log("[InputCombo] OpenQuestLog: button=F", LogLevel.Trace);
        _bridge.Tap(SButton.F, () => Game1.activeClickableMenu is StardewValley.Menus.QuestLog, timeout: 30);
    }

    /// <summary>关闭当前菜单（按键盘 Escape 键，对应 SDV cancelButton）。</summary>
    public void CloseMenu()
    {
        _monitor.Log("[InputCombo] CloseMenu: button=Escape", LogLevel.Trace);
        _bridge.Tap(SButton.Escape, () => Game1.activeClickableMenu == null, timeout: 30);
    }

    // ── GameMenu Tab 切换（LB/RB）──
    // TODO: LB/RB 实测不切 tab，待研究真正机制。当前 MenuNavigator.Click 已改为直接调
    //       GameMenu.changeTab API，下面的方法保留作为参考但不被推荐使用。

    /// <summary>切到上一个 GameMenu tab（LB / LeftShoulder）。</summary>
    /// <remarks>TODO: LB/RB 实测不切 tab，待研究真正机制。</remarks>
    public void SwitchTabLeft()
    {
        _bridge.Tap(SButton.LeftShoulder, timeout: 30);
        _monitor.Log("[InputCombo] SwitchTabLeft: button=LeftShoulder", LogLevel.Trace);
    }

    /// <summary>切到下一个 GameMenu tab（RB / RightShoulder）。</summary>
    /// <remarks>TODO: LB/RB 实测不切 tab，待研究真正机制。</remarks>
    public void SwitchTabRight()
    {
        _bridge.Tap(SButton.RightShoulder, timeout: 30);
        _monitor.Log("[InputCombo] SwitchTabRight: button=RightShoulder", LogLevel.Trace);
    }

    /// <summary>切到指定 GameMenu tab 索引（0-9，按需按 LB/RB N 次）。</summary>
    /// <param name="targetTabIndex">目标 tab 索引（0=inventory, 1=skills, ... 9=exit）</param>
    /// <returns>true=已入队切换序列，false=非 GameMenu 或参数错误</returns>
    /// <remarks>TODO: LB/RB 实测不切 tab，待研究真正机制。</remarks>
    public bool SwitchToTab(int targetTabIndex)
    {
        if (Game1.activeClickableMenu is not StardewValley.Menus.GameMenu gameMenu) return false;
        if (targetTabIndex < 0 || targetTabIndex >= gameMenu.pages.Count) return false;

        int delta = targetTabIndex - gameMenu.currentTab;
        if (delta == 0) return true;  // 已在目标

        SButton button = delta > 0 ? SButton.RightShoulder : SButton.LeftShoulder;
        int times = Math.Abs(delta);

        for (int i = 0; i < times; i++)
        {
            _bridge.Tap(button, timeout: 30);
        }

        _monitor.Log($"[InputCombo] SwitchToTab: {gameMenu.currentTab} → {targetTabIndex}, 入队 {times} 次 {button}", LogLevel.Debug);
        return true;
    }

    // ── 辅助方法 ──

    /// <summary>
    /// 计算从 from 到 to 的面朝方向。
    /// 基于 dx/dy 绝对值比较确定主方向。
    /// </summary>
    /// <returns>0=上 1=右 2=下 3=左</returns>
    public static int GetFacingDirection(Vector2 from, Vector2 to)
    {
        if (from == to)
            return Game1.player.FacingDirection;

        float dx = to.X - from.X;
        float dy = to.Y - from.Y;

        // SDV 坐标系：Y 向下为正
        if (Math.Abs(dx) >= Math.Abs(dy))
            return dx >= 0 ? 1 : 3; // 右 : 左
        else
            return dy > 0 ? 2 : 0;  // 下 : 上
    }

    /// <summary>
    /// 方向值转 SButton 按键。使用键盘 WASD。
    /// </summary>
    /// <param name="direction">0=上(W) 1=右(D) 2=下(S) 3=左(A)</param>
    private static SButton DirectionToButton(int direction)
    {
        return direction switch
        {
            0 => SButton.W,
            1 => SButton.D,
            2 => SButton.S,
            3 => SButton.A,
            _ => throw new ArgumentOutOfRangeException(nameof(direction),
                $"方向值必须是 0-3，实际: {direction}")
        };
    }

    /// <summary>工具栏栏位编号转 SButton 按键。</summary>
    /// <param name="slot">1-12</param>
    private static SButton SlotToButton(int slot)
    {
        return slot switch
        {
            1 => SButton.D1,
            2 => SButton.D2,
            3 => SButton.D3,
            4 => SButton.D4,
            5 => SButton.D5,
            6 => SButton.D6,
            7 => SButton.D7,
            8 => SButton.D8,
            9 => SButton.D9,
            10 => SButton.D0,
            11 => SButton.OemMinus,
            12 => SButton.OemPlus,
            _ => throw new ArgumentOutOfRangeException(nameof(slot),
                $"栏位编号必须是 1-12，实际: {slot}")
        };
    }
}
