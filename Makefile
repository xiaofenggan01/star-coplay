# Star-CoPlay Protocol Code Generation
# Generates Python and C# classes from protocol/proto/*.proto

PROTO_DIR     := protocol/proto
PROTO_FILES   := $(wildcard $(PROTO_DIR)/*.proto)
PYTHON        ?= python
PROTOC        ?= protoc
DOTNET        ?= dotnet
GRPC_TOOLS    ?= grpc_tools.protoc

# Python output
PY_OUT_DIR    := agent/generated/protocol
# C# output
CS_OUT_DIR    := mod/StarCoPlay/Generated/Protocol
MOD_CSPROJ    := mod/StarCoPlay/StarCoPlay.csproj

.PHONY: proto proto-python proto-csharp proto-clean mod-build protocol-mapper-test protocol-check eval eval-test

proto: proto-python proto-csharp

proto-python: $(PROTO_FILES)
	@mkdir -p $(PY_OUT_DIR)
	$(PYTHON) -m $(GRPC_TOOLS) \
		--proto_path=$(PROTO_DIR) \
		--python_out=$(PY_OUT_DIR) \
		--pyi_out=$(PY_OUT_DIR) \
		$(notdir $(PROTO_FILES))
	@# Fix imports: convert absolute to relative within the package
	@$(PYTHON) -c "from pathlib import Path; import re; [p.write_text(re.sub(r'^import ([a-z_]*_pb2)', r'from . import \1', p.read_text(encoding='utf-8'), flags=re.M), encoding='utf-8') for p in Path('$(PY_OUT_DIR)').glob('*_pb2.py')]; [p.write_text(re.sub(r'^import ([a-z_]*_pb2)', r'from . import \1', p.read_text(encoding='utf-8'), flags=re.M), encoding='utf-8') for p in Path('$(PY_OUT_DIR)').glob('*_pb2.pyi')]"
	@touch $(PY_OUT_DIR)/__init__.py
	@echo "Python protobuf classes generated in $(PY_OUT_DIR)/"

proto-csharp: $(PROTO_FILES)
	@mkdir -p $(CS_OUT_DIR)
	$(PROTOC) \
		--proto_path=$(PROTO_DIR) \
		--csharp_out=$(CS_OUT_DIR) \
		$(notdir $(PROTO_FILES))
	@echo "C# protobuf classes generated in $(CS_OUT_DIR)/"

mod-build: proto-csharp
	$(DOTNET) build $(MOD_CSPROJ)

protocol-mapper-test:
	$(DOTNET) test mod/StarCoPlay.Tests/StarCoPlay.Tests.csproj --filter FullyQualifiedName~ProtoCommandMapperTests

protocol-check: proto mod-build protocol-mapper-test
	@echo "Protocol generation, mod build, and mapper tests completed"

proto-clean:
	rm -f $(PY_OUT_DIR)/*_pb2.py $(PY_OUT_DIR)/*_pb2.pyi $(PY_OUT_DIR)/*_pb2_grpc.py $(PY_OUT_DIR)/__init__.py
	rm -f $(CS_OUT_DIR)/*.cs
	@echo "Generated protocol files cleaned"

# ── Deterministic eval set (see eval/README.md) ─────────────────────────
PY_AGENT      ?= agent/.venv/bin/python

eval:
	$(PY_AGENT) eval/run_eval.py

eval-test:
	$(PY_AGENT) -m pytest eval/tests -q
