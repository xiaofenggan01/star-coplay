# Star-Coplay Protocol 2.4.5

This directory contains the Agent-Mod protocol work for the `after_third_beta`
iteration.

## Scope

Protocol 2.4.5 defines the minimum executable contract between Agent and Mod:

- WebSocket transport envelope.
- Command lifecycle.
- Fixed, compiled capability params/results.
- Unified command result and error structure.
- LLM FunctionSchema generation from protobuf.

It does not define gRPC services and does not use JSON or YAML as the protocol
source of truth.

## Source Of Truth

The hand-written protocol source is protobuf under `protocol/proto/`.

Generated artifacts must be derived from protobuf:

- Python classes: `agent/generated/protocol/`
- C# classes: `mod/StarCoPlay/Generated/Protocol/`
- Flattened LLM tool schemas: `protocol/schemas/llm-tools/`
- Debug/example payloads: `protocol/examples/`

## Current Design Rules

- One WebSocket binary frame carries one protobuf `Envelope`.
- `CommandRequest` uses a fixed `oneof params`; no dynamic params bag.
- `CommandResult` uses a fixed `oneof data`; no dynamic result bag.
- `google.protobuf.Any`, `Struct`, and free-form maps are not allowed on the
  core command path.
- Runtime manifests only announce enabled compiled capabilities.
- LLM tools are generated from capability params and fully expanded without
  `$ref`.
- Transport fields such as `command_id`, `session_id`, and `protocol_version`
  are wrapped by Agent runtime and must not be exposed to LLM tools.

## Proto Layout

```text
protocol/proto/common.proto
protocol/proto/transport.proto
protocol/proto/capability.proto
protocol/proto/capability_params.proto
protocol/proto/capability_results.proto
```

The detailed design source is currently tracked in:

`/Users/river/Dev/star/stardew-agent/workspace/after_third_beta/协议定义/Protocol 2.4.5 技术方案.md`
