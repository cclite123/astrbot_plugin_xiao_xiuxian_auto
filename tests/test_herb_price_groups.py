from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_xiao_xiuxian_auto.auto_alchemy_optimizer import AutoAlchemyOptimizer


class HerbPriceGroupTests(unittest.TestCase):
    def make_optimizer(self, temp_dir: str, account_data: dict, catalog_data: dict):
        account_path = Path(temp_dir) / "account.yaml"
        catalog_path = Path(temp_dir) / "catalog.yaml"
        account_path.write_text(
            yaml.safe_dump(account_data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        catalog_path.write_text(
            yaml.safe_dump(catalog_data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        optimizer = AutoAlchemyOptimizer(
            official_qq="3889001741",
            recipe_path=str(Path(temp_dir) / "recipes.txt"),
            config={
                "herb_max_prices_path": str(account_path),
                "herb_grade_catalog_path": str(catalog_path),
            },
        )
        return optimizer, account_path

    def test_nested_yaml_preserves_groups_and_builds_flat_lookup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            optimizer, _ = self.make_optimizer(
                temp_dir,
                {"九品药材": {"尘磊岩麟果": 960}, "一品药材": {"清灵草": 12}},
                {"九品药材": {"尘磊岩麟果": 1000}, "一品药材": {"清灵草": 10}},
            )

            payload = optimizer.get_herb_price_config()

            self.assertEqual(960.0, payload["groups"]["九品药材"]["尘磊岩麟果"])
            self.assertEqual(12.0, payload["groups"]["一品药材"]["清灵草"])
            self.assertEqual({}, payload["groups"]["二品药材"])
            self.assertEqual(12.0, optimizer.herb_max_prices["清灵草"])
            self.assertEqual({}, payload["unclassified"])

    def test_flat_yaml_uses_catalog_and_keeps_unknown_items_unclassified(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            optimizer, _ = self.make_optimizer(
                temp_dir,
                {"尘磊岩麟果": 960, "自定义草": 25},
                {"九品药材": {"尘磊岩麟果": 1000}},
            )

            payload = optimizer.get_herb_price_config()

            self.assertEqual(960.0, payload["groups"]["九品药材"]["尘磊岩麟果"])
            self.assertEqual({"自定义草": 25.0}, payload["unclassified"])
            self.assertEqual(25.0, payload["prices"]["自定义草"])

    def test_save_writes_nested_yaml_and_updates_flat_lookup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            optimizer, account_path = self.make_optimizer(temp_dir, {}, {})

            optimizer.set_herb_price_groups(
                {
                    "九品药材": {"尘磊岩麟果": 960},
                    "一品药材": {"清灵草": 12},
                }
            )

            saved = yaml.safe_load(account_path.read_text(encoding="utf-8"))
            self.assertEqual(960.0, saved["九品药材"]["尘磊岩麟果"])
            self.assertEqual(12.0, saved["一品药材"]["清灵草"])
            self.assertEqual(
                {"尘磊岩麟果": 960.0, "清灵草": 12.0},
                optimizer.herb_max_prices,
            )
            self.assertEqual({}, optimizer.unclassified_herb_prices)

    def test_invalid_save_does_not_change_memory_or_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            optimizer, account_path = self.make_optimizer(
                temp_dir,
                {"九品药材": {"旧药": 9}},
                {},
            )
            before_file = account_path.read_text(encoding="utf-8")
            before_prices = dict(optimizer.herb_max_prices)

            with self.assertRaisesRegex(ValueError, "重复"):
                optimizer.set_herb_price_groups(
                    {
                        "九品药材": {"同名药": 10},
                        "八品药材": {"同名药": 20},
                    }
                )

            self.assertEqual(before_file, account_path.read_text(encoding="utf-8"))
            self.assertEqual(before_prices, optimizer.herb_max_prices)

    def test_save_rejects_unknown_grade_empty_name_and_nonpositive_price(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            optimizer, _ = self.make_optimizer(temp_dir, {}, {})
            cases = (
                ({"十品药材": {"新药": 10}}, "未知药材品级"),
                ({"九品药材": {"  ": 10}}, "药材名不能为空"),
                ({"九品药材": {"新药": 0}}, "价格必须大于 0"),
            )

            for groups, message in cases:
                with self.subTest(message=message):
                    with self.assertRaisesRegex(ValueError, message):
                        optimizer.set_herb_price_groups(groups)

    def test_replace_failure_keeps_runtime_state_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            optimizer, account_path = self.make_optimizer(
                temp_dir,
                {"九品药材": {"旧药": 9}},
                {},
            )
            before_file = account_path.read_text(encoding="utf-8")
            before_groups = optimizer.get_herb_price_config()["groups"]
            before_prices = dict(optimizer.herb_max_prices)

            with patch(
                "astrbot_plugin_xiao_xiuxian_auto.auto_alchemy_optimizer.os.replace",
                side_effect=OSError("disk failure"),
            ):
                with self.assertRaisesRegex(OSError, "disk failure"):
                    optimizer.set_herb_price_groups({"一品药材": {"新药": 1}})

            self.assertEqual(before_file, account_path.read_text(encoding="utf-8"))
            self.assertEqual(before_groups, optimizer.get_herb_price_config()["groups"])
            self.assertEqual(before_prices, optimizer.herb_max_prices)


if __name__ == "__main__":
    unittest.main()
