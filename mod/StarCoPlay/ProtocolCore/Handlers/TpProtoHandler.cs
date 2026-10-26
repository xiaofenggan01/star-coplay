using StardewModdingAPI;
using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.ProtocolCore.Results;
using StarCoPlay.GameServices.SimulatedInput;
using SmapiContext = StardewModdingAPI.Context;

namespace StarCoPlay.ProtocolCore.Handlers;

public sealed class TpProtoHandler : IProtoCommandHandler
{
    private readonly IMonitor _monitor;
    private TilePosition _target = null!;
    private string _resolvedMapId = "";
    private TeleportReason _reason;
    private LocationSnapshot _from = null!;
    private int _stableFrames;

    public string Capability => "tp";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.Tp;
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveShort;
    public bool IsActive { get; private set; }

    public TpProtoHandler(IMonitor monitor)
    {
        _monitor = monitor;
    }

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        var p = ctx.Request.Tp;
        if (p.Target == null || string.IsNullOrEmpty(p.Target.MapId))
            return ProtoValidationResult.Invalid("target.map_id is required");
        if (p.Reason == TeleportReason.Unspecified)
            return ProtoValidationResult.Invalid("reason must not be UNSPECIFIED");
        if (ctx.Source == CommandSource.Llm)
            return ProtoValidationResult.Invalid("tp is not allowed from COMMAND_SOURCE_LLM");
        if (ctx.Source != CommandSource.Diagnostic && ctx.Source != CommandSource.AgentRuntime)
            return ProtoValidationResult.Invalid("tp source must be DIAGNOSTIC or AGENT_RUNTIME");

        var location = Utility.fuzzyLocationSearch(p.Target.MapId);
        if (location == null)
            return ProtoValidationResult.Invalid($"map '{p.Target.MapId}' not found");

        return ProtoValidationResult.Ok();
    }

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        var p = ctx.Request.Tp;
        _target = p.Target;
        _reason = p.Reason;
        _from = ProtoSnapshotBuilder.CurrentLocationSnapshot();
        _stableFrames = 0;

        var resolved = Utility.fuzzyLocationSearch(p.Target.MapId);
        _resolvedMapId = resolved!.NameOrUniqueName;

        ProtoSnapshotBuilder.CaptureStartState(ctx);

        int faceDir = p.FaceDirection != Direction.Unspecified
            ? ProtoSnapshotBuilder.FromProtoDirection(p.FaceDirection)
            : Game1.player.FacingDirection;

        Game1.warpFarmer(_resolvedMapId, _target.X, _target.Y, faceDir);
        IsActive = true;
        _monitor.Log($"[TpProto] Started: resolved={_resolvedMapId} ({_target.X},{_target.Y}) reason={_reason}", LogLevel.Debug);
        return HandlerStartResult.Async();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        if (Game1.currentLocation == null)
            return HandlerTickResult.Continue;

        if (Game1.currentLocation.NameOrUniqueName != _resolvedMapId)
            return HandlerTickResult.Continue;

        var playerTile = Game1.player.Tile;
        if ((int)playerTile.X != _target.X || (int)playerTile.Y != _target.Y)
            return HandlerTickResult.Continue;

        if (!SmapiContext.CanPlayerMove || Game1.player.UsingTool)
        {
            _stableFrames = 0;
            return HandlerTickResult.Continue;
        }

        _stableFrames++;
        if (_stableFrames < 2)
            return HandlerTickResult.Continue;

        string usedBy = _reason switch
        {
            TeleportReason.TeleportGoToBed => "go_to_bed",
            TeleportReason.TeleportTest => "diagnostic",
            TeleportReason.TeleportRecovery => "recovery",
            _ => "unknown"
        };

        ctx.TypedResult = new TpResult
        {
            Reason = _reason,
            From = _from,
            Target = new TilePosition { MapId = _resolvedMapId, X = _target.X, Y = _target.Y },
            Final = ProtoSnapshotBuilder.CurrentLocationSnapshot(),
            FaceDirection = ProtoSnapshotBuilder.CurrentFacingDirection(),
            Authorized = true,
            UsedBy = usedBy,
            Timing = ProtoTiming.Capture(ctx, "warp_arrived")
        };
        IsActive = false;
        return HandlerTickResult.Completed;
    }

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
        IsActive = false;
        _monitor.Log($"[TpProto] Cancelled: {reason}", LogLevel.Debug);
    }
}
