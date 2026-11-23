"""Agent HTTP peer for Gateway's opt-in PostgreSQL end-to-end test."""

from fastapi import FastAPI

from app.api.v1 import internal
from app.domain.intent_schema import IntentAnalysisResult

app = FastAPI()
app.include_router(internal.router)


async def classify(text, context, session):
    if text == "Prepare a new file":
        return IntentAnalysisResult(
            intent_type="prepare_write", tool_name="workspace.drive.write",
            parameters={"method": "files.create", "body": {"name": "Peer write.txt"}},
        )
    if text.startswith("Confirm "):
        return IntentAnalysisResult(intent_type="action_confirm", confirmation_id=text.split(" ", 1)[1])
    assert text == "List my Workspace files"
    return IntentAnalysisResult(intent_type="tool_plan", steps=[{
        "id": "files",
        "tool_name": "workspace.drive.read",
        "parameters": {"method": "files.list"},
    }])


# Fix only the model's output. The HTTP endpoints, executor and client stay real.
internal.natural_pipeline.analyze_input = classify
