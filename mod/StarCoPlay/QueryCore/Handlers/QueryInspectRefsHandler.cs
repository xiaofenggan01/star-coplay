using System;
using StardewModdingAPI;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId;
using StarCoPlay.V2.RefId.Inspection;
using StarCoPlay.V2.RefId.Resolution;

namespace StarCoPlay.V2.Query.Handlers;

public class QueryInspectRefsHandler
{
    private readonly RefIdInspector _inspector;
    private readonly IMonitor _monitor;

    public QueryInspectRefsHandler(IRefIdResolver resolver, IMonitor monitor)
    {
        _inspector = new RefIdInspector(resolver, new ResolvedRefFactProjector(monitor));
        _monitor = monitor;
    }

    internal QueryInspectRefsHandler(IRefIdResolver resolver, IMonitor monitor, IRefFactProjector projector)
    {
        _inspector = new RefIdInspector(resolver, projector);
        _monitor = monitor;
    }

    public QueryInspectRefsResult Handle(QueryInspectRefsParams request)
    {
        var result = new QueryInspectRefsResult();
        var includeDebug = request.IncludeDebug;

        foreach (var protoRef in request.Refs)
        {
            var rawRefId = protoRef.Value;

            try
            {
                var inspected = _inspector.Inspect(rawRefId, includeDebug);

                // Gate: resolved but oneof data is empty → downgrade to REF_UNSUPPORTED
                if (inspected.Resolution?.Status is RefResolveStatus.RefResolved or RefResolveStatus.RefResolvedDegraded &&
                    inspected.DataCase == InspectedRef.DataOneofCase.None)
                {
                    inspected.Resolution.Status = RefResolveStatus.RefUnsupported;
                    inspected.DebugReason = $"Resolved but no typed data for {inspected.RefType}/{inspected.Type}";
                }

                result.Inspected.Add(inspected);
            }
            catch (Exception ex)
            {
                _monitor.Log($"[Query] InspectRefs error for '{rawRefId}': {ex.Message}", LogLevel.Warn);
                result.Inspected.Add(BuildProjectionError(rawRefId, ex.Message));
                result.Warnings.Add(new QueryWarning { Code = "inspect_projection_exception", Message = ex.Message });
            }
        }

        _monitor.Log($"[Query] InspectRefs: {request.Refs.Count} refs -> {result.Inspected.Count} results", LogLevel.Trace);
        return result;
    }

    private static InspectedRef BuildProjectionError(string rawRefId, string reason)
    {
        return new InspectedRef
        {
            Resolution = new RefResolution
            {
                Ref = new Protocol.V245.RefId { Value = rawRefId },
                Status = RefResolveStatus.RefUnsupported,
                DebugReason = reason,
            },
            Ref = rawRefId,
            FactKind = "",
            Summary = "",
            DebugReason = reason,
        };
    }
}
