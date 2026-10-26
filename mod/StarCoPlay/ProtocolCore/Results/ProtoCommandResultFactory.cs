using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;

namespace StarCoPlay.ProtocolCore.Results;

public sealed class ProtoCommandResultFactory
{
    public CommandResult Completed(ProtoCommandContext ctx)
    {
        var result = new CommandResult
        {
            CommandId = ctx.CommandId,
            Capability = ctx.Capability,
            Success = true,
            Status = CommandStatus.Completed,
            Observation = new Observation { Text = $"{ctx.Capability} completed" }
        };

        SetTypedData(result, ctx);
        return result;
    }

    public CommandResult Failed(ProtoCommandContext ctx, ErrorCode code, string message)
    {
        return new CommandResult
        {
            CommandId = ctx.CommandId,
            Capability = ctx.Capability,
            Success = false,
            Status = CommandStatus.Failed,
            Error = ProtoErrorMapper.Build(code, message),
            Observation = new Observation { Text = message }
        };
    }

    public CommandResult Rejected(ProtoCommandContext ctx, ErrorCode code, string message)
    {
        return new CommandResult
        {
            CommandId = ctx.CommandId,
            Capability = ctx.Capability,
            Success = false,
            Status = CommandStatus.Rejected,
            Error = ProtoErrorMapper.Build(code, message),
            Observation = new Observation { Text = message }
        };
    }

    public CommandResult RejectedNoContext(string commandId, string capability, ErrorCode code, string message)
    {
        return new CommandResult
        {
            CommandId = commandId,
            Capability = capability,
            Success = false,
            Status = CommandStatus.Rejected,
            Error = ProtoErrorMapper.Build(code, message),
            Observation = new Observation { Text = message }
        };
    }

    public CommandResult TimedOut(ProtoCommandContext ctx)
    {
        return new CommandResult
        {
            CommandId = ctx.CommandId,
            Capability = ctx.Capability,
            Success = false,
            Status = CommandStatus.Timeout,
            Error = ProtoErrorMapper.Build(ErrorCode.Timeout, $"{ctx.Capability} timed out after {ctx.ElapsedFrames} frames"),
            Observation = new Observation { Text = $"{ctx.Capability} timed out" }
        };
    }

    public CommandResult Cancelled(ProtoCommandContext ctx)
    {
        return new CommandResult
        {
            CommandId = ctx.CommandId,
            Capability = ctx.Capability,
            Success = false,
            Status = CommandStatus.Cancelled,
            Error = ProtoErrorMapper.Build(ErrorCode.Cancelled, ctx.CancelReason ?? "cancelled"),
            Observation = new Observation { Text = $"{ctx.Capability} cancelled" }
        };
    }

    private static void SetTypedData(CommandResult result, ProtoCommandContext ctx)
    {
        if (ctx.TypedResult == null) return;

        switch (ctx.TypedResult)
        {
            case Protocol.V245.FaceResult face: result.Face = face; break;
            case Protocol.V245.EquipResult equip: result.Equip = equip; break;
            case Protocol.V245.InteractResult interact: result.Interact = interact; break;
            case Protocol.V245.UseToolResult useTool: result.UseTool = useTool; break;
            case Protocol.V245.MoveToResult moveTo: result.MoveTo = moveTo; break;
            case Protocol.V245.GoToResult goTo: result.GoTo = goTo; break;
            case Protocol.V245.GoToBedResult goToBed: result.GoToBed = goToBed; break;
            case Protocol.V245.MenuResult menu: result.Menu = menu; break;
            case Protocol.V245.MenuSnapshot menuSnapshot: result.MenuSnapshot = menuSnapshot; break;
            case Protocol.V245.TpResult tp: result.Tp = tp; break;
            case Protocol.V245.SayResult say: result.Say = say; break;
            case Protocol.V245.EmoteResult emote: result.Emote = emote; break;
            case Protocol.V245.QueryWorldRegionResult qwr: result.QueryWorldRegion = qwr; break;
            case Protocol.V245.QueryRuntimeSnapshotResult qrs: result.QueryRuntimeSnapshot = qrs; break;
            case Protocol.V245.QueryInventorySnapshotResult qis: result.QueryInventorySnapshot = qis; break;
            case Protocol.V245.QueryUiSnapshotResult qus: result.QueryUiSnapshot = qus; break;
            case Protocol.V245.QueryInspectRefsResult qir: result.QueryInspectRefs = qir; break;
        }
    }
}
