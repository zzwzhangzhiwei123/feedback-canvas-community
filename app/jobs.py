"""Small worker queue; every UI callback is delivered on the GUI thread."""
import traceback
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

class Work(QRunnable):
    def __init__(self, owner, ident, function):
        super().__init__();self.owner=owner;self.ident=ident;self.function=function
    def run(self):
        try: result,error=self.function(),''
        except Exception: result,error=None,traceback.format_exc()
        self.owner.completed.emit(self.ident,result,error)

class Jobs(QObject):
    completed=Signal(int,object,str)
    def __init__(self,parent=None):
        super().__init__(parent);self.pool=QThreadPool(self);self.pool.setMaxThreadCount(2)
        self.callbacks={};self.serial=0
        self.completed.connect(self._deliver)
    def submit(self,function,callback,priority=0):
        self.serial+=1;ident=self.serial;self.callbacks[ident]=callback
        self.pool.start(Work(self,ident,function),priority)
        return ident
    @Slot(int,object,str)
    def _deliver(self,ident,result,error):
        callback=self.callbacks.pop(ident,None)
        if callback:callback(result,error)
