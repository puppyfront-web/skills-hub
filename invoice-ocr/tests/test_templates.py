import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from templates import auto_confirm_and_learn, load_index, validate_extraction


def valid_result(filename="001.jpg"):
    return {
        "filename": filename,
        "status": "success",
        "review_status": "pending",
        "data": {
            "document_type": "delivery",
            "document_title": "销售码单",
            "delivery_note": {
                "supplier_name": "旺泰纺织",
                "note_number": "WT-001",
                "date": "2026-06-25",
            },
            "items": [{
                "material_type": "面料",
                "fabric_code": "A100",
                "unit_price": 10.0,
                "quantity": 20.0,
                "unit": "米",
                "total_amount": 200.0,
            }],
            "total_amount": 200.0,
            "needs_review": [],
        },
    }


class TemplateAutomationTests(unittest.TestCase):
    def test_valid_new_supplier_is_confirmed_and_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            templates_dir = Path(tmp)

            result = auto_confirm_and_learn(
                valid_result(),
                templates_dir,
            )

            self.assertEqual(result["review_status"], "confirmed")
            self.assertTrue(result["auto_template_created"])
            self.assertEqual(
                len(load_index(templates_dir)["templates"]),
                1,
            )

    def test_missing_price_stays_pending_without_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            templates_dir = Path(tmp)
            result = valid_result()
            result["data"]["items"][0]["unit_price"] = None

            processed = auto_confirm_and_learn(result, templates_dir)

            self.assertEqual(processed["review_status"], "pending")
            self.assertEqual(load_index(templates_dir)["templates"], {})

    def test_line_amount_mismatch_is_reported(self):
        data = valid_result()["data"]
        data["items"][0]["total_amount"] = 199.0

        issues = validate_extraction(data, None)

        self.assertTrue(any("金额不一致" in issue for issue in issues))

    def test_existing_model_review_issue_is_preserved(self):
        data = valid_result()["data"]
        data["needs_review"] = ["fabric_code_unclear"]

        issues = validate_extraction(data, None)

        self.assertIn("fabric_code_unclear", issues)

    def test_missing_supplier_and_items_are_reported(self):
        data = valid_result()["data"]
        data["delivery_note"]["supplier_name"] = ""
        data["items"] = []

        issues = validate_extraction(data, None)

        self.assertIn("供应商缺失", issues)
        self.assertIn("没有识别到明细行", issues)


if __name__ == "__main__":
    unittest.main()
