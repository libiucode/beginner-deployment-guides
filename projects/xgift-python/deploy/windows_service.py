"""SCM wrappers: run with the venv Python. No registration on import."""
import os
from pathlib import Path
import subprocess
import sys
import time
import win32event
import win32service
import win32serviceutil

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

class BaseService(win32serviceutil.ServiceFramework):
    command=''
    def __init__(self,args):
        super().__init__(args)
        self.stop_event=win32event.CreateEvent(None,0,0,None)
        self.child=None
    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
    def SvcDoRun(self):
        from deploy.environment import load_environment
        env=os.environ.copy()
        load_environment(ROOT/'private'/'server.env',env)
        control=ROOT/'private'/f'{self._svc_name_}.stop'
        control.unlink(missing_ok=True)
        # Explicit venv executable, never SCM's pythonservice.exe.
        python=ROOT/'.venv'/'Scripts'/'python.exe'
        self.child=subprocess.Popen([str(python),str(ROOT/'manage.py'),self.command,'--stop-file',str(control)],cwd=ROOT,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
        while self.child.poll() is None:
            if win32event.WaitForSingleObject(self.stop_event,1000)==win32event.WAIT_OBJECT_0:
                control.touch()
                # Keep reporting stop progress; do not terminate an in-flight payment.
                while self.child.poll() is None:
                    self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING,waitHint=10000)
                    time.sleep(1)
                break
        control.unlink(missing_ok=True)
        if self.child.returncode:
            raise RuntimeError('X child exited unsuccessfully; inspect configuration privately')

class WebService(BaseService):
    _svc_name_='XGiftWeb'
    _svc_display_name_='X Website'
    command='serve_xgift'

class WorkerService(BaseService):
    _svc_name_='XGiftWorker'
    _svc_display_name_='X Worker'
    command='run_worker'

if __name__=='__main__':
    kind=sys.argv.pop(1)
    win32serviceutil.HandleCommandLine({'web':WebService,'worker':WorkerService}[kind])
