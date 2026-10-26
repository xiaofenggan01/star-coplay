using System;
using System.Collections.Generic;
using StardewModdingAPI;
using StardewValley;
using StarCoPlay.Protocol.V245;
using StarCoPlay.V2.Query.Scanning;
using StarCoPlay.V2.RefId.Encoding;

namespace StarCoPlay.V2.Query.Handlers;

public class QueryWorldRegionHandler
{
    private readonly IMonitor _monitor;

    public QueryWorldRegionHandler(IMonitor monitor)
    {
        _monitor = monitor;
    }

    public QueryWorldRegionResult Handle(QueryWorldRegionParams request)
    {
        var result = new QueryWorldRegionResult();

        try
        {
            GameLocation? location = LocationScanner.ResolveMapToken(request.MapToken);

            if (location is null)
            {
                result.Warnings.Add(new QueryWarning
                {
                    Code = "MAP_NOT_FOUND",
                    Message = $"Location not found: '{request.MapToken}'"
                });
                return result;
            }

            var entities = LocationScanner.ScanEntities(location, request);
            var characters = request.IncludeCharacters
                ? LocationScanner.ScanCharacters(location, request)
                : new List<CharacterFact>();
            var refsByTile = LocationScanner.BuildRefsByTile(entities);
            var tiles = request.IncludeTiles
                ? LocationScanner.ScanTiles(location, request, refsByTile, _monitor)
                : new List<TileFact>();

            var fact = new WorldRegionFact
            {
                MapToken   = RefIdTokenEncoder.EncodeMapToken(location.NameOrUniqueName),
                MapId      = location.NameOrUniqueName,
                Region     = request.Region,
                IsOutdoors = location.IsOutdoors,
            };
            fact.Entities.AddRange(entities);
            fact.Characters.AddRange(characters);
            fact.Tiles.AddRange(tiles);

            result.Fact = fact;

            _monitor.Log(
                $"[Query] WorldRegion: map={fact.MapToken} entities={entities.Count} characters={characters.Count} tiles={tiles.Count}",
                LogLevel.Trace);
        }
        catch (Exception ex)
        {
            _monitor.Log($"[Query] WorldRegion error: {ex.Message}", LogLevel.Warn);
            result.Warnings.Add(new QueryWarning
            {
                Code    = "INTERNAL_ERROR",
                Message = ex.Message
            });
        }

        return result;
    }
}
