"""Create (or add a new version of) the GhostTrace prompt agent in Foundry. Simulated data only.

Uses `instructions.md` and `explanation.schema.json` in this folder; the app validates answers
against the same schema. Keyless: DefaultAzureCredential (Azure CLI sign-in).

    set FOUNDRY_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>
    python agents/prompt/create_prompt_agent.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    PromptAgentDefinition,
    PromptAgentDefinitionTextOptions,
    TextResponseFormatJsonSchema,
)
from azure.identity import DefaultAzureCredential

SRC = Path(__file__).resolve().parent
AGENT_NAME = os.environ.get("PROMPT_AGENT_NAME", "ghosttrace-explainer-prompt")
DEPLOYMENT = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4-mini")


def main() -> None:
    instructions = (SRC / "instructions.md").read_text(encoding="utf-8")
    schema = json.loads((SRC / "explanation.schema.json").read_text(encoding="utf-8"))
    client = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=DefaultAzureCredential())
    definition = PromptAgentDefinition(
        model=DEPLOYMENT,
        instructions=instructions,
        text=PromptAgentDefinitionTextOptions(
            format=TextResponseFormatJsonSchema(name="ghosttrace_explanation", schema=schema, strict=True)
        ),
    )
    agent = client.agents.create_version(
        agent_name=AGENT_NAME,
        definition=definition,
        description="Explains GhostTrace match results for human review. Simulated data only.",
    )
    print(json.dumps({"name": agent.name, "version": agent.version, "id": agent.id}))


if __name__ == "__main__":
    main()
