from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel


class TransactionKind(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class Transaction(BaseModel):
    external_id: str
    amount: int
    description: str
    date: date
    kind: str
    payee: str


class ProblemSeverity(str, Enum):
    # Transactions are not arriving. Data is being lost right now.
    BROKEN = "BROKEN"
    # Transactions are still arriving, but something will stop them soon.
    WARNING = "WARNING"


class ConnectionProblem(BaseModel):
    severity: ProblemSeverity
    description: str


class ConnectionHealth(BaseModel):
    """
    The state of the Pluggy connection (the "item") feeding one bank account.

    `problems` is empty when the connection looks healthy.
    """

    status: str
    execution_status: str
    last_updated_at: datetime | None
    consent_expires_at: datetime | None
    problems: list[ConnectionProblem]

    @property
    def is_healthy(self) -> bool:
        return len(self.problems) == 0


class AccountSyncResult(BaseModel):
    """
    What actually happened when one account's transactions were sent to YNAB.

    The two counts are tracked separately because this sync re-sends the same
    transactions every day inside its lookback window, and YNAB rejects the
    repeats. Only `created_count` represents new data arriving in the budget.
    """

    created_count: int
    already_imported_count: int
