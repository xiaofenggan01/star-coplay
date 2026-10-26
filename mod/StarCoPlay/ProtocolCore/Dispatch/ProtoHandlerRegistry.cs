using System;
using System.Collections.Generic;
using System.Diagnostics.CodeAnalysis;
using StarCoPlay.Protocol.V245;

namespace StarCoPlay.ProtocolCore.Dispatch;

public sealed class ProtoHandlerRegistry
{
    private readonly Dictionary<CommandRequest.ParamsOneofCase, IProtoCommandHandler> _byParamsCase = new();
    private readonly Dictionary<string, CommandRequest.ParamsOneofCase> _paramsCaseByCapability = new();

    public void Register(IProtoCommandHandler handler)
    {
        if (string.IsNullOrEmpty(handler.Capability))
            throw new ArgumentException($"Handler capability cannot be empty");

        if (handler.ParamsCase == CommandRequest.ParamsOneofCase.None)
            throw new ArgumentException($"Handler {handler.Capability} ParamsCase cannot be None");

        if (_byParamsCase.ContainsKey(handler.ParamsCase))
            throw new InvalidOperationException($"ParamsCase {handler.ParamsCase} already registered");

        if (_paramsCaseByCapability.ContainsKey(handler.Capability))
            throw new InvalidOperationException($"Capability '{handler.Capability}' already registered");

        _byParamsCase[handler.ParamsCase] = handler;
        _paramsCaseByCapability[handler.Capability] = handler.ParamsCase;
    }

    public bool TryGet(CommandRequest.ParamsOneofCase paramsCase, [NotNullWhen(true)] out IProtoCommandHandler? handler)
    {
        return _byParamsCase.TryGetValue(paramsCase, out handler);
    }

    public bool IsRegistered(CommandRequest.ParamsOneofCase paramsCase)
    {
        return _byParamsCase.ContainsKey(paramsCase);
    }

    public bool IsCapabilityConsistent(string capability, CommandRequest.ParamsOneofCase paramsCase)
    {
        if (!_paramsCaseByCapability.TryGetValue(capability, out var expectedCase))
            return false;
        return expectedCase == paramsCase;
    }

    public IEnumerable<string> RegisteredCapabilities => _paramsCaseByCapability.Keys;
}
