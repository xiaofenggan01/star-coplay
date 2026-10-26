using StarCoPlay.Protocol.V245;

namespace StarCoPlay.ProtocolCore.Results;

public static class ProtoErrorMapper
{
    public static ErrorCode InvalidParams => ErrorCode.InvalidParams;
    public static ErrorCode CapabilityMissing => ErrorCode.CapabilityMissing;
    public static ErrorCode Busy => ErrorCode.Busy;
    public static ErrorCode Timeout => ErrorCode.Timeout;
    public static ErrorCode Cancelled => ErrorCode.Cancelled;
    public static ErrorCode ExecutionFailed => ErrorCode.ExecutionFailed;

    public static Error Build(ErrorCode code, string message, string? detail = null)
    {
        var error = new Error
        {
            Code = code,
            Message = message
        };
        if (detail != null)
            error.Detail = detail;
        return error;
    }
}
