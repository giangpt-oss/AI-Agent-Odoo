import sqlite3
import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from app.workflows.models import Template, Workflow, WorkflowExecution, WorkflowStep
from app.services.file_service import file_service

class SQLiteWorkflowStore:
    def __init__(self):
        self.db_path = Path(file_service.workspace_root) / "workflows.db"
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS templates (
                    template_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    template_type TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    format TEXT NOT NULL,
                    schema_def TEXT NOT NULL,
                    content TEXT,
                    user_id TEXT NOT NULL,
                    workspace_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    version INTEGER NOT NULL,
                    inputs_schema TEXT NOT NULL,
                    steps TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    workspace_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    workflow_version INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    workspace_id TEXT,
                    status TEXT NOT NULL,
                    current_step_index INTEGER NOT NULL,
                    context_data TEXT NOT NULL,
                    artifacts TEXT NOT NULL,
                    error_msg TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    waiting_skill TEXT,
                    waiting_payload TEXT
                )
            """)
            conn.commit()

    # --- TEMPLATES ---
    def save_template(self, t: Template) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO templates 
                (template_id, name, description, template_type, version, format, schema_def, content, user_id, workspace_id, created_at, updated_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                t.template_id, t.name, t.description, t.template_type, t.version, t.format, json.dumps(t.schema_def),
                t.content, t.user_id, t.workspace_id, t.created_at, t.updated_at, t.status
            ))
            conn.commit()

    def get_template(self, template_id: str) -> Optional[Template]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM templates WHERE template_id = ?", (template_id,))
            row = cursor.fetchone()
            if row:
                return Template(
                    template_id=row[0], name=row[1], description=row[2], template_type=row[3], version=row[4],
                    format=row[5], schema_def=json.loads(row[6]), content=row[7], user_id=row[8], workspace_id=row[9],
                    created_at=row[10], updated_at=row[11], status=row[12]
                )
        return None

    def list_templates(self, user_id: str, workspace_id: Optional[str] = None) -> List[Template]:
        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT * FROM templates WHERE (user_id = ? OR workspace_id = ?) AND status = 'ACTIVE'"
            cursor = conn.execute(query, (user_id, workspace_id))
            return [
                Template(
                    template_id=row[0], name=row[1], description=row[2], template_type=row[3], version=row[4],
                    format=row[5], schema_def=json.loads(row[6]), content=row[7], user_id=row[8], workspace_id=row[9],
                    created_at=row[10], updated_at=row[11], status=row[12]
                ) for row in cursor.fetchall()
            ]

    # --- WORKFLOWS ---
    def save_workflow(self, w: Workflow) -> None:
        with sqlite3.connect(self.db_path) as conn:
            steps_json = json.dumps([s.model_dump() for s in w.steps])
            conn.execute("""
                INSERT OR REPLACE INTO workflows 
                (workflow_id, name, description, version, inputs_schema, steps, user_id, workspace_id, created_at, updated_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                w.workflow_id, w.name, w.description, w.version, json.dumps(w.inputs_schema), steps_json,
                w.user_id, w.workspace_id, w.created_at, w.updated_at, w.status
            ))
            conn.commit()

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,))
            row = cursor.fetchone()
            if row:
                steps_data = json.loads(row[5])
                steps = [WorkflowStep(**s) for s in steps_data]
                return Workflow(
                    workflow_id=row[0], name=row[1], description=row[2], version=row[3],
                    inputs_schema=json.loads(row[4]), steps=steps, user_id=row[6], workspace_id=row[7],
                    created_at=row[8], updated_at=row[9], status=row[10]
                )
        return None

    def list_workflows(self, user_id: str, workspace_id: Optional[str] = None) -> List[Workflow]:
        with sqlite3.connect(self.db_path) as conn:
            query = "SELECT * FROM workflows WHERE (user_id = ? OR workspace_id = ?) AND status = 'ACTIVE'"
            cursor = conn.execute(query, (user_id, workspace_id))
            out = []
            for row in cursor.fetchall():
                steps_data = json.loads(row[5])
                steps = [WorkflowStep(**s) for s in steps_data]
                out.append(Workflow(
                    workflow_id=row[0], name=row[1], description=row[2], version=row[3],
                    inputs_schema=json.loads(row[4]), steps=steps, user_id=row[6], workspace_id=row[7],
                    created_at=row[8], updated_at=row[9], status=row[10]
                ))
            return out

    # --- EXECUTIONS ---
    def save_execution(self, e: WorkflowExecution) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO executions 
                (execution_id, workflow_id, workflow_version, user_id, workspace_id, status, current_step_index, 
                context_data, artifacts, error_msg, started_at, finished_at, waiting_skill, waiting_payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                e.execution_id, e.workflow_id, e.workflow_version, e.user_id, e.workspace_id, e.status, e.current_step_index,
                json.dumps(e.context_data), json.dumps(e.artifacts), e.error_msg, e.started_at, e.finished_at,
                e.waiting_skill, json.dumps(e.waiting_payload) if e.waiting_payload else None
            ))
            conn.commit()

    def get_execution(self, execution_id: str) -> Optional[WorkflowExecution]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT * FROM executions WHERE execution_id = ?", (execution_id,))
            row = cursor.fetchone()
            if row:
                return WorkflowExecution(
                    execution_id=row[0], workflow_id=row[1], workflow_version=row[2], user_id=row[3], workspace_id=row[4],
                    status=row[5], current_step_index=row[6], context_data=json.loads(row[7]), artifacts=json.loads(row[8]),
                    error_msg=row[9], started_at=row[10], finished_at=row[11], waiting_skill=row[12], 
                    waiting_payload=json.loads(row[13]) if row[13] else None
                )
        return None

workflow_store = SQLiteWorkflowStore()
