import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

if __package__:
    from .core import BridgeClient
else:
    from core import BridgeClient


DEFAULT_URL = 'https://augent.uva-advanced.ru'
MAX_EVENTS = 500
MAX_LOG_LINES = 300
POLL_MS = 100
CLOSE_TIMEOUT = 4.0
STATUSES = {
    'connecting': 'Connecting',
    'online': 'Online',
    'reconnecting': 'Reconnecting',
    'stopped': 'Stopped',
    'unauthorized': 'Unauthorized',
}


def _run_client(client, emit):
    try:
        client.run()
    except Exception:
        emit('error', 'Connection worker failed. Check the server and try again.')


def _stop_client(client, emit):
    try:
        client.stop()
    except Exception:
        emit('error', 'Could not stop the connection cleanly. Close the window if needed.')


class BridgeDesktop:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue(maxsize=MAX_EVENTS)
        self.client = None
        self.worker = None
        self.stopper = None
        self.closing = False
        self.disconnecting = False
        self.unauthorized = False
        self.close_deadline = None
        self.url = tk.StringVar(value=DEFAULT_URL)
        self.token = tk.StringVar()
        self.status = tk.StringVar(value='Stopped')
        self._build_ui()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(POLL_MS, self._poll)

    def _build_ui(self):
        self.root.title('Device Bridge')
        self.root.geometry('760x540')
        self.root.minsize(640, 480)
        style = ttk.Style(self.root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('Title.TLabel', font=('Segoe UI', 19, 'bold'))
        style.configure('Status.TLabel', font=('Segoe UI', 11, 'bold'))
        style.configure('Hint.TLabel', foreground='#526174')
        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(7, weight=1)
        ttk.Label(frame, text='Device Bridge', style='Title.TLabel').grid(
            row=0, column=0, sticky='w')
        ttk.Label(frame, text='Secure device connection · Ping only · No shell',
                  style='Hint.TLabel').grid(row=1, column=0, sticky='w', pady=(4, 20))

        fields = ttk.Frame(frame)
        fields.grid(row=2, column=0, sticky='ew')
        fields.columnconfigure(1, weight=1)
        ttk.Label(fields, text='Server URL').grid(row=0, column=0, sticky='w', padx=(0, 14))
        self.url_entry = ttk.Entry(fields, textvariable=self.url)
        self.url_entry.grid(row=0, column=1, columnspan=3, sticky='ew', pady=(0, 12))
        ttk.Label(fields, text='Device token').grid(row=1, column=0, sticky='w', padx=(0, 14))
        self.token_entry = ttk.Entry(fields, textvariable=self.token, show='*')
        self.token_entry.grid(row=1, column=1, sticky='ew')
        self.paste_button = ttk.Button(fields, text='Paste', command=self.paste_token, width=8)
        self.paste_button.grid(row=1, column=2, padx=(8, 0))
        self.clear_token_button = ttk.Button(fields, text='Clear', command=self.clear_token, width=8)
        self.clear_token_button.grid(row=1, column=3, padx=(8, 0))
        ttk.Label(frame, text='Use the device token issued in your browser. Never a login token.\n'
                  'Token stays in memory only; the field is cleared when you connect.',
                  style='Hint.TLabel', wraplength=680).grid(
                      row=3, column=0, sticky='w', pady=(10, 16))

        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, sticky='ew')
        self.connect_button = ttk.Button(actions, text='Connect', command=self.connect)
        self.connect_button.pack(side='left')
        self.disconnect_button = ttk.Button(actions, text='Disconnect', command=self.disconnect,
                                            state='disabled')
        self.disconnect_button.pack(side='left', padx=8)
        ttk.Label(actions, textvariable=self.status, style='Status.TLabel').pack(side='right')
        ttk.Separator(frame).grid(row=5, column=0, sticky='ew', pady=16)
        log_header = ttk.Frame(frame)
        log_header.grid(row=6, column=0, sticky='ew', pady=(0, 8))
        ttk.Label(log_header, text='Connection log').pack(side='left')
        self.clear_log_button = ttk.Button(log_header, text='Clear log', command=self.clear_log)
        self.clear_log_button.pack(side='right')
        self.log = ScrolledText(frame, height=9, wrap='word', state='disabled',
                                font=('Consolas', 10), relief='solid', borderwidth=1,
                                background='#f7f9fc', foreground='#243246')
        self.log.grid(row=7, column=0, sticky='nsew')
        self.log.tag_configure('error', foreground='#a32121')
        self.log.tag_configure('status', foreground='#235b91')
        self._append_log('log', 'Ready. Paste a device token, then select Connect.')
        self.token_entry.focus_set()

    def _busy(self):
        return any(thread is not None and thread.is_alive()
                   for thread in (self.worker, self.stopper))

    def _set_controls(self, active):
        editable = not active and not self.closing
        state = 'normal' if editable else 'disabled'
        for widget in (self.url_entry, self.token_entry, self.paste_button,
                       self.clear_token_button, self.connect_button):
            widget.configure(state=state)
        self.disconnect_button.configure(
            state='normal' if active and not self.disconnecting and not self.closing else 'disabled')
        self.clear_log_button.configure(state='disabled' if self.closing else 'normal')

    def paste_token(self):
        if self._busy() or self.closing:
            return
        try:
            value = self.root.clipboard_get()
        except tk.TclError:
            self._append_log('error', 'Clipboard does not contain text.')
            return
        if len(value) > 8192 or '\n' in value.strip() or '\r' in value.strip():
            self._append_log('error', 'Paste a single device token, not a response or document.')
            return
        self.token.set(value.strip())
        self.token_entry.focus_set()
        self.token_entry.icursor('end')

    def clear_token(self):
        self.token.set('')

    def clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def _append_log(self, kind, message):
        text = ' '.join(str(message).split())[:1000]
        line = f'{time.strftime("%H:%M:%S")}  {kind.upper()}  {text}\n'
        self.log.configure(state='normal')
        self.log.insert('end', line, kind)
        count = int(self.log.index('end-1c').split('.')[0]) - 1
        if count > MAX_LOG_LINES:
            self.log.delete('1.0', f'{count - MAX_LOG_LINES + 1}.0')
        self.log.configure(state='disabled')
        self.log.see('end')

    def _event_sink(self, token):
        events = self.events
        secrets = tuple(value for value in (token, token.strip()) if value)

        def emit(kind, message):
            if kind not in ('status', 'log', 'error'):
                return
            text = str(message)
            for secret in secrets:
                text = text.replace(secret, '[redacted]')
            item = (kind, ' '.join(text.split())[:1000])
            try:
                events.put_nowait(item)
            except queue.Full:
                try:
                    events.get_nowait()
                except queue.Empty:
                    pass
                try:
                    events.put_nowait(item)
                except queue.Full:
                    pass

        return emit

    def connect(self):
        if self.closing or self._busy() or self.client is not None:
            return
        emit = self._event_sink(self.token.get())
        try:
            client = BridgeClient(self.url.get(), self.token.get(), on_event=emit)
        except ValueError:
            self._append_log('error', 'Invalid server URL or device token. Check both fields.')
            return
        except Exception:
            self._append_log('error', 'Could not create the client. Check the installation.')
            return
        self.clear_token()
        self.unauthorized = False
        self.disconnecting = False
        self.client = client
        self.status.set('Connecting')
        self._set_controls(True)
        self.worker = threading.Thread(target=_run_client, args=(client, emit), daemon=True,
                                       name='device-bridge-connection')
        try:
            self.worker.start()
        except RuntimeError:
            self.client = None
            self.worker = None
            self.status.set('Stopped')
            self._set_controls(False)
            self._append_log('error', 'Could not start the connection worker.')

    def _request_stop(self):
        if self.client is None or self.stopper is not None:
            return
        self.stopper = threading.Thread(target=_stop_client,
                                       args=(self.client, self._event_sink('')),
                                       daemon=True, name='device-bridge-stop')
        try:
            self.stopper.start()
        except RuntimeError:
            self.stopper = None
            self._append_log('error', 'Could not start shutdown. Close the window if needed.')

    def disconnect(self):
        if self.closing or self.client is None or self.disconnecting:
            return
        self.disconnecting = True
        self.status.set('Disconnecting')
        self._set_controls(True)
        self._request_stop()

    def _poll(self):
        for _ in range(100):
            try:
                kind, message = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'status':
                if message == 'unauthorized':
                    self.unauthorized = True
                if not self.closing and not self.disconnecting:
                    self.status.set('Unauthorized' if self.unauthorized
                                    else STATUSES.get(message, 'Working'))
            self._append_log(kind, message)
        if self.closing:
            if not self._busy() or time.monotonic() >= self.close_deadline:
                self.root.destroy()
                return
        elif self.client is not None and not self._busy() and self.events.empty():
            self.worker = None
            self.stopper = None
            self.client = None
            self.disconnecting = False
            self.status.set('Unauthorized' if self.unauthorized else 'Stopped')
            self._set_controls(False)
            self._append_log('log', 'Connection ended. Paste the token again to reconnect.')
        self.root.after(POLL_MS, self._poll)

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.clear_token()
        self.close_deadline = time.monotonic() + CLOSE_TIMEOUT
        self.status.set('Closing')
        self._set_controls(True)
        self._request_stop()
        if not self._busy():
            self.root.destroy()


def main():
    root = tk.Tk()
    BridgeDesktop(root)
    root.mainloop()


if __name__ == '__main__':
    main()
