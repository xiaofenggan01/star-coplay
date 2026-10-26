using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// Interface for compound (multi-step) capability handlers.
/// Unlike atomic handlers that return immediately or defer to AnimationAwaiter,
/// compound handlers manage their own state machine across multiple frames.
///
/// Lifecycle:
///   1. CommandProcessor calls Start(cmd) — handler initializes state machine
///   2. Each frame, CommandProcessor calls Tick() — handler advances one step
///   3. Tick() returns V2Result when complete (success or failure), null while running
///
/// Compound handlers own their internal navigation + tool use cycles.
/// They use ActionExecutor directly (same as MoveToHandler pattern).
/// </summary>
public interface ICompoundHandler
{
    /// <summary>Whether the handler is currently executing a compound action.</summary>
    bool IsActive { get; }

    /// <summary>
    /// Initialize and start the compound action.
    /// Returns V2Result immediately if validation fails (e.g. no crops, no tool).
    /// Returns null if async execution has started — caller should Tick() each frame.
    /// </summary>
    V2Result? Start(V2Command cmd);

    /// <summary>
    /// Advance the state machine by one frame.
    /// Returns V2Result when the compound action is complete.
    /// Returns null while still in progress.
    /// </summary>
    V2Result? Tick();

    /// <summary>
    /// Force-cancel the current compound action.
    /// Returns a partial-success result with progress so far.
    /// </summary>
    V2Result Cancel();
}

/// <summary>
/// Optional detailed state for SMAPI/debug diagnostics.
/// </summary>
public interface ICompoundDebugState
{
    string GetDebugState();
}
