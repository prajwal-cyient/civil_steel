"""Regression checks for native profile lookup and rules before input approval."""
import ast
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from langchain_core.messages import AIMessage
from chatbot.policy_requests import requested_policy_values, require_requested_policy
import test_chat as chat_fixtures
from test_chat import ControlledModel, call


class ProfileLookupTests(unittest.TestCase):
    def lookup(self, library, parametric):
        path = Path(__file__).resolve().parents[1] / 'src/agents/A6_tekla/tekla_open_api.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == '_get_profile_parameter')
        # Load only the production lookup function; importing the module loads native DLLs.
        namespace = {'Optional': __import__('typing').Optional,
                     'LibraryProfileItem': library, 'ParametricProfileItem': parametric}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        return namespace['_get_profile_parameter']

    def test_exact_parametric_names_supply_live_dimensions_to_a3(self):
        from agents.A3_generate_structure import validate_profile_catalogue
        calls = []
        class Library:
            def Select(self, name):
                calls.append(('library', name))
                self.aProfileItemParameters = [SimpleNamespace(Property='HEIGHT', Value=133.),
                                               SimpleNamespace(Property='WIDTH', Value=140.)]
                return name == 'HEA140'
        class Parametric:
            def Select(self, name):
                calls.append(('parametric', name))
                size = {'CFRHS100X100X4': 100., 'CFRHS120X120X5': 120.}.get(name)
                self.aProfileItemParameters = [SimpleNamespace(Property='HEIGHT', Value=size),
                                               SimpleNamespace(Property='WIDTH', Value=size)]
                return size is not None
        lookup = self.lookup(Library, Parametric)
        module = SimpleNamespace(get_profile_height=lambda name: lookup(name, 'HEIGHT'),
                                 get_profile_width=lambda name: lookup(name, 'WIDTH'))
        with patch.dict(sys.modules, {'agents.A6_tekla.tekla_open_api': module}):
            validate_profile_catalogue({'column_profile':'CFRHS120X120X5', 'beam_profile':'HEA140',
                'longitudinal_brace_profile':'CFRHS100X100X4', 'transverse_brace_profile':'CFRHS100X100X4'})
            with self.assertRaises(ValueError):
                validate_profile_catalogue(dict.fromkeys(['column_profile','beam_profile',
                    'longitudinal_brace_profile','transverse_brace_profile'], 'MISSING'))
        self.assertIn(('parametric', 'CFRHS100X100X4'), calls)
        self.assertIn(('parametric', 'CFRHS120X120X5'), calls)
        self.assertNotIn(('parametric', 'HEA140'), calls)


class ExplicitPolicyTests(unittest.TestCase):
    def test_supported_numbers_and_ambiguous_values(self):
        text = ('Run an 8x5 bay frame. Bolt utilization 90%. Weld utilization 85%. '
                'Plate limit 95%. Local deformation utilization 80%. '
                'Member ratio <= 0.8; drift <= H/300.')
        values = requested_policy_values(text)
        self.assertEqual(values, {'max_bolt_utilization_percent':90., 'max_weld_utilization_percent':85.,
            'max_plate_utilization_percent':95., 'max_deformation_utilization_percent':80.,
            'max_member_ratio':0.8, 'drift_denominator':300.})
        for text in ['Bolt utilization 90%. Bolt utilization 80%.', 'Bolt utilization 101%.']:
            with self.assertRaises(ValueError): requested_policy_values(text)
        self.assertEqual(requested_policy_values('Run the current 8x5 inputs using defaults.'), {})

    def test_missing_or_wrong_policy_cannot_use_defaults(self):
        session = {'project_input':{'grid':{}}, '_requested_run_policy':{'max_bolt_utilization_percent':90.}}
        for policy in [{}, {'max_bolt_utilization_percent':75.}]:
            with self.assertRaisesRegex(ValueError, 'Do not substitute default'):
                require_requested_policy(session, policy)
        require_requested_policy(session, {'max_bolt_utilization_percent':90.})


class ChatPolicyBindingTests(unittest.IsolatedAsyncioTestCase):
    setUp = chat_fixtures.ChatTests.setUp
    tearDown = chat_fixtures.ChatTests.tearDown
    respond = chat_fixtures.ChatTests.respond
    async def test_early_preview_rejected_then_correct_policy_can_retry(self):
        model = ControlledModel(call('update_project_inputs', {'grid_updates':{'n_long_bays':10}}),
            call('set_next_run_policy', {'run_policy':{'max_bolt_utilization_percent':90.}}, 'policy'),
            call('update_project_inputs', {'grid_updates':{'n_long_bays':10}}, 'preview'))
        turn = await self.respond(model, 'Change to a 10x5 bay frame and run A1-A8. '
            'Use maximum bolt utilization 90% for this run only. Show the approval preview first.', live=True)
        self.assertEqual(turn['outcome'], 'CHANGE_APPROVAL_REQUIRED')
        self.assertTrue(any(e['type']=='tool_error' and e.get('tool')=='update_project_inputs' for e in turn['events']))
        plan = turn['change_proposals'][0]
        self.assertEqual(plan['run_policy']['max_bolt_utilization_percent'], 90.)
        self.assertEqual(plan['requested_run_policy'], {'max_bolt_utilization_percent':90.})
        self.assertEqual(plan['candidate']['grid']['n_long_bays'], 10)
        self.assertEqual(self.store.session(self.session['id'])['project_input']['grid']['n_long_bays'], 8)
        # Approval is deterministic and loads a fresh session, without ephemeral
        # request fields. The queued policy must still reach the actual A1/A8.
        from chatbot.chatbot import ChatRuntime
        from orchestration.state import WorkflowState
        from agents.A1_requirements import run_a1_requirements
        from agents.A7_reporting import run_a8_reporting
        async def execute(data, run_id, *, chat_parameter_rerun=False):
            self.assertTrue(chat_parameter_rerun)
            state = run_a8_reporting(run_a1_requirements(WorkflowState(run_id=run_id, raw_input=data)))
            self.workflows.save_state(state)
            return state
        controller = SimpleNamespace(run_id=None, execute=AsyncMock(side_effect=execute))
        approved = self.store.transition_plan(plan['id'], self.session['id'], 'PENDING', 'EXECUTING')
        approval_turn = self.store.new_turn(self.session['id'], 'Apply approved change')
        with patch('orchestration.execution.CONTROLLER', controller), patch('chatbot.chatbot.WORKFLOW_STORE', self.workflows):
            await ChatRuntime(self.store).execute_approved(approval_turn, approved, True)
        self.assertEqual(approval_turn['status'], 'AWAITING_APPROVAL', approval_turn)
        saved = self.workflows.get_state(approval_turn['run_id'])
        self.assertEqual(saved.final_report['run_policy']['max_bolt_utilization_percent'], 90.)
        self.assertEqual(saved.grid_config['n_long_bays'], 10)
        self.assertNotIn('next_run_policy', self.store.session(self.session['id']))

    async def test_omitting_policy_does_not_create_preview_or_run(self):
        model = ControlledModel(call('update_project_inputs', {'grid_updates':{'n_long_bays':10}}),
                                AIMessage(content='The requested policy must be set before a preview.'))
        turn = await self.respond(model, 'Run a 10x5 frame with bolt utilization 90%.', live=True)
        self.assertFalse(turn.get('change_proposals'))
        self.assertEqual(self.store.session(self.session['id'])['project_input']['grid']['n_long_bays'], 8)


if __name__ == '__main__':
    unittest.main()
