class SkillError(Exception):
    """Base exception for all skill-related errors."""
    pass

class SkillValidationError(SkillError):
    """Raised when a skill's input or setup is invalid."""
    pass

class SkillExecutionError(SkillError):
    """Raised when a skill encounters an error during execution."""
    pass

class SkillPermissionError(SkillError):
    """Raised when the user does not have permission to execute the skill."""
    pass

class SkillNotFoundError(SkillError):
    """Raised when a requested skill is not found in the registry."""
    pass

class SkillConfirmationRequired(SkillError):
    """Raised when a skill requires explicit user confirmation before executing."""
    pass
