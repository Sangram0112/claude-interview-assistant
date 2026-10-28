"""Profile persistence on top of :class:`app.storage.database.Database`."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from app.profiles.models import UserProfile
from app.storage.database import Database

log = logging.getLogger(__name__)


class ProfileRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list(self) -> list[tuple[int, str, bool]]:
        return self._db.list_profiles()

    def get(self, profile_id: int) -> UserProfile | None:
        data = self._db.load_profile(profile_id)
        if data is None:
            return None
        try:
            return UserProfile.model_validate(data)
        except ValidationError:
            log.error("Stored profile %s is invalid; returning an empty profile", profile_id)
            return UserProfile()

    def active(self) -> tuple[int | None, UserProfile]:
        profile_id = self._db.active_profile_id()
        if profile_id is None:
            return None, UserProfile()
        return profile_id, self.get(profile_id) or UserProfile()

    def save(self, profile: UserProfile, profile_id: int | None = None) -> int:
        return self._db.save_profile(profile.name or "My profile", profile.model_dump(mode="json"), profile_id)

    def set_active(self, profile_id: int) -> None:
        self._db.set_active_profile(profile_id)

    def delete(self, profile_id: int) -> None:
        self._db.delete_profile(profile_id)
