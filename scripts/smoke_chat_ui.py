"""Browser UI regression with in-memory chat fixtures; no LLM or native tools run.

Run using a Python environment with Playwright and Chromium installed, while the
local application is running: python scripts/smoke_chat_ui.py
"""
import argparse
import json
from urllib.parse import parse_qs, urlsplit
from pathlib import Path
from playwright.sync_api import sync_playwright


def main(base, browser_channel=None):
    sessions, submissions, attachments = {}, [], {}
    running = [True]
    approval_waiting, approval_recorded, approvals = [False], [False], []
    native_fixture = [None]
    native_run = {
        'run_id': 'run_abcdef12', 'layout_version': 2, 'status': 'RUNNING', 'current_agent': 'A3', 'design_generation': 1,
        'raw_input': {}, 'grid_config': {'col_height_mm': 5000}, 'readiness': {},
        'agent_statuses': {'A1': {'status': 'PASSED'}, 'A2': {'status': 'PASSED'}, 'A3': {'status': 'PASSED'}},
        'frame_candidate': {'members': [{'name': 'column-0-0', 'type': 'column', 'profile': 'CFRHS120X120X5',
            'material': 'S355JR', 'class': '11', 'p1': [0,0,0], 'p2': [0,0,5000]}],
            'summary': {'total_members': 1, 'members_by_type': {'column': 1}, 'effective_col_height_mm': 5000, 'beam_height_mm': 133}},
    }
    native_runs = {native_run['run_id']: native_run}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **({'channel': browser_channel} if browser_channel else {}))
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value:{writeText:async text=>{window.copiedChat=text}}}); Object.defineProperty(navigator,'share',{value:undefined});")
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))

        def route_chat(route):
            req = route.request
            path = req.url.split('/api/v2/chat/')[-1]
            if path == 'config':
                return route.fulfill(json={"enabled": True, "model": "controlled-browser-fixture"})
            if path == 'sessions' and req.method == 'POST':
                ident = 'chat_fixture_' + str(len(sessions) + 1)
                sessions[ident] = {"id": ident, "title": "New chat", "project_input": {}, "run_id": None, "turns": []}
                return route.fulfill(status=201, json=sessions[ident])
            if path == 'sessions':
                return route.fulfill(json={"sessions": [{"id": s['id'], "title": s['title'], "running": running[0] and bool(s['turns'])} for s in sessions.values()]})
            if path.startswith('sessions/'):
                session = sessions[path.split('/')[1]]
                if '/attachments' in path:
                    if req.method == 'DELETE':
                        ident = path.split('/')[-1]; attachments.pop(ident)
                        return route.fulfill(json={'removed':ident})
                    name = parse_qs(urlsplit(req.url).query)['filename'][0]
                    ident = 'file_fixture_' + str(len(attachments) + 1)
                    value = {'id':ident,'session':session['id'],'name':name,'kind':name.split('.')[-1],'size':len(req.post_data_buffer),'status':'readable','notice':''}
                    if name.endswith('.json'):
                        value['project_input'] = json.loads(req.post_data_buffer)
                    attachments[ident] = value
                    return route.fulfill(status=201,json=value)
                if req.method == 'PATCH':
                    session['title'] = req.post_data_json['title']
                if session['turns'] and not running[0] and session['turns'][0]['message'] != 'Run agentic workflow':
                    session['turns'][0].update(status='COMPLETED', assistant='Original conversation finished.')
                used = {f['id'] for t in session['turns'] for f in t.get('attachments',[])}
                return route.fulfill(json={**session,'draft_attachments':[f for f in attachments.values() if f['session']==session['id'] and f['id'] not in used]})
            if path == 'messages':
                payload = req.post_data_json
                submissions.append(payload)
                session = sessions[payload['session_id']]
                session.update(project_input=payload['project_input'], title=payload['message'])
                turn = {"id": 'turn_' + session['id'] + '_' + str(len(session['turns'])+1), "message": payload['message'], "status": "RUNNING", "events": [], "assistant": "", "error": None, "run_id": None,'attachments':[attachments[i] for i in payload.get('attachment_ids',[])]}
                if payload['message'] in ('Generate a structure fixture', 'Run agentic workflow'):
                    turn['run_id'] = native_run['run_id']
                    session['run_id'] = native_run['run_id']
                    native_run['raw_input'] = payload['project_input']
                session['turns'].append(turn)
                return route.fulfill(status=202, json={"session_id":session['id'],"turn_id":turn['id'],"status":"RUNNING"})
            if path.startswith('turns/'):
                session, turn = next((s,t) for s in sessions.values() for t in s['turns'] if t['id'] == path.split('/')[1])
                if turn['message'] == 'Run agentic workflow' and approval_waiting[0]:
                    text = 'A1–A7 finished processing. Waiting for manual engineer approval.'
                    if approval_recorded[0]:
                        text += '\n\nDesign accepted and signed off. This run request is completed; continue in the same conversation.'
                    turn.update(status='COMPLETED' if approval_recorded[0] else 'AWAITING_APPROVAL', assistant=text)
                elif not running[0]:
                    turn.update(status='COMPLETED', assistant='Original conversation finished.', events=[{"sequence":1,"type":"tool_completed","tool":"get_project_context","result":{"kind":"MOCK_UI_ONLY"}}])
                if turn['message'] in ('Generate a structure fixture', 'Run agentic workflow'):
                    # The progress endpoint follows the session's current action,
                    # even before the enclosing chat turn has finished.
                    turn['run_id'] = native_run['run_id']
                    session['run_id'] = native_run['run_id']
                return route.fulfill(json={**turn, "project_input":session['project_input']})
            raise AssertionError('Unexpected chat request ' + path)

        page.route('**/api/v2/chat/**', route_chat)
        page.route('**/api/v2/agentic/active', lambda r: r.fulfill(content_type='application/json', body=json.dumps(native_fixture[0])))
        page.route('**/api/v2/agentic/runs/run_abcdef*', lambda r: r.fulfill(json=native_runs[r.request.url.rsplit('/',1)[-1]]))
        page.goto(base + '/radiator_support', wait_until='networkidle')
        page.wait_for_function('Boolean(window.structuralChatBridge)')
        original_input = page.evaluate('window.structuralChatBridge.getInput()')
        page.route('**/api/v2/agentic/runs/run_12345678', lambda r: r.fulfill(json={"raw_input":original_input}))
        page.evaluate("window.structuralChatBridge.getRunId = () => 'run_12345678'")
        page.locator('#structural-chat-toggle').click()
        page.wait_for_function("!document.querySelector('#structural-chat button[type=submit]').disabled")
        assert page.locator('#structural-chat input[type=checkbox]').count() == 0
        assert page.get_by_role('button', name='Export chat', exact=True).count() == 0
        assert page.locator('#structural-chat .sc-source').count() == 0
        assert page.get_by_role('button', name='Settings', exact=True).count() == 0
        page.get_by_role('textbox', name='Chat message').fill('Review this frame')
        page.get_by_role('button', name='Send', exact=True).click()
        page.get_by_role('button', name='Stop turn').wait_for()
        page.wait_for_function("document.querySelector('.sc-title').textContent==='Review this frame'")
        assert submissions[0]['allow_native_tools'] is True
        assert 'grid' in submissions[0]['project_input']
        assert submissions[0]['run_id'] == 'run_12345678'
        original = next(iter(sessions))
        page.get_by_role('button', name='New chat', exact=True).click()
        page.wait_for_function("!document.querySelector('#structural-chat button[type=submit]').disabled")
        assert len(sessions) == 2
        assert sessions[original]['turns'][0]['status'] == 'RUNNING'
        assert page.locator('#structural-chat .sc-message.user').count() == 0
        assert page.locator('#generate-btn').is_disabled(), 'Background chat must retain its manual-action lock'
        running[0] = False
        page.wait_for_timeout(1400)
        page.wait_for_function("!document.getElementById('generate-btn').disabled")
        assert 'Original conversation finished.' not in page.locator('#structural-chat .sc-history').inner_text()
        page.get_by_role('button', name='Chat history', exact=True).click()
        page.get_by_role('button', name='Review this frame', exact=True).click()
        page.get_by_text('Original conversation finished.', exact=False).wait_for()
        page.get_by_role('button', name='Chat options').click()
        page.get_by_role('menuitem', name='Rename', exact=True).click()
        page.get_by_role('textbox', name='Chat title').fill('8x5 connection review')
        page.get_by_role('button', name='Save', exact=True).click()
        page.wait_for_function("document.querySelector('.sc-title').textContent==='8x5 connection review'")
        assert sessions[original]['title'] == '8x5 connection review'
        page.get_by_role('button', name='Chat options').click()
        page.get_by_role('menuitem', name='Copy chat', exact=True).click()
        page.wait_for_function("window.copiedChat && window.copiedChat.includes('Original conversation finished.')")
        page.get_by_role('button', name='Chat options').click()
        page.get_by_role('menuitem', name='Share', exact=True).click()
        page.wait_for_function("window.copiedChat && window.copiedChat.includes('?chat=')")
        shared_url = page.evaluate('window.copiedChat')
        page.goto(shared_url, wait_until='networkidle')
        page.get_by_role('button', name='Engineering chat', exact=True).click()
        page.get_by_text('Original conversation finished.', exact=False).wait_for()
        page.get_by_role('button', name='New chat', exact=True).click()
        page.wait_for_function("!document.querySelector('#structural-chat button[type=submit]').disabled")
        before_import = page.evaluate('window.structuralChatBridge.getInput()')
        page.get_by_role('button', name='Attach files').click()
        # File input remains hidden behind the + action.
        page.locator('#structural-chat input[type=file]').set_input_files({"name":"frame.json","mimeType":"application/json","buffer":json.dumps({"grid":{"n_long_bays":8,"n_trans_bays":5}}).encode()})
        page.get_by_text('Attached frame.json', exact=True).wait_for()
        assert page.evaluate('window.structuralChatBridge.getInput().grid.n_long_bays') == 8
        page.get_by_role('button',name='Remove frame.json').click()
        page.wait_for_function("document.querySelector('.sc-attachments').hidden")
        assert page.evaluate('window.structuralChatBridge.getInput()') == before_import
        page.locator('#structural-chat input[type=file]').set_input_files({"name":"frame.json","mimeType":"application/json","buffer":json.dumps({"grid":{"n_long_bays":8,"n_trans_bays":5}}).encode()})
        page.get_by_text('Attached frame.json', exact=True).wait_for()
        page.locator('#structural-chat input[type=file]').set_input_files([
            {'name':'design-notes.pdf','mimeType':'application/pdf','buffer':b'%PDF controlled UI fixture'},
            {'name':'workflow.svg','mimeType':'image/svg+xml','buffer':b'<svg><text>Frame check</text></svg>'}])
        page.wait_for_function("document.querySelectorAll('.sc-attachments .sc-file-card').length===3 && !document.querySelector('#structural-chat button[type=submit]').disabled")
        page.get_by_role('button',name='Remove design-notes.pdf').click()
        page.wait_for_function("document.querySelectorAll('.sc-attachments .sc-file-card').length===2")
        output = Path('output_chat_validation'); output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/'chat-attachments-desktop.png'))
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(output/'chat-attachments-mobile.png'))
        assert page.locator('#structural-chat .sc-input').bounding_box()['height'] < 80
        page.set_viewport_size({'width':1440,'height':1000})
        page.locator('#n_long_bays').evaluate('(field, value) => { field.value = value; }', original_input['grid']['n_long_bays'] + 1)
        page.get_by_role('textbox', name='Chat message').fill('Review updated inputs')
        page.get_by_role('button', name='Send', exact=True).click()
        page.get_by_text('Response complete', exact=True).wait_for()
        assert 'run_id' not in submissions[-1], 'Changed inputs must not bind old solver evidence'
        names = [attachments[i]['name'] for i in submissions[-1]['attachment_ids']]
        assert names == ['frame.json','workflow.svg'], names
        assert page.locator('.sc-attachments .sc-file-card').count()==0
        assert page.locator('.sc-sent-files .sc-file-card').count()==2
        page.get_by_role('button', name='Chat options').click()
        assert page.locator('#structural-chat .sc-input').bounding_box()['height'] < 80
        page.screenshot(path=str(output / 'chat-redesign-desktop.png'))
        page.set_viewport_size({"width":390,"height":844})
        assert page.locator('#structural-chat').bounding_box()['width'] <= 390
        page.screenshot(path=str(output / 'chat-redesign-mobile.png'))
        page.set_viewport_size({'width':1440,'height':1000})
        page.get_by_role('button', name='New chat', exact=True).click()
        page.wait_for_function("!document.querySelector('#structural-chat button[type=submit]').disabled")
        page.evaluate('window.structuralChatBridge.getRunId = () => null')
        running[0] = True
        native_fixture[0] = native_run
        page.get_by_role('textbox', name='Chat message').fill('Generate a structure fixture')
        page.get_by_role('button', name='Send', exact=True).click()
        page.wait_for_function("document.getElementById('active-run-id').textContent.includes('run_abcdef12')")
        assert page.locator('#hud').is_visible(), 'Intermediate geometry should render before the chat response completes'
        assert page.locator('#generate-btn').is_disabled()
        assert page.locator('#new-run-btn').is_disabled()
        assert page.get_by_role('button', name='Stop turn').is_visible()

        def advance_candidate(run_id, agent, active=True):
            native_runs[native_run['run_id']] = json.loads(json.dumps(native_run))
            native_run.update(run_id=run_id, current_agent=agent, status='RUNNING' if active else 'READY')
            native_run['agent_statuses'][agent] = {'status': 'RUNNING' if active else 'PASSED'}
            native_runs[run_id] = json.loads(json.dumps(native_run))
            native_fixture[0] = native_run if active else None

        advance_candidate('run_abcdef13', 'A4')
        page.wait_for_function("document.getElementById('active-run-id').textContent.includes('run_abcdef13')")
        assert page.locator('#step-A4').evaluate("node => node.classList.contains('running')")
        native_run['agent_statuses']['A4'] = {'status':'PASSED'}
        native_run['staad_results'] = {'created':1, 'candidate_signature':'fixture-frame', 'analysis_complete':True,
            'member_data_by_id': {'1': {'name':'column-0-0','class':'11','profile':'CFRHS120X120X5',
                'material':'S355JR','start':1,'end':2,'p1':[0,0,0],'p2':[0,5000,0]}}}
        native_run['ideastatica_results'] = {'fixture_family': {'connection_id':1,'passed':True}}
        advance_candidate('run_abcdef14', 'A5')
        page.wait_for_function("document.getElementById('active-run-id').textContent.includes('run_abcdef14')")
        assert page.locator('#step-A5').evaluate("node => node.classList.contains('running')")
        assert page.locator('#staad-results-panel').is_visible()
        assert page.locator('#idea-results-content').inner_text().find('fixture_family') >= 0
        assert page.get_by_role('button', name='Stop turn').is_visible(), 'Results must appear before the chat reply'

        # The next candidate completes between polls while the LLM is still busy.
        native_run['agent_statuses']['A5'] = {'status':'PASSED'}
        advance_candidate('run_abcdef15', 'A5c', active=False)
        page.wait_for_function("document.getElementById('active-run-id').textContent.includes('run_abcdef15')")
        assert page.locator('#generate-btn').is_disabled(), 'Pending chat retains ownership between actions'
        running[0] = False
        page.get_by_text('Response complete', exact=True).wait_for()
        page.wait_for_function("!document.getElementById('generate-btn').disabled")
        page.screenshot(path=str(output/'chat-live-geometry-fixture.png'))

        # Exercise the ordinary Run button independently of chat dispatch.
        button_request = [None]
        page.route('**/api/v2/agentic/run', lambda r: button_request.__setitem__(0, r))
        advance_candidate('run_abcdef16', 'A3')
        page.locator('#agentic-run-btn').click()
        page.wait_for_function("document.getElementById('active-run-id').textContent.includes('run_abcdef16')")
        assert button_request[0] is not None
        assert page.locator('#generate-btn').is_disabled()
        assert page.locator('#hud').is_visible()
        native_run['current_agent'] = 'A5'
        native_run['agent_statuses'].update(A3={'status':'PASSED'}, A4={'status':'PASSED'}, A5={'status':'RUNNING'})
        page.wait_for_function("document.getElementById('step-A5').classList.contains('running')")
        assert page.locator('#staad-results-panel').is_visible()
        native_run.update(status='PAUSED_AT_GATE3', current_agent='A7',
            final_report={'summary':{'staad_max_stress_ratio':0.2,'package_integrity_hash':'fixture'}})
        native_run['agent_statuses'].update(A5={'status':'PASSED'}, A5c={'status':'PASSED'},
            A6={'status':'PASSED'}, A7={'status':'AWAITING_APPROVAL'})
        native_fixture[0] = None
        button_request[0].fulfill(json=native_run)
        page.wait_for_function("!document.getElementById('generate-btn').disabled")
        assert page.locator('#gate3-banner').is_visible()
        page.screenshot(path=str(output/'button-live-geometry-fixture.png'))

        # The full chat request stays unfinished until a real manual click.
        page.get_by_role('button', name='New chat', exact=True).click()
        page.wait_for_function("!document.querySelector('#structural-chat button[type=submit]').disabled")
        advance_candidate('run_abcdef17', 'A3')
        running[0] = True
        page.get_by_role('textbox', name='Chat message').fill('Run agentic workflow')
        page.get_by_role('button', name='Send', exact=True).click()
        page.wait_for_function("document.getElementById('active-run-id').textContent.includes('run_abcdef17')")
        full_session = submissions[-1]['session_id']
        for agent in ('A3','A4','A5','A5c','A6'):
            native_run['agent_statuses'][agent] = {'status':'PASSED'}
        native_run['agent_statuses']['A7'] = {'status':'AWAITING_APPROVAL'}
        native_run.update(status='PAUSED_AT_GATE3',current_agent='A7',gate3_approved=False)
        native_runs[native_run['run_id']] = json.loads(json.dumps(native_run))
        native_fixture[0] = None; running[0] = False; approval_waiting[0] = True
        page.wait_for_function("document.querySelector('.sc-progress').textContent.includes('Waiting for engineer approval')")
        page.wait_for_function("!document.getElementById('gate3-accept-btn').disabled")
        assert page.locator('#gate3-accept-btn').is_enabled(), 'Pending chat must release the manual approval control'
        assert page.get_by_role('button', name='Send', exact=True).is_disabled()
        assert not approvals, 'No acceptance request may be sent automatically'
        page.screenshot(path=str(output/'chat-awaiting-manual-approval.png'))

        # Reload preserves the waiting request and its conversation, without a model replay.
        requests_before_reload = len(submissions)
        page.reload(wait_until='networkidle')
        page.get_by_role('button', name='Engineering chat', exact=True).click()
        page.wait_for_function("document.querySelector('.sc-progress').textContent.includes('Waiting for engineer approval')")
        page.wait_for_function("!document.getElementById('gate3-accept-btn').disabled")
        assert page.evaluate("localStorage.getItem('structuralChatSession')") == full_session, {
            'expected_session':full_session,'actual_session':page.evaluate("localStorage.getItem('structuralChatSession')"),'url':page.url}
        assert len(submissions) == requests_before_reload
        assert page.locator('#gate3-accept-btn').is_enabled()

        def accept_fixture(route):
            assert route.request.method == 'POST'
            approvals.append(route.request.post_data_json)
            native_run.update(status='COMPLETED',gate3_approved=True,accepted_by='Lead Structural Engineer')
            native_run['agent_statuses']['A7'] = {'status':'PASSED'}
            native_runs[native_run['run_id']] = json.loads(json.dumps(native_run))
            approval_recorded[0] = True
            route.fulfill(json=native_run)
        page.route('**/api/v2/agentic/accept/run_abcdef17', accept_fixture)
        page.locator('#gate3-accept-btn').click()
        page.get_by_text('Response complete', exact=True).wait_for()
        page.wait_for_function("!document.querySelector('#structural-chat button[type=submit]').disabled")
        assert len(approvals) == 1
        assert 'Design accepted and signed off' in page.locator('.sc-history').inner_text()
        assert page.locator('#step-A7').evaluate("node=>node.classList.contains('passed')")
        page.get_by_role('textbox', name='Chat message').fill('What is the latest result?')
        page.get_by_role('button', name='Send', exact=True).click()
        page.wait_for_function("document.querySelectorAll('#structural-chat .sc-message.user').length===2")
        page.get_by_text('Response complete', exact=True).wait_for()
        assert submissions[-1]['session_id'] == full_session
        assert len(sessions[full_session]['turns']) == 2
        page.screenshot(path=str(output/'chat-approved-same-conversation.png'))
        assert not errors, errors
        (output / 'chat-redesign-smoke.json').write_text(json.dumps({"kind":"Browser with mocked chat and engineering APIs; no external requests or FEA", "removed_controls":True,"automatic_inputs":True,"matching_run_selected":True,"stale_run_not_bound":True,"new_chat_while_running":True,"background_turn_lock":True,"live_geometry_before_response":True,"multi_action_run_id_transitions":True,"quick_action_between_polls":True,"live_staad_and_idea_before_response":True,"button_live_geometry_and_results":True,"button_gate3_after_completion":True,"chat_waits_for_manual_approval":True,"manual_approval_enabled":True,"waiting_survives_reload":True,"approval_updates_chat_once":True,"continue_same_conversation":True,"worker_completion_unlock":True,"no_cross_chat_response":True,"reopen_history":True,"rename":True,"copy_chat":True,"share_link_restore":True,"json_attachment":True,"file_cards":True,"remove_file":True,"remove_restores_form":True,"removed_file_not_sent":True,"multiple_formats":True,"mobile":True,"page_errors":errors},indent=2))
        browser.close()
        print('PASS: compact chat, automatic inputs, saved history, new chat during run, rename/share/copy, JSON and mobile layout')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8060')
    parser.add_argument('--browser-channel', help='Installed Playwright browser channel, e.g. msedge.')
    args = parser.parse_args()
    main(args.base_url.rstrip('/'), args.browser_channel)
