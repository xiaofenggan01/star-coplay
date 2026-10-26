using StarCoPlay.Protocol.V245;

namespace StarCoPlay.ProtocolCore.Context;

public sealed class ProtoCommandContext
{
    public string RequestId { get; }
    public string SessionId { get; }
    public string MessageId { get; }
    public CommandRequest Request { get; }

    public string CommandId { get; }
    public string Capability { get; }
    public CommandSource Source { get; }
    public long TimeoutMs { get; }
    public CommandRequest.ParamsOneofCase ParamsCase { get; }

    public int StartedAtFrame { get; }
    public int DeadlineFrame { get; }
    public int CurrentFrame { get; private set; }
    public int ElapsedFrames => CurrentFrame - StartedAtFrame;
    public bool IsTimedOut => CurrentFrame >= DeadlineFrame;

    public float StartStamina { get; set; }
    public int StartMoney { get; set; }
    public string StartLocation { get; set; } = "";
    public int StartTileX { get; set; }
    public int StartTileY { get; set; }
    public int StartFacing { get; set; }
    public bool StartMenuOpen { get; set; }

    public bool IsCancelled { get; private set; }
    public string? CancelReason { get; private set; }

    public Google.Protobuf.IMessage? TypedResult { get; set; }

    public ProtoCommandContext(
        Envelope envelope,
        CommandRequest request,
        int currentGameFrame)
    {
        RequestId = envelope.RequestId;
        SessionId = envelope.SessionId;
        MessageId = envelope.MessageId;
        Request = request;

        CommandId = request.CommandId;
        Capability = request.Capability;
        Source = request.Source;
        TimeoutMs = request.TimeoutMs > 0 ? request.TimeoutMs : 30000;
        ParamsCase = request.ParamsCase;

        StartedAtFrame = currentGameFrame;
        CurrentFrame = currentGameFrame;

        int timeoutFrames = (int)(TimeoutMs / 16.67);
        if (timeoutFrames < 60) timeoutFrames = 60;
        DeadlineFrame = currentGameFrame + timeoutFrames;
    }

    public void AdvanceFrame(int currentGameFrame)
    {
        CurrentFrame = currentGameFrame;
    }

    public void MarkCancelled(string reason)
    {
        IsCancelled = true;
        CancelReason = reason;
    }
}
