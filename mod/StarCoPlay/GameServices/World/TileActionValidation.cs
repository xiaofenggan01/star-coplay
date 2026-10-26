namespace StarCoPlay.GameServices.World;

public readonly record struct TileActionValidation(bool IsValid, string? ErrorMessage)
{
    public static TileActionValidation Ok() => new(true, null);
    public static TileActionValidation Invalid(string message) => new(false, message);
}
