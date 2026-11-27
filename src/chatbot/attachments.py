"""Bounded, read-only chat attachment readers. No macros, scripts or archives execute."""
import base64
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET

MAX_FILE = 20 * 1024 * 1024
MAX_TEXT = 60000
ROOT = Path(__file__).resolve().parents[2] / 'data' / 'chat_attachments'
TEXT_TYPES = {'.txt', '.csv', '.tsv', '.json', '.md', '.xml', '.svg', '.drawio', '.yaml', '.yml', '.log', '.std', '.ifc', '.dxf', '.html', '.css', '.js', '.py', '.ini', '.toml'}
IMAGE_TYPES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff'}


def archive(data):
    bundle = zipfile.ZipFile(BytesIO(data))
    entries = bundle.infolist()
    if len(entries) > 500 or sum(i.file_size for i in entries) > 50 * 1024 * 1024:
        bundle.close()
        raise ValueError('Archive expands beyond the supported file/count limits.')
    for item in entries:
        path = PurePosixPath(item.filename.replace('\\', '/'))
        if path.is_absolute() or '..' in path.parts or ':' in item.filename or item.flag_bits & 1:
            bundle.close()
            raise ValueError('Encrypted or unsafe archive entries are not supported.')
        if item.file_size > MAX_FILE or item.file_size > max(1, item.compress_size) * 300:
            bundle.close()
            raise ValueError('Archive entry exceeds the size or compression limit.')
    return bundle


def decode(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    return data.decode('utf-8-sig')


def xml(data):
    text = decode(data)
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('XML entity declarations are not supported.')
    return ET.fromstring(text)


def drawio_text(data):
    import zlib
    from urllib.parse import unquote
    root = xml(data)
    blocks = [decode(data)]
    for diagram in root.iter('diagram'):
        if diagram.text and not list(diagram):
            try:
                decoder = zlib.decompressobj(-15)
                expanded = decoder.decompress(base64.b64decode(diagram.text.strip()), MAX_TEXT + 1)
                if len(expanded) > MAX_TEXT or decoder.unconsumed_tail:
                    raise ValueError('Compressed diagram is too large.')
                blocks.append(unquote(expanded.decode('utf-8')))
            except (ValueError, zlib.error):
                blocks.append('[Compressed diagram could not be decoded within limits.]')
    return '\n'.join(blocks)


def extract(name, data, inside_zip=False):
    """Return honest reader status and text; binary formats without a reader stay attached."""
    suffix = Path(name).suffix.lower()
    result = {'status': 'readable', 'text': '', 'notice': '', 'kind': suffix.lstrip('.') or 'file'}
    if suffix in TEXT_TYPES:
        result['text'] = drawio_text(data) if suffix == '.drawio' else decode(data)
        if suffix == '.svg':
            xml(data)  # Text only: SVG scripts and external references never render.
        if suffix == '.json':
            try:
                value = json.loads(result['text'])
                if isinstance(value, dict) and ('grid' in value or 'n_long_bays' in value or 'n_trans_bays' in value):
                    from chatbot.chatbot import project_input
                    result['project_input'] = project_input(value if 'grid' in value else {'grid': value})
            except (ValueError, TypeError):
                result['notice'] = 'JSON attached as reference; it is not a valid engineering input specification.'
    elif suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ValueError('Password-protected PDF cannot be read.')
        blocks, count, has_text = [], 0, False
        for index, page in enumerate(reader.pages):
            if index >= 100 or count >= MAX_TEXT:
                break
            text = page.extract_text() or ''
            has_text = has_text or bool(text.strip())
            blocks.append(f'[Page {index + 1}]\n' + text[:MAX_TEXT - count]); count += len(text)
        result['text'] = '\n'.join(blocks)
        if not has_text:
            result.update(status='limited', notice='No selectable text found. Scanned pages need OCR; OCR is not enabled.')
        result['notice'] += ' Text extraction only; page graphics are not interpreted.'
        if len(reader.pages) > 100 or count >= MAX_TEXT:
            result['notice'] += ' PDF text was truncated at the page/text limit.'
    elif suffix in {'.docx', '.pptx'}:
        with archive(data) as bundle:
            paths = [n for n in bundle.namelist() if n == 'word/document.xml' or re.fullmatch(r'ppt/slides/slide\d+\.xml', n)]
            blocks = []
            for path in sorted(paths)[:100]:
                root = xml(bundle.read(path))
                blocks.append('[' + path + ']\n' + '\n'.join(' '.join(node.itertext()) for node in root.iter() if node.tag.endswith('}t')))
            result['text'] = '\n'.join(blocks)
            result['notice'] = 'Text and table cell text only; embedded objects and drawings are not interpreted.'
    elif suffix in {'.xlsx', '.xlsm'}:
        from openpyxl import load_workbook
        with archive(data):
            pass
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False)
        cached = load_workbook(BytesIO(data), read_only=True, data_only=True, keep_links=False)
        blocks, count = [], 0
        try:
            for sheet in workbook.worksheets[:30]:
                blocks.append('[Sheet: ' + sheet.title + ']')
                rows = sheet.iter_rows(max_row=min(sheet.max_row or 200, 200), max_col=min(sheet.max_column or 40, 40))
                cache_rows = cached[sheet.title].iter_rows(max_row=min(sheet.max_row or 200, 200), max_col=min(sheet.max_column or 40, 40))
                for row, values in zip(rows, cache_rows):
                    for cell, cached_cell in zip(row, values):
                        if cell.value is None:
                            continue
                        line = f'{cell.coordinate}: {cell.value}'
                        if cell.data_type == 'f':
                            line += f' [saved result: {cached_cell.value}; not recalculated]'
                        blocks.append(line[:4000]); count += len(line)
                    if count >= MAX_TEXT:
                        break
                if count >= MAX_TEXT:
                    break
            result['text'] = '\n'.join(blocks)
            result['notice'] = 'Bounded preview: up to 30 sheets, 200 rows and 40 columns per sheet. Formulas and saved results are read; no recalculation or macros run.'
        finally:
            workbook.close(); cached.close()
    elif suffix in IMAGE_TYPES:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = 20000000
        with Image.open(BytesIO(data)) as picture:
            if picture.width * picture.height > 20000000:
                raise ValueError('Image exceeds 20 million pixels.')
            picture.seek(0); picture.thumbnail((1280, 1280)); converted = picture.convert('RGB')
            output = BytesIO(); converted.save(output, format='JPEG', quality=80)
            result['image'] = base64.b64encode(output.getvalue()).decode('ascii')
            result['text'] = f'Image {picture.width} × {picture.height}; first frame only.'
            result['notice'] = 'Image is sent to the configured model for visual interpretation when this message is submitted; requires a vision-capable model.'
    elif suffix == '.zip' and not inside_zip:
        blocks = []
        with archive(data) as bundle:
            entries = [i for i in bundle.infolist() if not i.is_dir()]
            for index, item in enumerate(entries):
                blocks.append(f'[ZIP entry: {item.filename}, {item.file_size} bytes]')
                if index >= 30 or sum(map(len, blocks)) >= MAX_TEXT:
                    blocks.append('[Remaining entry contents omitted; archive preview limit reached.]'); break
                if Path(item.filename).suffix.lower() in TEXT_TYPES | {'.pdf', '.docx', '.xlsx', '.xlsm', '.pptx'}:
                    try:
                        child = extract(item.filename, bundle.read(item), inside_zip=True)
                        blocks.append(child['text'][:MAX_TEXT // 2]); blocks.append(child.get('notice', ''))
                    except Exception:
                        blocks.append('[Entry could not be read; no content was executed.]')
                else:
                    blocks.append('[Binary/nested archive content not interpreted.]')
        result['text'] = '\n'.join(blocks)
        result['notice'] = 'ZIP preview only: up to 30 entries. Archives are never extracted to disk; nested archives and image entries are not interpreted.'
    else:
        # Accept arbitrary extensions, but never pretend an unknown binary is understood.
        try:
            text = decode(data)
            if '\x00' in text or sum(ord(c) < 32 and c not in '\n\r\t' for c in text[:5000]) > 5:
                raise ValueError('Binary')
            result['text'] = text
            result['notice'] = 'Read as plain text; no specialized reader for this extension.'
        except (UnicodeError, ValueError):
            result.update(status='unsupported', notice='File attached, but this binary format has no content reader. Export it to PDF, image, text or a supported document format.')
    if len(result['text']) > MAX_TEXT:
        result['notice'] += ' Extracted content was truncated.'
        result['text'] = result['text'][:MAX_TEXT]
    return result


class AttachmentStore:
    def __init__(self, root=None):
        self.root = Path(root or ROOT)
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self, ident, session_id):
        if not re.fullmatch(r'file_[a-f0-9]{32}', ident):
            raise ValueError('Invalid attachment ID.')
        try:
            value = json.loads((self.root / (ident + '.json')).read_text(encoding='utf-8'))
        except FileNotFoundError as exc:
            raise ValueError('Attachment not found.') from exc
        if value['session_id'] != session_id:
            raise ValueError('Attachment belongs to another chat.')
        return value

    @staticmethod
    def public(value):
        return {key: value[key] for key in ('id', 'name', 'size', 'kind', 'status', 'notice')}

    def save(self, session_id, name, data):
        if not data or len(data) > MAX_FILE:
            raise ValueError('Choose a non-empty file of at most 20 MB.')
        name = name.replace('\\', '/').split('/')[-1][:180]
        name = ''.join(c for c in name if ord(c) >= 32) or 'attachment'
        try:
            parsed = extract(name, data)
        except Exception as exc:
            parsed = {'kind': Path(name).suffix.lstrip('.') or 'file', 'status': 'unreadable', 'text': '',
                      'notice': 'Content could not be read (' + type(exc).__name__ + '). Damaged, encrypted or over-limit files need a supported export.'}
        value = {'id': 'file_' + uuid.uuid4().hex, 'session_id': session_id, 'name': name, 'size': len(data), **parsed}
        path = self.root / (value['id'] + '.json')
        path.write_text(json.dumps(value, ensure_ascii=True, allow_nan=False), encoding='utf-8')
        # Store normalized text/preview only, not executable original documents.
        return value

    def delete(self, ident, session_id):
        self.load(ident, session_id)
        (self.root / (ident + '.json')).unlink()

    def drafts(self, session_id, used):
        values = []
        for path in self.root.glob('file_*.json'):
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                if value['session_id'] == session_id and value['id'] not in used:
                    values.append(self.public(value))
            except (OSError, ValueError):
                continue
        return values
