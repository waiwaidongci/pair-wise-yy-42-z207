import unittest
from src import rules
from src.domain import ConflictError, ValidationError
class RulesTest(unittest.TestCase):
    def test_priority_deadline_and_escalation(self):
        low=rules.priority_score(rules.SEVERITIES[0],1,10,0); high=rules.priority_score(rules.SEVERITIES[-1],30,10,3)
        self.assertGreater(high,low); self.assertLessEqual(rules.response_deadline_hours(rules.SEVERITIES[-1],30,10),rules.response_deadline_hours(rules.SEVERITIES[0],1,10))
        self.assertTrue(rules.escalation_required(rules.SEVERITIES[-1],1,10)); self.assertTrue(rules.escalation_required(rules.SEVERITIES[0],10,10))
    def test_transition_guards(self):
        self.assertTrue(rules.can_transition(rules.STATES[0],rules.STATES[1]))
        with self.assertRaises(ConflictError): rules.validate_transition(rules.STATES[0],rules.STATES[-1])
        with self.assertRaises(ValidationError): rules.priority_score("not-a-severity",1,1)
    def test_rollcall_first_result_wins_and_conflict_pending(self):
        crews=['A','B','C','D']
        reports=[
            {'crew':'A','status':'arrived'},
            {'crew':'B','status':'missing'},
            {'crew':'B','status':'arrived'},
            {'crew':'C','status':'arrived'},
            {'crew':'C','status':'missing'},
            {'crew':'A','status':'arrived'},
        ]
        roll=rules.evaluate_rollcall(crews,reports)
        self.assertEqual(roll['arrived'],['A','C'])
        self.assertEqual(roll['missing'],['B'])
        self.assertEqual(roll['silent'],['D'])
        self.assertEqual(roll['disputed'],['B','C'])
        self.assertFalse(roll['accounted'])
        self.assertEqual(roll['unaccounted'],['B','C','D'])
        reported=[{'crew':c,'status':'arrived'} for c in crews]
        done=rules.evaluate_rollcall(crews,reported)
        self.assertTrue(done['accounted'])
        self.assertEqual(done['unaccounted'],[])
        reviewed=rules.evaluate_rollcall(crews,reports,{'B':'arrived','C':'arrived','D':'arrived'})
        self.assertEqual(reviewed['disputed'],[])
        self.assertTrue(reviewed['accounted'])
        still=rules.evaluate_rollcall(crews,reports,{'B':'missing'})
        self.assertIn('B',still['unaccounted']); self.assertNotIn('B',still['disputed'])
    def test_rollcall_unknown_crew_and_reopen_blockers(self):
        roll=rules.evaluate_rollcall(['A'],[{'crew':'X','status':'arrived'}])
        self.assertEqual(roll['unknown'],['X'])
        self.assertFalse(roll['accounted'])
        blockers=rules.reopen_blockers(roll,True)
        self.assertTrue(any('未登记班组' in b for b in blockers))
        clear=rules.evaluate_rollcall(['A'],[{'crew':'A','status':'arrived'}])
        self.assertEqual(rules.reopen_blockers(clear,True),[])
        self.assertTrue(rules.reopen_blockers(clear,False))
if __name__=="__main__": unittest.main()
