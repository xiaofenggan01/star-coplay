using System.Collections.Generic;
using System.Linq;
using StardewModdingAPI;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2;

/// <summary>
/// 处理 emote(type) 能力：播放表情气泡动画。
/// </summary>
public class EmoteHandler
{
    private readonly ActionExecutor _executor;
    private readonly IMonitor _monitor;

    public EmoteHandler(ActionExecutor executor, IMonitor monitor)
    {
        _executor = executor;
        _monitor = monitor;
    }

    public V2Result? Handle(V2Command cmd)
    {
        if (!TryGetString(cmd.Params, "type", out var emoteType))
        {
            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                "emote 缺少 type 参数");
        }

        var ok = _executor.DoEmote(emoteType);
        if (!ok)
        {
            var available = _executor.GetAvailableEmotes();
            var emoteList = string.Join(", ", available.Keys.OrderBy(k => k));

            return ResultBuilder.Failure(
                cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.InvalidParams,
                $"不支持的表情: {emoteType}，可用表情: {emoteList}");
        }

        _monitor.Log($"[V2] emote: 播放表情 {emoteType}", LogLevel.Trace);

        return ResultBuilder.Success(
                cmd.Seq, cmd.ActionId,
                new { emote = emoteType })
            .WithObservation($"已播放表情: {emoteType}");
    }

    private static bool TryGetString(Dictionary<string, object> p, string key, out string value)
    {
        value = "";
        if (!p.TryGetValue(key, out var raw)) return false;
        value = raw?.ToString() ?? "";
        return !string.IsNullOrEmpty(value);
    }
}
