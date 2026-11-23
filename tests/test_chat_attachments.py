"""Local attachment fixtures; never call an external model or engineering software."""
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agentic.chat_attachments import AttachmentStore, extract, archive


def bundle(entries):
    output = BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        for key, value in entries.items():
            z.writestr(key, value)
    return output.getvalue()


class AttachmentReaders(unittest.TestCase):
    def test_json_grid_import_and_invalid_json_reference(self):
        parsed = extract('frame.json', b'{"grid":{"n_long_bays":8,"n_trans_bays":5}}')
        self.assertEqual(parsed['project_input']['grid']['n_long_bays'], 8)
        self.assertNotIn('project_input', extract('results.json', b'{"passed":true}'))
        self.assertNotIn('project_input', extract('invalid.json', b'{"grid":{"n_long_bays":-1}}'))

    def test_word_and_zip_entry_text(self):
        docx = bundle({'word/document.xml': '<w:document xmlns:w="w"><w:p><w:t>Column load 12 kN</w:t></w:p></w:document>'})
        self.assertIn('Column load 12 kN', extract('loads.docx', docx)['text'])
        zipped = bundle({'notes.txt': 'Check beam displacement', 'loads.docx': docx, 'nested.zip': bundle({'x':'x'})})
        value = extract('package.zip', zipped)
        self.assertIn('Column load 12 kN', value['text'])
        self.assertIn('not interpreted', value['text'])

    def test_excel_formulas_are_read_without_calculation(self):
        from openpyxl import Workbook
        workbook = Workbook(); workbook.active.title = 'Inputs'
        workbook.active['A1'] = 25; workbook.active['B1'] = '=A1*2'
        workbook.create_sheet('Checks')['A1'] = 'Beam'
        stream = BytesIO(); workbook.save(stream); workbook.close()
        result = extract('checks.xlsx', stream.getvalue())
        self.assertIn('B1: =A1*2', result['text'])
        self.assertIn('not recalculated', result['text'])
        self.assertIn('[Sheet: Checks]', result['text'])

    def test_pdf_text_and_scan_limitation(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject, DictionaryObject
        writer = PdfWriter(); page = writer.add_blank_page(200,200)
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        content = DecodedStreamObject(); content.set_data(b'BT /F1 12 Tf 10 10 Td (Beam check evidence) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(content)
        out = BytesIO(); writer.write(out)
        self.assertIn('Beam check evidence', extract('report.pdf', out.getvalue())['text'])
        blank = PdfWriter(); blank.add_blank_page(100,100); out = BytesIO(); blank.write(out)
        self.assertEqual(extract('scan.pdf', out.getvalue())['status'], 'limited')

    def test_images_svg_drawio_and_unknown_binary(self):
        from PIL import Image
        image = BytesIO(); Image.new('RGB',(20,10),'blue').save(image,format='PNG')
        self.assertTrue(extract('frame.png', image.getvalue())['image'])
        self.assertNotIn('image', extract('frame.svg', b'<svg><text>Beam</text></svg>'))
        self.assertIn('Beam', extract('diagram.drawio', b'<mxfile><diagram><mxGraphModel><mxCell value="Beam"/></mxGraphModel></diagram></mxfile>')['text'])
        self.assertEqual(extract('native.dwg', b'\x00\xff\x01')['status'], 'unsupported')
        self.assertEqual(extract('custom.xyz', b'Structural notes')['status'], 'readable')

    def test_archive_paths_and_entities_are_not_processed(self):
        with self.assertRaises(ValueError): archive(bundle({'../outside.txt':'bad'}))
        with self.assertRaises(ValueError): extract('entity.svg', b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///x">]><svg>&x;</svg>')

    def test_attachment_ownership_normalization_and_delete(self):
        with tempfile.TemporaryDirectory() as root:
            store = AttachmentStore(root)
            value = store.save('chat_a', '../../notes.txt', b'Beam load')
            self.assertEqual(value['name'],'notes.txt')
            self.assertEqual(len(store.drafts('chat_a',set())),1)
            self.assertEqual(store.drafts('chat_a',{value['id']}),[])
            with self.assertRaises(ValueError): store.load(value['id'],'chat_b')
            with self.assertRaises(ValueError): store.load('../notes','chat_a')
            store.delete(value['id'],'chat_a')
            with self.assertRaises(ValueError): store.load(value['id'],'chat_a')


class AttachmentAPI(unittest.TestCase):
    def test_current_image_reaches_model_without_entering_logs(self):
        import asyncio
        from PIL import Image
        from langchain_core.messages import AIMessage, HumanMessage
        from agentic import chat_router
        from agentic.chat import ChatRuntime
        from agentic.chat_store import ChatStore
        class Model:
            messages = []
            def bind_tools(self, tools, **kwargs): return self
            async def ainvoke(self, messages):
                self.messages = messages
                return AIMessage(content='Controlled image response; no engineering result.')
        with tempfile.TemporaryDirectory() as root:
            store = ChatStore(Path(root)/'chat.db'); files = AttachmentStore(Path(root)/'files')
            session = store.session(); image = BytesIO(); Image.new('RGB',(20,10),'blue').save(image,format='PNG')
            attached = files.save(session['id'],'frame.png',image.getvalue())
            turn = store.new_turn(session['id'],'Explain this structural frame image')
            turn['attachments'] = [files.public(attached)]; store.save_turn(turn)
            model = Model()
            with patch.object(chat_router,'ATTACHMENTS',files):
                asyncio.run(ChatRuntime(store,lambda:model).respond(turn))
            content = next(m.content for m in model.messages if isinstance(m,HumanMessage))
            self.assertEqual(content[1]['type'],'image_url')
            self.assertTrue(content[1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
            self.assertNotIn(attached['image'],json.dumps(turn))

    def test_upload_remove_and_sent_attachment_model_context(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agentic import chat_router
        from agentic.chat import ChatRuntime
        from agentic.chat_store import ChatStore
        from langchain_core.messages import AIMessage, ToolMessage
        class Model:
            count = 0
            messages = []
            def bind_tools(self, tools, **kwargs): return self
            async def ainvoke(self, messages):
                self.messages = messages[:]; self.count += 1
                if self.count == 1:
                    return AIMessage(content='',tool_calls=[{'name':'read_attachment','args':{'attachment_id':attached['id']},'id':'read'}])
                return AIMessage(content='The attached note says beam load 12 kN; no solver ran.')
        with tempfile.TemporaryDirectory() as root:
            chats = ChatStore(Path(root)/'chat.db'); files = AttachmentStore(Path(root)/'files'); model = Model()
            with patch.object(chat_router,'CHAT_STORE',chats), patch.object(chat_router,'ATTACHMENTS',files), \
                 patch.object(chat_router,'RUNTIME',ChatRuntime(chats,lambda:model)), \
                 patch.dict('os.environ',{'OPENAI_API_KEY':'test-placeholder','PLANT_CHAT_ENABLED':'true','PLANT_AGENT_MODEL':'controlled'}):
                app = FastAPI(); app.include_router(chat_router.router)
                with TestClient(app) as client:
                    session = client.post('/api/v2/chat/sessions').json()['id']
                    path = '/api/v2/chat/sessions/' + session
                    attached = client.post(path+'/attachments?filename=loads.txt',content=b'Beam load 12 kN').json()
                    self.assertEqual(attached['status'],'readable')
                    unused = client.post(path+'/attachments?filename=unused.txt',content=b'Unsent note').json()
                    self.assertEqual(client.delete(path+'/attachments/'+unused['id']).status_code,200)
                    self.assertEqual(len(client.get(path).json()['draft_attachments']),1)
                    response = client.post('/api/v2/chat/messages',json={'session_id':session,'message':'Explain this project note','attachment_ids':[attached['id']]})
                    self.assertEqual(response.status_code,202)
                    import time
                    for _ in range(100):
                        turn = client.get('/api/v2/chat/turns/'+response.json()['turn_id']).json()
                        if turn['status'] != 'RUNNING': break
                        time.sleep(.01)
                    self.assertEqual(turn['status'],'COMPLETED')
                    self.assertEqual(turn['attachments'][0]['name'],'loads.txt')
                    read = next(json.loads(m.content) for m in model.messages if isinstance(m,ToolMessage))
                    self.assertEqual(read['text'],'Beam load 12 kN')
                    self.assertEqual(client.get(path).json()['draft_attachments'],[])
                    self.assertEqual(client.delete(path+'/attachments/'+attached['id']).status_code,409)
                    other = client.post('/api/v2/chat/sessions').json()['id']
                    bad = client.post('/api/v2/chat/messages',json={'session_id':other,'message':'Read','attachment_ids':[attached['id']]})
                    self.assertEqual(bad.status_code,422)


if __name__ == '__main__': unittest.main()
