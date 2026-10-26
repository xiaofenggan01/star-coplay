using StardewModdingAPI;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.V2.Query.Handlers;
using StarCoPlay.V2.RefId.Resolution;

namespace StarCoPlay.ProtocolCore.Handlers;

// ─────────────────────────────────────────────
//  1. QueryWorldRegionProtoHandler
// ─────────────────────────────────────────────

public sealed class QueryWorldRegionProtoHandler : IProtoCommandHandler
{
    private readonly QueryWorldRegionHandler _inner;

    public string Capability => "query_world_region";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.QueryWorldRegion;
    public ProtoCommandKind Kind => ProtoCommandKind.PassiveRead;
    public bool IsActive => false;

    public QueryWorldRegionProtoHandler(IMonitor monitor)
        => _inner = new QueryWorldRegionHandler(monitor);

    public ProtoValidationResult Validate(ProtoCommandContext ctx) => ProtoValidationResult.Ok();

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        ctx.TypedResult = _inner.Handle(ctx.Request.QueryWorldRegion);
        return HandlerStartResult.Immediate();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx) => HandlerTickResult.Continue;
    public void Cancel(ProtoCommandContext ctx, string reason) { }
}

// ─────────────────────────────────────────────
//  2. QueryRuntimeSnapshotProtoHandler
// ─────────────────────────────────────────────

public sealed class QueryRuntimeSnapshotProtoHandler : IProtoCommandHandler
{
    private readonly QueryRuntimeSnapshotHandler _inner;

    public string Capability => "query_runtime_snapshot";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.QueryRuntimeSnapshot;
    public ProtoCommandKind Kind => ProtoCommandKind.PassiveRead;
    public bool IsActive => false;

    public QueryRuntimeSnapshotProtoHandler(IMonitor monitor)
        => _inner = new QueryRuntimeSnapshotHandler(monitor);

    public ProtoValidationResult Validate(ProtoCommandContext ctx) => ProtoValidationResult.Ok();

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        ctx.TypedResult = _inner.Handle(ctx.Request.QueryRuntimeSnapshot);
        return HandlerStartResult.Immediate();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx) => HandlerTickResult.Continue;
    public void Cancel(ProtoCommandContext ctx, string reason) { }
}

// ─────────────────────────────────────────────
//  3. QueryInventorySnapshotProtoHandler
// ─────────────────────────────────────────────

public sealed class QueryInventorySnapshotProtoHandler : IProtoCommandHandler
{
    private readonly QueryInventorySnapshotHandler _inner;

    public string Capability => "query_inventory_snapshot";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.QueryInventorySnapshot;
    public ProtoCommandKind Kind => ProtoCommandKind.PassiveRead;
    public bool IsActive => false;

    public QueryInventorySnapshotProtoHandler(IMonitor monitor)
        => _inner = new QueryInventorySnapshotHandler(monitor);

    public ProtoValidationResult Validate(ProtoCommandContext ctx) => ProtoValidationResult.Ok();

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        ctx.TypedResult = _inner.Handle(ctx.Request.QueryInventorySnapshot);
        return HandlerStartResult.Immediate();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx) => HandlerTickResult.Continue;
    public void Cancel(ProtoCommandContext ctx, string reason) { }
}

// ─────────────────────────────────────────────
//  4. QueryUiSnapshotProtoHandler
// ─────────────────────────────────────────────

public sealed class QueryUiSnapshotProtoHandler : IProtoCommandHandler
{
    private readonly QueryUiSnapshotHandler _inner;

    public string Capability => "query_ui_snapshot";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.QueryUiSnapshot;
    public ProtoCommandKind Kind => ProtoCommandKind.PassiveRead;
    public bool IsActive => false;

    public QueryUiSnapshotProtoHandler(IMonitor monitor)
        => _inner = new QueryUiSnapshotHandler(monitor);

    public ProtoValidationResult Validate(ProtoCommandContext ctx) => ProtoValidationResult.Ok();

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        ctx.TypedResult = _inner.Handle(ctx.Request.QueryUiSnapshot);
        return HandlerStartResult.Immediate();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx) => HandlerTickResult.Continue;
    public void Cancel(ProtoCommandContext ctx, string reason) { }
}

// --------------------------------------------
//  5. QueryInspectRefsProtoHandler
// --------------------------------------------

public sealed class QueryInspectRefsProtoHandler : IProtoCommandHandler
{
    private readonly QueryInspectRefsHandler _inner;

    public string Capability => "query_inspect_refs";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.QueryInspectRefs;
    public ProtoCommandKind Kind => ProtoCommandKind.PassiveRead;
    public bool IsActive => false;

    public QueryInspectRefsProtoHandler(IRefIdResolver resolver, IMonitor monitor)
        => _inner = new QueryInspectRefsHandler(resolver, monitor);

    public ProtoValidationResult Validate(ProtoCommandContext ctx) => ProtoValidationResult.Ok();

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        ctx.TypedResult = _inner.Handle(ctx.Request.QueryInspectRefs);
        return HandlerStartResult.Immediate();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx) => HandlerTickResult.Continue;
    public void Cancel(ProtoCommandContext ctx, string reason) { }
}
