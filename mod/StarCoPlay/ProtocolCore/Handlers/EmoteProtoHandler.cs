using StardewModdingAPI;
using StarCoPlay.Protocol.V245;
using StarCoPlay.ProtocolCore.Context;
using StarCoPlay.ProtocolCore.Dispatch;
using StarCoPlay.GameServices.Social;

namespace StarCoPlay.ProtocolCore.Handlers;

public sealed class EmoteProtoHandler : IProtoCommandHandler
{
    private readonly SocialActionService _social;
    private readonly IMonitor _monitor;

    public string Capability => "emote";
    public CommandRequest.ParamsOneofCase ParamsCase => CommandRequest.ParamsOneofCase.Emote;
    public ProtoCommandKind Kind => ProtoCommandKind.ActiveShort;
    public bool IsActive => false;

    public EmoteProtoHandler(SocialActionService social, IMonitor monitor)
    {
        _social = social;
        _monitor = monitor;
    }

    public ProtoValidationResult Validate(ProtoCommandContext ctx)
    {
        var p = ctx.Request.Emote;
        if (p.EmoteType == EmoteType.Unspecified)
            return ProtoValidationResult.Invalid("emote_type must not be UNSPECIFIED");
        if (_social.ToSdvEmoteString(p.EmoteType) == null)
            return ProtoValidationResult.Invalid($"emote_type {p.EmoteType} is not supported");
        return ProtoValidationResult.Ok();
    }

    public HandlerStartResult Start(ProtoCommandContext ctx)
    {
        var p = ctx.Request.Emote;
        if (!_social.DoEmote(p.EmoteType))
            return HandlerStartResult.Rejected("emote failed");

        ctx.TypedResult = new EmoteResult { EmoteType = p.EmoteType };
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
