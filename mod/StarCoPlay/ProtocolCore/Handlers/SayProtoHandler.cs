using StardewModdingAPI;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.GameServices.Social;

namespace StarCoPlay.ProtocolCore.Handlers;

public sealed class SayProtoHandler : IProtoCommandHandler
{
    private readonly SocialActionService _social;
    private readonly IMonitor _monitor;

    public string Capability => "say";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.Say;
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveShort;
    public bool IsActive => false;

    public SayProtoHandler(SocialActionService social, IMonitor monitor)
    {
        _social = social;
        _monitor = monitor;
    }

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        var p = ctx.Request.Say;
        if (string.IsNullOrEmpty(p.Content))
            return ProtoValidationResult.Invalid("content is required");
        if (p.Content.Length > 200)
            return ProtoValidationResult.Invalid("content must be <= 200 characters");
        if (p.Content.StartsWith('/'))
            return ProtoValidationResult.Invalid("content must not start with '/' (reserved for game commands)");
        if (StardewValley.Game1.chatBox == null)
            return ProtoValidationResult.Invalid("chat box unavailable");
        return ProtoValidationResult.Ok();
    }

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        var p = ctx.Request.Say;
        if (!_social.SendChat(p.Content))
            return HandlerStartResult.Rejected("chat box unavailable");

        ctx.TypedResult = new SayResult { ContentLength = p.Content.Length };
        return HandlerStartResult.Immediate();
    }

    public HandlerTickResult Tick(ProtoCommandContext ctx)
    {
        return HandlerTickResult.Continue;
    }

    public void Cancel(ProtoCommandContext ctx, string reason)
    {
    }
}
