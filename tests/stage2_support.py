"""Explicit local-client TEST DOUBLE for offline runner tests, not real MCP."""
from contextlib import asynccontextmanager, contextmanager
import importlib
import sys
from types import ModuleType


@contextmanager
def runner_module():
    names=['holdout.mcp_server','holdout.agent']
    saved={name:sys.modules.get(name) for name in names}
    import holdout
    attrs={name.split('.')[-1]:getattr(holdout,name.split('.')[-1],None) for name in names}
    fake=ModuleType('holdout.mcp_server')
    fake.INSTRUCTIONS='TEST-ONLY local protocol double.'
    fake.result_dict=lambda result:result
    @asynccontextmanager
    async def local_client(service):
        class Client:
            async def call_tool(self,name,args):
                if name=='release_status':return service.compact_status()
                if name=='recall_memory':return service.recall_memories(**args)
                if name=='suggest_procedures':return service.suggest_procedures(**args)
                if name=='plan_investigation':return service.plan_investigation(**args)
                if name=='investigate_candidate':return service.investigate(**args)
                if name=='solve_release':return service.solve_release(**args)
                if name=='stage_release':return service.stage_release(**args)
                raise AssertionError('Unimplemented test-double tool '+name)
        yield Client()
    fake.local_client=local_client
    for name in names:sys.modules.pop(name,None)
    sys.modules['holdout.mcp_server']=fake
    try:
        yield importlib.import_module('holdout.agent')
    finally:
        for name,old in saved.items():
            sys.modules.pop(name,None)
            if old is not None:sys.modules[name]=old
        for name,value in attrs.items():
            if value is None:
                if hasattr(holdout,name):delattr(holdout,name)
            else:setattr(holdout,name,value)
