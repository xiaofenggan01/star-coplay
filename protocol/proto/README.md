# Proto Sources

This directory will contain the hand-written protobuf sources for Protocol
2.4.5.

Files:

- `common.proto`: shared enums and reusable value types.
- `transport.proto`: WebSocket `Envelope`, handshake, and transport errors.
- `capability.proto`: command lifecycle messages.
- `capability_params.proto`: fixed params messages for compiled capabilities.
- `capability_results.proto`: fixed result data messages for compiled capabilities.

Do not add JSON Schema, YAML schema, `google.protobuf.Any`, or
`google.protobuf.Struct` as the core params/result contract.
