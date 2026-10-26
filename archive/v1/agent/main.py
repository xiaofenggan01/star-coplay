from __future__ import annotations

import atexit

from agent.bridge.command_sender import CommandSender
from agent.bridge.dry_run import (
    FakeEventReader,
    FakeStateWatcher,
    PrintCommandSender,
    PrintStatusWriter,
)
from agent.bridge.event_reader import EventReader
from agent.bridge.state_watcher import StateWatcher
from agent.bridge.status_writer import NoOpStatusWriter, StatusWriter
from agent.config import load_config
from agent.debug.runtime import DebugRuntime
from agent.execution.tool_registry import ToolRegistry
from agent.execution.worker import WorkerExecutor
from agent.interface.perceiver import Perceiver
from agent.interface.planner import Planner
from agent.interface.reflector import Reflector
from agent.knowledge.evolver import KnowledgeEvolver
from agent.knowledge.router import KnowledgeRouter
from agent.llm.client import LLMClient
from agent.memory.manager import MemoryManager
from agent.memory.session_isolate import archive_memory_store_if_nonempty
from agent.planning.event_reactor import EventReactor
from agent.planning.goal import GoalManager
from agent.planning.mcp import MCPCallback
from agent.planning.plan_queue import PlanQueue
from agent.planning.precondition import PreconditionChecker
from agent.planning.task import TaskLogManager
from agent.turn_runner import TurnRunner


def _init_evomap(config, memory, evolver):
    from agent.evomap.client import GEPClient
    from agent.evomap.integration import EvoMapIntegration

    client = GEPClient(store_dir=config.evomap_store_dir, model=config.llm_model)
    integration = EvoMapIntegration(client, memory.rules)
    if integration.ensure_registered():
        integration.publish_preset()
        integration.start_heartbeat()
    else:
        print("evomap: registration failed, running without EvoMap")
    evolver.evomap = integration
    return integration


def main() -> None:
    config = load_config()
    if config.memory_isolate_sessions:
        archive_memory_store_if_nonempty(config.memory_store_dir)

    def _shutdown_memory_archive() -> None:
        if config.memory_isolate_sessions:
            archive_memory_store_if_nonempty(config.memory_store_dir)

    atexit.register(_shutdown_memory_archive)
    config.ensure_dirs()

    if config.dry_run:
        event_reader = FakeEventReader()
        watcher = FakeStateWatcher(config.poll_interval, event_reader=event_reader)
        sender = PrintCommandSender(state_ref=watcher.state_ref)
        status_writer = PrintStatusWriter()
    else:
        watcher = StateWatcher(
            config.bridge_dir,
            config.poll_interval,
            heartbeat_seconds=config.state_heartbeat_seconds,
        )
        sender = CommandSender(config.bridge_dir)
        event_reader = EventReader(config.bridge_dir)
        status_writer = StatusWriter(config.bridge_dir)
    perceiver = Perceiver()
    registry = ToolRegistry(bridge_dir=config.bridge_dir if not config.dry_run else None)
    knowledge_router = KnowledgeRouter(config.knowledge_dir)
    llm_client = LLMClient(
        provider=config.llm_provider,
        model=config.llm_model,
        api_key=config.llm_api_key,
        base_url=config.llm_base_url,
    )
    planner = Planner(knowledge_router, llm_client)
    memory = MemoryManager(
        store_dir=config.memory_store_dir,
        limit=config.memory_limit,
        recent_limit=config.recent_limit,
    )
    reflector = Reflector(llm_client, memory, reflect_mode=config.reflect_mode)
    evolver = KnowledgeEvolver(llm_client, memory, config.knowledge_dir)
    debug_runtime = DebugRuntime(config.debug_dir)
    goal_manager = GoalManager()
    plan_queue = PlanQueue()
    precondition_checker = PreconditionChecker()
    event_reactor = EventReactor()
    task_log = TaskLogManager(config.memory_store_dir / "task_log.jsonl")
    mcp = MCPCallback(task_log, memory)
    worker = WorkerExecutor(goal_manager, plan_queue, precondition_checker)
    turn_runner = TurnRunner(
        config=config,
        sender=sender,
        event_reader=event_reader,
        status_writer=status_writer,
        perceiver=perceiver,
        registry=registry,
        planner=planner,
        reflector=reflector,
        evolver=evolver,
        memory=memory,
        debug_runtime=debug_runtime,
        task_log=task_log,
        mcp=mcp,
        worker=worker,
        goal_manager=goal_manager,
        plan_queue=plan_queue,
        precondition_checker=precondition_checker,
        event_reactor=event_reactor,
    )

    evomap_integration = None
    if config.evomap_enabled:
        evomap_integration = _init_evomap(config, memory, evolver)

    if config.dry_run:
        print("dry_run: STARCoplay_AGENT_DRY_RUN")
    else:
        print("bridge:", config.bridge_dir)
    print("Plan-Execute-Reflect loop ready. Ctrl+C to stop.")

    snapshot = watcher.wait_until_ready()
    last_state_mtime = snapshot.mtime
    last_game_day = -1
    turn_count = 0

    try:
        while True:
            snapshot = watcher.wait_for_next(last_state_mtime)
            last_state_mtime = snapshot.mtime
            last_game_day, turn_count = turn_runner.run(snapshot, last_game_day, turn_count)
    except KeyboardInterrupt:
        print("\nShutting down...")
        if evomap_integration is not None:
            evomap_integration.stop_heartbeat()
        status_writer.shutdown()
        if config.memory_isolate_sessions:
            archive_memory_store_if_nonempty(config.memory_store_dir)


if __name__ == "__main__":
    main()
