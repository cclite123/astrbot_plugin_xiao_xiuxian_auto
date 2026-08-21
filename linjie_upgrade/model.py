"""灵界查询快照与 ROI 候选模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


PAGE_KINDS = ("profile", "buildings", "upgrades", "workers")
PLAN_ALGORITHM_VERSION = 5
QUERY_COMMANDS = (
    "灵界我的信息",
    "灵界建筑列表",
    "灵界升级列表",
    "灵界杂役名录",
)


@dataclass(frozen=True)
class DisplayOutput:
    base: float
    bonus: float = 0.0

    @property
    def total(self) -> float:
        return self.base + self.bonus


@dataclass(frozen=True)
class Building:
    name: str
    count: int
    output: DisplayOutput | None
    next_cost: int | None
    locked: bool


@dataclass(frozen=True)
class BuildingUpgrade:
    name: str
    level: int
    cost: int | None
    available: bool


@dataclass(frozen=True)
class WorkerPosition:
    name: str
    workers: int
    capacity: int
    output: DisplayOutput
    single_output: DisplayOutput
    next_cost: int | None


@dataclass(frozen=True)
class ProfilePage:
    balance: int
    total_output: DisplayOutput
    skill_dao: int
    skill_realm: int
    worker_total: int
    has_monthly_card: bool
    raw_text: str


@dataclass(frozen=True)
class BuildingsPage:
    balance: int
    total_output: DisplayOutput
    buildings: tuple[Building, ...]
    raw_text: str


@dataclass(frozen=True)
class UpgradesPage:
    balance: int
    upgrades: tuple[BuildingUpgrade, ...]
    raw_text: str


@dataclass(frozen=True)
class WorkersPage:
    balance: int
    worker_total: int
    worker_capacity: int
    worker_rank: int
    rank_cost: int
    workers: tuple[WorkerPosition, ...]
    raw_text: str


@dataclass(frozen=True)
class LinjieSnapshot:
    account_id: str
    group_id: str
    balance: int
    total_output: DisplayOutput
    skill_dao: int
    skill_realm: int
    has_monthly_card: bool
    buildings: tuple[Building, ...]
    upgrades: tuple[BuildingUpgrade, ...]
    worker_total: int
    worker_capacity: int
    worker_rank: int
    rank_cost: int
    workers: tuple[WorkerPosition, ...]
    collected_at: str
    source_texts: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "LinjieSnapshot":
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("灵界快照字段无效")
        values = dict(data)
        values["total_output"] = DisplayOutput(**values["total_output"])
        values["buildings"] = tuple(
            Building(
                **{
                    **item,
                    "output": DisplayOutput(**item["output"]) if item["output"] is not None else None,
                }
            )
            for item in values["buildings"]
        )
        values["upgrades"] = tuple(BuildingUpgrade(**item) for item in values["upgrades"])
        values["workers"] = tuple(
            WorkerPosition(
                **{
                    **item,
                    "output": DisplayOutput(**item["output"]),
                    "single_output": DisplayOutput(**item["single_output"]),
                }
            )
            for item in values["workers"]
        )
        snapshot = cls(**values)
        if not snapshot.account_id or not snapshot.group_id or snapshot.balance < 0:
            raise ValueError("灵界快照内容无效")
        if set(snapshot.source_texts) != set(PAGE_KINDS):
            raise ValueError("灵界快照来源不完整")
        return snapshot


@dataclass(frozen=True)
class LinjieCandidate:
    kind: str
    name: str
    cost: int
    gain: float
    command: str
    note: str
    available_after_seconds: int = 0
    projected_balance_after: float = 0.0
    route_name: str | None = None
    route_target_count: int | None = None
    route_target_level: int | None = None
    amount: int = 1

    @property
    def roi_days(self) -> float:
        return self.cost / self.gain / 86400
