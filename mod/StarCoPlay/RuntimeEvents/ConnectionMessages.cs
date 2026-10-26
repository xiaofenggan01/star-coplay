namespace StarCoPlay
{
    /// <summary>连接异常的用户友好文案。</summary>
    public static class ConnectionMessages
    {
        public static string ForAuthError(string? code) => code switch
        {
            "invalid_token" => "Token 无效，请打开 Mod 配置菜单检查",
            "missing_token" => "未填写 Token，请在配置菜单中输入",
            "no_auth_backend" => "服务端认证服务不可用，请稍后重试",
            "auth_backend_error" => "认证服务异常，请稍后重试",
            _ => $"认证失败（{code ?? "未知"}），请检查 Token 或联系 QQ 群 564214272"
        };

        public static string ForSessionEnd(string? reason) => reason switch
        {
            "agent_crash" => "AI 搭子遇到问题，正在自动恢复...",
            "shutdown" => "服务端维护中，请稍后重连",
            "kicked" => "会话被管理员关闭",
            "server_ended" => "服务端主动结束了会话",
            _ => $"会话结束（{reason ?? "未知原因"}）"
        };

        public const string Disconnected = "与服务器断开连接，正在重连...";
        public const string ReconnectSuccess = "已重新连接！AI 搭子恢复在线";
        public const string Connecting = "正在连接云端服务...";
    }
}
