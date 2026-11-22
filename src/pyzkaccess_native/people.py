"""Optional "person + category" numbering of UserIDs.

Access events only tell *which UserID* was recognized, not which finger or
which card. To let one person trigger different actions (open, arm, lock...),
give that person one UserID per action category:

    UserID = person number followed by one category digit

With 2-digit person numbers, person 12 has UserID 120 for category 0, 123 for
category 3, and so on. Each UserID can hold several fingerprints, one card and
one keypad code, so every credential of a person maps to exactly one category.

This module only does the arithmetic; what a category *means* is up to the
application (the Home Assistant integration stores it in its settings).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class CategoryScheme:
    """UserID <-> (person, category) conversion."""

    #: number of digits of the person number (2 = persons 1 to 99)
    person_digits: int = 2

    @property
    def max_person(self) -> int:
        return 10 ** self.person_digits - 1

    def user_id(self, person: int, category: int) -> int:
        if not 1 <= person <= self.max_person:
            raise ValueError(f"person number must be between 1 and {self.max_person}")
        if not 0 <= category <= 9:
            raise ValueError("category must be a single digit (0-9)")
        return person * 10 + category

    def split(self, user_id: int) -> Optional[Tuple[int, int]]:
        """``(person, category)``, or ``None`` when ``user_id`` does not follow the scheme."""
        person, category = divmod(int(user_id), 10)
        if 1 <= person <= self.max_person:
            return person, category
        return None
