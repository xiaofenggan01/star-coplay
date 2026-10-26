# Protocol Tools

This directory will contain generation and validation tooling for Protocol
2.4.5.

Planned tools:

- protobuf code generation into `agent/generated/protocol/` and
  `mod/StarCoPlay/Generated/Protocol/`.
- protobuf descriptor export.
- LLM FunctionSchema generation from capability params.
- schema flattening with no `$ref` in LLM-facing output.
- protocol validation tests and sample message decoders.
