using StardewModdingAPI;
using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.ProtocolCore.Results;
using StarCoPlay.GameServices.SimulatedInput;

namespace StarCoPlay.ProtocolCore.Handlers;

public sealed class FaceProtoHandler : IProtoCommandHandler
{
    private readonly InputCombo _combo;
    private readonly InputBridge _inputBridge;
    private readonly IMonitor _monitor;

    private int _requestedDirection; // 0=up 1=right 2=down 3=left

    public string Capability => "face";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.Face;
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveShort;
    public bool IsActive { get; private set; }

    public FaceProtoHandler(InputCombo combo, InputBridge inputBridge, IMonitor monitor)
    {
        _combo = combo;
        _inputBridge = inputBridge;
        _monitor = monitor;
    }

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        var p = ctx.Request.Face;
        if (p.Direction == Direction.Unspecified)
            return ProtoValidationResult.Invalid("direction must not be UNSPECIFIED");
        return ProtoValidationResult.Ok();
    }

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        var p = ctx.Request.Face;
        _requestedDirection = ProtoSnapshotBuilder.FromProtoDirection(p.Direction);

        // 已朝向目标 → 直接完成
        if (Game1.player.FacingDirection == _requestedDirection)
        {
            ctx.TypedResult = new FaceResult
            {
                FinalDirection = p.Direction,
                AlreadyFacing = true
            };
            IsActive = false;
            return HandlerStartResult.Immediate();
        }

        _combo.Face(_requestedDirection);
        IsActive = true;
        _monitor.Log($"[FaceProto] Started: direction={p.Direction} ({_requestedDirection})", LogLevel.Debug);
        return HandlerStartResult.Async();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        if (_inputBridge.IsBusy)
            return HandlerTickResult.Continue;

        if (Game1.player.UsingTool)
            return HandlerTickResult.Continue;

        if (Game1.player.FacingDirection != _requestedDirection)
            return HandlerTickResult.Continue;

        var p = ctx.Request.Face;
        ctx.TypedResult = new FaceResult
        {
            FinalDirection = ProtoSnapshotBuilder.ToProtoDirection(Game1.player.FacingDirection),
            AlreadyFacing = false
        };
        IsActive = false;
        return HandlerTickResult.Completed;
    }

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
        _inputBridge.Clear();
        IsActive = false;
        _monitor.Log($"[FaceProto] Cancelled: {reason}", LogLevel.Debug);
    }
}
