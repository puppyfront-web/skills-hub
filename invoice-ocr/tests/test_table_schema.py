import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from table_schema import (
    ALLOWED_FIELDS,
    exact_mappings,
    header_signature,
    normalize_header,
    validate_suggestions,
)


class TableSchemaTests(unittest.TestCase):
    def test_normalizes_unicode_and_whitespace(self):
        self.assertEqual(normalize_header("  单　价  "), "单 价")

    def test_signature_preserves_order_and_duplicate_headers(self):
        first = header_signature(["单号", "数量", "数量"])
        second = header_signature(["单号", "数量", "数量"])
        reordered = header_signature(["数量", "单号", "数量"])

        self.assertEqual(first, second)
        self.assertNotEqual(first, reordered)

    def test_exact_matching_only_uses_canonical_headers(self):
        mappings = exact_mappings(["单号", "供货单位", "单价", "数量"])

        self.assertEqual(mappings[1]["target_field"], "note_number")
        self.assertEqual(mappings[3]["target_field"], "unit_price")
        self.assertNotIn(2, mappings)

    def test_rejects_unknown_target_field(self):
        accepted, pending = validate_suggestions(
            ["结算价"],
            [{
                "column_index": 1,
                "header": "结算价",
                "target_field": "shell_command",
                "confidence": 0.99,
                "reason": "invalid",
            }],
            set(),
        )

        self.assertEqual(accepted, {})
        self.assertEqual(pending[0]["reason_code"], "invalid_target")

    def test_high_confidence_null_is_accepted_as_ignored(self):
        accepted, pending = validate_suggestions(
            ["内部备注"],
            [{
                "column_index": 1,
                "header": "内部备注",
                "target_field": None,
                "confidence": 0.95,
                "reason": "用户维护列",
            }],
            set(),
        )

        self.assertIsNone(accepted[1]["target_field"])
        self.assertEqual(pending, [])

    def test_duplicate_target_fields_require_confirmation(self):
        accepted, pending = validate_suggestions(
            ["供货单位", "厂家"],
            [
                {
                    "column_index": 1,
                    "header": "供货单位",
                    "target_field": "supplier_name",
                    "confidence": 0.96,
                    "reason": "供应方",
                },
                {
                    "column_index": 2,
                    "header": "厂家",
                    "target_field": "supplier_name",
                    "confidence": 0.95,
                    "reason": "供应方",
                },
            ],
            set(),
        )

        self.assertEqual(accepted, {})
        self.assertEqual(
            {item["reason_code"] for item in pending},
            {"target_conflict"},
        )

    def test_allowed_fields_do_not_include_user_maintained_columns(self):
        self.assertNotIn("description", ALLOWED_FIELDS)
        self.assertNotIn("piece_count", ALLOWED_FIELDS)


if __name__ == "__main__":
    unittest.main()
