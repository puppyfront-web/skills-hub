import unittest
from pathlib import Path


TEXT = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(
    encoding="utf-8"
)


class SkillContractTests(unittest.TestCase):
    def test_describes_one_time_table_setup_and_last_table_reuse(self):
        self.assertIn("上传 Excel 模板或提供表头字段", TEXT)
        self.assertIn("复用最后一次使用的表", TEXT)

    def test_describes_adaptive_header_mapping(self):
        self.assertIn("已学习映射", TEXT)
        self.assertIn("高置信度", TEXT)
        self.assertIn("低置信度", TEXT)

    def test_new_supplier_is_not_an_exception_by_itself(self):
        self.assertIn("新供应商不等于异常", TEXT)

    def test_preserves_price_rule(self):
        self.assertIn("same supplier + fabric_code", TEXT)
        self.assertIn("highlighted red in Excel", TEXT)

    def test_does_not_claim_arbitrary_invoice_schema_support(self):
        self.assertIn("通用目标表适配", TEXT)
        self.assertIn("仍面向服装工厂", TEXT)


if __name__ == "__main__":
    unittest.main()
