"""Read actual reversible parameter/geometry bindings, without calculating."""
import sys
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from config.settings import load_environment
load_environment()
from agents.A5_idea_statica import _get_client,_to_jsonable,_set_parameters
with _get_client() as c:
    c.project.open_project_from_filepath(str(ROOT/'sample_projects/two-longitudinal.ideaCon'))
    p=c.project.active_project_id
    print('APIS',[x for x in dir(c) if not x.startswith('_')])
    base=_to_jsonable(c.export.export_iom_connection_data(p,1))
    print('BOLT MATERIALS',str(_to_jsonable(c.material.get_bolt_assemblies(p)))[:2200])
    report={}
    for key in ['EP_d','GRD_d','CPL_d','Bolt_Type']:
        try:
            _set_parameters(c,p,1,[{'key':key,'expression':'"M20 8.8"' if key=='Bolt_Type' else '0.020'}])
            after=_to_jsonable(c.export.export_iom_connection_data(p,1))
            report[key]={'changed_categories':[k for k,v in after.items() if v!=base.get(k)],
                         'bolt_grids':after['boltGrids']}
            print(key,report[key]['changed_categories'],[(g['id'],g['boltAssembly']) for g in after['boltGrids']])
            print('BOLT MATERIALS',str(_to_jsonable(c.material.get_bolt_assemblies(p)))[:700])
        finally:
            _set_parameters(c,p,1,[{'key':key,'expression':'"M16 8.8"' if key=='Bolt_Type' else '0.016'}])
    (ROOT/'output_validation/backend_loop_test/bolt_parameter_bindings.json').write_text(json.dumps(report,indent=2))
