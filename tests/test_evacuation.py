import tempfile, unittest
from pathlib import Path
from src.domain import ConflictError, PermissionDenied, ValidationError
from src.repository import Repository
from src.service import Service
class EvacuationTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.repo=Repository(str(Path(self.tmp.name)/"test.db")); self.service=Service(self.repo)
        self.order=self.service.create_evacuation(
            {"order_no":"EVAC-1","area":"东岭","reason":"大风转向","crews":["A班","B班","C班"]},
            "指挥1","incident_commander")
    def tearDown(self): self.repo.close(); self.tmp.cleanup()
    def _arrived(self,crew,actor="队长",role="field_commander"):
        return self.service.report_evacuation("EVAC-1",{"crew":crew,"status":"arrived"},actor,role)
    def test_create_roles_and_duplicate_crew_registration(self):
        with self.assertRaises(PermissionDenied):
            self.service.create_evacuation({"order_no":"EVAC-X","area":"北沟","reason":"x","crews":["Z班"]},"c","viewer")
        with self.assertRaises(ValidationError):
            self.service.create_evacuation({"order_no":"EVAC-2","area":"北沟","reason":"x","crews":["Z班","Z班"]},"c","incident_commander")
        with self.assertRaises(ConflictError):
            self.service.create_evacuation({"order_no":"EVAC-3","area":"东岭","reason":"x","crews":["Z班"]},"c","incident_commander")
    def test_duplicate_same_number_keeps_first_result(self):
        first=self._arrived("A班")
        self.assertFalse(first["duplicate"])
        again=self._arrived("A班")
        self.assertTrue(again["duplicate"]); self.assertFalse(again["disputed"])
        self.assertEqual(again["report"]["id"],first["report"]["id"])
        self.assertEqual(len(self.repo.list_evacuation_reports(self.order["id"])),1)
        rc=again["rollcall"]; self.assertEqual(rc["arrived"],["A班"]); self.assertEqual(rc["unaccounted"],["B班","C班"])
    def test_conflicting_status_pending_review(self):
        self._arrived("B班")
        conflict=self.service.report_evacuation("EVAC-1",{"crew":"B班","status":"missing"},"队2","logistics")
        self.assertTrue(conflict["duplicate"]); self.assertTrue(conflict["disputed"])
        rc=conflict["rollcall"]
        self.assertEqual(rc["arrived"],["B班"])
        self.assertIn("B班",rc["disputed"]); self.assertIn("B班",rc["unaccounted"])
    def test_report_validation_and_closed_order(self):
        with self.assertRaises(ValidationError):
            self.service.report_evacuation("EVAC-1",{"crew":"外星班","status":"arrived"},"队","field_commander")
        with self.assertRaises(ValidationError):
            self.service.report_evacuation("EVAC-1",{"crew":"A班","status":"ok"},"队","field_commander")
        with self.assertRaises(PermissionDenied):
            self.service.report_evacuation("EVAC-1",{"crew":"A班","status":"arrived"},"队","viewer")
        self._arrived("A班"); self._arrived("B班"); self._arrived("C班")
        self.service.return_equipment(self.order["id"],{},"后勤","logistics")
        self.service.reopen_evacuation(self.order["id"],"指挥1","incident_commander")
        with self.assertRaises(ConflictError):
            self._arrived("A班")
    def test_dispatch_conflict_lists_missing_crews(self):
        self._arrived("A班"); self._arrived("B班")
        with self.assertRaises(ConflictError) as ctx:
            self.service.create_dispatch({"area":"东岭","title":"新扑打任务"},"指挥1","incident_commander")
        self.assertEqual(ctx.exception.details["missing_crews"],["C班"])
        self.assertEqual(ctx.exception.details["order_no"],"EVAC-1")
    def test_reopen_requires_accounted_and_equipment(self):
        self._arrived("A班"); self._arrived("B班"); self._arrived("C班")
        with self.assertRaises(ConflictError):
            self.service.reopen_evacuation(self.order["id"],"指挥1","incident_commander")
        with self.assertRaises(PermissionDenied):
            self.service.return_equipment(self.order["id"],{},"队3","field_commander")
        self.service.return_equipment(self.order["id"],{},"后勤","logistics")
        reopened=self.service.reopen_evacuation(self.order["id"],"指挥1","incident_commander")
        self.assertEqual(reopened["status"],"reopened")
        self.assertEqual(reopened["reopen_blockers"],[])
    def test_dispatch_allowed_after_reopen(self):
        self._arrived("A班"); self._arrived("B班")
        with self.assertRaises(ConflictError):
            self.service.create_dispatch({"area":"东岭","title":"t"},"指挥1","incident_commander")
        self._arrived("C班")
        self.service.return_equipment(self.order["id"],{},"后勤","logistics")
        self.service.reopen_evacuation(self.order["id"],"指挥1","incident_commander")
        dispatch=self.service.create_dispatch({"area":"东岭","title":"复燃巡查"},"指挥1","incident_commander")
        self.assertEqual(dispatch["area"],"东岭")
    def test_review_resolves_disputed_crew(self):
        self._arrived("A班"); self._arrived("B班"); self._arrived("C班")
        self.service.report_evacuation("EVAC-1",{"crew":"B班","status":"missing"},"队2","logistics")
        detail=self.service.get_evacuation(self.order["id"],"viewer")
        self.assertIn("B班",detail["rollcall"]["disputed"])
        self.service.return_equipment(self.order["id"],{},"后勤","logistics")
        with self.assertRaises(ConflictError):
            self.service.reopen_evacuation(self.order["id"],"指挥1","incident_commander")
        with self.assertRaises(PermissionDenied):
            self.service.review_evacuation_crew(self.order["id"],{"crew":"B班","status":"arrived"},"队","field_commander")
        reviewed=self.service.review_evacuation_crew(self.order["id"],{"crew":"B班","status":"arrived","note":"现场核对到达"},"指挥1","incident_commander")
        self.assertNotIn("B班",reviewed["rollcall"]["unaccounted"])
        with self.assertRaises(ConflictError):
            self.service.review_evacuation_crew(self.order["id"],{"crew":"B班","status":"arrived"},"指挥1","incident_commander")
        reopened=self.service.reopen_evacuation(self.order["id"],"指挥1","incident_commander")
        self.assertEqual(reopened["status"],"reopened")
    def test_missing_and_silent_crews_block_until_reported(self):
        self._arrived("A班")
        self.service.report_evacuation("EVAC-1",{"crew":"B班","status":"missing"},"队2","field_commander")
        detail=self.service.get_evacuation(self.order["id"],"viewer")
        rc=detail["rollcall"]
        self.assertEqual(rc["missing"],["B班"]); self.assertEqual(rc["silent"],["C班"])
        self.assertEqual(rc["unaccounted"],["B班","C班"])
        self.assertTrue(self.repo.verify_audit_chain())
if __name__=="__main__": unittest.main()
