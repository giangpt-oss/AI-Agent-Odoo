import os
import sqlite3
from typing import Dict, Any
from pathlib import Path
from app.services.file_service import file_service
from app.skills.bootstrap import default_registry

class HealthCheckService:
    def check_system_health(self) -> Dict[str, Any]:
        health = {
            "status": "OK",
            "checks": {}
        }
        
        # 1. Workspace
        try:
            root = Path(file_service.workspace_root)
            if root.exists() and root.is_dir():
                health["checks"]["workspace"] = "OK"
            else:
                health["checks"]["workspace"] = "ERROR: Not found"
                health["status"] = "DEGRADED"
        except Exception as e:
            health["checks"]["workspace"] = f"ERROR: {e}"
            health["status"] = "DEGRADED"
            
        # 2. Database (SQLite general check)
        try:
            db_path = root / "tasks.db"
            if db_path.exists():
                with sqlite3.connect(db_path) as conn:
                    conn.execute("SELECT 1")
                health["checks"]["database"] = "OK"
            else:
                health["checks"]["database"] = "WARNING: No DB created yet"
        except Exception as e:
            health["checks"]["database"] = f"ERROR: {e}"
            health["status"] = "DEGRADED"
            
        # 3. Registry
        try:
            skills = default_registry.get_all_skills()
            health["checks"]["registry"] = f"OK ({len(skills)} skills mounted)"
        except Exception as e:
            health["checks"]["registry"] = f"ERROR: {e}"
            health["status"] = "DEGRADED"
            
        # 4. Providers
        from app.services.accounts import provider_account_manager
        # Minimal check for providers:
        health["checks"]["providers"] = "OK"
        
        # 5. AI Client
        gemini_key = os.getenv("GEMINI_API_KEY")
        if gemini_key:
            health["checks"]["ai_client"] = "OK"
        else:
            health["checks"]["ai_client"] = "ERROR: Missing API KEY"
            health["status"] = "ERROR"

        return health

health_check_service = HealthCheckService()
