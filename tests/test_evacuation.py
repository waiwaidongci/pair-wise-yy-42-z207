import tempfile, unittest
from pathlib import Path
from src.domain import ConflictError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service


class EvacuationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repository(str(Path(self.tmp.name) / "test.db"))
        self.service = Service(self.repo)
        self.item = self.service.create_item(
            {"title": "wind shift incident", "description": "task area evacuation",
             "severity": "high", "quantity": 12, "threshold": 6,
             "external_ref": "EVAC-ITEM"}, "creator", "field_commander")
        self.evac = self.service.create_evacuation(
            self.item["id"],
            {"zone": "North Ridge", "teams": ["Alpha", "Bravo"],
             "external_ref": "EV-1"}, "commander", "incident_commander")

    def tearDown(self):
        self.repo.close(); self.tmp.cleanup()

    def _report(self, team, ref, status="accounted", equipment=True,
                actor="team-lead", role="field_commander"):
        return self.service.report_rollcall(
            self.evac["id"],
            {"team": team, "report_ref": ref, "status": status,
             "equipment_returned": equipment}, actor, role)

    def _dispatch(self, zone="North Ridge", detail="send crew"):
        return self.service.add_record(
            self.item["id"],
            {"kind": "dispatch", "detail": detail, "status": "open", "zone": zone},
            "dispatcher", "logistics")

    def test_dispatch_blocked_rollcall_then_reopen(self):
        # 封锁区有人未核销：新派单冲突并列出缺失班组
        with self.assertRaises(ConflictError) as ctx:
            self._dispatch()
        self.assertEqual(ctx.exception.details["missing_teams"], ["Alpha", "Bravo"])
        self.assertIn("North Ridge", str(ctx.exception))
        # Alpha 回报到达且装备归还
        first = self._report("Alpha", "RA-1")
        self.assertEqual(first["result"], "recorded")
        # 同号重复沿用首次结果，不产生新台账记录
        again = self._report("Alpha", "RA-1")
        self.assertEqual(again["result"], "reused")
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(len(self.repo.list_reports(self.evac["id"])), 1)
        # Bravo 仍缺失：派单冲突只列 Bravo
        with self.assertRaises(ConflictError) as ctx2:
            self._dispatch()
        self.assertEqual(ctx2.exception.details["missing_teams"], ["Bravo"])
        # 人员未核销齐全前不能重新开放
        with self.assertRaises(ConflictError) as ctx3:
            self.service.reopen_evacuation(self.evac["id"], "commander", "incident_commander")
        self.assertIn("班组未核销", str(ctx3.exception))
        # Bravo 先报到但装备未归还：人员已核销，派单放行
        self._report("Bravo", "RB-1", equipment=False)
        record = self._dispatch(detail="recon")
        self.assertEqual(record["zone"], "North Ridge")
        # 装备未归还：重新开放仍冲突
        with self.assertRaises(ConflictError) as ctx4:
            self.service.reopen_evacuation(self.evac["id"], "commander", "incident_commander")
        self.assertIn("装备未归还", str(ctx4.exception))
        # Bravo 同号同状态补报装备归还
        supplemented = self._report("Bravo", "RB-1", equipment=True)
        self.assertEqual(supplemented["result"], "supplemented")
        self.assertTrue(supplemented["equipment_returned"])
        self.assertEqual(len(self.repo.list_reports(self.evac["id"])), 2)
        # 人员核销且装备归还：重新开放
        reopened = self.service.reopen_evacuation(
            self.evac["id"], "commander", "incident_commander")
        self.assertEqual(reopened["status"], "reopened")
        self.assertEqual(reopened["reopened_by"], "commander")
        # 重复重新开放冲突
        with self.assertRaises(ConflictError):
            self.service.reopen_evacuation(self.evac["id"], "commander", "incident_commander")
        # 重新开放后停止点名
        with self.assertRaises(ConflictError):
            self._report("Alpha", "RA-2")
        # 撤离单不再封锁：派单放行
        self._dispatch(detail="resume operations")
        # 审计链完整
        actions = [e["action"] for e in self.service.audit("viewer", self.item["id"])]
        for expected in ("evacuate", "rollcall", "rollcall_supplement", "reopen"):
            self.assertIn(expected, actions)
        self.assertTrue(self.repo.verify_audit_chain())

    def test_conflicting_status_kept_for_review(self):
        first = self._report("Alpha", "RA-9", status="accounted", equipment=True)
        # 同号矛盾状态：留待核对，首次结果不变
        conflict = self._report("Alpha", "RA-9", status="missing", equipment=False,
                                actor="other", role="logistics")
        self.assertEqual(conflict["result"], "conflict")
        self.assertTrue(conflict["needs_review"])
        self.assertEqual(conflict["status"], "accounted")
        self.assertEqual(conflict["conflict_detail"]["status"], "missing")
        self.assertEqual(len(self.repo.list_reports(self.evac["id"])), 1)
        # 已归还改口未归还同样视为矛盾
        downgrade = self._report("Alpha", "RA-9", status="accounted", equipment=False)
        self.assertEqual(downgrade["result"], "conflict")
        self.assertTrue(self.repo.get_report(first["id"])["equipment_returned"])
        # 台账视图暴露待核对记录
        detail = self.service.get_evacuation(self.evac["id"], "viewer")
        self.assertEqual(detail["pending_review"], [first["id"]])
        # 首次结果为已核销，矛盾不推翻点名结论
        self.assertEqual(detail["missing_teams"], ["Bravo"])

    def test_permissions_and_validation(self):
        with self.assertRaises(PermissionDenied):
            self.service.create_evacuation(
                self.item["id"], {"zone": "Z", "teams": ["A"]}, "x", "viewer")
        with self.assertRaises(PermissionDenied):
            self._report("Alpha", "RA-1", role="viewer")
        with self.assertRaises(PermissionDenied):
            self.service.reopen_evacuation(self.evac["id"], "x", "viewer")
        # 未登记班组不能回报
        with self.assertRaises(ValidationError):
            self._report("Charlie", "RC-1")
        with self.assertRaises(ValidationError):
            self._report("Alpha", "RA-1", status="unknown")
        with self.assertRaises(ValidationError):
            self.service.create_evacuation(
                self.item["id"], {"zone": "Z", "teams": []}, "commander",
                "incident_commander")
        # 派单必须指定任务区
        with self.assertRaises(ValidationError):
            self.service.add_record(
                self.item["id"], {"kind": "dispatch", "detail": "no zone"},
                "dispatcher", "logistics")
        # 其他任务区不受封锁影响，非派单记录也不受影响
        self._dispatch(zone="South Ridge", detail="elsewhere")
        self.service.add_record(
            self.item["id"], {"kind": "evidence", "detail": "photo"},
            "recorder", "field_commander")


if __name__ == "__main__":
    unittest.main()
