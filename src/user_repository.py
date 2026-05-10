"""
Convex-backed user repository. No-op if CONVEX_URL is unset.
"""

import os
import logging

log = logging.getLogger(__name__)


class UserRepository:
    def __init__(self):
        self._client = None
        self._enabled = bool(os.getenv("CONVEX_URL"))
        if not self._enabled:
            log.info("[UserRepository] CONVEX_URL not set, persistence disabled")

    def _get_client(self):
        if not self._enabled:
            return None
        if self._client is None:
            from convex import ConvexClient

            self._client = ConvexClient(os.getenv("CONVEX_URL"))
        return self._client

    def get_user(self, telegram_user_id: int) -> dict | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            return client.query("users:getUser", {"telegramUserId": telegram_user_id})
        except Exception as exc:
            log.warning(f"[UserRepository] getUser failed: {exc}")
            return None

    def upsert_user(self, telegram_user_id: int, telegram_username: str | None) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            args: dict = {"telegramUserId": telegram_user_id}
            if telegram_username:
                args["telegramUsername"] = telegram_username
            client.mutation("users:upsertUser", args)
        except Exception as exc:
            log.warning(f"[UserRepository] upsertUser failed: {exc}")

    def set_name(self, telegram_user_id: int, name: str) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.mutation(
                "users:setName",
                {"telegramUserId": telegram_user_id, "name": name},
            )
        except Exception as exc:
            log.warning(f"[UserRepository] setName failed: {exc}")

    def set_machine(self, telegram_user_id: int, machine: str) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.mutation(
                "users:setMachine",
                {"telegramUserId": telegram_user_id, "machine": machine},
            )
        except Exception as exc:
            log.warning(f"[UserRepository] setMachine failed: {exc}")

    def increment_message_count(self, telegram_user_id: int) -> int | None:
        client = self._get_client()
        if client is None:
            return None
        try:
            return client.mutation(
                "users:incrementMessageCount",
                {"telegramUserId": telegram_user_id},
            )
        except Exception as exc:
            log.warning(f"[UserRepository] incrementMessageCount failed: {exc}")
            return None

    def set_bio(self, telegram_user_id: int, bio: str) -> None:
        client = self._get_client()
        if client is None:
            return
        try:
            client.mutation(
                "users:setBio",
                {"telegramUserId": telegram_user_id, "bio": bio},
            )
        except Exception as exc:
            log.warning(f"[UserRepository] setBio failed: {exc}")
