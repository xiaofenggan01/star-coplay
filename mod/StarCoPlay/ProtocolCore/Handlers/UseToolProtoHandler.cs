using StardewModdingAPI;
using StardewValley;
using StardewValley.Tools;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.ProtocolCore.Results;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.GameServices.World;
using ProtoTilePoint = StarCoPlay.Protocol.V245.TilePoint;

namespace StarCoPlay.ProtocolCore.Handlers;

public sealed class UseToolProtoHandler : IProtoCommandHandler
{
    private readonly TileActionService _tileAction;
    private readonly InputBridge _inputBridge;
    private readonly IMonitor _monitor;
    private ProtoTilePoint _target = null!;
    private string _toolName = "";
    private int _chargeLevel;

    public string Capability => "use_tool";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.UseTool;
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveShort;
    public bool IsActive { get; private set; }

    public UseToolProtoHandler(TileActionService tileAction, InputBridge inputBridge, IMonitor monitor)
    {
        _tileAction = tileAction;
        _inputBridge = inputBridge;
        _monitor = monitor;
    }

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        var p = ctx.Request.UseTool;
        if (p.TargetTile == null)
            return ProtoValidationResult.Invalid("target_tile is required");
        if (p.ChargeLevel < 0 || p.ChargeLevel > 4)
            return ProtoValidationResult.Invalid("charge_level must be 0-4");

        var validation = _tileAction.ValidateTarget(p.TargetTile, requireTool: true);
        if (!validation.IsValid)
            return ProtoValidationResult.Invalid(validation.ErrorMessage!);

        if (p.ChargeLevel > 0)
        {
            var tool = Game1.player.CurrentTool;
            if (tool is not Hoe && tool is not WateringCan)
                return ProtoValidationResult.Invalid("charge_level requires upgraded Hoe or WateringCan");
            if (tool.UpgradeLevel < p.ChargeLevel)
                return ProtoValidationResult.Invalid("charge_level exceeds current tool upgrade level");
        }

        return ProtoValidationResult.Ok();
    }

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        var p = ctx.Request.UseTool;
        _target = p.TargetTile;
        _chargeLevel = p.ChargeLevel;
        _toolName = Game1.player.CurrentTool?.DisplayName ?? Game1.player.CurrentTool?.Name ?? "";

        ProtoSnapshotBuilder.CaptureStartState(ctx);
        _tileAction.StartUseTool(_target, _chargeLevel, Game1.ticks);
        IsActive = true;
        _monitor.Log($"[UseToolProto] Started: tool={_toolName} target=({_target.X},{_target.Y}) chargeLevel={_chargeLevel}", LogLevel.Debug);
        return HandlerStartResult.Async();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        var timeoutFrames = TileActionService.ComputeUseToolTimeoutFrames(_chargeLevel);
        _tileAction.TickUseTool(Game1.ticks, timeoutFrames);

        if (!_tileAction.IsSettled())
            return HandlerTickResult.Continue;

        if (_tileAction.State == TileActionState.Failed)
        {
            var timeoutReason = _chargeLevel > 0 ? "charged_animation_timeout" : "animation_timeout";
            ctx.TypedResult = new UseToolResult
            {
                Effect = new TileEffectResult
                {
                    TargetTile = _target,
                    Stamina = ProtoSnapshotBuilder.StaminaDelta(ctx),
                    Timing = ProtoTiming.Capture(ctx, timeoutReason)
                },
                ToolName = _toolName,
                ChargeLevel = _chargeLevel
            };
            IsActive = false;
            return HandlerTickResult.Failed(ErrorCode.ExecutionFailed, "use_tool animation timed out");
        }

        ctx.TypedResult = new UseToolResult
        {
            Effect = new TileEffectResult
            {
                TargetTile = _target,
                Stamina = ProtoSnapshotBuilder.StaminaDelta(ctx),
                Timing = ProtoTiming.Capture(ctx, "animation_done")
            },
            ToolName = _toolName,
            ChargeLevel = _chargeLevel
        };
        IsActive = false;
        return HandlerTickResult.Completed;
    }

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
        _inputBridge.Clear();
        IsActive = false;
        _monitor.Log($"[UseToolProto] Cancelled: {reason}", LogLevel.Debug);
    }
}
