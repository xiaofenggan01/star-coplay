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
    }
}
