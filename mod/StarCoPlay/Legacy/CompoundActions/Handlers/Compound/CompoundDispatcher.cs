using System;
using System.Collections.Generic;
using StardewModdingAPI;
using StarCoPlay.V2.Models;

namespace StarCoPlay.V2.Compound;

/// <summary>
/// Manages registration and dispatch of compound capability handlers.
/// CommandProcessor delegates to this for any registered compound capability.
/// </summary>
public class CompoundDispatcher
{
    private readonly Dictionary<string, ICompoundHandler> _handlers = new();
    private readonly IMonitor _monitor;

    /// <summary>The currently active compound handler (only one at a time).</summary>
    private ICompoundHandler? _activeHandler;
    private string _activeCapability = "";

    public CompoundDispatcher(IMonitor monitor)
    {
        _monitor = monitor;
    }

    /// <summary>Register a compound handler for a capability name.</summary>
    public void Register(string capability, ICompoundHandler handler)
    {
        if (!_handlers.TryAdd(capability, handler))
            throw new InvalidOperationException($"Compound capability already registered: {capability}");
        _monitor.Log($"[Compound] Registered: {capability}", LogLevel.Debug);
    }

    /// <summary>Check if a capability is handled by a compound handler.</summary>
    public bool IsCompoundCapability(string capability)
        => _handlers.ContainsKey(capability);

    /// <summary>Whether a compound action is currently executing.</summary>
    public bool IsActive => _activeHandler is { IsActive: true };

    /// <summary>The capability name of the currently active compound action.</summary>
    public string ActiveCapability => _activeCapability;

    /// <summary>Human-readable active handler state for SMAPI diagnostics.</summary>
    public string GetDebugStatus()
    {
        if (_activeHandler == null)
            return "none";

        var detail = _activeHandler is ICompoundDebugState debugState
            ? $",state={debugState.GetDebugState()}"
            : "";
        return $"cap={_activeCapability},handler={_activeHandler.GetType().Name},active={_activeHandler.IsActive}{detail}";
    }

    /// <summary>
    /// Start a compound action. Returns V2Result immediately on validation failure,
    /// or null if async execution has started.
    /// </summary>
    public V2Result? Start(string capability, V2Command cmd)
    {
        if (!_handlers.TryGetValue(capability, out var handler))
        {
            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.UnknownCapability,
                $"Unknown compound capability: {capability}");
        }

        if (_activeHandler is { IsActive: true })
        {
            return ResultBuilder.Failure(cmd.Seq, cmd.ActionId,
                ResultBuilder.Errors.ExecutionFailed,
                $"Compound action '{_activeCapability}' already in progress");
        }

        _monitor.Log($"[Compound] Starting: {capability} (seq={cmd.Seq})", LogLevel.Info);
        _activeHandler = handler;
        _activeCapability = capability;

        var result = handler.Start(cmd);
        if (result != null)
        {
            // Completed or failed immediately
            _activeHandler = null;
            _activeCapability = "";
        }
        return result;
    }

    /// <summary>
    /// Tick the active compound handler. Returns V2Result when complete.
    /// </summary>
    public V2Result? Tick()
    {
        if (_activeHandler == null || !_activeHandler.IsActive)
            return null;

        var result = _activeHandler.Tick();
        if (result != null)
        {
            _monitor.Log($"[Compound] Completed: {_activeCapability} (success={result.Success})", LogLevel.Info);
            _activeHandler = null;
            _activeCapability = "";
        }
        return result;
    }

    /// <summary>Cancel the active compound action.</summary>
    public V2Result? Cancel()
    {
        if (_activeHandler == null || !_activeHandler.IsActive)
            return null;

        var result = _activeHandler.Cancel();
        _monitor.Log($"[Compound] Cancelled: {_activeCapability}", LogLevel.Info);
        _activeHandler = null;
        _activeCapability = "";
        return result;
    }
}
