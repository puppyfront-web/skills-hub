import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import templates


class HeaderMappingPreferenceTests(unittest.TestCase):
    def test_saves_and_loads_mapping_by_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs_path = Path(tmp) / "prefs.json"
            with patch.object(templates, "PREFS_PATH", prefs_path):
                templates.save_header_mapping(
                    "sig",
                    ["供货单位", "结算价"],
                    {
                        1: {
                            "header": "供货单位",
                            "target_field": "supplier_name",
                            "source": "llm",
                            "confidence": 0.97,
                        },
                        2: {
                            "header": "结算价",
                            "target_field": "unit_price",
                            "source": "user",
                            "confidence": 1.0,
                        },
                    },
                )
                loaded = templates.get_header_mapping("sig")

            self.assertEqual(
                loaded["mappings"]["2"]["target_field"],
                "unit_price",
            )

    def test_user_mapping_overrides_previous_llm_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs_path = Path(tmp) / "prefs.json"
            with patch.object(templates, "PREFS_PATH", prefs_path):
                templates.save_header_mapping(
                    "sig",
                    ["款号"],
                    {
                        1: {
                            "header": "款号",
                            "target_field": "fabric_code",
                            "source": "llm",
                            "confidence": 0.91,
                        },
                    },
                )
                templates.save_header_mapping(
                    "sig",
                    ["款号"],
                    {
                        1: {
                            "header": "款号",
                            "target_field": "style_number",
                            "source": "user",
                            "confidence": 1.0,
                        },
                    },
                )
                loaded = templates.get_header_mapping("sig")

            self.assertEqual(
                loaded["mappings"]["1"]["target_field"],
                "style_number",
            )


if __name__ == "__main__":
    unittest.main()
