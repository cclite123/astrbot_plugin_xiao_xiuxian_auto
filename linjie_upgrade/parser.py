"""严格解析四类灵界官方查询页面。"""

from __future__ import annotations

import re

from .model import (
    Building,
    BuildingsPage,
    BuildingUpgrade,
    DisplayOutput,
    ProfilePage,
    UpgradesPage,
    WorkerPosition,
    WorkersPage,
)


AMOUNT_UNITS = {"": 1, "万": 10_000, "亿": 100_000_000, "兆": 1_000_000_000_000, "京": 10_000_000_000_000_000}


def parse_amount(value: str) -> int:
    match = re.search(r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>[万亿兆京]?)", str(value or "").replace(",", ""))
    if match is None:
        raise ValueError("金额缺少有效数值")
    return round(float(match.group("number")) * AMOUNT_UNITS[match.group("unit")])


def parse_output(value: str) -> DisplayOutput:
    text = str(value or "").replace("**", "")
    base_match = re.search(r"(\d+(?:\.\d+)?)\s*([万亿兆京]?)", text)
    if base_match is None:
        raise ValueError("产出缺少基础值")
    base = float(base_match.group(1)) * AMOUNT_UNITS[base_match.group(2)]
    bonus_match = re.search(r"👑\s*\+\s*(\d+(?:\.\d+)?)\s*([万亿兆京]?)", text)
    bonus = 0.0
    if bonus_match is not None:
        bonus = float(bonus_match.group(1)) * AMOUNT_UNITS[bonus_match.group(2)]
    return DisplayOutput(base=base, bonus=bonus)


class LinjiePageParser:
    def parse_profile(self, text: str) -> ProfilePage:
        value = str(text or "")
        self._require(value, "个人面板", "技艺道行", "技艺境界", "灵矿石储备", "总产出")
        return ProfilePage(
            balance=self._amount_after(value, "灵矿石储备"),
            total_output=self._output_after(value, "总产出"),
            skill_dao=self._integer_after(value, "技艺道行"),
            skill_realm=self._integer_after(value, "技艺境界"),
            worker_total=self._integer_match(value, r"杂役[：:]\s*总计\s*(\d+)人"),
            has_monthly_card="👑+" in value.replace("**", "").replace(" ", ""),
            raw_text=value,
        )

    def parse_buildings(self, text: str) -> BuildingsPage:
        value = str(text or "")
        self._require(value, "建筑列表", "|建筑名称|拥有|建造|单产|下一个价格|", "总建筑数", "总产出", "灵矿石储备")
        rows: dict[str, Building] = {}
        for cells in self._table_rows(value, 5):
            name, owned, _, output, price = cells
            match = re.fullmatch(r"×\s*(\d+)", owned)
            if match is None:
                raise ValueError("建筑拥有数量格式无效")
            locked = "🔒" in price
            item = Building(
                name=name,
                count=int(match.group(1)),
                output=None if output.strip() == "—" else parse_output(output),
                next_cost=None if locked else parse_amount(price),
                locked=locked,
            )
            self._add_unique(rows, name, item)
        if not rows:
            raise ValueError("建筑列表没有建筑行")
        return BuildingsPage(
            balance=self._amount_after(value, "灵矿石储备"),
            total_output=self._output_after(value, "总产出"),
            buildings=tuple(rows.values()),
            raw_text=value,
        )

    def parse_upgrades(self, text: str) -> UpgradesPage:
        value = str(text or "")
        self._require(value, "可升级建筑", "|建筑名称|建筑等级|升级价格|升级|", "灵矿石储备")
        rows: dict[str, BuildingUpgrade] = {}
        for cells in self._table_rows(value, 4):
            name, level, price, action = cells
            if not level.isdigit():
                raise ValueError("建筑等级格式无效")
            cost = None if price.strip() == "—" else parse_amount(price)
            self._add_unique(rows, name, BuildingUpgrade(name, int(level), cost, cost is not None and "条件不足" not in action))
        return UpgradesPage(self._amount_after(value, "灵矿石储备"), tuple(rows.values()), value)

    def parse_workers(self, text: str) -> WorkersPage:
        value = str(text or "")
        self._require(value, "杂役概览", "杂役总数", "杂役等阶", "|建筑名称|在岗/岗位|招募|产出加成|杂役单产|下一个价格|", "杂役升阶")
        totals = re.search(r"杂役总数[：:]\s*(\d+)\s*/\s*(\d+)人", value)
        rank = re.search(r"杂役等阶[：:]\s*LV\s*(\d+)", value, re.I)
        rank_cost = re.search(r"杂役升阶[^\n]*?需要\s*([\d.]+\s*[万亿兆京]?)\s*灵矿石", value)
        if totals is None or rank is None or rank_cost is None:
            raise ValueError("杂役概览字段不完整")
        rows: dict[str, WorkerPosition] = {}
        for cells in self._table_rows(value, 6):
            name, slots, _, output, single, price = cells
            slot_match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", slots)
            if slot_match is None:
                raise ValueError("杂役岗位格式无效")
            item = WorkerPosition(
                name=name,
                workers=int(slot_match.group(1)),
                capacity=int(slot_match.group(2)),
                output=parse_output(output),
                single_output=parse_output(single),
                next_cost=parse_amount(price),
            )
            self._add_unique(rows, name, item)
        return WorkersPage(
            balance=self._amount_after(value, "灵矿石储备"),
            worker_total=int(totals.group(1)),
            worker_capacity=int(totals.group(2)),
            worker_rank=int(rank.group(1)),
            rank_cost=parse_amount(rank_cost.group(1)),
            workers=tuple(rows.values()),
            raw_text=value,
        )

    @staticmethod
    def _require(text: str, *markers: str) -> None:
        if any(marker not in text for marker in markers):
            raise ValueError("灵界页面类型或字段不完整")

    @staticmethod
    def _add_unique(items: dict[str, object], name: str, item: object) -> None:
        previous = items.get(name)
        if previous is not None and previous != item:
            raise ValueError(f"灵界页面包含冲突的{name}数据")
        items[name] = item

    @staticmethod
    def _table_rows(text: str, count: int) -> list[list[str]]:
        rows: list[list[str]] = []
        for line in text.splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [cell.strip().replace("**", "") for cell in line.strip().strip("|").split("|")]
            if len(cells) != count or cells[0] == "建筑名称" or all(set(cell) <= {":", "-"} for cell in cells):
                continue
            rows.append(cells)
        return rows

    @staticmethod
    def _integer_match(text: str, pattern: str) -> int:
        match = re.search(pattern, text)
        if match is None:
            raise ValueError("灵界整数状态缺失")
        return int(match.group(1))

    def _integer_after(self, text: str, label: str) -> int:
        return self._integer_match(text, rf"{re.escape(label)}[：:]\s*(\d+)")

    @staticmethod
    def _amount_after(text: str, label: str) -> int:
        match = re.search(rf"{re.escape(label)}[：:]\s*([\d.]+\s*[万亿兆京]?)", text)
        if match is None:
            raise ValueError(f"{label}缺失")
        return parse_amount(match.group(1))

    @staticmethod
    def _output_after(text: str, label: str) -> DisplayOutput:
        match = re.search(rf"{re.escape(label)}[：:]\s*([^\n]+?)灵矿石/秒", text)
        if match is None:
            raise ValueError(f"{label}缺失")
        return parse_output(match.group(1))
