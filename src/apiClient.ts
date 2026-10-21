export async function readJson(response: Response): Promise<any> {
  const text = await response.text();
  let data: any = null;
  if (text.trim()) {
    try { data = JSON.parse(text); }
    catch {
      throw new Error(response.status >= 500
        ? `服务内部错误（HTTP ${response.status}），错误响应无法读取。当前填写内容保留，请重试；仍失败时请核查 API 错误日志。`
        : `服务响应格式异常（HTTP ${response.status}）：返回内容不是 JSON。当前填写内容保留，请重试。`);
    }
  }
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === 'string' && detail.trim() ? detail : `请求失败（HTTP ${response.status}）。请检查服务状态后重试。`);
  }
  if (data === null) throw new Error('服务返回空响应。请确认本机 API 正在运行，然后重试。');
  return data;
}

export async function requestJson(path: string, body?: unknown, method?: string, options?: {signal?:AbortSignal;timeoutMs?:number}): Promise<any> {
  const controller=options?.timeoutMs?new AbortController():null;
  let expired=false;
  const abort=()=>controller?.abort();
  if(options?.signal?.aborted)abort();
  else options?.signal?.addEventListener('abort',abort,{once:true});
  const timer=controller?setTimeout(()=>{expired=true;controller.abort();},options!.timeoutMs):null;
  try {
    const response=await fetch(path, {
      signal:controller?.signal||options?.signal,
      method:method||(body===undefined?'GET':'POST'),
      headers:body===undefined?undefined:{'Content-Type':'application/json'},
      body:body===undefined?undefined:JSON.stringify(body),
    }).catch(error=>{
      if(error instanceof DOMException&&error.name==='AbortError')throw error;
      throw new Error('无法连接本机 API。请检查服务是否运行；恢复后可在当前页面重试，已填写内容仍保留。');
    });
    try {return await readJson(response);}
    finally {
      if(typeof window!=='undefined'&&/\/(submit|refresh|resolve-legacy)$/.test(path))
        window.dispatchEvent(new Event('fpilot:binding-changed'));
    }
  } catch(error) {
    if(expired)throw new Error('本次请求等待超时，已结束等待，草稿保留。没有自动重试；这不代表远端程序已停止。');
    throw error;
  } finally {
    if(timer!==null)clearTimeout(timer);
    options?.signal?.removeEventListener('abort',abort);
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof TypeError || (error instanceof Error && /failed to fetch|networkerror|load failed/i.test(error.message)))
    return '无法连接本机 API。请检查服务是否运行；恢复后在当前页面重试。';
  return error instanceof Error ? error.message : '请求未完成。请检查服务状态后重试。';
}
