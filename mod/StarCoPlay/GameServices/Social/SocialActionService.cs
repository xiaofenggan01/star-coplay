using StardewModdingAPI;
using StardewValley;
using StarCoPlay.Protocol.V245;

namespace StarCoPlay.GameServices.Social;

public sealed class SocialActionService
{
    private readonly IMonitor _monitor;

    public SocialActionService(IMonitor monitor)
    {
        _monitor = monitor;
    }

    public bool SendChat(string content)
    {
        if (Game1.chatBox == null)
            return false;

        Game1.chatBox.textBoxEnter(content);
        _monitor.Log($"[Social] SendChat: \"{content}\" (len={content.Length})", LogLevel.Debug);
        return true;
    }

    public bool DoEmote(EmoteType emoteType)
    {
        string? emoteString = ToSdvEmoteString(emoteType);
        if (emoteString == null) return false;
        if (Game1.player == null) return false;

        Game1.player.netDoEmote(emoteString);
        _monitor.Log($"[Social] DoEmote: {emoteType} -> \"{emoteString}\"", LogLevel.Debug);
        return true;
    }

    public string? ToSdvEmoteString(EmoteType emoteType)
    {
        return emoteType switch
        {
            EmoteType.EmoteHappy => "happy",
            EmoteType.EmoteSad => "sad",
            EmoteType.EmoteAngry => "angry",
            EmoteType.EmoteHeart => "heart",
            EmoteType.EmoteExclamation => "exclamation",
            EmoteType.EmoteQuestion => "question",
            EmoteType.EmoteLaugh => "laugh",
            EmoteType.EmoteNote => "note",
            EmoteType.EmoteSleep => "sleep",
            EmoteType.EmoteGame => "game",
            EmoteType.EmoteX => "x",
            EmoteType.EmotePause => "pause",
            EmoteType.EmoteBlush => "blush",
            EmoteType.EmoteYes => "yes",
            EmoteType.EmoteNo => "no",
            EmoteType.EmoteSick => "sick",
            EmoteType.EmoteSurprised => "surprised",
            EmoteType.EmoteHi => "hi",
            EmoteType.EmoteTaunt => "taunt",
            EmoteType.EmoteUh => "uh",
            EmoteType.EmoteMusic => "music",
            EmoteType.EmoteJar => "jar",
            _ => null
        };
    }
}
