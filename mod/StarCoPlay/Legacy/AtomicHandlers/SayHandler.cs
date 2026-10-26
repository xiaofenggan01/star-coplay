using System.Collections.Generic;
using StardewModdingAPI;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2;

/// <summary>
/// 处理 say(message) 能力：通过游戏聊天框发送消息。
/// </summary>
public class SayHandler
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    public SayHandler(ActionExecutor executor, IMonitor monitor)
    {
        _executor = executor;
        _monitor = monitor;
    }

    public V2Result? Handle(V2Command cmd)
    {
        if (!TryGetString(cmd.Params, "message", out var message))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "say 缺少 message 参数");
        }

        if (StardewValley.Game1.chatBox == null)
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                "聊天框不可用（可能不在游戏中或非多人模式）");
        }

        _executor.SendChat(message);
        _monitor.Log($"[V2] say: 已发送消息 \"{message}\"", LogLevel.Trace);

        return ResultBuilder.Success(
                cmd.Seq, cmd.ActionId,
                new { message })
            .WithObservation($"已发送消息: {message}");
    }

    private static bool TryGetString(Dictionary<string, object> p, string key, out string value)
    {
        value = "";
        if (!p.TryGetValue(key, out var raw)) return false;
        value = raw?.ToString() ?? "";
        return !string.IsNullOrEmpty(value);
    }
}
