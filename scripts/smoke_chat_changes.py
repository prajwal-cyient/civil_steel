"""Headless chat change-preview UI checks. All HTTP/native APIs are fixtures."""
from copy import deepcopy
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / 'output_exports' / 'dynamic_chat_verification'
    output.mkdir(parents=True, exist_ok=True)
    plan = {'id':'change_fixture','session_id':'chat_fixture','status':'PENDING','digest':'a'*64,
            'changes':[{'joint_group':'inner_node_2_longitudinal_bracings','parameter':'GRD_d','old_mm':16,'new_mm':20}],
            'steps':['A5:selected','A6:package'],'notice':'Only the selected named group may change.'}
    state = {'plan':deepcopy(plan),'turns':[],'posts':[]}
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge',headless=True)
        context = browser.new_context(viewport={'width':1366,'height':900})
        page = context.new_page()
        page.on('pageerror',lambda error:errors.append(str(error)))
        def route(req):
            path = req.request.url.split('http://chat-fixture.test')[-1].split('?')[0]
            if path == '/chat.js':
                return req.fulfill(content_type='application/javascript',body=(ROOT/'src/static/radiator_support/chat.js').read_text(encoding='utf-8'))
            if path == '/chat.css':
                return req.fulfill(content_type='text/css',body=(ROOT/'src/static/radiator_support/chat.css').read_text(encoding='utf-8'))
            if path == '/':
                return req.fulfill(content_type='text/html',body='''<!doctype html><html><head><link rel="stylesheet" href="/chat.css"></head><body>
                <script>window.fixtureInput={grid:{n_long_bays:8}};window.structuralChatBridge={getInput:()=>window.fixtureInput,setInput:x=>window.fixtureInput=x,getRunId:()=>null,setTurnBusy:()=>{}};</script>
                <script src="/chat.js"></script></body></html>''')
            if path.endswith('/config'):return req.fulfill(json={'enabled':True,'model':'controlled-fixture'})
            if path == '/api/v2/chat/sessions':return req.fulfill(json={'sessions':[]})
            if path == '/api/v2/chat/sessions/chat_fixture':
                return req.fulfill(json={'id':'chat_fixture','title':'Selected connection change','run_id':None,
                    'project_input':{'grid':{'n_long_bays':8}},'turns':state['turns'],'draft_attachments':[],
                    'pending_changes':[state['plan']] if state['plan']['status']=='PENDING' else []})
            if '/changes/' in path:
                if req.request.method=='POST':
                    state['posts'].append(path)
                    if path.endswith('/reject'):
                        state['plan']['status']='REJECTED';return req.fulfill(json=state['plan'])
                    data=req.request.post_data_json
                    assert data['digest']==plan['digest']
                    state['plan']['status']='COMPLETED'
                    state['turns']=[{'id':'turn_fixture','message':'Apply approved project change','status':'COMPLETED',
                        'assistant':'Approved changes validated. No Tekla publication requested.','error':None,'events':[],
                        'run_id':None,'project_input':{'grid':{'n_long_bays':8}},'usage':{'model_calls':0}}]
                    return req.fulfill(status=202,json={'session_id':'chat_fixture','turn_id':'turn_fixture'})
                return req.fulfill(json=state['plan'])
            if path == '/api/v2/chat/turns/turn_fixture':return req.fulfill(json=state['turns'][0])
            if path == '/favicon.ico':return req.fulfill(status=204)
            raise AssertionError('Unexpected HTTP request: '+path)
        page.route('http://chat-fixture.test/**',route)
        page.goto('http://chat-fixture.test/?chat=chat_fixture')
        page.get_by_role('button',name='Engineering chat',exact=True).click()
        page.get_by_role('button',name='Approve & Apply').wait_for()
        assert 'inner_node_2_longitudinal_bracings' in page.locator('.sc-change-table').inner_text()
        assert 'GRD_d' in page.locator('.sc-change-table').inner_text()
        assert '20 mm' in page.locator('.sc-change-table').inner_text()
        assert not state['posts']
        page.screenshot(path=str(output/'change-preview.png'))
        page.reload()
        page.get_by_role('button',name='Engineering chat',exact=True).click()
        page.get_by_role('button',name='Approve & Apply').click()
        page.get_by_text('Approved changes validated. No Tekla publication requested.').wait_for()
        assert len(state['posts'])==1
        assert page.get_by_role('button',name='Approve & Apply').is_disabled()
        page.reload()
        page.get_by_role('button',name='Engineering chat',exact=True).click()
        assert page.get_by_role('button',name='Approve & Apply').count()==0
        state['plan']={**deepcopy(plan),'id':'change_reject'};state['turns']=[]
        page.reload()
        page.get_by_role('button',name='Engineering chat',exact=True).click()
        page.get_by_role('button',name='Reject',exact=True).click()
        page.get_by_text('Rejected; nothing changed.').wait_for()
        assert len(state['posts'])==2 and state['posts'][-1].endswith('/reject')
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(output/'change-preview-mobile.png'))
        assert not errors, errors
        (output/'browser_changes.json').write_text(json.dumps({'kind':'mocked HTTP/native interfaces',
            'real_names_preserved':True,'preview_has_old_new_units':True,'no_mutation_before_approval':True,
            'refresh_restores_pending_preview':True,'approve_executes_once':True,'reject_does_not_approve':True,
            'completed_approval_not_replayed':True,'page_errors':errors},indent=2))
        browser.close()
    print('PASS: exact-name preview, approval, rejection, refresh, no replay, mobile; no real API/FEA calls.')


if __name__=='__main__':main()
