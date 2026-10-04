"""Deterministic empty-workspace smoke. Synthetic POTCAR; no SSH or VASP."""
import base64,json,os,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,os.environ.get('FPILOT_TEST_SOURCE',str(Path(__file__).resolve().parents[1])))


def run(directory,structure_file):
    os.environ['FPILOT_WORKSPACE']=str(Path(directory).resolve())
    from backend import inputs,execution as ex,servers,recycle,branch_bindings,remote_transport
    structure=json.loads(Path(structure_file).read_text(encoding='utf-8'))
    potcar=b'TITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\nENMAX = 245.0;\nEnd of Dataset\n'
    identity=inputs.upload_potcar(base64.b64encode(potcar).decode())['id']
    project={'id':'portable-project','name':'模拟项目','remoteSlug':'portable-project','nodes':[
        {'id':'s','data':{'kind':'structure','structure':structure,'executionMode':'vasp','stageSequence':1,'stageSlug':'structure'}},
        {'id':'c','data':{'kind':'scf','executionMode':'vasp','stageSequence':2,'stageSlug':'scf',
            'parameters':{'preset':'basic','values':{}},'inputDraft':{'grid':'4 4 4','center':'Gamma','potcar_id':identity}}}],
        'edges':[{'source':'s','target':'c'}],
        'parameters':{'shared':{'values':{'XC_FUNCTIONAL':'PBE','ENCUT':'400','EDIFF':'1e-6','ISPIN':'1','ISMEAR':'0','SIGMA':'0.05','LDAU':'false'}}}}
    frozen=inputs.freeze(project,'c','仅本机模拟，不能用于实际 VASP 计算')
    config={**servers.DEFAULT,'alias':'d-test','queue':'batch','mpi_executable':'/opt/mpi/mpirun',
        'vasp_executable':'/opt/vasp/vasp_std','environment_script':'/opt/env.sh'}
    profile=servers.profile(config,'node01',4)
    script=servers.pbs.render_script(profile)
    calls=[]
    def remote(alias,command,input_bytes=None,**kwargs):
        calls.append(command)
        if '.submit-once' in command:
            return {'exit_code':0,'stdout':'FPILOT_JOB_ID=99999.mock\n','stderr':''}
        if 'FPILOT_TIMING=' in command:
            return {'exit_code':0,'stdout':'job_state = C\nFPILOT_JOB_ID=99999.mock\nFPILOT_EXIT=0\nFPILOT_TIMING=1\nFPILOT_EDIFF=1\nFPILOT_MPI_SINGLETON=0\n','stderr':''}
        return {'exit_code':0,'stdout':'FPILOT_ROOT_ABS=/home/test/230269265lht\nFPILOT_ROOT_SNAPSHOT=1:2:3:4\n','stderr':''}
    with patch.object(servers,'capacity_probe',return_value={'id':'mock-probe'}),patch.object(remote_transport,'_interpreter',return_value='/usr/bin/python3'),patch.object(servers,'preview',return_value={'resource_ready':True,'profile':profile,'script':script}),patch.object(ex,'ssh',side_effect=remote):
        attempt=ex.prepare(frozen['id'],config,'mock-probe','node01',cores=4,project_id=project['id'])
        assert attempt['state']=='PREPARED'
        assert branch_bindings.check(project,'c')['state']=='UNBOUND'
        submitted=ex.submit(attempt['id']);ex.submit(attempt['id'])
        assert submitted['job_id']=='99999.mock'
        assert sum('.submit-once' in call for call in calls)==1
        completed=ex.refresh(attempt['id']);assert completed['state']=='COMPLETED',completed
        assert branch_bindings.check(project,'c')['alias']=='d-test'
    # Even an ambiguous receipt remains recyclable and cannot trigger re-submit.
    with patch.object(servers,'capacity_probe',return_value={'id':'mock-probe'}),patch.object(remote_transport,'_interpreter',return_value='/usr/bin/python3'),patch.object(servers,'preview',return_value={'resource_ready':True,'profile':profile,'script':script}),patch.object(ex,'ssh',side_effect=remote):
        unknown=ex.prepare(frozen['id'],config,'mock-probe','node01',cores=4,project_id=project['id'])
        with patch.object(ex,'ssh',return_value={'exit_code':None,'stdout':'','stderr':'simulated disconnect'}) as lost:
            ex.submit(unknown['id']);ex.submit(unknown['id'])
            assert lost.call_count==1 and ex.get(unknown['id'])['state']=='UNKNOWN'
    recycled=recycle.move('projects',project['id'],project)
    assert recycle.inactive('attempts',unknown['id'],unknown)
    recycle.restore('projects',project['id'])
    assert not recycle.inactive('projects',project['id'])
    return {'simulation':True,'real_ssh_requests':0,'freeze':'PASS','prepare':'PASS','once_submit':'PASS',
        'completion_parser':'PASS','branch_binding':'PASS','unknown_no_resubmit':'PASS','project_recycle_restore':'PASS',
        'scientific_validation':'NOT_PERFORMED','synthetic_potcar':True}


if __name__=='__main__':print(json.dumps(run(sys.argv[1],sys.argv[2]),ensure_ascii=False))
