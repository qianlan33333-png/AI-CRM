from __future__ import annotations


class ApplicationError(Exception):
    status_code = 400


class NotFoundError(ApplicationError):
    status_code = 404


class ContractError(ApplicationError):
    status_code = 400
