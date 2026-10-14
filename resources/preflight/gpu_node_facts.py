"""Direct-node GPU facts; existing NVML first, one nvidia-smi XML fallback.

No package installation. Free VRAM is diagnostic, not a reservation or a
prediction of the next calculation's peak memory. Unknown stays unknown.
"""
import base64,json,re,socket,subprocess,time
import xml.etree.ElementTree as ET


def nvml_devices():
    import pynvml as nv
    nv.nvmlInit()
    try:
        rows=[]
        for index in range(nv.nvmlDeviceGetCount()):
            handle=nv.nvmlDeviceGetHandleByIndex(index)
            try:current,_=nv.nvmlDeviceGetMigMode(handle)
            except nv.NVMLError_NotSupported:current=0
            if current:raise ValueError('MIG is outside whole-node GPU mapping')
            if nv.nvmlDeviceGetComputeMode(handle)==nv.NVML_COMPUTEMODE_PROHIBITED:raise ValueError('GPU compute is prohibited')
            memory=nv.nvmlDeviceGetMemoryInfo(handle)
            processes=nv.nvmlDeviceGetComputeRunningProcesses(handle)
            name=nv.nvmlDeviceGetName(handle);uuid=nv.nvmlDeviceGetUUID(handle)
            rows.append({'index':index,'uuid':uuid.decode() if isinstance(uuid,bytes) else uuid,
                'name':name.decode() if isinstance(name,bytes) else name,
                'memory_total_mib':memory.total/1048576,'memory_used_mib':(memory.total-memory.free)/1048576,
                'memory_free_mib':memory.free/1048576,'utilization_percent':nv.nvmlDeviceGetUtilizationRates(handle).gpu,
                'compute_processes':len(processes)})
        return rows,'NVML'
    finally:nv.nvmlShutdown()


def xml_devices(text):
    rows=[]
    def number(value):
        match=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)\s*(?:MiB|%)?',str(value).strip())
        return float(match[1]) if match else None
    for gpu in ET.fromstring(text).findall('gpu'):
        if gpu.findtext('mig_mode/current_mig')=='Enabled' or gpu.findtext('compute_mode')=='Prohibited':raise ValueError('GPU execution mode is unsupported')
        index=gpu.findtext('minor_number');processes=gpu.find('processes')
        if not str(index).isdigit() or processes is None or (processes.text or '').strip():raise ValueError('GPU identity/process report is incomplete')
        active=[]
        for process in processes.findall('process_info'):
            kind=process.findtext('type')
            if kind not in ('C','G','C+G'):raise ValueError('Unknown process type')
            if 'C' in kind:active.append(process)
        total=number(gpu.findtext('fb_memory_usage/total'));free=number(gpu.findtext('fb_memory_usage/free'))
        rows.append({'index':int(index),'uuid':gpu.findtext('uuid'),'name':gpu.findtext('product_name'),
            'memory_total_mib':total,'memory_used_mib':total-free if total is not None and free is not None else None,
            'memory_free_mib':free,
            'utilization_percent':number(gpu.findtext('utilization/gpu_util')),'compute_processes':len(active)})
    return rows,'NVIDIA_SMI_XML'


def collect():
    try:return nvml_devices()
    except Exception:
        result=subprocess.run(['nvidia-smi','-q','-x'],capture_output=True,text=True,timeout=12)
        if result.returncode:raise ValueError('GPU read-only query failed')
        return xml_devices(result.stdout)


def main():
    node=socket.gethostname().split('.')[0];started=time.monotonic()
    try:
        rows,method=collect()
        if not rows or len({r['index'] for r in rows})!=len(rows):raise ValueError('GPU inventory is ambiguous')
        detail=base64.b64encode(json.dumps(rows,separators=(',',':')).encode()).decode()
        print('GPU_NODE={} GPU_QUERY=OK GPUS_PHYSICAL_TOTAL={} GPU_INDICES={} GPU_ACTIVE_PROCESSES={} GPU_METHOD={} GPU_QUERY_SECONDS={:.4f} GPU_DETAIL_B64={}'.format(
            node,len(rows),','.join(str(r['index']) for r in rows),sum(r['compute_processes'] for r in rows),method,time.monotonic()-started,detail))
    except Exception:
        print('GPU_NODE={} GPU_QUERY=UNKNOWN GPU_QUERY_SECONDS={:.4f}'.format(node,time.monotonic()-started))


if __name__=='__main__':main()
