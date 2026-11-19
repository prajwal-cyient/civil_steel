"""Browser UI regression with in-memory chat fixtures; no LLM or native tools run.

Run using a Python environment with Playwright and Chromium installed, while the
local application is running: python scripts/smoke_chat_ui.py
"""
import argparse
import json
from urllib.parse import parse_qs, urlsplit
from pathlib import Path
from playwright.sync_api import sync_playwright


def main(base):
    sessions, submissions, attachments = {}, [], {}
    running = [True]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
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
                if session['turns'] and not running[0]:
                    session['turns'][0].update(status='COMPLETED', assistant='Original conversation finished.')
                used = {f['id'] for t in session['turns'] for f in t.get('attachments',[])}
                return route.fulfill(json={**session,'draft_attachments':[f for f in attachments.values() if f['session']==session['id'] and f['id'] not in used]})
            if path == 'messages':
                payload = req.post_data_json
                submissions.append(payload)
                session = sessions[payload['session_id']]
                session.update(project_input=payload['project_input'], title=payload['message'])
                turn = {"id": 'turn_' + session['id'], "message": payload['message'], "status": "RUNNING", "events": [], "assistant": "", "error": None, "run_id": None,'attachments':[attachments[i] for i in payload.get('attachment_ids',[])]}
                session['turns'].append(turn)
                return route.fulfill(status=202, json={"session_id":session['id'],"turn_id":turn['id'],"status":"RUNNING"})
            if path.startswith('turns/'):
                session = next(s for s in sessions.values() if s['turns'] and s['turns'][0]['id'] == path.split('/')[1])
                turn = session['turns'][0]
                if not running[0]:
                    turn.update(status='COMPLETED', assistant='Original conversation finished.', events=[{"sequence":1,"type":"tool_completed","tool":"get_project_context","result":{"kind":"MOCK_UI_ONLY"}}])
                return route.fulfill(json={**turn, "project_input":session['project_input']})
            raise AssertionError('Unexpected chat request ' + path)

        page.route('**/api/v2/chat/**', route_chat)
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
        running[0] = False
        page.wait_for_timeout(1400)
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
        assert not errors, errors
        (output / 'chat-redesign-smoke.json').write_text(json.dumps({"kind":"Browser with mocked chat API; no external requests or FEA", "removed_controls":True,"automatic_inputs":True,"matching_run_selected":True,"stale_run_not_bound":True,"new_chat_while_running":True,"no_cross_chat_response":True,"reopen_history":True,"rename":True,"copy_chat":True,"share_link_restore":True,"json_attachment":True,"file_cards":True,"remove_file":True,"remove_restores_form":True,"removed_file_not_sent":True,"multiple_formats":True,"mobile":True,"page_errors":errors},indent=2))
        browser.close()
        print('PASS: compact chat, automatic inputs, saved history, new chat during run, rename/share/copy, JSON and mobile layout')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8060')
    main(parser.parse_args().base_url.rstrip('/'))
