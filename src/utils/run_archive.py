"""Best-effort, per-run evidence archives. Never changes native source files."""
from datetime import datetime, timedelta, timezone
from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

from config.settings import ROOT
from utils.archive_io import io_path, retry_replace, checkpoint_lock

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED'}


def archive_agents(state):
    return [f'A{n}' for n in range(1, 8)]


def write_json(path, value):
    path = io_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.a' + uuid.uuid4().hex[:12] + '.tmp')
    try:
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False, default=str), encoding='utf-8')
        retry_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_name(value):
    name = re.sub(r'[^A-Za-z0-9_.-]', '_', str(value)).strip('.')[:140]
    return name or 'unnamed'


def guarded(state, operation, *args):
    """Archive trouble is visible but must not change engineering decisions."""
    try:
        return operation(state, *args)
    except Exception as exc:
        from utils.audit import clean
        notice = clean(f'{operation.__name__}: {type(exc).__name__}: {exc}')
        warnings = state.output_archive.setdefault('warnings', [])
        if notice not in warnings:
            warnings.append(notice)
        logger.warning('Run archive %s: %s', state.run_id, notice)
        return None


def prepare_archive(state, database_path, created_at):
    database = Path(database_path).resolve()
    root = ROOT if database == (ROOT / 'data' / 'runs.db').resolve() else database.parent
    timestamp = datetime.fromisoformat(created_at).astimezone(IST)
    folder = root / 'store output run' / f'{safe_name(state.run_id)}_{timestamp:%d_%B_%Y_%H-%M-%S}_IST'
    if state.output_archive.get('run_id') != state.run_id:
        state.output_archive = {'run_id': state.run_id, 'directory': str(folder), 'warnings': []}
    else:
        # Never trust a path inherited from a client or a previous database.
        state.output_archive['directory'] = str(folder)
    for item in ['00_input', '02_software/STAAD', '02_software/IDEA_StatiCa',
                 '02_software/Tekla', '03_final_output', '04_logs']:
        (folder / item).mkdir(parents=True, exist_ok=True)
    for agent in archive_agents(state):
        (folder / '01_agents' / agent).mkdir(parents=True, exist_ok=True)
    original = folder / '00_input' / 'original_input.json'
    if not original.exists():
        write_json(original, state.raw_input)


def archive_folder(state):
    info = state.output_archive
    if info.get('run_id') != state.run_id:
        return None
    return Path(info['directory']) if info.get('directory') else None


def attempt_folder(state, agent):
    root = archive_folder(state)
    if root is None:
        return None
    attempt = int(state.agent_timings.get(agent, {}).get('attempts', 1))
    return root / '01_agents' / safe_name(agent) / f'attempt_{attempt:03d}'


def process_record(state, agent, exception=None):
    return {'run_id': state.run_id, 'agent': agent, 'timing': state.agent_timings.get(agent, {}),
            'status': state.agent_statuses.get(agent, {}), 'error': exception or state.error,
            'events': [entry.model_dump() for entry in state.logs if entry.agent_id == agent],
            'audit_events': [entry for entry in state.audit_events if entry.get('agent') == agent],
            'loop_a_count': state.loop_a_count, 'loop_b_count': state.loop_b_count,
            'loop_c_count': state.loop_c_count,
            'notice': 'Recorded operations and evidence only; no private LLM reasoning.'}


def start_stage(state, agent):
    folder = attempt_folder(state, agent)
    if folder is None:
        return
    write_json(folder / 'input.json', state.model_dump())
    write_json(folder / 'process.json', process_record(state, agent))


def checkpoint(state, database_path=None):
    folder = archive_folder(state)
    if folder is None:
        return
    with checkpoint_lock(folder):
        if database_path:
            # Another API/worker save may have won while we waited for the lock.
            # Export the latest committed snapshot rather than a stale caller.
            with closing(sqlite3.connect(database_path, timeout=10)) as conn:
                row = conn.execute('SELECT state_json FROM workflow_runs WHERE run_id=?', (state.run_id,)).fetchone()
            if row:
                from orchestration.state import WorkflowState
                state = WorkflowState.model_validate_json(row[0])
        _checkpoint(state)


def _checkpoint(state):
    folder = archive_folder(state)
    if folder is None:
        return
    write_json(folder / '03_final_output' / 'workflow_output.json', state.model_dump())
    effective = {'grid': state.grid_config or state.raw_input.get('grid', {}),
                 'load_configuration': state.load_config or state.raw_input.get('load_configuration', {}),
                 'run_policy': state.run_policy or state.raw_input.get('run_policy', {})}
    write_json(folder / '00_input' / 'effective_input.json', effective)
    write_json(folder / '00_input' / 'run_policy.json', effective['run_policy'])
    final = folder / '03_final_output'
    write_json(final / 'final_report.json', state.final_report)
    write_json(final / 'validation_results.json', state.validation_report)
    write_json(final / 'agent_timings.json', state.agent_timings)
    write_json(final / 'evaluation_results.json', [e for e in state.audit_events if e.get('kind') == 'evaluation'])
    write_json(final / 'corrective_actions.json', [e for e in state.audit_events if e.get('kind') == 'correction'])
    write_json(final / 'engineer_approval.json', {'approved':state.gate3_approved,
        'accepted_by':state.accepted_by, 'accepted_at':state.acceptance_timestamp,
        'run_status':state.status})
    write_json(folder / '04_logs' / 'events.json', [entry.model_dump() for entry in state.logs])
    write_json(folder / '04_logs' / 'audit_events.json', state.audit_events)
    for agent in archive_agents(state):
        attempt = attempt_folder(state, agent)
        if (attempt / 'input.json').exists() and (state.agent_timings.get(agent,{}).get('running') or
                                                 not (attempt / 'output.json').exists()):
            write_json(attempt / 'process.json', process_record(state, agent))
            if state.status in TERMINAL:
                write_json(attempt / 'output.json', state.model_dump())
    write_json(folder / 'manifest.json', {
        'run_id':state.run_id, 'status':state.status, 'updated_at':datetime.now(timezone.utc).isoformat(),
        'archive_warnings':state.output_archive.get('warnings', []),
        'layout_version':state.layout_version,
        'agents':{agent:{'status':state.agent_statuses.get(agent,{}).get('status','NOT_RUN'),
            'attempts':[p.name for p in sorted((folder/'01_agents'/agent).glob('attempt_*'))],
            'execution_recorded':any((folder/'01_agents'/agent).glob('attempt_*/input.json'))} for agent in archive_agents(state)},
        'software':{name:{'stage_status':state.agent_statuses.get(agent,{}).get('status','NOT_RUN'),
            'capture_manifests':[str(p.relative_to(folder)) for p in (folder/'02_software'/name).rglob('copy_manifest.json')]}
            for name,agent in [('STAAD','A4'),('IDEA_StatiCa','A5'),('Tekla','A6')]},
        'native_files_notice':'See each software copy_manifest.json. Files are not claimed saved unless listed. Open Tekla model copies are not independently reopen-validated.',
    })
    # Copy a changing worker log on terminal/approval checkpoints, never the source itself.
    if state.status in TERMINAL | {'PAUSED_AT_GATE3', 'READY'}:
        worker_log = ROOT / 'data' / 'worker_logs' / (safe_name(state.run_id) + '.log')
        if worker_log.is_file():
            shutil.copy2(worker_log, folder / '04_logs' / 'worker.log')


def copy_files(files, destination, source_root=None):
    """Copy exact evidence with hashes; unstable, locked and missing files are explicit."""
    destination = io_path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    saved, missing, excluded = [], [], []
    for source in files:
        source = Path(source)
        if source.is_symlink():
            excluded.append({'source':str(source),'reason':'Symlink not followed'})
            continue
        relative = source.relative_to(source_root) if source_root else Path(source.name)
        target = destination / relative
        try:
            source_io = io_path(source)
            before = source_io.stat()
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source_io,target)
            after = source_io.stat()
            with target.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                target.unlink(missing_ok=True)
                raise OSError('Source changed while being copied')
            saved.append({'source':str(source),'file':str(relative),'bytes':after.st_size,'sha256':digest})
        except OSError as exc:
            missing.append({'source':str(source),'reason':str(exc)})
    report = {'saved_files':saved,'missing_or_failed_files':missing,'excluded_files':excluded}
    write_json(destination / 'copy_manifest.json',report)
    return report


def start_idea(state, group, payload):
    root = archive_folder(state)
    if root is None:
        return
    parent = root / '02_software' / 'IDEA_StatiCa' / safe_name(group)
    index = len(list(parent.glob('calculation_*'))) + 1 if parent.exists() else 1
    folder = parent / f'calculation_{index:03d}'
    write_json(folder/'input.json',payload)
    return folder


def record_idea(state, group, payload, result, folder=None):
    folder = folder or start_idea(state, group, payload)
    if folder is None:
        return
    write_json(folder/'results.json',result)
    paths = [Path(result[key]) for key in ['check_summary_file','raw_results_file','iom_connection_data_file',
             'report_pdf_file','saved_project_path'] if result.get(key)]
    report = copy_files(paths,folder/'native_files')
    report['not_generated'] = [key for key in ['raw_results_file','report_pdf_file','saved_project_path'] if not result.get(key)]
    report['raw_results_error'] = result.get('raw_results_error')
    write_json(folder/'copy_manifest.json',report)


def finish_stage(state, agent, exception=None):
    from orchestration.state import workflow_stage
    folder = attempt_folder(state,agent)
    if folder is None:
        return
    write_json(folder/'output.json',state.model_dump())
    write_json(folder/'process.json',process_record(state,agent,exception))
    root = archive_folder(state)
    attempt = folder.name
    if agent == 'A4':
        software = root/'02_software'/'STAAD'/attempt
        write_json(software/'input.json',{'grid':state.grid_config,
            'members_split_beams':state.frame_candidate.get('members_split_beams',[]),
            'load_configuration':state.load_config})
        write_json(software/'results.json',state.staad_results)
        # Copy only when this attempt returned STAAD evidence; do not label an
        # old open model as output of an early failed attempt.
        source = Path(state.readiness.get('staad_model_path',''))
        files = []
        if state.staad_results and source.is_file():
            files = [p for p in source.parent.iterdir() if p.is_file() and
                     (p.name == source.name or p.name.lower().startswith(source.stem.lower()+'.'))]
        report=copy_files(files,software/'native_files')
        if not files:
            report['unavailable_reason']='No returned analysis evidence or saved STAAD model path.'
            write_json(software/'native_files'/'copy_manifest.json',report)
    elif agent == 'A5':
        write_json(root/'02_software'/'IDEA_StatiCa'/'results.json',state.ideastatica_results)
    elif agent == workflow_stage(state, 'tekla'):
        software = root/'02_software'/'Tekla'/attempt
        write_json(software/'input.json',{'members':state.frame_candidate.get('members',[]),
            'grid':state.grid_config,'joint_groups':state.validation_report.get('validated_joint_groups_payload',{})})
        write_json(software/'publication_results.json',state.tekla_results)
        write_json(software/'verified_components.json',state.tekla_results.get('connection_manifest',[]))
        source = Path(state.tekla_results.get('model_path',''))
        report = {'saved_files':[], 'unavailable_reason':'No successful saved Tekla publication in this attempt.'}
        if state.agent_statuses.get(workflow_stage(state, 'tekla'),{}).get('status') == 'PASSED' and source.is_dir() and state.tekla_results.get('model_path'):
            files, excluded = [], []
            for path in source.rglob('*'):
                if any(parent.is_symlink() for parent in [path,*path.parents]):
                    excluded.append({'source':str(path),'reason':'Symlink not followed'})
                    continue
                if not path.is_file():
                    continue
                if path.suffix.lower() in {'.lock','.lck','.tmp'} or path.name.lower().endswith('.db1_user') or path.name.lower().startswith('.env'):
                    excluded.append({'source':str(path),'reason':'Lock, temporary or environment file'})
                    continue
                files.append(path)
            report=copy_files(files,software/'model_snapshot',source)
            report['excluded_files'].extend(excluded)
            # Check the whole captured set again, not just each copy operation.
            changed=[]
            for item in report['saved_files']:
                try:
                    with Path(item['source']).open('rb') as stream:
                        if hashlib.file_digest(stream, 'sha256').hexdigest()!=item['sha256']:
                            changed.append(item['source'])
                except OSError:
                    changed.append(item['source'])
            report.update(changed_during_snapshot=changed,
                snapshot_status='INCOMPLETE' if changed or report['missing_or_failed_files'] else 'COPIED_NOT_REOPEN_VALIDATED',
                independently_reopen_verified=False,
                notice='Saved open-model file copy; no Save As, source edits or independent reopen test was performed. Lock/temp files are excluded.')
            if not any(Path(item['file']).suffix.lower() == '.db1' for item in report['saved_files']):
                report['snapshot_status'] = 'INCOMPLETE'
                report['unavailable_reason'] = 'No main .db1 model database was copied.'
        write_json(software/'copy_manifest.json',report)
