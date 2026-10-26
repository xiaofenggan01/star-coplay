using StardewModdingAPI;
using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.ProtocolCore.Results;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.GameServices.World;
using ProtoTilePoint = StarCoPlay.Protocol.V245.TilePoint;

namespace StarCoPlay.ProtocolCore.Handlers;

public sealed class InteractProtoHandler : IProtoCommandHandler
{
    private readonly TileActionService _tileAction;
    private readonly InputBridge _inputBridge;
    private readonly IMonitor _monitor;
    private ProtoTilePoint _target = null!;
    private const int InteractTimeoutFrames = 120;

    public string Capability => "interact";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.Interact;
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveShort;
    public bool IsActive { get; private set; }

    public InteractProtoHandler(TileActionService tileAction, InputBridge inputBridge, IMonitor monitor)
    {
        _tileAction = tileAction;
        _inputBridge = inputBridge;
        _monitor = monitor;
    }

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        var p = ctx.Request.Interact;
        if (p.TargetTile == null)
            return ProtoValidationResult.Invalid("target_tile is required");

        var validation = _tileAction.ValidateTarget(p.TargetTile, requireTool: false);
        if (!validation.IsValid)
            return ProtoValidationResult.Invalid(validation.ErrorMessage!);

        return ProtoValidationResult.Ok();
    }

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        var p = ctx.Request.Interact;
        _target = p.TargetTile;

        ProtoSnapshotBuilder.CaptureStartState(ctx);
        _tileAction.StartInteract(_target, Game1.ticks);
        IsActive = true;
        return HandlerStartResult.Async();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        _tileAction.TickInteract(Game1.ticks, InteractTimeoutFrames);

        if (!_tileAction.IsSettled())
            return HandlerTickResult.Continue;

        var reason = _tileAction.State == TileActionState.Failed
            ? _tileAction.FailureReason ?? "unknown"
            : _tileAction.CompletionReason ?? "unknown";

        ctx.TypedResult = new InteractResult
        {
            Effect = new TileEffectResult
            {
                TargetTile = _target,
                Stamina = ProtoSnapshotBuilder.StaminaDelta(ctx),
                Timing = ProtoTiming.Capture(ctx, reason)
            }
        };
        IsActive = false;

        if (_tileAction.State == TileActionState.Failed)
        {
            var msg = reason switch
            {
                "micro_position_overshot" => "micro-positioning overshot tile boundary",
                "target_not_in_grab_tile" => "could not align action button with target tile",
                _ => $"interact failed: {reason}",
            };
            return HandlerTickResult.Failed(ErrorCode.ExecutionFailed, msg);
        }

        return HandlerTickResult.Completed;
    }

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
        _inputBridge.Clear();
        IsActive = false;
        _monitor.Log($"[InteractProto] Cancelled: {reason}", LogLevel.Debug);
    }
}
