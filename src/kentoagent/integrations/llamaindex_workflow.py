from __future__ import annotations

from importlib import import_module

ROLE_PROMPTS = {
    "LocateAgent": (
        "Find and report survivor candidates. Use observation tools only; never assign, "
        "rank, plan routes, or claim a rescue. Return structured detection records."
    ),
    "PriorityAgent": (
        "Rank detected survivors using severity, accessibility, distance, hazard exposure, "
        "and rescue probability. Never authorize movement."
    ),
    "CoordinateAgent": (
        "Allocate unique rescue work, resolve assignment conflicts, and hand route work to "
        "PlannerAgent. Never override world constraints."
    ),
    "PlannerAgent": (
        "Produce structured rescue routes over supplied road tools. On rejection, incorporate "
        "the typed reason and propose a different traversable route."
    ),
    "ControlAgent": (
        "Inspect proposals and request corrections. Deterministic validators are authoritative; "
        "never turn an invalid action into a valid one."
    ),
}


def build_agent_workflow(llm: object, tools_by_agent: dict[str, list[object]]) -> object:
    """Build an optional LlamaIndex handoff layer around KentoAgent specialists.

    Callers must still submit every returned action to the deterministic guardrails.
    """
    workflow_module = import_module("llama_index.core.agent.workflow")
    function_agent = workflow_module.FunctionAgent
    agent_workflow = workflow_module.AgentWorkflow
    agents = [
        function_agent(
            name=name,
            description=prompt.split(".")[0],
            system_prompt=prompt,
            llm=llm,
            tools=tools_by_agent.get(name, []),
            can_handoff_to=[candidate for candidate in ROLE_PROMPTS if candidate != name],
        )
        for name, prompt in ROLE_PROMPTS.items()
    ]
    return agent_workflow(
        agents=agents,
        root_agent="LocateAgent",
        initial_state={"world_revision": 0, "correction_attempts": 0},
    )
