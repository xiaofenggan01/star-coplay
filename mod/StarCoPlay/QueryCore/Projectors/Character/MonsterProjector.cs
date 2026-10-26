using System;
using System.Text;
using StardewValley.Monsters;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Building;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors.Character;

public static class MonsterProjector
{
    public static CharacterFact Project(Monster monster)
    {
        var name = monster.Name;
        var mapName = monster.currentLocation?.NameOrUniqueName ?? "unknown";
        var tileX = (int)monster.Tile.X;
        var tileY = (int)monster.Tile.Y;

        // Monsters have no persistent guid; derive one from identity for RefId
        var syntheticGuid = DeriveGuid(mapName, name, tileX, tileY);

        var monsterFact = new MonsterFact
        {
            MonsterKey      = RefIdTokenEncoder.EncodeProtocolKey(name),
            CharacterGuid   = syntheticGuid.ToString("N"),
            CurrentMapToken = RefIdTokenEncoder.EncodeMapToken(mapName),
            Health          = monster.Health,
            MaxHealth       = monster.MaxHealth,
            DamageToFarmer  = monster.DamageToFarmer,
            IsMoving        = monster.isMoving(),
            Facing          = MapFacing(monster.FacingDirection),
        };

        return new CharacterFact
        {
            Ref = RefIdBuilder.BuildMonster(mapName, syntheticGuid),
            Type = "monster",
            FactKind = "monster",
            Name = name,
            DisplayName = monster.displayName ?? name,
            Position = new TilePosition
            {
                MapId = RefIdTokenEncoder.EncodeMapToken(mapName),
                X = tileX,
                Y = tileY,
            },
            Summary = $"{name} HP:{monster.Health} at {mapName} ({tileX},{tileY})",
            Monster = monsterFact,
        };
    }

    internal static Guid DeriveGuid(string map, string name, int x, int y)
    {
        var bytes = Encoding.UTF8.GetBytes($"{map}:{name}:{x}:{y}");
        using var md5 = System.Security.Cryptography.MD5.Create();
        return new Guid(md5.ComputeHash(bytes));
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
