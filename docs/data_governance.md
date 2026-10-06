# Data Governance

## Data Inventory

- **Memory**: Preferences, project contexts, configurations. Stored in SQLite (`memory.db`). Handled by `memory_service.py`. Kept indefinitely unless superseded or explicitly deleted.
- **Workflow State**: Intermediary steps, confirmation states, and execution histories. Stored in SQLite (`workflows.db`). Retention: Kept until completion, then garbage collected after 30 days (TBD).
- **Templates**: User-created and Built-in Markdown/DOCX templates. Stored in `workflows.db`.
- **Knowledge/Vector Index**: Extracted facts and text embeddings. Stored in local LanceDB/FAISS. Can be rebuilt from canonical source documents.
- **Tasks & Notes**: Extracted from meetings or explicitly requested. Stored locally or pushed to Odoo.

## Deletion Policy
- User can explicitly ask to "Quên thông tin này", which removes the MemoryRecord.
- Workspace wipe removes `workflows.db`, `memory.db`, and all associated artifact folders.

## Secrets
- API Keys, OAuth tokens, and Telegram tokens are strictly filtered. The Security layer explicitly raises `SensitiveMemoryError` if the router attempts to save them into long-term Memory.
