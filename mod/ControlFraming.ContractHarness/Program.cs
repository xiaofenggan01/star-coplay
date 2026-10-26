using System;
using System.IO;
using System.Text.Json;
using StarCoPlay.Bridge;
using StarCoPlay.Protocol.V245;

if (args.Length != 1)
    throw new InvalidOperationException("usage: ControlFraming.ContractHarness <hosted-route-contract.json>");

using var fixtureDocument = JsonDocument.Parse(File.ReadAllText(args[0]));
var framing = fixtureDocument.RootElement.GetProperty("control_framing");
var checks = 0;
if (HostedRouteHello.ContractVersion != fixtureDocument.RootElement.GetProperty("route_contract_version").GetString()
    || HostedRouteHello.SnapshotVersion != fixtureDocument.RootElement.GetProperty("capability_snapshot_version").GetString())
    throw new InvalidOperationException("hosted hello contract version mismatch");
checks += 2;

var digestVector = fixtureDocument.RootElement.GetProperty("canonical_digest_vector");
var digestEntries = new System.Collections.Generic.List<CapabilitySnapshotEntry>();
foreach (var entry in digestVector.GetProperty("entries").EnumerateArray())
    digestEntries.Add(new CapabilitySnapshotEntry(
        entry[0].GetString()!, entry[1].GetString()!, entry[2].GetString()!));
if (HostedRouteHello.ComputeCapabilityDigest(digestEntries)
    != digestVector.GetProperty("expected").GetString())
    throw new InvalidOperationException("capability digest fixture mismatch");
checks += 1;

Envelope DecodeAndCheck(string key, ControlFrameKind expected, string expectedRequestId)
{
    var request = ControlFramingCodec.DecodeRequestBase64(framing.GetProperty(key).GetString()!);
    if (ControlFramingCodec.Classify(request) != expected
        || request.RequestId != expectedRequestId
        || request.SessionId != "gs-vector")
        throw new InvalidOperationException($"{key}: route/correlation mismatch");
    var commandId = expected == ControlFrameKind.ProtoStatus
        ? request.CommandStatusRequest.CommandId : request.CommandCancel.CommandId;
    if (commandId != "cmd-vector")
        throw new InvalidOperationException($"{key}: command_id mismatch");
    checks += 4;
    return request;
}

var status = DecodeAndCheck("proto_status_base64", ControlFrameKind.ProtoStatus,
    framing.GetProperty("proto_status_request_id").GetString()!);
var cancel = DecodeAndCheck("proto_cancel_base64", ControlFrameKind.ProtoCancel,
    framing.GetProperty("proto_cancel_request_id").GetString()!);

var statusResponse = ControlFramingCodec.BuildStatusResponse(status, new CommandStatusResponse
{
    CommandId = "cmd-vector", Status = CommandStatus.Running, Terminal = false,
});
var cancelResponse = ControlFramingCodec.BuildCancelResult(cancel, new CommandResult
{
    CommandId = "cmd-vector", Status = CommandStatus.Cancelled, Success = false,
});
foreach (var pair in new[] { (Request: status, Response: statusResponse), (Request: cancel, Response: cancelResponse) })
{
    var response = pair.Response;
    var commandId = response.BodyCase == Envelope.BodyOneofCase.CommandStatusResponse
        ? response.CommandStatusResponse.CommandId
        : response.BodyCase == Envelope.BodyOneofCase.CommandResult
            ? response.CommandResult.CommandId : "";
    if (response.RequestId != pair.Request.RequestId
        || response.SessionId != pair.Request.SessionId
        || commandId != "cmd-vector"
        || response.MessageId == pair.Request.MessageId
        || string.IsNullOrWhiteSpace(response.MessageId))
        throw new InvalidOperationException("encoded response correlation/new message_id mismatch");
    checks += 5;
}

var v2 = framing.GetProperty("v2_cancel");
if (ControlFramingCodec.ClassifyV2(v2, out var v2CommandId) != ControlFrameKind.V2Cancel
    || v2CommandId != "cmd-vector")
    throw new InvalidOperationException("v2 cancel route mismatch");
checks += 2;

Console.WriteLine($"[PASS] Hosted route contract harness: {checks} assertions; capability digest + Proto status/cancel + V2 cancel");
