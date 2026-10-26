using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Projectors;

public static class RuntimeSnapshotProjector
{
    public static RuntimeSnapshotFact Project()
    {
        var fact = new RuntimeSnapshotFact
        {
            GameDate    = $"{Game1.currentSeason} {Game1.dayOfMonth}, Year {Game1.year}",
            TimeOfDay   = Game1.timeOfDay,
            Season      = Game1.currentSeason,
            DayOfMonth  = Game1.dayOfMonth,
            Year        = Game1.year,
            IsFestivalDay = Utility.isFestivalDay(Game1.dayOfMonth, Game1.season),
            IsMenuOpen  = Game1.activeClickableMenu != null,
        };

        // Per-location weather (preferred); fall back to global flag
        var player = Game1.player;
        if (player != null)
        {
            fact.Money      = player.Money;
            fact.Stamina    = (int)player.Stamina;
            fact.MaxStamina = player.MaxStamina;

            fact.Health    = player.health;
            fact.MaxHealth = player.maxHealth;

            var loc = player.currentLocation;
            if (loc != null)
            {
                var weather = loc.GetWeather();
                fact.IsCurrentlyRaining = weather?.IsRaining    ?? Game1.isRaining;
                fact.IsLightning        = weather?.IsLightning  ?? false;
                fact.IsSnowing          = weather?.IsSnowing    ?? false;
                fact.IsGreenRain        = weather?.IsGreenRain  ?? false;
                fact.IsOutdoors         = loc.IsOutdoors;

                fact.PlayerLocation = new LocationSnapshot
                {
                    MapToken = RefIdTokenEncoder.EncodeMapToken(loc.NameOrUniqueName),
                    X      = (int)player.Tile.X,
                    Y      = (int)player.Tile.Y,
                    Facing = MapFacing(player.FacingDirection),
                };
            }
            else
            {
                fact.IsCurrentlyRaining = Game1.isRaining;
                fact.IsLightning        = false;
                fact.IsSnowing          = false;
                fact.IsGreenRain        = false;
                fact.IsOutdoors         = false;
            }
        }
        else
        {
            fact.IsCurrentlyRaining = Game1.isRaining;
        }

        fact.Debug.Add(new StringPair { Key = "tick", Value = Game1.ticks.ToString() });
        return fact;
    }

    // Stardew: 0=up, 1=right, 2=down, 3=left
    // Proto Direction: Unspecified=0, Up=1, Down=2, Left=3, Right=4
    private static Direction MapFacing(int sdvFacing) => sdvFacing switch
    {
        0 => Direction.Up,
        1 => Direction.Right,
        2 => Direction.Down,
        3 => Direction.Left,
        _ => Direction.Unspecified,
    };
}
