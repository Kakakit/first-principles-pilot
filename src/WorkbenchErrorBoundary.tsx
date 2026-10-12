import {Component} from 'react';
import type {ErrorInfo,ReactNode} from 'react';

export default class WorkbenchErrorBoundary extends Component<{children:ReactNode;onReturn?:()=>void},{error:Error|null}> {
  state:{error:Error|null}={error:null};
  static getDerivedStateFromError(error:Error){return {error};}
  componentDidCatch(error:Error,info:ErrorInfo){console.error('工作区显示失败',error,info.componentStack);}
  render(){
    if(!this.state.error)return this.props.children;
    return <section className="empty-state" role="alert"><h2>此板块暂时无法显示</h2><p>页面加载发生错误。已保存的项目、任务和冻结输入继续保留。</p><p>先返回项目概览；仍无法打开时，在工作台控制页重启服务，再重新载入。</p><div className="row">{this.props.onReturn&&<button className="button primary" onClick={this.props.onReturn}>返回项目概览</button>}<button className="button" onClick={()=>location.reload()}>重新载入页面</button></div><details><summary>错误详情</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{this.state.error.message}</pre></details></section>;
  }
}
