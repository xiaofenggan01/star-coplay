using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;

namespace StarCoPlay.ProtocolCore.Dispatch;

public enum ProtoCommandKind
{
    PassiveRead,
    PassiveLight,
    ActiveShort,
    ActiveLong
}

public readonly struct HandlerTickResult
{
    public HandlerTickStatus Status { get; }
    public ErrorCode? FailureCode { get; }
    public string? FailureMessage { get; }

    private HandlerTickResult(HandlerTickStatus status, ErrorCode? code, string? message)
    {
        Status = status;
        FailureCode = code;
        FailureMessage = message;
    }

    public static HandlerTickResult Continue => new(HandlerTickStatus.Continue, null, null);
    public static HandlerTickResult Completed => new(HandlerTickStatus.Completed, null, null);
    public static HandlerTickResult Failed(ErrorCode code, string message) => new(HandlerTickStatus.Failed, code, message);
}

public enum HandlerTickStatus
{
    Continue,
    Completed,
    Failed
}

public readonly struct ProtoValidationResult
{
    public bool IsValid { get; }
    public string? ErrorMessage { get; }

    private ProtoValidationResult(bool valid, string? error)
    {
        IsValid = valid;
        ErrorMessage = error;
    }

    public static ProtoValidationResult Ok() => new(true, null);
    public static ProtoValidationResult Invalid(string message) => new(false, message);
}

public readonly struct HandlerStartResult
{
    public bool Accepted { get; }
    public bool CompletedImmediately { get; }
    public string? ErrorMessage { get; }

    private HandlerStartResult(bool accepted, bool completed, string? error)
    {
        Accepted = accepted;
        CompletedImmediately = completed;
        ErrorMessage = error;
    }

    public static HandlerStartResult Async() => new(true, false, null);
    public static HandlerStartResult Immediate() => new(true, true, null);
    public static HandlerStartResult Rejected(string message) => new(false, false, message);
}

public interface IProtoCommandHandler
{
    string Capability { get; }
    CommandRequest.ParamsOneofCase ParamsCase { get; }
    ProtoCommandKind Kind { get; }

    ProtoValidationResult Validate(ProtoCommandContext ctx);
    HandlerStartResult Start(ProtoCommandContext ctx);
    HandlerTickResult Tick(ProtoCommandContext ctx);
    void Cancel(ProtoCommandContext ctx, string reason);
}
