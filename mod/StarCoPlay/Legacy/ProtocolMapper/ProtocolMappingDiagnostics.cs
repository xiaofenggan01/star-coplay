using System.Collections.Generic;
using StarCoPlay.V2;

namespace StarCoPlay.Protocol;

/// <summary>
/// Carries mapper warnings and phase-one gaps without affecting runtime handlers.
/// </summary>
public class ProtocolMappingDiagnostics
{
    public List<string> Warnings { get; } = new();
    public List<string> Phase1Gaps { get; } = new();

    public bool HasNotes => Warnings.Count > 0 || Phase1Gaps.Count > 0;

    public void Warn(string message)
    {
        if (!string.IsNullOrWhiteSpace(message))
            Warnings.Add(message);
    }

    public void Gap(string message)
    {
        if (!string.IsNullOrWhiteSpace(message))
            Phase1Gaps.Add(message);
    }

    public Dictionary<string, object> ToDictionary() => new()
    {
        ["warnings"] = Warnings,
        ["phase1_gaps"] = Phase1Gaps,
    };
}

public class ProtocolMappingException : System.Exception
{
    public string ErrorCode { get; }

    public ProtocolMappingException(string errorCode, string message) : base(message)
    {
        ErrorCode = errorCode;
    }

    public static ProtocolMappingException InvalidParams(string message) =>
        new(ResultBuilder.Errors.InvalidParams, message);

    public static ProtocolMappingException CapabilityMissing(string message) =>
        new(ResultBuilder.Errors.UnknownCapability, message);

    public static ProtocolMappingException NotSupported(string message) =>
        new(ResultBuilder.Errors.NotSupported, message);
}
