using StarCoPlay.V2.Models;

namespace StarCoPlay.GameServices.Movement;

/// <summary>
/// move_to handler 接口 — 抽象旧兼容实现（直接 SDV API + 跨地图编排）和
/// 当前执行原子实现（模拟输入三模式 A/B/C）两种实现。
///
/// 让 <see cref="StarCoPlay.Bridge.CommandProcessor"/> 和未来的 go_to L2 实现
/// （旧 <c>StarCoPlay.V2.Compound.GoToHandler</c> 已弃用，等待重设计）
/// 可以根据 <c>config.UseInputBridgeHandlers</c> 在运行时切换 L1 实现，
/// 而无需关心具体类型。
///
/// 调用语义：
///   - <see cref="Start"/> / <see cref="Handle"/>：启动一个 move_to 命令。
///     - 同步失败：返回 <see cref="V2Result"/>（含 error code）。
///     - 异步进行：返回 <c>null</c>，调用方需在后续帧轮询 <see cref="Tick"/>。
///   - <see cref="Tick"/>：每帧驱动状态机。返回非 null 表示完成（成功/失败），null 表示仍在进行。
///   - <see cref="IsActive"/>：是否有 in-flight 命令（CommandProcessor 用此判断是否需要继续 Tick）。
///   - <see cref="Cancel"/>：取消当前 in-flight 命令（清理状态机但不返回结果，由调用方自行写 cancel result）。
/// </summary>
public interface IMoveToHandler
{
    /// <summary>是否有正在执行的 move_to 命令。</summary>
    bool IsActive { get; }

    /// <summary>启动 move_to 命令。同步失败返回 V2Result，异步进行返回 null。</summary>
    V2Result? Start(V2Command cmd);

    /// <summary>AdapterV2 注册入口（语义等同 <see cref="Start"/>）。</summary>
    V2Result? Handle(V2Command cmd);

    /// <summary>每帧驱动状态机。完成返回 V2Result，仍在进行返回 null。</summary>
    V2Result? Tick();

    /// <summary>取消当前 in-flight 命令并清理状态。</summary>
    void Cancel();
}
