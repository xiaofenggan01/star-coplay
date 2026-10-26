using System;
using System.Text.Json;
using StarCoPlay.Protocol.V245;

namespace StarCoPlay.Bridge
{
    public enum ControlFrameKind
    {
        NotControl,
        ProtoStatus,
        ProtoCancel,
        V2Cancel,
    }

    /// <summary>Pure Protocol control framing shared by production and contract harness.</summary>
    public static class ControlFramingCodec
    {
        public static ControlFrameKind Classify(Envelope envelope) => envelope.BodyCase switch
        {
            Envelope.BodyOneofCase.CommandStatusRequest => ControlFrameKind.ProtoStatus,
            Envelope.BodyOneofCase.CommandCancel => ControlFrameKind.ProtoCancel,
            _ => ControlFrameKind.NotControl,
        };

        public static ControlFrameKind ClassifyV2(JsonElement frame, out string commandId)
        {
            commandId = "";
            if (!frame.TryGetProperty("type", out var type) || type.GetString() != "command"
                || !frame.TryGetProperty("cancel", out var cancel) || cancel.ValueKind != JsonValueKind.True
                || !frame.TryGetProperty("action_id", out var actionId)
                || string.IsNullOrWhiteSpace(actionId.GetString()))
                return ControlFrameKind.NotControl;
            commandId = actionId.GetString()!;
            return ControlFrameKind.V2Cancel;
        }

        public static Envelope DecodeRequestBase64(string payload)
        {
            var envelope = Envelope.Parser.ParseFrom(Convert.FromBase64String(payload));
            if (envelope.ProtocolVersion != "2.4.5" || string.IsNullOrWhiteSpace(envelope.MessageId)
                || string.IsNullOrWhiteSpace(envelope.RequestId) || string.IsNullOrWhiteSpace(envelope.SessionId)
                || (envelope.BodyCase != Envelope.BodyOneofCase.CommandCancel
                    && envelope.BodyCase != Envelope.BodyOneofCase.CommandStatusRequest))
                throw new InvalidOperationException("invalid Protocol control request");
            return envelope;
        }

        public static Envelope BuildStatusResponse(Envelope request, CommandStatusResponse response)
        {
            if (request.BodyCase != Envelope.BodyOneofCase.CommandStatusRequest
                || request.CommandStatusRequest.CommandId != response.CommandId)
                throw new InvalidOperationException("status response correlation mismatch");
            return new Envelope
            {
                ProtocolVersion = "2.4.5", MessageId = $"msg_{Guid.NewGuid():N}",
                RequestId = request.RequestId, SessionId = request.SessionId,
                TimestampMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                CommandStatusResponse = response,
            };
        }

        public static Envelope BuildCancelResult(Envelope request, CommandResult result)
        {
            if (request.BodyCase != Envelope.BodyOneofCase.CommandCancel
                || request.CommandCancel.CommandId != result.CommandId)
                throw new InvalidOperationException("cancel result correlation mismatch");
            return NewResponse(request.RequestId, request.SessionId, result);
        }

        public static Envelope BuildResultResponse(string requestId, string sessionId, CommandResult result)
        {
            if (string.IsNullOrWhiteSpace(requestId) || string.IsNullOrWhiteSpace(sessionId)
                || string.IsNullOrWhiteSpace(result.CommandId))
                throw new InvalidOperationException("result response correlation missing");
            return NewResponse(requestId, sessionId, result);
        }

        private static Envelope NewResponse(string requestId, string sessionId, CommandResult result) => new()
        {
            ProtocolVersion = "2.4.5", MessageId = $"msg_{Guid.NewGuid():N}",
            RequestId = requestId, SessionId = sessionId,
            TimestampMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            CommandResult = result,
        };
    }
}
