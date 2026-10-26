using System;
using StardewModdingAPI;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.Query.Projectors.Ui;

namespace StarCoPlay.V2.Query.Handlers;

public class QueryUiSnapshotHandler
{
    private readonly IMonitor _monitor;

    public QueryUiSnapshotHandler(IMonitor monitor)
    {
        _monitor = monitor;
    }

    public QueryUiSnapshotResult Handle(QueryUiSnapshotParams request)
    {
        var result = new QueryUiSnapshotResult();

        try
        {
            result.Fact = UiSnapshotProjector.Project(request.IncludeElements);
            _monitor.Log("[Query] UiSnapshot: projected", LogLevel.Trace);
        }
        catch (Exception ex)
        {
            _monitor.Log($"[Query] UiSnapshot error: {ex.Message}", LogLevel.Warn);
            result.Warnings.Add(new QueryWarning
            {
                Code    = "UI_SNAPSHOT_ERROR",
                Message = ex.Message,
            });
        }

        return result;
    }
}
