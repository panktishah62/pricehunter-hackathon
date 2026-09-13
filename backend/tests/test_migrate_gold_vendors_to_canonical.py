import unittest

from scripts.migrate_gold_vendors_to_canonical import (
    _identity_decision,
    _index_main_vendors,
    normalize_gst,
    normalize_host,
    normalize_phone,
)


class GoldVendorCanonicalMigrationTests(unittest.TestCase):
    def test_identity_normalizers_ignore_masked_or_shared_values(self) -> None:
        self.assertEqual(normalize_phone("+91 98765 43210"), "9876543210")
        self.assertEqual(normalize_gst("27ABCDE1234F1Z5"), "27ABCDE1234F1Z5")
        self.assertEqual(normalize_gst("27**********1ZY"), "")
        self.assertEqual(normalize_host("https://www.example.com/live-rates"), "example.com")
        self.assertEqual(normalize_host("https://wa.me/919876543210"), "")


    def test_unique_phone_is_a_strong_existing_canonical_match(self) -> None:
        canonical = [{"vendor_id": "canonical:one", "name": "Dealer One", "phone": "9876543210"}]
        source = [{"vendor_id": "legacy:one", "name": "Dealer One", "phone_primary": "+91 98765 43210"}]

        decision = _identity_decision(
            source[0],
            _index_main_vendors(canonical),
            _index_main_vendors(source),
        )

        self.assertEqual(decision["canonical_vendor_id"], "canonical:one")
        self.assertEqual(decision["match_type"], "phone")
        self.assertEqual(decision["status"], "linked")


    def test_same_name_and_city_is_reviewed_not_automatically_merged(self) -> None:
        source = [
            {"vendor_id": "legacy:one", "name": "Example Bullion LLP", "city": "Mumbai"},
            {"vendor_id": "legacy:two", "name": "Example Bullion LLP", "city": "MUMBAI"},
        ]

        decision = _identity_decision(
            source[0],
            _index_main_vendors([]),
            _index_main_vendors(source),
        )

        self.assertEqual(decision["canonical_vendor_id"], "legacy:one")
        self.assertEqual(decision["match_type"], "new_canonical")
        self.assertEqual(decision["status"], "review")
        self.assertEqual(decision["review_candidates"], ["legacy:two"])


    def test_shared_website_is_reviewed_not_automatically_merged(self) -> None:
        source = [
            {"vendor_id": "legacy:one", "name": "Example One", "website": "https://example.com/rates"},
            {"vendor_id": "legacy:two", "name": "Example Two", "website": "http://www.example.com"},
        ]

        decision = _identity_decision(
            source[0],
            _index_main_vendors([]),
            _index_main_vendors(source),
        )

        self.assertEqual(decision["canonical_vendor_id"], "legacy:one")
        self.assertEqual(decision["status"], "review")
        self.assertEqual(decision["review_candidates"], ["legacy:two"])


if __name__ == "__main__":
    unittest.main()
