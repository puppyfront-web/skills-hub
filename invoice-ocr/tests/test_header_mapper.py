import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import header_mapper


class HeaderMapperTests(unittest.TestCase):
    def test_prompt_contains_only_headers_and_allowed_fields(self):
        prompt = header_mapper.build_prompt(
            [
                {"column_index": 1, "header": "供货单位"},
                {"column_index": 2, "header": "结算价"},
            ],
            "采购表.xlsx",
        )

        self.assertIn('"供货单位"', prompt)
        self.assertIn('"unit_price"', prompt)
        self.assertNotIn("历史业务数据", prompt)

    def test_maps_unknown_headers_from_structured_response(self):
        response = json.dumps({
            "mappings": [
                {
                    "column_index": 1,
                    "header": "供货单位",
                    "target_field": "supplier_name",
                    "confidence": 0.97,
                    "reason": "供应方名称",
                },
                {
                    "column_index": 2,
                    "header": "结算价",
                    "target_field": "unit_price",
                    "confidence": 0.95,
                    "reason": "价格字段",
                },
            ]
        }, ensure_ascii=False)
        with patch.object(
            header_mapper,
            "call_openclaw_text",
            return_value=response,
        ):
            result = header_mapper.infer_headers(
                "http://127.0.0.1:18789",
                "token",
                "openclaw/default",
                [
                    {"column_index": 1, "header": "供货单位"},
                    {"column_index": 2, "header": "结算价"},
                ],
                "采购表.xlsx",
            )

        self.assertEqual(result[0]["column_index"], 1)
        self.assertEqual(result[1]["target_field"], "unit_price")

    def test_invalid_json_returns_pending_error(self):
        with patch.object(
            header_mapper,
            "call_openclaw_text",
            return_value="not-json",
        ):
            result = header_mapper.infer_headers(
                "http://127.0.0.1:18789",
                "",
                "openclaw/default",
                [{"column_index": 1, "header": "结算价"}],
                "采购表.xlsx",
            )

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "invalid_response")


if __name__ == "__main__":
    unittest.main()
