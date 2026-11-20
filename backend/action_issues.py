"""Stable UI locations for existing blocking errors; never classify translated messages."""
class ActionBlocked(ValueError):
    def __init__(self,message,issues):
        super().__init__(message);self.issues=issues


class IssueMessages(list):
    def __init__(self,project_id=None,node_id=None):
        super().__init__();self.issues=[];self.project_id=project_id;self.node_id=node_id

    def add(self,code,message,field,page='config',subpage='files',action='edit',node_id=None,section=None):
        self.append(message)
        target={'page':page,'field':field}
        if self.project_id:target['projectId']=self.project_id
        if node_id or self.node_id:target['nodeId']=node_id or self.node_id
        if subpage:target['subpage']=subpage
        if section:target['section']=section
        self.issues.append({'code':code,'message':message,'level':'error','action':action,'target':target})


def error_detail(error):
    issues=getattr(error,'issues',None)
    return {'message':str(error),'issues':issues} if issues else str(error)
