# Hopita AI Office Assistant Architecture

```mermaid
graph TD
    User([Telegram User]) --> Bot[run_telegram_bot_polling.py]
    Bot --> Orchestrator[AgentOrchestrator]
    
    subgraph Core
        Orchestrator --> Router[SkillRouter]
        Orchestrator --> CM[ConfirmationManager]
        Router --> Registry[SkillRegistry]
    end
    
    subgraph Execution
        Registry --> Skills[Mounted Skills]
        Skills --> Providers[Providers Abstraction]
    end
    
    subgraph Infrastructure
        Providers --> SQLite[(SQLite DBs)]
        Providers --> Odoo[Odoo Cloud]
        Providers --> Gmail[Google API]
        
        Scheduler[SchedulerService] --> SQLite
        Scheduler --> Telegram[TelegramNotifier]
    end
    
    subgraph Security
        Orchestrator --> Perms[PermissionService]
        Orchestrator --> Audit[AuditLogger]
    end
```

## Core Components
- **AgentOrchestrator**: Manages conversation history, invokes LLM, intercepts function calls, and handles Confirmation & Permissions.
- **SkillRouter**: Routes intents to a subset of skills to avoid context overflow.
- **Providers**: Isolates Skills from raw API requests. Example: `LocalTaskProvider`, `OdooAsyncClient`.
- **ConfirmationManager**: Gating destructive/external actions. Prevents replay attacks.
- **Scheduler**: High-performance background polling for reminders.
