from __future__ import annotations

from typing import Any, Dict, Optional

from .domain import (ConflictError, ValidationError, ensure_role,
                     normalize_severity, require_number, require_text)
from .repository import Repository
from .rules import (AUDIT_ROLES, CREATE_ROLES, ENTITY, EVAC_ENTITY,
                    EVACUATE_ROLES, RECORD_ROLES, REOPEN_ROLES, REPORT_ROLES,
                    TERMINAL_STATES, VIEW_ROLES, accounted_teams,
                    classify_report, completion_blockers,
                    equipment_pending_teams, escalation_required,
                    normalize_report_status, normalize_teams, priority_score,
                    reopen_blockers, response_deadline_hours,
                    role_for_transition, unaccounted_teams, validate_transition)


class Service:
    def __init__(self, repository: Repository):
        self.repository = repository

    def _view(self, role: str) -> None:
        ensure_role(role, VIEW_ROLES)

    def create_item(self, payload: Dict[str, Any], actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, CREATE_ROLES)
        actor = require_text(actor, "actor", 100)
        title = require_text(payload.get("title"), "title", 200)
        description = require_text(payload.get("description"), "description")
        severity = normalize_severity(payload.get("severity"))
        quantity = require_number(payload.get("quantity", 0), "quantity")
        threshold = require_number(payload.get("threshold", 1), "threshold", 0.000001)
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        item = self.repository.create_item(title, description, severity, quantity,
                                           threshold, external_ref, actor)
        self.repository.append_audit("create", ENTITY, item["id"], actor, {
            "title": title, "severity": severity, "quantity": quantity,
            "priority": priority_score(severity, quantity, threshold),
        })
        return self.enrich(item)

    def add_record(self, item_id: int, payload: Dict[str, Any], actor: str,
                   role: str) -> Dict[str, Any]:
        ensure_role(role, RECORD_ROLES)
        actor = require_text(actor, "actor", 100)
        kind = require_text(payload.get("kind"), "kind", 100)
        detail = require_text(payload.get("detail"), "detail")
        status = payload.get("status", "open")
        if status not in ("open", "closed"):
            raise ValueError("status必须是open或closed")
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        zone = payload.get("zone")
        if zone is not None:
            zone = require_text(zone, "zone", 200)
        if kind == "dispatch":
            if zone is None:
                raise ValidationError("派单必须指定任务区zone")
            self._check_dispatch_zone(item_id, zone)
        record = self.repository.add_record(item_id, kind, detail, status,
                                            external_ref, actor, zone)
        self.repository.append_audit("record", ENTITY, item_id, actor, {
            "record_id": record["id"], "kind": kind, "status": status,
        })
        return record

    def _check_dispatch_zone(self, item_id: int, zone: str) -> None:
        missing: list = []
        for evacuation in self.repository.list_blocking_evacuations(item_id, zone):
            reports = self.repository.list_reports(evacuation["id"])
            for team in unaccounted_teams(evacuation["teams"], reports):
                if team not in missing:
                    missing.append(team)
        if missing:
            raise ConflictError(
                f"任务区{zone}封锁中，存在未核销班组：" + "、".join(missing),
                details={"zone": zone, "missing_teams": missing})

    def transition(self, item_id: int, target: str, expected_version: int,
                   actor: str, role: str) -> Dict[str, Any]:
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        validate_transition(item["status"], target)
        ensure_role(role, role_for_transition(target))
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError("expected_version必须是正整数")
        blockers = completion_blockers(target, self.repository.open_record_count(item_id))
        if blockers:
            raise ConflictError("；".join(blockers))
        updated = self.repository.transition_item(item_id, target, expected_version, actor)
        self.repository.append_audit("transition", ENTITY, item_id, actor, {
            "from": item["status"], "to": target,
            "escalation_required": escalation_required(
                item["severity"], item["quantity"], item["threshold"]),
        })
        return self.enrich(updated)

    def get_item(self, item_id: int, role: str) -> Dict[str, Any]:
        self._view(role)
        return self.enrich(self.repository.get_item(item_id))

    def list_items(self, role: str, status: Optional[str] = None) -> list:
        self._view(role)
        return [self.enrich(item) for item in self.repository.list_items(status)]

    def list_records(self, item_id: int, role: str) -> list:
        self._view(role)
        return self.repository.list_records(item_id)

    def audit(self, role: str, item_id: Optional[int] = None) -> list:
        ensure_role(role, AUDIT_ROLES)
        return self.repository.list_audit(item_id)

    # ---- 撤离点名与重新开放 ----
    def create_evacuation(self, item_id: int, payload: Dict[str, Any], actor: str,
                          role: str) -> Dict[str, Any]:
        ensure_role(role, EVACUATE_ROLES)
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        if item["status"] in TERMINAL_STATES:
            raise ConflictError("事件已关闭，无法登记撤离单")
        zone = require_text(payload.get("zone"), "zone", 200)
        teams = normalize_teams(payload.get("teams"))
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        evacuation = self.repository.create_evacuation(item_id, zone, teams,
                                                       external_ref, actor)
        self.repository.append_audit("evacuate", EVAC_ENTITY, item_id, actor, {
            "evacuation_id": evacuation["id"], "zone": zone, "teams": teams,
        })
        return self.enrich_evacuation(evacuation, [])

    def report_rollcall(self, evacuation_id: int, payload: Dict[str, Any], actor: str,
                        role: str) -> Dict[str, Any]:
        ensure_role(role, REPORT_ROLES)
        actor = require_text(actor, "actor", 100)
        evacuation = self.repository.get_evacuation(evacuation_id)
        if evacuation["status"] != "blocked":
            raise ConflictError("撤离单已重新开放，无法继续点名")
        team = require_text(payload.get("team"), "team", 100)
        if team not in evacuation["teams"]:
            raise ValidationError("班组不在撤离单登记范围内")
        report_ref = require_text(payload.get("report_ref"), "report_ref", 100)
        status = normalize_report_status(payload.get("status"))
        equipment_returned = payload.get("equipment_returned", False)
        if not isinstance(equipment_returned, bool):
            raise ValidationError("equipment_returned必须是布尔值")
        existing = self.repository.get_report_by_ref(evacuation_id, report_ref)
        if existing is not None:
            return self._apply_existing_report(evacuation, existing, team, status,
                                               equipment_returned, actor)
        try:
            report = self.repository.add_report(evacuation_id, team, report_ref,
                                                status, equipment_returned, actor)
        except ConflictError:
            # 并发同号：按已落账的首次结果重新判定
            existing = self.repository.get_report_by_ref(evacuation_id, report_ref)
            return self._apply_existing_report(evacuation, existing, team, status,
                                               equipment_returned, actor)
        self.repository.append_audit("rollcall", EVAC_ENTITY, evacuation["item_id"],
                                     actor, {
            "evacuation_id": evacuation_id, "report_id": report["id"], "team": team,
            "status": status, "equipment_returned": equipment_returned,
        })
        return self._report_view(report, "recorded")

    def _apply_existing_report(self, evacuation: Dict[str, Any], existing: Dict[str, Any],
                               team: str, status: str, equipment_returned: bool,
                               actor: str) -> Dict[str, Any]:
        action = classify_report(existing, team, status, equipment_returned)
        if action == "reuse":
            return self._report_view(existing, "reused")
        if action == "supplement":
            report = self.repository.mark_equipment_returned(existing["id"])
            self.repository.append_audit("rollcall_supplement", EVAC_ENTITY,
                                         evacuation["item_id"], actor, {
                "evacuation_id": evacuation["id"], "report_id": report["id"],
                "team": report["team"],
            })
            return self._report_view(report, "supplemented")
        report = self.repository.mark_report_conflict(existing["id"], {
            "team": team, "status": status, "equipment_returned": equipment_returned,
            "actor": actor,
        })
        self.repository.append_audit("rollcall_conflict", EVAC_ENTITY,
                                     evacuation["item_id"], actor, {
            "evacuation_id": evacuation["id"], "report_id": report["id"],
            "team": team, "status": status,
        })
        return self._report_view(report, "conflict")

    @staticmethod
    def _report_view(report: Dict[str, Any], result: str) -> Dict[str, Any]:
        view = dict(report)
        view["result"] = result
        return view

    def reopen_evacuation(self, evacuation_id: int, actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, REOPEN_ROLES)
        actor = require_text(actor, "actor", 100)
        evacuation = self.repository.get_evacuation(evacuation_id)
        reports = self.repository.list_reports(evacuation_id)
        blockers = reopen_blockers(evacuation["teams"], reports)
        if blockers:
            raise ConflictError("；".join(blockers))
        updated = self.repository.reopen_evacuation(evacuation_id, actor)
        self.repository.append_audit("reopen", EVAC_ENTITY, updated["item_id"], actor, {
            "evacuation_id": evacuation_id, "zone": updated["zone"],
        })
        return self.enrich_evacuation(updated, reports)

    def get_evacuation(self, evacuation_id: int, role: str) -> Dict[str, Any]:
        self._view(role)
        evacuation = self.repository.get_evacuation(evacuation_id)
        return self.enrich_evacuation(evacuation,
                                      self.repository.list_reports(evacuation_id))

    def list_evacuations(self, item_id: int, role: str) -> list:
        self._view(role)
        return [self.enrich_evacuation(evacuation,
                                       self.repository.list_reports(evacuation["id"]))
                for evacuation in self.repository.list_evacuations(item_id)]

    @staticmethod
    def enrich_evacuation(evacuation: Dict[str, Any], reports: list) -> Dict[str, Any]:
        result = dict(evacuation)
        teams = evacuation["teams"]
        result["reports"] = reports
        result["accounted_teams"] = accounted_teams(teams, reports)
        result["missing_teams"] = unaccounted_teams(teams, reports)
        result["equipment_pending_teams"] = equipment_pending_teams(teams, reports)
        result["pending_review"] = [r["id"] for r in reports if r["needs_review"]]
        result["reopen_blockers"] = (reopen_blockers(teams, reports)
                                     if evacuation["status"] == "blocked" else [])
        return result

    @staticmethod
    def enrich(item: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(item)
        result["priority"] = priority_score(
            item["severity"], item["quantity"], item["threshold"])
        result["deadline_hours"] = response_deadline_hours(
            item["severity"], item["quantity"], item["threshold"])
        result["escalation_required"] = escalation_required(
            item["severity"], item["quantity"], item["threshold"])
        return result
