using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.Character;

public static class NpcProjector
{
    public static CharacterFact Project(NPC npc)
    {
        var name = npc.Name;
        var mapName = npc.currentLocation?.NameOrUniqueName ?? "unknown";
        var tileX = (int)npc.Tile.X;
        var tileY = (int)npc.Tile.Y;

        var friendshipPoints = 0;
        if (Game1.player.friendshipData.TryGetValue(name, out var friendship))
            friendshipPoints = friendship.Points;

        // has_dialogue: safely check TemporaryDialogue (avoid calling CurrentDialogue getter which may trigger lazy load)
        var hasDialogue = npc.TemporaryDialogue != null && npc.TemporaryDialogue.Count > 0;

        var npcFact = new NpcFact
        {
            NpcKey           = RefIdTokenEncoder.EncodeProtocolKey(name),
            CurrentMapToken  = RefIdTokenEncoder.EncodeMapToken(mapName),
            CanSocialize     = npc.CanSocialize,
            ScheduleKey      = npc.dayScheduleName?.Value ?? "",
            FriendshipPoints = friendshipPoints,
            Facing           = MapFacing(npc.FacingDirection),
            BirthdaySeason   = npc.birthday_Season.Value ?? "",
            BirthdayDay      = npc.birthday_Day.Value,
            HasDialogue      = hasDialogue,
        };

        return new CharacterFact
        {
            Ref = RefIdBuilder.BuildNpc(name),
            Type = "npc",
            FactKind = "npc",
            Name = name,
            DisplayName = npc.displayName ?? name,
            Position = new TilePosition
            {
                MapId = RefIdTokenEncoder.EncodeMapToken(mapName),
                X = tileX,
                Y = tileY,
            },
            Summary = $"{npc.displayName ?? name} at {mapName} ({tileX},{tileY})",
            Npc = npcFact,
        };
    }

    private static Direction MapFacing(int sdvFacing) => sdvFacing switch
    {
        0 => Direction.Up,
        1 => Direction.Right,
        2 => Direction.Down,
        3 => Direction.Left,
        _ => Direction.Unspecified,
    };
}
