"""Shared process and database handling for job reconcilers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable, ClassVar

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from orchestration.settings import OrchestrationSettings


class BaseReconciler(ABC):
    """Handle the common loop, outage backoff, and database cleanup."""

    session_factory: Callable[[], Session]
    settings: OrchestrationSettings
    sleep: Callable[[float], None]
    clock: Callable[[], float]
    shared_service_errors: ClassVar[tuple[type[Exception], ...]]
    shared_service_error_message: ClassVar[str]
    _outage_delay: int

    def __post_init__(self) -> None:
        self._outage_delay = self.settings.outage_initial_backoff_seconds

    def run_round(self) -> int:
        """Run one round and always close its database session."""

        db = self.session_factory()
        db.expire_on_commit = False
        try:
            return self._run_round(db)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @abstractmethod
    def _run_round(self, db: Session) -> int:
        """Perform one reconciler-specific round."""

    def run_forever(self, *, rounds: int | None = None) -> None:
        """Run non-overlapping rounds and back off during shared outages."""

        completed_rounds = 0
        while rounds is None or completed_rounds < rounds:
            try:
                delay = self._run_round_and_get_poll_delay()
            except self.shared_service_errors:
                logging.getLogger(type(self).__module__).warning(
                    self.shared_service_error_message
                )
                delay = self._outage_delay
                self._outage_delay = min(
                    self._outage_delay * 2,
                    self.settings.outage_max_backoff_seconds,
                )
            else:
                self._outage_delay = self.settings.outage_initial_backoff_seconds

            completed_rounds += 1
            if rounds is None or completed_rounds < rounds:
                self.sleep(delay)

    def _run_round_and_get_poll_delay(self) -> float:
        started_at = self.clock()
        self.run_round()
        elapsed = max(0.0, self.clock() - started_at)
        return max(0.0, self.poll_interval_seconds - elapsed)

    @property
    @abstractmethod
    def poll_interval_seconds(self) -> float:
        """Return the normal delay after a successful round."""

    @staticmethod
    def _commit(db: Session) -> None:
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise
