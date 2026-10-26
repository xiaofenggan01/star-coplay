namespace StarCoPlay.Bridge
{
    public static class WsProtocol
    {
        public const int ProtocolVersion = 1;

        public const string MsgHello = "hello";
        public const string MsgHelloOk = "hello_ok";
        public const string MsgAuthError = "auth_error";
        public const string MsgCommand = "command";
        public const string MsgCommandResult = "command_result";
        public const string MsgProtocolEnvelope = "protocol_envelope";
        public const string MsgStateSnapshot = "state_snapshot";
        public const string MsgGameEvent = "game_event";
        public const string MsgAgentStatus = "agent_status";
        public const string MsgHostedStatus = "hosted_status";
        public const string MsgTelemetry = "telemetry";
        public const string MsgPing = "ping";
        public const string MsgPong = "pong";
        public const string MsgSessionEnd = "session.end";
        public const string MsgAppearanceSync = "appearance_sync";
    }
}
