using System;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Menus;
using StarCoPlay.GameServices.SimulatedInput;
using ProtoTilePoint = StarCoPlay.Protocol.V245.TilePoint;

namespace StarCoPlay.GameServices.World;

public enum TileActionState
{
    Idle,
    Running,
    Succeeded,
    Failed
}

public sealed class TileActionService
{
    public const int UseToolBaseTimeoutFrames = 180;
    public const int UseToolFramesPerChargeLevel = 90;
    public const int InteractEffectWaitFrames = 45;

    private const int MicroMoveTimeoutTicks = 15;

    public static int ComputeUseToolTimeoutFrames(int chargeLevel)
    {
        return UseToolBaseTimeoutFrames + Math.Max(0, chargeLevel) * UseToolFramesPerChargeLevel;
    }

    private enum InteractPhase { None, Facing, MicroPositioning, Pressing, WaitingEffect }

    private readonly InputCombo _combo;
    private readonly InputBridge _inputBridge;
    private readonly IMonitor _monitor;
    private readonly InteractTargetingService _targeting = new();
    private int _startedAtFrame;

    private InteractPhase _interactPhase;
    private int _interactFacingDirection;
    private Vector2 _interactTarget;
    private Vector2 _interactStartTile;
    private string? _interactStartLocation;
    private int _effectWaitStartFrame;
    private bool _usedMicroPositioning;
    private bool _sawPlayerBusyDuringEffectWait;
    private string _playerBusyDetail = "";
    private string? _completionReason;
    private string? _failureReason;

    public TileActionState State { get; private set; } = TileActionState.Idle;
    public string? CompletionReason => _completionReason;
    public string? FailureReason => _failureReason;
    public bool UsedMicroPositioning => _usedMicroPositioning;

    public TileActionService(InputCombo combo, InputBridge inputBridge, IMonitor monitor)
    {
        _combo = combo;
        _inputBridge = inputBridge;
        _monitor = monitor;
    }

    public TileActionValidation ValidateTarget(ProtoTilePoint target, bool requireTool)
    {
        if (Game1.player == null)
            return TileActionValidation.Invalid("player is null");
        if (Game1.currentLocation == null)
            return TileActionValidation.Invalid("current location is null");
        if (!_inputBridge.IsPlayerFree)
            return TileActionValidation.Invalid("player is not free");

        int playerX = (int)Game1.player.Tile.X;
        int playerY = (int)Game1.player.Tile.Y;
        int dx = target.X - playerX;
        int dy = target.Y - playerY;

        if (Math.Abs(dx) + Math.Abs(dy) != 1)
            return TileActionValidation.Invalid("target_tile must be cardinal-adjacent to player");

        if (requireTool && Game1.player.CurrentTool == null)
            return TileActionValidation.Invalid("current item is not a tool");

        return TileActionValidation.Ok();
    }

    // ── Interact ──

    public void StartInteract(ProtoTilePoint target, int currentFrame)
    {
        State = TileActionState.Running;
        _startedAtFrame = currentFrame;
        _interactTarget = new Vector2(target.X, target.Y);
        _interactStartTile = Game1.player.Tile;
        _interactStartLocation = Game1.currentLocation?.NameOrUniqueName;
        _interactPhase = InteractPhase.Facing;
        _usedMicroPositioning = false;
        _sawPlayerBusyDuringEffectWait = false;
        _playerBusyDetail = "";
        _completionReason = null;
        _failureReason = null;

        int direction = InteractTargetingService.ComputeFacingDirection(Game1.player.Tile, _interactTarget);
        _interactFacingDirection = direction;
        _combo.Face(direction);

        _monitor.Log($"[TileAction] Interact: target=({target.X},{target.Y}) dir={direction} location={_interactStartLocation}", LogLevel.Debug);
    }

    public void TickInteract(int currentFrame, int timeoutFrames)
    {
        if (State != TileActionState.Running) return;

        if (currentFrame - _startedAtFrame > timeoutFrames)
        {
            FailInteract("interact_timeout");
            return;
        }

        switch (_interactPhase)
        {
            case InteractPhase.Facing:
                TickFacing();
                break;
            case InteractPhase.MicroPositioning:
                TickMicroPositioning();
                break;
            case InteractPhase.Pressing:
                TickPressing(currentFrame);
                break;
            case InteractPhase.WaitingEffect:
                TickWaitingEffect(currentFrame);
                break;
        }
    }

    private void TickFacing()
    {
        if (_inputBridge.IsBusy) return;

        if (_targeting.IsGrabTileAligned(_interactTarget))
        {
            _combo.PressActionButton();
            _interactPhase = InteractPhase.Pressing;
            return;
        }

        var alignStop = _targeting.CreateAlignmentStopCondition(_interactTarget);
        var boundaryStop = InteractTargetingService.CreateTileBoundaryCondition(_interactStartTile);
        _combo.MicroMove(_interactFacingDirection, () => alignStop() || boundaryStop(), MicroMoveTimeoutTicks);
        _usedMicroPositioning = true;
        _interactPhase = InteractPhase.MicroPositioning;
        _monitor.Log($"[TileAction] MicroPositioning: dir={_interactFacingDirection}", LogLevel.Trace);
    }

    private void TickMicroPositioning()
    {
        if (_inputBridge.IsBusy) return;

        if ((int)Game1.player.Tile.X != (int)_interactStartTile.X ||
            (int)Game1.player.Tile.Y != (int)_interactStartTile.Y)
        {
            FailInteract("micro_position_overshot");
            return;
        }

        if (_targeting.IsGrabTileAligned(_interactTarget))
        {
            _combo.PressActionButton();
            _interactPhase = InteractPhase.Pressing;
        }
        else
        {
            FailInteract("target_not_in_grab_tile");
        }
    }

    private void TickPressing(int currentFrame)
    {
        if (_inputBridge.IsBusy) return;

        _effectWaitStartFrame = currentFrame;
        _interactPhase = InteractPhase.WaitingEffect;
    }

    private void TickWaitingEffect(int currentFrame)
    {
        var elapsed = currentFrame - _effectWaitStartFrame;

        if (Game1.currentLocation?.NameOrUniqueName != _interactStartLocation)
        {
            SucceedInteract($"map_changed:{_interactStartLocation}->{Game1.currentLocation?.NameOrUniqueName}");
            return;
        }

        if (Game1.activeClickableMenu is DialogueBox)
        {
            SucceedInteract("dialogue_opened");
            return;
        }

        if (Game1.activeClickableMenu != null)
        {
            SucceedInteract($"menu_opened:{Game1.activeClickableMenu.GetType().Name}");
            return;
        }

        if (!Game1.player.CanMove || Game1.player.UsingTool || Game1.player.FarmerSprite.PauseForSingleAnimation)
        {
            _sawPlayerBusyDuringEffectWait = true;
            _playerBusyDetail =
                $"can_move={Game1.player.CanMove},using_tool={Game1.player.UsingTool},pause_animation={Game1.player.FarmerSprite.PauseForSingleAnimation}";
        }

        if (elapsed > InteractEffectWaitFrames)
        {
            if (_sawPlayerBusyDuringEffectWait)
                SucceedInteract($"player_busy:{_playerBusyDetail}");
            else
                SucceedInteract("no_observed_effect");
        }
    }

    private void SucceedInteract(string reason)
    {
        _completionReason = reason;
        State = TileActionState.Succeeded;
        _monitor.Log($"[TileAction] Interact succeeded: {reason}", LogLevel.Debug);
    }

    private void FailInteract(string reason)
    {
        _failureReason = reason;
        State = TileActionState.Failed;
        _monitor.Log($"[TileAction] Interact failed: {reason}", LogLevel.Debug);
    }

    // ── UseTool ──

    public void StartUseTool(ProtoTilePoint target, int chargeLevel, int currentFrame)
    {
        State = TileActionState.Running;
        _startedAtFrame = currentFrame;

        var targetVec = new Vector2(target.X, target.Y);
        _monitor.Log($"[TileAction] UseTool: target=({target.X},{target.Y}) chargeLevel={chargeLevel}", LogLevel.Debug);

        if (chargeLevel > 0)
            _combo.UseToolCharged(targetVec, chargeLevel);
        else
            _combo.UseTool(targetVec);
    }

    public void TickUseTool(int currentFrame, int timeoutFrames)
    {
        if (State != TileActionState.Running)
            return;

        var elapsed = currentFrame - _startedAtFrame;

        if (elapsed < 6)
            return;

        if (_inputBridge.IsBusy)
            return;

        var player = Game1.player;
        if (!player.UsingTool
            && player.canMove
            && !player.FarmerSprite.PauseForSingleAnimation)
        {
            State = TileActionState.Succeeded;
            return;
        }

        if (elapsed > timeoutFrames)
        {
            State = TileActionState.Failed;
        }
    }

    public bool IsSettled()
    {
        return State == TileActionState.Succeeded || State == TileActionState.Failed;
    }
}
