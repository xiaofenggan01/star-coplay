using System.IO;
using Google.Protobuf;
using StarCoPlay.Protocol.V245;

namespace StarCoPlay.Protocol;

/// <summary>
/// Loads protocol-equivalent JSON fixtures into generated CommandRequest messages.
/// </summary>
public static class ProtocolFixtureLoader
{
    private static readonly JsonParser Parser = new(JsonParser.Settings.Default.WithIgnoreUnknownFields(false));

    public static CommandRequest LoadCommandRequest(string path)
    {
        if (string.IsNullOrWhiteSpace(path))
            throw ProtocolMappingException.InvalidParams("fixture path is required");
        if (!File.Exists(path))
            throw ProtocolMappingException.InvalidParams($"fixture file does not exist: {path}");

        var json = File.ReadAllText(path);
        return Parser.Parse<CommandRequest>(json);
    }

    public static CommandRequest LoadCommandRequestFromJson(string json)
    {
        if (string.IsNullOrWhiteSpace(json))
            throw ProtocolMappingException.InvalidParams("fixture json is required");

        return Parser.Parse<CommandRequest>(json);
    }
}
