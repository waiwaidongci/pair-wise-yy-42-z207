import json, tempfile, threading, unittest, urllib.error, urllib.parse, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from src.http_api import make_handler
from src.repository import Repository
from src.service import Service
def request(url, method="GET", payload=None, actor="x", role="viewer"):
    data=json.dumps(payload).encode() if payload is not None else None
    req=urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type","application/json")
    req.add_header("X-Actor",actor); req.add_header("X-Role",role)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())
class EvacuationHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.repo=Repository(str(Path(cls.tmp.name)/"http.db"))
        cls.service=Service(cls.repo)
        handler=make_handler(cls.service,str(Path(__file__).resolve().parent.parent/"static"))
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),handler)
        cls.port=cls.server.server_address[1]
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
        cls.base=f"http://127.0.0.1:{cls.port}"
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.repo.close(); cls.tmp.cleanup()
    def _report(self,order_no,crew,status,actor="f1",role="field_commander"):
        return request(f"{self.base}/api/evacuations/by-no/{order_no}/reports","POST",
            {"crew":crew,"status":status},actor=actor,role=role)
    def test_evacuation_dispatch_conflict_and_reopen_flow(self):
        status,body=request(self.base+"/api/evacuations","POST",
            {"order_no":"EVAC-H1","area":"西坡","reason":"大风转向","crews":["甲班","乙班"]},
            actor="ic",role="incident_commander")
        self.assertEqual(status,201); self.assertEqual(body["rollcall"]["unaccounted"],["甲班","乙班"])
        oid=body["id"]
        status,_=self._report("EVAC-H1","甲班","arrived")
        self.assertEqual(status,201)
        status,body=request(self.base+"/api/dispatches","POST",
            {"area":"西坡","title":"增援"},actor="ic",role="incident_commander")
        self.assertEqual(status,409)
        self.assertEqual(body["details"]["missing_crews"],["乙班"])
        status,_=self._report("EVAC-H1","乙班","missing",actor="f2")
        self.assertEqual(status,201)
        status,body=request(f"{self.base}/api/evacuations/{oid}/reopen","POST",None,
            actor="ic",role="incident_commander")
        self.assertEqual(status,409); self.assertIn("乙班",body["details"]["missing_crews"])
        status,body=self._report("EVAC-H1","乙班","arrived",actor="f3")
        self.assertEqual(status,201); self.assertTrue(body["disputed"])
        status,body=request(f"{self.base}/api/evacuations/{oid}/reopen","POST",None,
            actor="ic",role="incident_commander")
        self.assertEqual(status,409)
        status,body=request(f"{self.base}/api/evacuations/{oid}/reviews","POST",
            {"crew":"乙班","status":"arrived","note":"现场核对到达"},actor="ic",role="incident_commander")
        self.assertEqual(status,201)
        status,body=request(f"{self.base}/api/evacuations/{oid}/equipment-return","POST",{},
            actor="lg",role="logistics")
        self.assertEqual(status,200)
        status,body=request(f"{self.base}/api/evacuations/{oid}/reopen","POST",None,
            actor="ic",role="incident_commander")
        self.assertEqual(status,200); self.assertEqual(body["status"],"reopened")
        status,body=request(self.base+"/api/dispatches","POST",
            {"area":"西坡","title":"复燃巡查"},actor="ic",role="incident_commander")
        self.assertEqual(status,201)
    def test_duplicate_report_idempotent_and_listing(self):
        status,body=request(self.base+"/api/evacuations","POST",
            {"order_no":"EVAC-H2","area":"南沟","reason":"大风转向","crews":["丙班"]},
            actor="ic",role="incident_commander")
        self.assertEqual(status,201); oid=body["id"]
        status,first=self._report("EVAC-H2","丙班","arrived")
        self.assertEqual(status,201)
        status,again=self._report("EVAC-H2","丙班","arrived")
        self.assertEqual(status,201)
        self.assertTrue(again["duplicate"]); self.assertEqual(again["report"]["id"],first["report"]["id"])
        status,body=request(self.base+"/api/evacuations?area="+urllib.parse.quote("南沟"))
        self.assertEqual(status,200); self.assertTrue(any(e["id"]==oid for e in body["evacuations"]))
if __name__=="__main__": unittest.main()
