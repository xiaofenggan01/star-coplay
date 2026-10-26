using System;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Results;

namespace StarCoPlay.ProtocolCore.Dispatch;

public sealed class ProtoCommandScheduler
{
    private readonly ProtoHandlerRegistry _registry;
    private readonly ProtoCommandResultFactory _resultFactory;
    private readonly IMonitor _monitor;
    private readonly Func<bool> _isLegacyBusy;
    private readonly Func<string> _legacyBusyReason;

    private ProtoCommandContext? _active;
    private IProtoCommandHandler? _activeHandler;

    public bool IsBusy => _active != null;
    public string? ActiveCommandId => _active?.CommandId;
    public string? ActiveCapability => _active?.Capability;

    public (string RequestId, string SessionId)? GetActiveRoutingInfo()
    {
        if (_active == null) return null;
        return (_active.RequestId, _active.SessionId);
    }

    public ProtoCommandScheduler(
        ProtoHandlerRegistry registry,
        ProtoCommandResultFactory resultFactory,
        IMonitor monitor,
        Func<bool> isLegacyBusy,
        Func<string> legacyBusyReason)
    {
        _registry = registry;
        _resultFactory = resultFactory;
        _monitor = monitor;
        _isLegacyBusy = isLegacyBusy;
        _legacyBusyReason = legacyBusyReason;
    }

    public CommandResult? Submit(ProtoCommandContext ctx, IProtoCommandHandler handler)
    {
        if (handler.Kind == ProtoCommandKind.PassiveRead || handler.Kind == ProtoCommandKind.PassiveLight)
        {
            return ExecutePassive(ctx, handler);
        }

        if (_active != null)
        {
            _monitor.Log($"[ProtoScheduler] REJECTED {ctx.Capability} ({ctx.CommandId}): proto active={_active.Capability} ({_active.CommandId})", LogLevel.Warn);
            return _resultFactory.Rejected(ctx, ErrorCode.Busy, $"Proto scheduler busy: {_active.Capability}");
        }

        if (_isLegacyBusy())
        {
            var reason = _legacyBusyReason();
            _monitor.Log($"[ProtoScheduler] REJECTED {ctx.Capability} ({ctx.CommandId}): legacy busy ({reason})", LogLevel.Warn);
            return _resultFactory.Rejected(ctx, ErrorCode.Busy, $"Legacy command processor busy: {reason}");
        }

        var validation = handler.Validate(ctx);
        if (!validation.IsValid)
        {
            _monitor.Log($"[ProtoScheduler] REJECTED {ctx.Capability} ({ctx.CommandId}): validation failed: {validation.ErrorMessage}", LogLevel.Warn);
            return _resultFactory.Rejected(ctx, ErrorCode.InvalidParams, validation.ErrorMessage!);
        }

        var startResult = handler.Start(ctx);
        if (!startResult.Accepted)
        {
            _monitor.Log($"[ProtoScheduler] REJECTED {ctx.Capability} ({ctx.CommandId}): start rejected: {startResult.ErrorMessage}", LogLevel.Warn);
            return _resultFactory.Failed(ctx, ErrorCode.ExecutionFailed, startResult.ErrorMessage ?? "Start rejected");
        }

        if (startResult.CompletedImmediately)
        {
            _monitor.Log($"[ProtoScheduler] COMPLETED {ctx.Capability} ({ctx.CommandId}) immediately", LogLevel.Debug);
            return _resultFactory.Completed(ctx);
        }

        _active = ctx;
        _activeHandler = handler;
        _monitor.Log($"[ProtoScheduler] STARTED {ctx.Capability} ({ctx.CommandId}) async, deadline frame={ctx.DeadlineFrame}", LogLevel.Debug);

        return null; // async, will complete via Tick()
    }

    public CommandResult? Tick(int currentGameFrame)
    {
        if (_active == null || _activeHandler == null)
            return null;

        _active.AdvanceFrame(currentGameFrame);

        if (_active.IsCancelled)
        {
            _activeHandler.Cancel(_active, _active.CancelReason ?? "cancelled");
            var result = _resultFactory.Cancelled(_active);
            ClearActive();
            return result;
        }

        if (_active.IsTimedOut)
        {
            _monitor.Log($"[ProtoScheduler] TIMEOUT {_active.Capability} ({_active.CommandId}) after {_active.ElapsedFrames} frames", LogLevel.Warn);
            _activeHandler.Cancel(_active, "timeout");
            var result = _resultFactory.TimedOut(_active);
            ClearActive();
            return result;
        }

        var tickResult = _activeHandler.Tick(_active);
        switch (tickResult.Status)
        {
            case HandlerTickStatus.Completed:
                _monitor.Log($"[ProtoScheduler] COMPLETED {_active.Capability} ({_active.CommandId}) in {_active.ElapsedFrames} frames", LogLevel.Debug);
                var completed = _resultFactory.Completed(_active);
                ClearActive();
                return completed;

            case HandlerTickStatus.Failed:
                var code = tickResult.FailureCode ?? ErrorCode.ExecutionFailed;
                var msg = tickResult.FailureMessage ?? "Execution failed";
                _monitor.Log($"[ProtoScheduler] FAILED {_active.Capability} ({_active.CommandId}): {msg}", LogLevel.Warn);
                var failed = _resultFactory.Failed(_active, code, msg);
                ClearActive();
                return failed;

            default:
                return null;
        }
    }

    public CommandResult? Cancel(string commandId, string reason)
    {
        if (_active == null || _active.CommandId != commandId)
            return null;

        _monitor.Log($"[ProtoScheduler] CANCEL {_active.Capability} ({_active.CommandId}): {reason}", LogLevel.Info);
        _active.MarkCancelled(reason);
        _activeHandler?.Cancel(_active, reason);
        var result = _resultFactory.Cancelled(_active);
        ClearActive();
        return result;
    }

    private CommandResult ExecutePassive(ProtoCommandContext ctx, IProtoCommandHandler handler)
    {
        var validation = handler.Validate(ctx);
        if (!validation.IsValid)
            return _resultFactory.Rejected(ctx, ErrorCode.InvalidParams, validation.ErrorMessage!);

        var startResult = handler.Start(ctx);
        if (!startResult.Accepted)
            return _resultFactory.Failed(ctx, ErrorCode.ExecutionFailed, startResult.ErrorMessage ?? "Passive execution failed");

        return _resultFactory.Completed(ctx);
    }

    private void ClearActive()
    {
        _active = null;
        _activeHandler = null;
    }
}
