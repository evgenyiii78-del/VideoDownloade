import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from user_registry import init_users_db, record_user, users_summary


class UserRegistryTests(unittest.TestCase):
    def test_record_and_update_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "users.sqlite3"
            init_users_db(db)

            user = SimpleNamespace(
                id=123,
                username="tester",
                first_name="Test",
                last_name="User",
            )

            record_user(db, user)
            record_user(db, user, increment_requests=True)
            record_user(db, user, increment_requests=True)

            total_users, total_requests, rows = users_summary(db)

            self.assertEqual(total_users, 1)
            self.assertEqual(total_requests, 2)
            self.assertEqual(rows[0]["user_id"], 123)
            self.assertEqual(rows[0]["username"], "tester")
            self.assertEqual(rows[0]["request_count"], 2)

    def test_user_profile_is_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "users.sqlite3"
            init_users_db(db)

            record_user(
                db,
                SimpleNamespace(
                    id=7,
                    username="old_name",
                    first_name="Old",
                    last_name=None,
                ),
            )
            record_user(
                db,
                SimpleNamespace(
                    id=7,
                    username="new_name",
                    first_name="New",
                    last_name="Name",
                ),
            )

            _, _, rows = users_summary(db)
            self.assertEqual(rows[0]["username"], "new_name")
            self.assertEqual(rows[0]["first_name"], "New")
            self.assertEqual(rows[0]["last_name"], "Name")


if __name__ == "__main__":
    unittest.main()
