using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2.Compound;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.V2;
using StarCoPlay.V2.Models;

namespace StarCoPlay.Capabilities.ExecutionAtoms;

/// <summary>
/// L1 V2 — interact 命令：按 X 键对面前 tile 进行交互。
///
/// 与旧 <see cref="InteractHandler"/> + <see cref="DoHandler"/> 的区别：
/// - 旧版按 intent（talk / check / pet / harvest / open_chest / answer / examine ……
///   共 19 种）走分支，每个分支手写调用游戏内部 API（checkAction/Beginning DialogueBox
///   /Object.performObjectDropInAction 等），耦合大、容易遗漏边界。
/// - V2 版统一为"面朝 + 点按 X"，让游戏自身根据当前 tile 上下文决定交互效果，
///   与真实玩家按动作键的行为一致。这意味着不再需要 intent 参数 —— 游戏会根据
///   目标 tile 上是否有 NPC / 动物 / 箱子 / 已成熟作物 / 招牌 …… 自动派发。
///
/// 替代范围：原 InteractHandler/DoHandler 中的全部交互类 intent
///   talk / check / examine / answer / pet / harvest / pick_up / consume /
///   gift / store / take / drop / ship / sleep / place / dismiss / craft …
/// （水浇 / 工具使用类继续走 use_tool；放置物品的 place 仍可由 X 键触发）
///
/// 入参契约：
///   x: int   目标 tile X
///   y: int   目标 tile Y
///
/// 注意：
/// - 本 Handler 不做距离/可达性校验。调用方应已通过 move_to 走到目标相邻 tile。
/// - 交互后产生的对话框（NPC/Sign/Chest）由 InteractHandler.PendingDismissFrames
///   或 AutoDismissHandler 等独立组件处理，本 Handler 仅负责把 X 键按下去。
/// </summary>
public class InteractHandler
{
    private readonly InputCombo _combo;
    private readonly AnimationAwaiter _animAwaiter;
    private readonly IMonitor _monitor;

    // 注：本 Handler 不直接持有 InputBridge —— 入队由 InputCombo 完成，
    // InputBridge 队列状态感知已下沉到 AnimationAwaiter（构造时持有 InputBridge 引用）。
    public InteractHandler(
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

        // 1. 解析坐标参数
        if (!CompoundUtils.TryGetInt(p, "x", out var x) || !CompoundUtils.TryGetInt(p, "y", out var y))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "interact 需要 x, y 参数");
        }

        var player = Game1.player;
        var target = new Vector2(x, y);

        // 3. 入队 face + tap X
        // InputCombo.Interact 内部已组合了"面朝目标 + 单次点按 X"两个 InputAction。
        _monitor.Log(
            $"[ExecutionAtom] interact: tap X at ({x},{y})",
            LogLevel.Debug);
        _combo.Interact(target);

        // 4. 注册异步等待
        // 大部分交互（捡起物品、抚摸动物、收割作物）都伴随 player.UsingTool 或
        // PauseForSingleAnimation，AnimationAwaiter 在 player.canMove && !UsingTool 时返回。
        // 对话类交互（NPC/Sign/Chest）只创建对话框、不锁角色，AnimationAwaiter 几乎立即完成；
        // 对话框本身由其它组件清理，不在 V2Result 的等待范围内。
        // 120 ticks (~2s) 与 use_tool 默认对齐，覆盖大多数收割/喂动物/捡拾的动画长度。
        _animAwaiter.Begin(
            cmd,
            "interact",
            player.Stamina,
            timeoutTicks: 120);

        return null;
    }

    // 参数解析下沉到 CompoundUtils.TryGetInt（C1 修复，避免 5 处独立实现漂移）。
}
