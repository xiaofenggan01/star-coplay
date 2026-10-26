using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;

namespace StarCoPlay.ProtocolCore.GoTo;

internal static class GoToChildCommandFactory
{
    public static ProtoCommandContext CreateMoveToContext(
        ProtoCommandContext parent,
        int stepIndex,
        MoveToParams moveTo,
        int currentGameFrame)
    {
        var commandId = $"{parent.CommandId}:step:{stepIndex}:move_to";
        var request = new CommandRequest
        {
            CommandId  = commandId,
            Capability = "move_to",
            TimeoutMs  = parent.TimeoutMs,
            Source     = parent.Source,
            MoveTo     = moveTo,
        };
        var envelope = new Envelope
        {
            RequestId = parent.RequestId,
            SessionId = parent.SessionId,
            MessageId = commandId,
        };
        return new ProtoCommandContext(envelope, request, currentGameFrame);
    }

    public static ProtoCommandContext CreateInteractContext(
        ProtoCommandContext parent,
        int stepIndex,
        Protocol.V245.TilePoint targetTile,
        int currentGameFrame)
    {
        var commandId = $"{parent.CommandId}:step:{stepIndex}:interact";
        var request = new CommandRequest
        {
            CommandId  = commandId,
            Capability = "interact",
            TimeoutMs  = parent.TimeoutMs,
            Source     = parent.Source,
            Interact   = new InteractParams { TargetTile = targetTile },
        };
        var envelope = new Envelope
        {
            RequestId = parent.RequestId,
            SessionId = parent.SessionId,
            MessageId = commandId,
        };
        return new ProtoCommandContext(envelope, request, currentGameFrame);
    }
}
