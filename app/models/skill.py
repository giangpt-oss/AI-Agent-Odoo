from enum import Enum
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

class OperationType(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"
    EXTERNAL_ACTION = "EXTERNAL_ACTION"

class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

class SkillCategory(str, Enum):
    ODOO = "ODOO"
    DOCUMENT = "DOCUMENT"
    SPREADSHEET = "SPREADSHEET"
    PRESENTATION = "PRESENTATION"
    FILE = "FILE"
    OFFICE = "OFFICE"
    SYSTEM = "SYSTEM"
    UTILITY = "UTILITY"
