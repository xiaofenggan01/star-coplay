using System;
using System.Linq;
using StardewModdingAPI;
using StardewModdingAPI.Utilities;
using StardewValley;
using StardewValley.Menus;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.ProtocolCore.GoTo;
using StarCoPlay.GameServices.SimulatedInput;
using StarCoPlay.ProtocolCore.Results;
using StarCoPlay.GameServices.Sleep;

namespace StarCoPlay.ProtocolCore.Handlers;

/// <summary>
/// Proto-native go_to_bed handler (L2 composite).
///
/// 7-phase state machine that:
///   1. Scans for sleepable beds
///   2. Navigates to the bed via child go_to
///   3. Triggers and confirms the sleep prompt
///   4. Waits for the new day transition
///   5. Drains post-sleep UI (ShippingMenu, SaveGameMenu, etc.)
///   6. Reports DecisionRequired if LevelUpMenu appears
///
/// Uses child GoToProtoHandler and InteractProtoHandler via
/// GoToChildCommandFactory pattern for movement and interaction.
/// </summary>
public sealed class GoToBedProtoHandler : IProtoCommandHandler
{
    private const int DefaultTimeoutFrames      = 7200;  // ~2 minutes at 60fps
    private const int SleepPromptWaitFrames     = 60;    // wait for TouchAction auto-trigger
    private const int SleepConfirmWaitFrames    = 120;   // wait for newDay after confirm
    private const int MaxDrainSteps             = 50;    // safety valve for post-sleep UI

    private readonly GoToProtoHandler      _goTo;
    private readonly InteractProtoHandler   _interact;
    private readonly InputBridge            _inputBridge;
    private readonly MenuNavigator          _menuNavigator;
    private readonly SleepBedScanner        _bedScanner;
    private readonly SleepStateObserver     _sleepState;
    private readonly EndOfDayUiDriver       _endOfDayUi;
    private readonly IMonitor               _monitor;

    public string Capability => "go_to_bed";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.GoToBed;
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveLong;
    public bool IsActive { get; private set; }

    // ── State machine ──
    private enum Phase
    {
        Idle,
        ScanBeds,
        MoveToBed,
        TriggerSleepPrompt,
        ConfirmSleep,
        WaitNewDay,
        DrainPostSleepUi,
        Complete,
        DecisionRequired
    }

    // ── Per-command state ──
    private Phase    _phase;
    private int      _elapsed;
    private int      _totalTimeout;
    private int      _phaseElapsed;

    // Before state
    private string   _dateBefore = "";
    private int      _timeBefore;
    private LocationSnapshot _locationBefore = null!;
    private float    _staminaBefore;
    private int      _moneyBefore;
    private int      _dayBefore;
    private int      _yearBefore;

    // Bed target
    private SleepBedCandidate _bed = null!;

    // Child handler contexts
    private ProtoCommandContext? _childCtx;
    private bool _childGoToActive;
    private bool _childInteractActive;

    // Progress tracking
    private MoveToResult? _moveResult;
    private bool _sleepPromptSeen;
    private bool _sleepConfirmed;
    private int  _postSleepUiSteps;
    private bool _decisionRequired;
    private bool _dayAdvanced;
    private string? _decisionKind;
    private string? _blockingMenuType;
    private string _terminalPhase = "";

    internal GoToBedProtoHandler(
        GoToProtoHandler goTo,
        InteractProtoHandler interact,
        InputBridge inputBridge,
        MenuNavigator menuNavigator,
        SleepBedScanner bedScanner,
        SleepStateObserver sleepState,
        EndOfDayUiDriver endOfDayUi,
        IMonitor monitor)
    {
        _goTo        = goTo;
        _interact    = interact;
        _inputBridge = inputBridge;
        _menuNavigator = menuNavigator;
        _bedScanner  = bedScanner;
        _sleepState  = sleepState;
        _endOfDayUi  = endOfDayUi;
        _monitor     = monitor;
    }

    // ─────────────────────────────────────────
    //  Validate
    // ─────────────────────────────────────────

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        if (Game1.player == null)
            return ProtoValidationResult.Invalid("player is null");
        if (Game1.currentLocation == null)
            return ProtoValidationResult.Invalid("currentLocation is null");

        return ProtoValidationResult.Ok();
    }

    // ─────────────────────────────────────────
    //  Start
    // ─────────────────────────────────────────

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        // Pre-checks
        if (Game1.activeClickableMenu != null)
            return HandlerStartResult.Rejected("menu is open");
        if (Game1.newDay)
            return HandlerStartResult.Rejected("already in new day transition");

        ResetState();

        var p = ctx.Request.GoToBed;
        _totalTimeout = p.TimeoutFrames > 0 ? p.TimeoutFrames : DefaultTimeoutFrames;

        // Capture before state
        ProtoSnapshotBuilder.CaptureStartState(ctx);
        _locationBefore = ProtoSnapshotBuilder.CurrentLocationSnapshot();
        _staminaBefore  = Game1.player!.stamina;
        _moneyBefore    = Game1.player.Money;
        _timeBefore     = Game1.timeOfDay;
        _dayBefore      = Game1.dayOfMonth;
        _yearBefore     = Game1.year;
        _dateBefore     = $"{Game1.currentSeason} {Game1.dayOfMonth}, Year {Game1.year}";

        _phase   = Phase.ScanBeds;
        IsActive = true;


        _monitor.Log("[GoToBedProto] Started", LogLevel.Info);
        return HandlerStartResult.Async();
    }

    // ─────────────────────────────────────────
    //  Tick
    // ─────────────────────────────────────────

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        _elapsed++;

        if (_elapsed >= _totalTimeout)
        {
            CancelActiveChild("total timeout");
            return Fail(ErrorCode.Timeout, $"go_to_bed timeout ({_elapsed} frames) in phase {_phase}");
        }

        return _phase switch
        {
            Phase.ScanBeds           => TickScanBeds(ctx),
            Phase.MoveToBed          => TickMoveToBed(ctx),
            Phase.TriggerSleepPrompt => TickTriggerSleepPrompt(ctx),
            Phase.ConfirmSleep       => TickConfirmSleep(ctx),
            Phase.WaitNewDay         => TickWaitNewDay(ctx),
            Phase.DrainPostSleepUi   => TickDrainPostSleepUi(ctx),
            _                        => HandlerTickResult.Continue
        };
    }

    // ─────────────────────────────────────────
    //  Phase: ScanBeds
    // ─────────────────────────────────────────

    private HandlerTickResult TickScanBeds(ProtoCommandContext ctx)
    {
        var beds = _bedScanner.Scan();
        if (beds.Count == 0)
            return Fail(ErrorCode.ExecutionFailed, "no sleepable bed found");

        _bed = beds[0]; // already sorted by priority (current map > home > other)
        _monitor.Log($"[GoToBedProto] Selected bed at {_bed.MapId} ({_bed.BedSpotX},{_bed.BedSpotY})", LogLevel.Info);

        // Check if player is already at the bed spot
        var player = Game1.player!;
        int px = (int)player.Tile.X;
        int py = (int)player.Tile.Y;

        if (_bed.IsCurrentMap && px == _bed.BedSpotX && py == _bed.BedSpotY)
        {
            _monitor.Log("[GoToBedProto] Already at bed spot, skipping navigation", LogLevel.Debug);
            _phase = Phase.TriggerSleepPrompt;
            _phaseElapsed = 0;
            return HandlerTickResult.Continue;
        }

        // Start child go_to navigation to bed spot
        return StartChildGoTo(ctx);
    }

    private HandlerTickResult StartChildGoTo(ProtoCommandContext ctx)
    {
        int remaining    = _totalTimeout - _elapsed;
        int childTimeout = Math.Min(remaining, 3600); // max 60s for navigation

        var goToParams = new GoToParams
        {
            MapId       = _bed.MapId,
            X           = _bed.BedSpotX,
            Y           = _bed.BedSpotY,
            ArrivalMode = ArrivalMode.ArrivalExact,
            TimeoutFrames = childTimeout,
        };

        var commandId = $"{ctx.CommandId}:go_to_bed:go_to";
        var request = new CommandRequest
        {
            CommandId  = commandId,
            Capability = "go_to",
            TimeoutMs  = ctx.TimeoutMs,
            Source     = ctx.Source,
            GoTo       = goToParams,
        };
        var envelope = new Envelope
        {
            RequestId = ctx.RequestId,
            SessionId = ctx.SessionId,
            MessageId = commandId,
        };
        _childCtx = new ProtoCommandContext(envelope, request, Game1.ticks);

        var validation = _goTo.Validate(_childCtx);
        if (!validation.IsValid)
        {
            _monitor.Log($"[GoToBedProto] go_to validation failed: {validation.ErrorMessage}", LogLevel.Warn);
            return Fail(ErrorCode.ExecutionFailed, $"cannot navigate to bed: {validation.ErrorMessage}");
        }

        var startResult = _goTo.Start(_childCtx);
        if (!startResult.Accepted)
        {
            _monitor.Log($"[GoToBedProto] go_to rejected: {startResult.ErrorMessage}", LogLevel.Warn);
            return Fail(ErrorCode.ExecutionFailed, $"cannot navigate to bed: {startResult.ErrorMessage}");
        }

        _childGoToActive = true;
        _phase = Phase.MoveToBed;

        if (startResult.CompletedImmediately)
        {
            _childGoToActive = false;
            _moveResult = _childCtx.TypedResult as MoveToResult;
            _phase = Phase.TriggerSleepPrompt;
            _phaseElapsed = 0;
        }

        return HandlerTickResult.Continue;
    }

    // ─────────────────────────────────────────
    //  Phase: MoveToBed
    // ─────────────────────────────────────────

    private HandlerTickResult TickMoveToBed(ProtoCommandContext ctx)
    {
        if (_childCtx == null) return HandlerTickResult.Continue;

        _childCtx.AdvanceFrame(Game1.ticks);
        var tickResult = _goTo.Tick(_childCtx);

        if (tickResult.Status == HandlerTickStatus.Completed)
        {
            _childGoToActive = false;
            // Capture move result (GoToResult contains final move info)
            if (_childCtx.TypedResult is GoToResult goToResult)
            {
                _moveResult = goToResult.FinalMoveResult;
            }
            _monitor.Log("[GoToBedProto] Arrived at bed", LogLevel.Debug);
            _phase = Phase.TriggerSleepPrompt;
            _phaseElapsed = 0;
            return HandlerTickResult.Continue;
        }

        if (tickResult.Status == HandlerTickStatus.Failed)
        {
            _childGoToActive = false;
            return Fail(tickResult.FailureCode ?? ErrorCode.ExecutionFailed,
                $"cannot reach bed: {tickResult.FailureMessage}");
        }

        return HandlerTickResult.Continue;
    }

    // ─────────────────────────────────────────
    //  Phase: TriggerSleepPrompt
    // ─────────────────────────────────────────

    private HandlerTickResult TickTriggerSleepPrompt(ProtoCommandContext ctx)
    {
        _phaseElapsed++;

        // Check if sleep prompt already appeared (TouchAction auto-trigger)
        if (_sleepState.IsSleepPromptOpen())
        {
            _sleepPromptSeen = true;
            _monitor.Log("[GoToBedProto] Sleep prompt detected (auto-triggered)", LogLevel.Debug);
            _phase = Phase.ConfirmSleep;
            _phaseElapsed = 0;
            return HandlerTickResult.Continue;
        }

        // Wait some frames for auto-trigger before using interact
        if (_phaseElapsed < SleepPromptWaitFrames)
            return HandlerTickResult.Continue;

        // Auto-trigger didn't fire, use interact on bed spot
        if (_phaseElapsed == SleepPromptWaitFrames)
        {
            _monitor.Log("[GoToBedProto] No auto-trigger, using interact on bed", LogLevel.Debug);
            return StartChildInteract(ctx);
        }

        // If interact child is active, drive it
        if (_childInteractActive && _childCtx != null)
        {
            _childCtx.AdvanceFrame(Game1.ticks);
            var tickResult = _interact.Tick(_childCtx);

            if (tickResult.Status == HandlerTickStatus.Completed)
            {
                _childInteractActive = false;
                // Check if sleep prompt appeared after interact
                if (_sleepState.IsSleepPromptOpen())
                {
                    _sleepPromptSeen = true;
                    _monitor.Log("[GoToBedProto] Sleep prompt detected after interact", LogLevel.Debug);
                    _phase = Phase.ConfirmSleep;
                    _phaseElapsed = 0;
                    return HandlerTickResult.Continue;
                }
            }
            else if (tickResult.Status == HandlerTickStatus.Failed)
            {
                _childInteractActive = false;
                // interact failed but maybe prompt appeared anyway
                if (_sleepState.IsSleepPromptOpen())
                {
                    _sleepPromptSeen = true;
                    _phase = Phase.ConfirmSleep;
                    _phaseElapsed = 0;
                    return HandlerTickResult.Continue;
                }
            }
            else
            {
                return HandlerTickResult.Continue;
            }
        }

        // After interact, wait a few more frames for prompt
        if (_phaseElapsed > SleepPromptWaitFrames + 60)
        {
            // Check one more time
            if (_sleepState.IsSleepPromptOpen())
            {
                _sleepPromptSeen = true;
                _phase = Phase.ConfirmSleep;
                _phaseElapsed = 0;
                return HandlerTickResult.Continue;
            }
            return Fail(ErrorCode.ExecutionFailed, "sleep prompt not appearing");
        }

        // Keep checking for prompt
        if (_sleepState.IsSleepPromptOpen())
        {
            _sleepPromptSeen = true;
            _phase = Phase.ConfirmSleep;
            _phaseElapsed = 0;
            return HandlerTickResult.Continue;
        }

        return HandlerTickResult.Continue;
    }

    private HandlerTickResult StartChildInteract(ProtoCommandContext ctx)
    {
        var tilePoint = new Protocol.V245.TilePoint { X = _bed.BedSpotX, Y = _bed.BedSpotY };
        _childCtx = GoToChildCommandFactory.CreateInteractContext(ctx, 0, tilePoint, Game1.ticks);

        var validation = _interact.Validate(_childCtx);
        if (!validation.IsValid)
        {
            _monitor.Log($"[GoToBedProto] interact validation failed: {validation.ErrorMessage}", LogLevel.Debug);
            // Not fatal — maybe TouchAction will still fire
            return HandlerTickResult.Continue;
        }

        var startResult = _interact.Start(_childCtx);
        if (!startResult.Accepted)
        {
            _monitor.Log($"[GoToBedProto] interact rejected: {startResult.ErrorMessage}", LogLevel.Debug);
            return HandlerTickResult.Continue;
        }

        _childInteractActive = true;

        if (startResult.CompletedImmediately)
        {
            _childInteractActive = false;
        }

        return HandlerTickResult.Continue;
    }

    // ─────────────────────────────────────────
    //  Phase: ConfirmSleep
    // ─────────────────────────────────────────

    private const int ConfirmRetryInterval = 20;  // frames between retries
    private const int MaxConfirmRetries = 3;
    private bool _confirmInputSent;
    private int  _confirmAttempts;

    private HandlerTickResult TickConfirmSleep(ProtoCommandContext ctx)
    {
        _phaseElapsed++;

        // Already confirmed — transition
        if (Game1.newDay)
        {
            _sleepConfirmed = true;
            _phase = Phase.WaitNewDay;
            _phaseElapsed = 0;
            return HandlerTickResult.Continue;
        }

        // Sleep prompt still open?
        if (Game1.activeClickableMenu is DialogueBox dlg
            && dlg.isQuestion
            && Game1.currentLocation?.lastQuestionKey == "Sleep")
        {
            if (_inputBridge.IsBusy)
                return HandlerTickResult.Continue;

            // Validate responses
            if (dlg.responses == null || dlg.responses.Length == 0)
                return Fail(ErrorCode.ExecutionFailed, "sleep prompt has no responses");

            var yes = dlg.responses[0];
            if (!string.Equals(yes.responseKey, "Yes", StringComparison.OrdinalIgnoreCase))
                return Fail(ErrorCode.ExecutionFailed, $"sleep prompt first response is not Yes: {yes.responseKey}");

            // Find Yes component via QueryMenu snapshot
            var snapshot = _menuNavigator.QueryMenu();
            var yesComponent = snapshot.Components.FirstOrDefault(c =>
                c.Source == MenuNavigator.SourcePage
                && c.Id == 0
                && (
                    string.Equals(c.Name, "Yes", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(c.Label, yes.responseText, StringComparison.OrdinalIgnoreCase)
                    || string.Equals(c.Label, "Yes", StringComparison.OrdinalIgnoreCase)
                ));

            if (yesComponent == null)
            {
                if (_phaseElapsed < 30)
                    return HandlerTickResult.Continue;

                return Fail(ErrorCode.ExecutionFailed, "sleep Yes response component not found by query_menu");
            }

            bool shouldSend = !_confirmInputSent
                || (_phaseElapsed > _confirmAttempts * ConfirmRetryInterval && _confirmAttempts < MaxConfirmRetries);

            if (shouldSend)
            {
                // DialogueBox.receiveLeftClick requires selectedResponse != -1 to process clicks
                // Set it to the target response index before clicking
                dlg.selectedResponse = yesComponent.Id;

                bool clicked = _menuNavigator.Click(yesComponent.Id, MenuNavigator.SourcePage);

                if (!clicked)
                {
                    _confirmAttempts++;
                    _monitor.Log($"[GoToBedProto] menu click Yes failed (attempt {_confirmAttempts})", LogLevel.Warn);

                    if (_confirmAttempts >= MaxConfirmRetries)
                        return Fail(ErrorCode.ExecutionFailed, "failed to click sleep Yes response");

                    return HandlerTickResult.Continue;
                }

                _confirmInputSent = true;
                _confirmAttempts++;
                _monitor.Log($"[GoToBedProto] clicked sleep Yes via menu_click path (component_id={yesComponent.Id})", LogLevel.Debug);
            }

            return HandlerTickResult.Continue;
        }

        // Dialogue closed but newDay not set yet — check a few more frames
        if (_confirmInputSent && _phaseElapsed < SleepConfirmWaitFrames)
            return HandlerTickResult.Continue;

        // Exceeded max retries or wait time
        if (_confirmAttempts >= MaxConfirmRetries || _phaseElapsed > SleepConfirmWaitFrames)
            return Fail(ErrorCode.ExecutionFailed, "sleep not triggered after confirm");

        return HandlerTickResult.Continue;
    }

    // ─────────────────────────────────────────
    //  Phase: WaitNewDay
    // ─────────────────────────────────────────

    private HandlerTickResult TickWaitNewDay(ProtoCommandContext ctx)
    {
        _phaseElapsed++;

        // Check if day has advanced
        if (_sleepState.HasDayAdvanced(_dayBefore, _yearBefore))
        {
            _dayAdvanced = true;
            _monitor.Log("[GoToBedProto] Day advanced, entering drain phase", LogLevel.Debug);
            _phase = Phase.DrainPostSleepUi;
            _phaseElapsed = 0;
            return HandlerTickResult.Continue;
        }

        // Check if post-sleep menu appeared (ShippingMenu / LevelUpMenu / SaveGameMenu)
        var menuType = _sleepState.GetActiveMenuTypeName();
        if (menuType is "ShippingMenu" or "LevelUpMenu" or "SaveGameMenu")
        {
            _dayAdvanced = true; // day has effectively advanced if these menus appear
            _monitor.Log($"[GoToBedProto] Post-sleep menu '{menuType}' detected", LogLevel.Debug);
            _phase = Phase.DrainPostSleepUi;
            _phaseElapsed = 0;
            return HandlerTickResult.Continue;
        }

        // Note: total timeout from outer Tick handles timeout here

        return HandlerTickResult.Continue;
    }

    // ─────────────────────────────────────────
    //  Phase: DrainPostSleepUi
    // ─────────────────────────────────────────

    private HandlerTickResult TickDrainPostSleepUi(ProtoCommandContext ctx)
    {
        _phaseElapsed++;

        var driveResult = _endOfDayUi.TryAdvance();

        switch (driveResult)
        {
            case UiDriveResult.Progressed:
                _postSleepUiSteps++;
                if (_postSleepUiSteps > MaxDrainSteps)
                    return Fail(ErrorCode.ExecutionFailed, "too many UI steps during post-sleep drain");
                return HandlerTickResult.Continue;

            case UiDriveResult.DecisionRequired:
                _decisionRequired  = true;
                _decisionKind      = "skill_profession_choice";
                _blockingMenuType  = _endOfDayUi.GetBlockingMenuType() ?? "LevelUpMenu";
                _dayAdvanced       = true;
                _terminalPhase     = "waiting_level_up_choice, please use query_menu and menu_click to continue. ";
                return Succeed(ctx);

            case UiDriveResult.NothingToDo:
                // Strict post-sleep completion: day advanced + no menu + no farmEvent
                // + not in endOfNight + player operable + no event/dialogue
                if (_sleepState.IsPostSleepComplete(_dayBefore, _yearBefore))
                {
                    _dayAdvanced = true;
                    _terminalPhase = "completed";
                    return Succeed(ctx);
                }
                return HandlerTickResult.Continue;

            case UiDriveResult.Unknown:
                var unknownMenu = Game1.activeClickableMenu?.GetType().Name ?? "unknown";
                _monitor.Log($"[GoToBedProto] Unknown blocking menu: {unknownMenu}", LogLevel.Warn);
                return Fail(ErrorCode.ExecutionFailed, $"unknown blocking menu: {unknownMenu}");

            default:
                return HandlerTickResult.Continue;
        }
    }

    // ─────────────────────────────────────────
    //  Cancel
    // ─────────────────────────────────────────

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
        CancelActiveChild(reason);
        IsActive = false;
        _phase   = Phase.Idle;

        // If sleep was already confirmed, we can't roll back — note in result
        if (_sleepConfirmed)
        {
            _monitor.Log($"[GoToBedProto] Cancelled after sleep confirmed (irreversible): {reason}", LogLevel.Warn);
        }
        else
        {
            _monitor.Log($"[GoToBedProto] Cancelled: {reason}", LogLevel.Debug);
        }
    }

    // ─────────────────────────────────────────
    //  Succeed / Fail helpers
    // ─────────────────────────────────────────

    private HandlerTickResult Succeed(ProtoCommandContext ctx)
    {
        var result = new GoToBedResult
        {
            DateBefore         = _dateBefore,
            DateAfter          = $"{Game1.currentSeason} {Game1.dayOfMonth}, Year {Game1.year}",
            TimeBefore         = _timeBefore,
            TimeAfter          = Game1.timeOfDay,
            LocationBefore     = _locationBefore,
            LocationAfter      = ProtoSnapshotBuilder.CurrentLocationSnapshot(),
            Stamina            = new ResourceDelta
            {
                Before = (int)_staminaBefore,
                After  = (int)(Game1.player?.stamina ?? 0f),
                Delta  = (int)((Game1.player?.stamina ?? 0f) - _staminaBefore)
            },
            MoneyBefore        = _moneyBefore,
            MoneyAfter         = Game1.player?.Money ?? _moneyBefore,
            BedPosition        = new TilePosition
            {
                MapId = _bed.MapId,
                X     = _bed.BedSpotX,
                Y     = _bed.BedSpotY
            },
            UsedTp             = false,
            MoveToBedResult    = _moveResult,
            SleepPromptSeen    = _sleepPromptSeen,
            SleepConfirmed     = _sleepConfirmed,
            PostSleepUiSteps   = _postSleepUiSteps,
            DecisionRequired   = _decisionRequired,
            DayAdvanced        = _dayAdvanced,
            PlayerCanMoveAfter = _sleepState.IsPlayerOperable(),
            Timing             = ProtoTiming.Capture(ctx, _terminalPhase),
            DecisionKind       = _decisionKind ?? "",
            BlockingMenuType   = _blockingMenuType ?? "",
            Phase              = _terminalPhase,
        };

        ctx.TypedResult = result;
        IsActive = false;
        _phase   = Phase.Complete;

        _monitor.Log($"[GoToBedProto] Completed: day_advanced={_dayAdvanced}, decision_required={_decisionRequired}, " +
                     $"ui_steps={_postSleepUiSteps}, phase={_terminalPhase}", LogLevel.Info);
        return HandlerTickResult.Completed;
    }

    private HandlerTickResult Fail(ErrorCode code, string message)
    {
        CancelActiveChild(message);
        IsActive = false;
        _phase   = Phase.Idle;
        _monitor.Log($"[GoToBedProto] Failed: {message}", LogLevel.Warn);
        return HandlerTickResult.Failed(code, message);
    }

    private void CancelActiveChild(string reason)
    {
        if (_childGoToActive && _childCtx != null)
        {
            _goTo.Cancel(_childCtx, reason);
            _childGoToActive = false;
        }
        if (_childInteractActive && _childCtx != null)
        {
            _interact.Cancel(_childCtx, reason);
            _childInteractActive = false;
        }
    }

    private void ResetState()
    {
        _phase              = Phase.Idle;
        _elapsed            = 0;
        _totalTimeout       = DefaultTimeoutFrames;
        _phaseElapsed       = 0;
        _dateBefore         = "";
        _timeBefore         = 0;
        _locationBefore     = null!;
        _staminaBefore      = 0;
        _moneyBefore        = 0;
        _dayBefore          = 0;
        _yearBefore         = 0;
        _bed                = null!;
        _childCtx           = null;
        _childGoToActive    = false;
        _childInteractActive = false;
        _confirmInputSent   = false;
        _confirmAttempts    = 0;
        _moveResult         = null;
        _sleepPromptSeen    = false;
        _sleepConfirmed     = false;
        _postSleepUiSteps   = 0;
        _decisionRequired   = false;
        _dayAdvanced        = false;
        _decisionKind       = null;
        _blockingMenuType   = null;
        _terminalPhase      = "";
        IsActive            = false;
    }
}
