"""Shared process and database handling for job reconcilers."""

from __future__ import annotations

import argparse
import logging
from abc import ABC, abstractmethod
from typing import Callable, ClassVar, Self, Sequence

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from settings import OrchestrationSettings


class BaseReconciler(ABC):
    """Handle common startup, looping, backoff, and database cleanup."""

    session_factory: Callable[[], Session]
    settings: OrchestrationSettings
    sleep: Callable[[float], None]
    clock: Callable[[], float]
    shared_service_errors: ClassVar[tuple[type[Exception], ...]]
    shared_service_error_message: ClassVar[str]
    reconciler_name: ClassVar[str]
    _outage_delay: int

    def __post_init__(self) -> None:
        self._outage_delay = self.settings.outage_initial_backoff_seconds

    @classmethod
    @abstractmethod
    def from_env(cls) -> Self:
        """Create the reconciler from process configuration."""

    @classmethod
    def run_cli(cls, argv: Sequence[str] | None = None) -> None:
        """Start one reconciler from the command line."""

        parser = argparse.ArgumentParser(
            description=f"Run the {cls.reconciler_name} job reconciler."
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one round and exit.",
        )
        arguments = parser.parse_args(argv)

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        logger = logging.getLogger(cls.__module__)

        try:
            reconciler = cls.from_env()
            mode = "once" if arguments.once else "continuous"
            logger.info(
                "reconciler_started reconciler=%s mode=%s",
                cls.reconciler_name,
                mode,
            )
            if arguments.once:
                reconciler.run_once()
            else:
                reconciler.run_forever()
            logger.info("reconciler_stopped reconciler=%s", cls.reconciler_name)
        except Exception:
            logger.exception(
                "reconciler_stopped_with_error reconciler=%s",
                cls.reconciler_name,
            )
            raise

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
            except self.shared_service_errors as error:
                logging.getLogger(type(self).__module__).warning(
                    "%s reconciler=%s retry_in_seconds=%s error_type=%s error=%s",
                    self.shared_service_error_message,
                    self.reconciler_name,
                    self._outage_delay,
                    type(error).__name__,
                    error,
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

    def run_once(self) -> None:
        """Run one round and let failures reach the caller."""

        self._run_round_and_get_poll_delay()

    def _run_round_and_get_poll_delay(self) -> float:
        started_at = self.clock()
        selected_jobs = self.run_round()
        elapsed = max(0.0, self.clock() - started_at)
        delay = max(0.0, self.poll_interval_seconds - elapsed)
        logging.getLogger(type(self).__module__).info(
            "round_complete reconciler=%s selected_jobs=%s "
            "duration_seconds=%.3f next_poll_seconds=%.3f",
            self.reconciler_name,
            selected_jobs,
            elapsed,
            delay,
        )
        return delay

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
