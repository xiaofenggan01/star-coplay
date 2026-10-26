using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;

namespace StarCoPlay.ProtocolCore.Results;

public static class ProtoTiming
{
    public static ExecutionTiming Capture(ProtoCommandContext ctx, string completionReason = "completed")
    {
        return new ExecutionTiming
        {
            ElapsedFrames = ctx.ElapsedFrames,
            WaitedTicks = ctx.ElapsedFrames,
            TimedOut = ctx.IsTimedOut,
            CompletionReason = completionReason
        };
    }

    public static ExecutionTiming CaptureTimeout(ProtoCommandContext ctx)
    {
        return Capture(ctx, "timeout");
    }

    public static ExecutionTiming CaptureCancelled(ProtoCommandContext ctx)
    {
        return Capture(ctx, ctx.CancelReason ?? "cancelled");
    }
}
