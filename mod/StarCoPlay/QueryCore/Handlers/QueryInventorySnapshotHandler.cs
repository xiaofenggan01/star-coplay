using System;
using StardewModdingAPI;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.Query.Projectors;
using StarCoPlay.V2.Query.Scanning;

namespace StarCoPlay.V2.Query.Handlers
{
    public class QueryInventorySnapshotHandler
    {
        private readonly IMonitor _monitor;

        public QueryInventorySnapshotHandler(IMonitor monitor)
        {
            _monitor = monitor;
        }

        public QueryInventorySnapshotResult Handle(QueryInventorySnapshotParams request)
        {
            var result = new QueryInventorySnapshotResult();

            try
            {
                var containerKind = string.IsNullOrWhiteSpace(request.ContainerKind)
                    ? "player"
                    : request.ContainerKind.Trim().ToLowerInvariant();
                var containerLocator = request.ContainerRef?.Value ?? "";
                _monitor.Log(
                    $"[Query] InventorySnapshot: containerKind={containerKind} containerLocator={containerLocator} includeEmptySlots={request.IncludeEmptySlots}",
                    LogLevel.Trace);

                switch (containerKind)
                {
                    case "player":
                        result.Fact = InventoryProjector.ProjectPlayerInventory(request.IncludeEmptySlots);
                        break;

                    case "chest":
                    case "fridge":
                        var chest = ContainerLocator.Resolve(containerKind, containerLocator);
                        if (chest == null)
                        {
                            result.Warnings.Add(new QueryWarning
                            {
                                Message = "CONTAINER_NOT_FOUND"
                            });
                            return result;
                        }
                        result.Fact = InventoryProjector.ProjectContainer(chest, containerKind, containerLocator, request.IncludeEmptySlots);
                        break;

                    default:
                        result.Warnings.Add(new QueryWarning
                        {
                            Message = $"unsupported container kind: '{request.ContainerKind}'"
                        });
                        return result;
                }
            }
            catch (Exception ex)
            {
                _monitor.Log($"[Query] InventorySnapshot error: {ex.Message}", LogLevel.Error);
                result.Warnings.Add(new QueryWarning
                {
                    Message = $"internal error: {ex.Message}"
                });
            }

            return result;
        }
    }
}
