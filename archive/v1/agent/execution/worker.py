from __future__ import annotations

from typing import Any

from agent.debug.runtime import TurnTrace
from agent.execution.tool_base import ToolDecision
from agent.planning.goal import GoalManager
from agent.planning.plan_queue import PlanQueue
from agent.planning.precondition import PreconditionChecker
from agent.planning.task import Task


class WorkerExecutor:

    def __init__(
        self,
        goal_manager: GoalManager,
        plan_queue: PlanQueue,
        precondition_checker: PreconditionChecker,
    ) -> None:
        self.goal_manager = goal_manager
        self.plan_queue = plan_queue
        self.precondition_checker = precondition_checker

    def resolve_tool(
        self,
        task: Task,
        context: Any,
        trace: TurnTrace,
    ) -> ToolDecision | None:
        if not self.plan_queue.is_empty():
            step = self.plan_queue.peek()
            assert step is not None
            prereqs = self.precondition_checker.check(step, context)
            if prereqs:
                self.plan_queue.push_front_many(prereqs)
                trace.record("worker_precondition_inject", [p.as_dict() for p in prereqs])
                step = self.plan_queue.peek()
                assert step is not None
            self.plan_queue.pop()
            trace.record("worker_plan_queue_pop", step.as_dict())
            return ToolDecision(
                tool_name=step.tool_name,
                arguments=step.arguments,
                reason=step.reason or step.source,
                expectation=step.expectation,
            )

        goal = self.goal_manager.current_goal
        if goal and goal.status == "active":
            exec_sg = next(
                (sg for sg in goal.sub_goals if sg.status == "active" and sg.tool_name),
                None,
            )
            if exec_sg:
                trace.record("worker_goal_step", exec_sg.as_dict())
                return ToolDecision(
                    tool_name=exec_sg.tool_name,
                    arguments=exec_sg.arguments,
                    reason=f"task:{task.description[:40]}",
                    expectation=exec_sg.description,
                )

        return None

    def is_task_done(self) -> bool:
        return self.goal_manager.is_idle() and self.plan_queue.is_empty()
