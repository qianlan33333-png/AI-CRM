from __future__ import annotations

from typing import Callable, Protocol

from .models import Command, CommandResult


class CommandHandler(Protocol):
    def __call__(self, command: Command) -> dict:
        ...


AuditHook = Callable[[Command, CommandResult], None]


class CommandBus:
    def __init__(self, *, audit_hook: AuditHook | None = None):
        self._handlers: dict[str, CommandHandler] = {}
        self._results_by_idempotency_key: dict[str, CommandResult] = {}
        self._audit_hook = audit_hook

    def register(self, command_name: str, handler: CommandHandler) -> None:
        name = command_name.strip()
        if not name:
            raise ValueError("command_name cannot be empty")
        self._handlers[name] = handler

    def execute(self, command: Command) -> CommandResult:
        if command.idempotency_key and command.idempotency_key in self._results_by_idempotency_key:
            return self._results_by_idempotency_key[command.idempotency_key]

        if command.context.dry_run:
            result = self._result(command, status="dry_run", payload={"dry_run": True})
            self._remember(command, result)
            return result

        handler = self._handlers.get(command.command_name)
        if not handler:
            result = self._result(command, status="failed", error=f"handler not registered: {command.command_name}")
            self._remember(command, result)
            return result

        try:
            result = self._result(command, status="completed", payload=handler(command) or {})
        except Exception as exc:
            result = self._result(command, status="failed", error=str(exc))
        self._remember(command, result)
        return result

    def _result(self, command: Command, *, status: str, payload: dict | None = None, error: str = "") -> CommandResult:
        context = command.context
        return CommandResult(
            command_name=command.command_name,
            command_id=command.command_id,
            status=status,  # type: ignore[arg-type]
            payload=payload or {},
            idempotency_key=command.idempotency_key,
            actor_id=context.actor_id,
            actor_type=context.actor_type,
            request_id=context.request_id,
            trace_id=context.trace_id,
            source_route=context.source_route,
            created_at=command.created_at,
            error=error,
        )

    def _remember(self, command: Command, result: CommandResult) -> None:
        if command.idempotency_key:
            self._results_by_idempotency_key[command.idempotency_key] = result
        if self._audit_hook:
            self._audit_hook(command, result)
