using System;
using StardewModdingAPI;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.Query.Projectors;

namespace StarCoPlay.V2.Query.Handlers;

public class QueryRuntimeSnapshotHandler
{
    private readonly IMonitor _monitor;

    public QueryRuntimeSnapshotHandler(IMonitor monitor)
    {
        _monitor = monitor;
    }

    public QueryRuntimeSnapshotResult Handle(QueryRuntimeSnapshotParams request)
    {
        var result = new QueryRuntimeSnapshotResult();

        try
        {
            // include_* filter flags are intentionally ignored for now — fill everything
            result.Fact = RuntimeSnapshotProjector.Project();
            _monitor.Log("[Query] RuntimeSnapshot: projected", LogLevel.Trace);
        }
        catch (Exception ex)
        {
            _monitor.Log($"[Query] RuntimeSnapshot error: {ex.Message}", LogLevel.Warn);
            result.Warnings.Add(new QueryWarning
            {
                Code    = "SNAPSHOT_ERROR",
                Message = ex.Message,
            });
        }

        return result;
    }
}
