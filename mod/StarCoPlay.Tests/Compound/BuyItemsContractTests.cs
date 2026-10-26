using System;
using System.Collections.Generic;
using System.Text.Json;
using NUnit.Framework;
using StardewModdingAPI;
using StardewModdingAPI.Framework.Logging;
using StarCoPlay.V2.Compound;
using StarCoPlay.V2.Models;

namespace StarCoPlay.Tests.Compound;

[TestFixture]
public sealed class BuyItemsContractTests
{
    [TestCaseSource(nameof(ValidItemShapes))]
    public void Start_AcceptsSupportedItemShapes(object items)
    {
        var handler = new BuyItemsHandler(null!, new StubMonitor());
        var command = CreateCommand(items);

        var result = handler.Start(command);

        Assert.Multiple(() =>
        {
            Assert.That(result, Is.Null);
            Assert.That(handler.IsActive, Is.True);
        });
    }

    [Test]
    public void Start_RejectsInvalidStructuredItemList()
    {
        var handler = new BuyItemsHandler(null!, new StubMonitor());
        var command = CreateCommand(new List<object>
        {
            new Dictionary<string, object>
            {
                ["name"] = "Parsnip Seeds",
                ["quantity"] = 0L,
            },
        });

        var result = handler.Start(command);

        Assert.Multiple(() =>
        {
            Assert.That(result, Is.Not.Null);
            Assert.That(result!.Error?.Code, Is.EqualTo("INVALID_PARAMS"));
            Assert.That(handler.IsActive, Is.False);
        });
    }

    private static IEnumerable<TestCaseData> ValidItemShapes()
    {
        yield return new TestCaseData("[{\"name\":\"Parsnip Seeds\",\"quantity\":10}]")
            .SetName("Start_AcceptsJsonStringItems");

        using var document = JsonDocument.Parse("[{\"name\":\"Parsnip Seeds\",\"quantity\":10}]");
        yield return new TestCaseData(document.RootElement.Clone())
            .SetName("Start_AcceptsJsonElementItems");

        yield return new TestCaseData(new List<object>
        {
            new Dictionary<string, object>
            {
                ["name"] = "Parsnip Seeds",
                ["quantity"] = 10L,
            },
        }).SetName("Start_AcceptsCommandProcessorListItems");
    }

    private static V2Command CreateCommand(object items) => new()
    {
        Seq = 1,
        ActionId = "hosted-buy-items-contract",
        Capability = "buy_items",
        Params = new Dictionary<string, object>
        {
            ["shop"] = "SeedShop",
            ["items"] = items,
        },
    };

    private sealed class StubMonitor : IMonitor
    {
        public bool IsVerbose => false;
        public string? LogPrefix { get => null; set { } }
        public void Log(string message, LogLevel level) { }
        public void LogOnce(string message, LogLevel level) { }
        public void VerboseLog(string message) { }
        public void VerboseLog(ref VerboseLogStringHandler message) { }
        public void VerboseLogOnce(string message) { }
    }
}
