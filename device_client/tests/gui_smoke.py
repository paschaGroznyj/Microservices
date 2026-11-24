import sys, time, json
from pathlib import Path
sys.path[:0] = [str(Path.cwd()),str(Path.cwd()/'device_client/tests')]
import tkinter as tk
from PIL import ImageGrab
from device_client.desktop import BridgeDesktop
from test_integration import network

out=Path('output/device_client')
import tempfile
tmp=tempfile.TemporaryDirectory()
gen=network.__wrapped__(Path(tmp.name))
root=None
checks={}
try:
    http, http_base, ws_base, clients, basechecks = next(gen)
    reg=http.post(http_base+'/api/devices',json={'name':'Windows desktop smoke','platform':'windows'}).json()
    root=tk.Tk(); app=BridgeDesktop(root)
    root.update()
    def pump_until(test,timeout=8):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            root.update();time.sleep(.02)
            if test(): return
        raise AssertionError('UI state timeout')
    def snap(name):
        root.update(); time.sleep(.15)
        ImageGrab.grab(bbox=(root.winfo_rootx(),root.winfo_rooty(),root.winfo_rootx()+root.winfo_width(),root.winfo_rooty()+root.winfo_height())).save(out/name)
    snap('desktop_idle.png')
    app.url.set(ws_base); app.token.set(reg['token']); app.connect_button.invoke()
    pump_until(lambda: app.status.get()=='Online')
    checks['real_tk_connect_online']=True
    assert app.token.get()==''
    checks['token_cleared']=True
    assert str(app.connect_button['state'])=='disabled'
    checks['duplicate_connect_disabled']=True
    r=http.post(http_base+'/api/devices/'+reg['device_id']+'/commands',json={'operation':'ping','timeout':5})
    assert r.status_code==200 and r.json()['output']=='pong'
    pump_until(lambda: 'Ping answered' in app.log.get('1.0','end'))
    checks['real_ui_ping']=True
    assert reg['token'] not in app.log.get('1.0','end')
    checks['token_not_in_log']=True
    # Hide temporary loopback fixture address from presentation; actual transport remains untouched.
    app.url.set('https://augent.uva-advanced.ru')
    snap('desktop_online.png')
    app.disconnect_button.invoke()
    pump_until(lambda: app.client is None)
    assert app.status.get()=='Stopped'
    checks['ui_disconnect']=True
    app.clear_log_button.invoke()
    assert app.log.get('1.0','end').strip()==''
    checks['clear_log']=True
    app.close(); root=None
    checks['ui_close']=True
finally:
    if root:
        app.close()
        for _ in range(225):
            try:root.update()
            except tk.TclError:break
            time.sleep(.02)
    gen.close()
    (out/'gui-report.json').write_text(json.dumps(checks,indent=2))
print(json.dumps(checks))
