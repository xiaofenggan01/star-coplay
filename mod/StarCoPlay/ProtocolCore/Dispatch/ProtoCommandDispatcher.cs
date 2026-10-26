using StardewModdingAPI;
using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Results;

namespace StarCoPlay.ProtocolCore.Dispatch;

public readonly struct ProtoDispatchResult
{
    public CommandResult Result { get; }
    public string RequestId { get; }
    public string SessionId { get; }

    public ProtoDispatchResult(CommandResult result, string requestId, string sessionId)
    {
        Result = result;
        RequestId = requestId;
        SessionId = sessionId;
    }
}

public sealed class ProtoCommandDispatcher
{
    private readonly ProtoHandlerRegistry _registry;
    private readonly ProtoCommandScheduler _scheduler;
    private readonly ProtoCommandResultFactory _resultFactory;
    private readonly IMonitor _monitor;

    public bool IsBusy => _scheduler.IsBusy;

    public ProtoCommandDispatcher(
        ProtoHandlerRegistry registry,
        ProtoCommandScheduler scheduler,
        ProtoCommandResultFactory resultFactory,
        IMonitor monitor)
    {
        _registry = registry;
        _scheduler = scheduler;
        _resultFactory = resultFactory;
        _monitor = monitor;
    }

    /// <summary>
    /// Dispatch a CommandRequest envelope. Returns a result if the command completed
    /// synchronously (immediate success, rejection, or validation failure).
    /// Returns null if the command is async and will complete via Tick().
    /// </summary>
    public ProtoDispatchResult? Dispatch(Envelope envelope)
    {
        var request = envelope.CommandRequest;
        var requestId = envelope.RequestId;
        var sessionId = envelope.SessionId;

        // 1. command_id 非空
        if (string.IsNullOrEmpty(request.CommandId))
            return Reject(requestId, sessionId, "", request.Capability ?? "", ErrorCode.InvalidParams, "Missing command_id");

        // 2. timeout_ms > 0
        if (request.TimeoutMs <= 0)
            return Reject(requestId, sessionId, request.CommandId, request.Capability ?? "", ErrorCode.InvalidParams, "timeout_ms must be > 0");

        // 3. source != UNSPECIFIED
        if (request.Source == CommandSource.Unspecified)
            return Reject(requestId, sessionId, request.CommandId, request.Capability ?? "", ErrorCode.InvalidParams, "source must not be UNSPECIFIED");

        // 4. capability 非空
        if (string.IsNullOrEmpty(request.Capability))
            return Reject(requestId, sessionId, request.CommandId, "", ErrorCode.InvalidParams, "Missing capability");

        // 5. ParamsCase != None
        if (request.ParamsCase == CommandRequest.ParamsOneofCase.None)
            return Reject(requestId, sessionId, request.CommandId, request.Capability, ErrorCode.InvalidParams, "No params provided");

        // 6. 根据 ParamsCase 找 handler
        if (!_registry.TryGet(request.ParamsCase, out var handler))
            return Reject(requestId, sessionId, request.CommandId, request.Capability, ErrorCode.CapabilityMissing,
                $"No handler registered for ParamsCase={request.ParamsCase}");

        // 7. capability 字符串与 ParamsCase 一致性校验
        if (!_registry.IsCapabilityConsistent(request.Capability, request.ParamsCase))
            return Reject(requestId, sessionId, request.CommandId, request.Capability, ErrorCode.InvalidParams,
                $"Capability '{request.Capability}' does not match ParamsCase={request.ParamsCase}");

        // 8. tp 必须 source 是 AGENT_RUNTIME 或 DIAGNOSTIC
        if (request.ParamsCase == CommandRequest.ParamsOneofCase.Tp
            && request.Source != CommandSource.AgentRuntime
            && request.Source != CommandSource.Diagnostic)
        {
            return Reject(requestId, sessionId, request.CommandId, request.Capability, ErrorCode.InvalidParams,
                "tp is internal-only: source must be AGENT_RUNTIME or DIAGNOSTIC");
        }

        // 9. 构建 context，捕获起始状态
        var ctx = new ProtoCommandContext(envelope, request, Game1.ticks);
        ProtoSnapshotBuilder.CaptureStartState(ctx);

        // 10. 提交给 scheduler
        var result = _scheduler.Submit(ctx, handler);
        if (result == null)
            return null; // async, will complete via Tick()

        return new ProtoDispatchResult(result, requestId, sessionId);
    }

    /// <summary>
    /// Tick the active command. Returns a result if the command completed/failed/timed out.
    /// Returns null if still in progress or no active command.
    /// </summary>
    public ProtoDispatchResult? Tick()
    {
        // Capture routing info BEFORE tick (scheduler clears active on completion)
        var routingInfo = _scheduler.GetActiveRoutingInfo();
        if (routingInfo == null)
            return null;

        var result = _scheduler.Tick(Game1.ticks);
        if (result == null)
            return null;

        return new ProtoDispatchResult(result, routingInfo.Value.RequestId, routingInfo.Value.SessionId);
    }

    public ProtoDispatchResult? Cancel(Envelope envelope)
    {
        var cancel = envelope.CommandCancel;
        var routingInfo = _scheduler.GetActiveRoutingInfo();

        var result = _scheduler.Cancel(cancel.CommandId, cancel.Reason);
        if (result != null)
        {
            return new ProtoDispatchResult(result, envelope.RequestId, envelope.SessionId);
        }

        var rejected = _resultFactory.RejectedNoContext(
            cancel.CommandId, "", ErrorCode.InvalidParams,
            $"No active command with id '{cancel.CommandId}'");
        return new ProtoDispatchResult(rejected, envelope.RequestId, envelope.SessionId);
    }

    public CommandStatusResponse Status(Envelope envelope)
    {
        var commandId = envelope.CommandStatusRequest.CommandId;
        var active = _scheduler.ActiveCommandId == commandId;
        return new CommandStatusResponse
        {
            CommandId = commandId,
            Status = active ? CommandStatus.Running : CommandStatus.Unspecified,
            Terminal = false,
        };
    }

    private ProtoDispatchResult Reject(string requestId, string sessionId, string commandId, string capability, ErrorCode code, string message)
    {
        var result = _resultFactory.RejectedNoContext(commandId, capability, code, message);
        return new ProtoDispatchResult(result, requestId, sessionId);
    }
}
