using System.Text.Json;
using System.Text.Json.Serialization;

namespace StarCoPlay.Models
{
    /// <summary>commands.jsonl 的单行命令模型。</summary>
    public class AgentCommand
    {
        [JsonPropertyName("cmd_id")]
        public string CmdId { get; set; } = "";

        [JsonPropertyName("action")]
        public string Action { get; set; } = "";

        [JsonPropertyName("params")]
        public JsonElement Params { get; set; }

        /// <summary>V2 协议版本标识。V2 命令走 AdapterV2 分发，V1/null 走原有逻辑。</summary>
        [JsonPropertyName("protocol_version")]
        public string? ProtocolVersion { get; set; }

        // ── V2 专用字段 ──────────────────────────────────────────
        // Python Agent 写入 V2 格式时使用 capability/action_id/seq，
        // 而非 V1 的 action/cmd_id。以下三个属性承接 V2 反序列化。

        /// <summary>V2 能力标识，对应 V1 的 action。</summary>
        [JsonPropertyName("capability")]
        public string? Capability { get; set; }

        /// <summary>V2 动作唯一 ID，对应 V1 的 cmd_id。</summary>
        [JsonPropertyName("action_id")]
        public string? ActionId { get; set; }

        /// <summary>V2 序列号，由 Python Agent 维护单调递增。</summary>
        [JsonPropertyName("seq")]
        public int? Seq { get; set; }

        /// <summary>V2 目标玩家 ID（多人游戏）。null/0 表示当前玩家。</summary>
        [JsonPropertyName("target_player_id")]
        public ulong? TargetPlayerId { get; set; }
    }
}
