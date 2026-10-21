from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import exists, inspect, select
from sqlalchemy.orm import Session

from common.models import CareGap, Measure, Member, SyntheaRecord


class Repository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_member(self, member_id: str) -> Member | None:
        return self.session.get(Member, member_id)

    def get_care_gap(self, care_gap_id: str) -> CareGap | None:
        return self.session.get(CareGap, care_gap_id)

    def get_measure(self, measure_id: str) -> Measure | None:
        return self.session.get(Measure, measure_id)

    def list_care_gaps(self) -> list[CareGap]:
        statement = select(CareGap).order_by(CareGap.care_gap_id.asc())
        return list(self.session.scalars(statement))

    def get_supporting_records(
        self,
        member_id: str,
        care_gap_id: str,
    ) -> list[SyntheaRecord]:
        statement = (
            select(SyntheaRecord)
            .where(
                SyntheaRecord.member_id == member_id,
                (SyntheaRecord.care_gap_id == care_gap_id)
                | (SyntheaRecord.care_gap_id.is_(None)),
            )
            .order_by(SyntheaRecord.source_date.asc())
        )
        return list(self.session.scalars(statement))

    def add_synthea_record(
        self,
        *,
        record_id: str,
        member_id: str,
        care_gap_id: str | None,
        source_type: str,
        source_date: dt.date,
        fact_key: str,
        fact_value: str | None,
        provenance: str,
    ) -> SyntheaRecord:
        record = SyntheaRecord(
            record_id=record_id,
            member_id=member_id,
            care_gap_id=care_gap_id,
            source_type=source_type,
            source_date=source_date,
            fact_key=fact_key,
            fact_value=fact_value,
            provenance=provenance,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def synthea_record_exists(
        self,
        *,
        member_id: str,
        care_gap_id: str | None,
        source_type: str,
        fact_key: str,
        provenance: str,
    ) -> bool:
        statement = select(
            exists().where(
                SyntheaRecord.member_id == member_id,
                SyntheaRecord.care_gap_id == care_gap_id,
                SyntheaRecord.source_type == source_type,
                SyntheaRecord.fact_key == fact_key,
                SyntheaRecord.provenance == provenance,
            )
        )
        return bool(self.session.execute(statement).scalar())


def row_to_dict(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    mapper = inspect(row).mapper
    return {
        attribute.key: getattr(row, attribute.key)
        for attribute in mapper.column_attrs
    }
