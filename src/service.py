from __future__ import annotations

from typing import Any, Dict, Optional

from .domain import (ConflictError, ensure_role, normalize_severity,
                     require_number, require_text)
from .repository import Repository
from .rules import (AUDIT_ROLES, CREATE_ROLES, DISPATCH_ENTITY, DISPATCH_ROLES,
                    ENTITY, EQUIPMENT_ROLES, EVAC_CREATE_ROLES, EVAC_ENTITY,
                    RECORD_ROLES, REOPEN_ROLES, REPORT_ROLES, REPORT_STATUSES,
                    TITLE, VIEW_ROLES, completion_blockers, escalation_required,
                    evaluate_rollcall, priority_score, reopen_blockers,
                    response_deadline_hours, role_for_transition,
                    validate_transition)


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
        record = self.repository.add_record(item_id, kind, detail, status,
                                            external_ref, actor)
        self.repository.append_audit("record", ENTITY, item_id, actor, {
            "record_id": record["id"], "kind": kind, "status": status,
        })
        return record

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
            from .domain import ConflictError
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

    def _evacuation_detail(self, order: Dict[str, Any]) -> Dict[str, Any]:
        crews = self.repository.list_crews(order["id"])
        reports = self.repository.list_evacuation_reports(order["id"])
        reviews = self.repository.list_evacuation_reviews(order["id"])
        rollcall = evaluate_rollcall(
            crews, reports, {r["crew"]: r["status"] for r in reviews})
        result = dict(order)
        result["crews"] = crews
        result["reports"] = reports
        result["reviews"] = reviews
        result["rollcall"] = rollcall
        result["reopen_blockers"] = reopen_blockers(
            rollcall, bool(order["equipment_returned"]))
        return result

    def create_evacuation(self, payload: Dict[str, Any], actor: str,
                          role: str) -> Dict[str, Any]:
        ensure_role(role, EVAC_CREATE_ROLES)
        actor = require_text(actor, "actor", 100)
        order_no = require_text(payload.get("order_no"), "order_no", 100)
        area = require_text(payload.get("area"), "area", 200)
        reason = require_text(payload.get("reason"), "reason")
        raw_crews = payload.get("crews")
        if not isinstance(raw_crews, list) or not raw_crews:
            from .domain import ValidationError
            raise ValidationError("crews必须为非空班组名单")
        crews, seen = [], set()
        for raw_crew in raw_crews:
            crew = require_text(raw_crew, "crew", 100)
            if crew in seen:
                from .domain import ValidationError
                raise ValidationError(f"班组重复登记：{crew}")
            seen.add(crew)
            crews.append(crew)
        if self.repository.active_evacuation_for_area(area) is not None:
            raise ConflictError(f"任务区{area}已有进行中的撤离单")
        order = self.repository.create_evacuation_order(
            order_no, area, reason, crews, actor)
        self.repository.append_audit("evacuation_create", EVAC_ENTITY, order["id"], actor, {
            "order_no": order_no, "area": area, "crews": crews,
        })
        return self._evacuation_detail(order)

    def report_evacuation(self, order_no: str, payload: Dict[str, Any],
                          actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, REPORT_ROLES)
        actor = require_text(actor, "actor", 100)
        order = self.repository.get_evacuation_order_by_no(
            require_text(order_no, "order_no", 100))
        crew = require_text(payload.get("crew"), "crew", 100)
        status = payload.get("status")
        if status not in REPORT_STATUSES:
            from .domain import ValidationError
            raise ValidationError("status必须是arrived或missing")
        note = payload.get("note")
        if note is not None:
            note = require_text(note, "note")
        crews = self.repository.list_crews(order["id"])
        if crew not in crews:
            from .domain import ValidationError
            raise ValidationError(f"班组{crew}不在撤离单名册中")
        if order["status"] != "active":
            raise ConflictError("撤离单已重新开放，不再接收回报")
        reports = self.repository.list_evacuation_reports(order["id"])
        prior = next((r for r in reports if r["crew"] == crew), None)
        duplicate = prior is not None
        if duplicate:
            # 同号重复沿用首次结果；矛盾状态留待核对，只登记不改变点名判定
            if prior["status"] != status:
                self.repository.append_audit("evacuation_report_conflict",
                                             EVAC_ENTITY, order["id"], actor, {
                    "order_no": order["order_no"], "crew": crew,
                    "first_status": prior["status"], "conflict_status": status,
                })
                report = self.repository.add_evacuation_report(
                    order["id"], crew, status, note, actor)
                self.repository.append_audit("evacuation_report",
                                             EVAC_ENTITY, order["id"], actor, {
                    "order_no": order["order_no"], "crew": crew, "status": status,
                    "disputed": True,
                })
                detail = self._evacuation_detail(order)
                detail["report"] = report
                detail["duplicate"] = True
                detail["disputed"] = True
                return detail
            # 同号同状态：沿用首次结果，不重复落账
            detail = self._evacuation_detail(order)
            detail["report"] = prior
            detail["duplicate"] = True
            detail["disputed"] = False
            return detail
        report = self.repository.add_evacuation_report(
            order["id"], crew, status, note, actor)
        self.repository.append_audit("evacuation_report", EVAC_ENTITY, order["id"], actor, {
            "order_no": order["order_no"], "crew": crew, "status": status,
        })
        detail = self._evacuation_detail(order)
        detail["report"] = report
        detail["duplicate"] = False
        detail["disputed"] = False
        return detail

    def review_evacuation_crew(self, order_id: int, payload: Dict[str, Any],
                               actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, REOPEN_ROLES)
        actor = require_text(actor, "actor", 100)
        order = self.repository.get_evacuation_order(order_id)
        if order["status"] != "active":
            raise ConflictError("撤离单已重新开放，不再接收核对")
        crew = require_text(payload.get("crew"), "crew", 100)
        status = payload.get("status")
        if status not in REPORT_STATUSES:
            from .domain import ValidationError
            raise ValidationError("status必须是arrived或missing")
        note = payload.get("note")
        if note is not None:
            note = require_text(note, "note")
        if crew not in self.repository.list_crews(order_id):
            from .domain import ValidationError
            raise ValidationError(f"班组{crew}不在撤离单名册中")
        review = self.repository.add_evacuation_review(
            order_id, crew, status, note, actor)
        self.repository.append_audit("evacuation_review", EVAC_ENTITY, order_id, actor, {
            "order_no": order["order_no"], "crew": crew, "status": status,
        })
        detail = self._evacuation_detail(order)
        detail["review"] = review
        return detail

    def return_equipment(self, order_id: int, payload: Dict[str, Any], actor: str,
                         role: str) -> Dict[str, Any]:
        ensure_role(role, EQUIPMENT_ROLES)
        actor = require_text(actor, "actor", 100)
        order = self.repository.get_evacuation_order(order_id)
        if order["status"] != "active":
            raise ConflictError("撤离单已重新开放")
        if order["equipment_returned"]:
            raise ConflictError("装备已归还登记")
        updated = self.repository.set_equipment_returned(order_id, actor)
        self.repository.append_audit("evacuation_equipment", EVAC_ENTITY, order_id, actor, {
            "order_no": order["order_no"], "returned": True,
        })
        return self._evacuation_detail(updated)

    def reopen_evacuation(self, order_id: int, actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, REOPEN_ROLES)
        actor = require_text(actor, "actor", 100)
        order = self.repository.get_evacuation_order(order_id)
        detail = self._evacuation_detail(order)
        blockers = detail["reopen_blockers"]
        if blockers:
            raise ConflictError("；".join(blockers), details={
                "missing_crews": detail["rollcall"]["unaccounted"],
                "blockers": blockers,
            })
        updated = self.repository.reopen_evacuation_order(order_id, actor)
        self.repository.append_audit("evacuation_reopen", EVAC_ENTITY, order_id, actor, {
            "order_no": order["order_no"], "area": order["area"],
        })
        return self._evacuation_detail(updated)

    def get_evacuation(self, order_id: int, role: str) -> Dict[str, Any]:
        self._view(role)
        return self._evacuation_detail(self.repository.get_evacuation_order(order_id))

    def get_evacuation_by_no(self, order_no: str, role: str) -> Dict[str, Any]:
        self._view(role)
        return self._evacuation_detail(
            self.repository.get_evacuation_order_by_no(order_no))

    def list_evacuations(self, role: str, area: Optional[str] = None,
                         status: Optional[str] = None) -> list:
        self._view(role)
        return [self._evacuation_detail(order) for order in
                self.repository.list_evacuation_orders(area, status)]

    def create_dispatch(self, payload: Dict[str, Any], actor: str,
                        role: str) -> Dict[str, Any]:
        ensure_role(role, DISPATCH_ROLES)
        actor = require_text(actor, "actor", 100)
        area = require_text(payload.get("area"), "area", 200)
        title = require_text(payload.get("title"), "title", 200)
        active = self.repository.active_evacuation_for_area(area)
        if active is not None:
            detail = self._evacuation_detail(active)
            unaccounted = detail["rollcall"]["unaccounted"]
            if unaccounted:
                raise ConflictError(
                    f"任务区{area}封锁中，存在未核销人员，无法派单", details={
                        "area": area, "order_no": active["order_no"],
                        "missing_crews": unaccounted,
                    })
        dispatch = self.repository.create_dispatch(area, title, actor)
        self.repository.append_audit("dispatch_create", DISPATCH_ENTITY, dispatch["id"],
                                     actor, {"area": area, "title": title})
        return dispatch

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
