using StardewModdingAPI;
using StardewValley;
using StarCoPlay.V2.Compound;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.V2;
using StarCoPlay.V2.Models;

namespace StarCoPlay.Capabilities.ExecutionAtoms;

/// <summary>
/// L1 V2 — face 命令：按方向键转身。
///
/// 与旧 <see cref="FaceHandler"/> 的区别：
/// - 旧版直接调用 player.faceDirection(dir) + StopAnimation()，
///   绕过键位输入，不会触发任何转身/走路动画反馈。
/// - V2 版改为 InputCombo.Face(direction)，通过 InputBridge 入队 W/A/S/D 点按，
///   让游戏自身根据按键执行转身动画，与真实玩家操作一致。
///
/// 入参契约：
///   direction: int    0=上 1=右 2=下 3=左
///   或
///   direction: string up/right/down/left（也接受 north/east/south/west）
///
/// face 通过 AnimationAwaiter 异步等待 InputBridge 队列消化完毕后返回 success，
/// 确保返回 success 时按键已实际注入（避免下游 interact/use_tool 在转身前就触发）。
/// </summary>
public class FaceHandler
{
    private readonly InputCombo _combo;
    private readonly AnimationAwaiter _animAwaiter;
    private readonly IMonitor _monitor;

    public FaceHandler(InputCombo combo, AnimationAwaiter animAwaiter, IMonitor monitor)
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

        // 1. 解析 direction（兼容数字 0-3 与字符串 up/right/down/left）
        int direction;
        if (CompoundUtils.TryGetInt(p, "direction", out var d))
        {
            direction = d;
        }
        else if (CompoundUtils.TryGetString(p, "direction", out var s))
        {
            direction = s.ToLowerInvariant() switch
            {
                "up" => 0, "north" => 0,
                "right" => 1, "east" => 1,
                "down" => 2, "south" => 2,
                "left" => 3, "west" => 3,
                _ => -1
            };
        }
        else
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "face 需要 direction 参数（int 0-3 或 string up/right/down/left）");
        }

        // 3. 范围校验
        if (direction < 0 || direction > 3)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                $"direction 必须是 0/1/2/3 或 up/right/down/left，实际: {direction}");
        }

        // 4. 入队
        _monitor.Log(
            $"[ExecutionAtom] face: tap direction button direction={direction}",
            LogLevel.Debug);
        _combo.Face(direction);

        // 5. 注册异步等待
        // face 本身极快（<= 10 帧完成），但 InputBridge 队列里可能还有其他动作没消化。
        // AnimationAwaiter 会优先等 InputBridge.IsBusy=false，再判 player 状态。
        // timeout 30 ticks (~0.5s) 足够覆盖单次方向键点按 + InputCombo 内部组合。
        _animAwaiter.Begin(cmd, $"face({direction})", Game1.player.Stamina, timeoutTicks: 30);
        return null;
    }

    // 参数解析下沉到 CompoundUtils.TryGetInt / TryGetString（C1 修复，避免 5 处独立实现漂移）。
}
