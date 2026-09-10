"""Domain-level exceptions for ethermig."""

from typing import Optional


class EthermigError(Exception):
    """Base exception for all ethermig errors."""

    def __init__(self, message: str, details: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def __str__(self) -> str:
        if self.details:
            return f"{self.message}\n{self.details}"
        return self.message


class ConfigurationError(EthermigError):
    """Raised when configuration loading, parsing, or validation fails."""
    pass


class DatabaseConnectionError(EthermigError):
    """Raised when a database connection cannot be established."""

    def __init__(self, message: str, environment: Optional[str] = None, details: Optional[str] = None) -> None:
        super().__init__(message, details)
        self.environment = environment


class MigrationSyncError(EthermigError):
    """Raised when migration synchronization check fails."""

    def __init__(
        self,
        message: str,
        reference_env: str,
        reference_revision: Optional[str] = None,
        unapplied_by_env: Optional[dict[str, list[str]]] = None,
        unreachable_envs: Optional[list[str]] = None,
        details: Optional[str] = None,
    ) -> None:
        super().__init__(message, details)
        self.reference_env = reference_env
        self.reference_revision = reference_revision
        self.unapplied_by_env = unapplied_by_env or {}
        self.unreachable_envs = unreachable_envs or []


class GenerationError(EthermigError):
    """Raised when database schema reflection or SQLModel code generation fails."""
    pass


class AlembicOperationError(EthermigError):
    """Raised when an Alembic operation (revision, upgrade, downgrade, stamp) fails."""
    pass
