import unittest

from app.open_classification import resolve_missing_open_classification


class ResolveMissingOpenClassificationTests(unittest.TestCase):
    def test_preserves_stored_proxy_type(self) -> None:
        self.assertEqual(
            (False, "apple"),
            resolve_missing_open_classification(
                proxy_type="apple",
                ip_address="8.8.8.8",
                user_agent="Mozilla/5.0",
            ),
        )

    def test_classifies_missing_google_proxy_type(self) -> None:
        self.assertEqual(
            (False, "google"),
            resolve_missing_open_classification(
                proxy_type=None,
                ip_address="66.102.1.1",
                user_agent="",
            ),
        )

    def test_classifies_missing_apple_proxy_type_for_generic_akamai_fetch(self) -> None:
        self.assertEqual(
            (False, "apple"),
            resolve_missing_open_classification(
                proxy_type=None,
                ip_address="172.226.188.13",
                user_agent="Mozilla/5.0",
            ),
        )

    def test_classifies_missing_real_open(self) -> None:
        self.assertEqual(
            (True, None),
            resolve_missing_open_classification(
                proxy_type=None,
                ip_address="8.8.8.8",
                user_agent="Mozilla/5.0",
            ),
        )


if __name__ == "__main__":
    unittest.main()
