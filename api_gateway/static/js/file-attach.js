/**
 * FileAttach — модуль прикрепления файлов к сообщению.
 *
 * Публичное API (window.FileAttach):
 *   init(attachBtn, inputContainer)  — инициализация для конкретного инпута
 *   getFiles()                       — текущий массив File
 *   upload(chatId, msgId)            — POST /api/chats/upload_files
 *   clear()                          — сбросить все файлы и превью
 */

const FileAttach = (() => {

    /* ─── состояние ──────────────────────────────────────────── */
    let _files        = [];          // File[]
    let _attachBtn    = null;        // <button id="attach-btn"> | <button id="start-attach-btn">
    let _inputBox     = null;        // .chat-input-box | .start-input-box
    let _previewBar   = null;        // .fa-preview-bar (создаём динамически)
    let _hiddenInput  = null;        // <input type="file"> скрытый

    /* ─── расширение → метка ─────────────────────────────────── */
    const EXT_LABEL = {
        pdf:  'PDF',  docx: 'DOC',  doc:  'DOC',
        xlsx: 'XLS',  xls:  'XLS',  csv:  'CSV',
        pptx: 'PPT',  ppt:  'PPT',
        txt:  'TXT',  md:   'MD',
        png:  'PNG',  jpg:  'JPG',  jpeg: 'JPG',  gif:  'GIF',  webp: 'WEBP', svg: 'SVG',
        mp4:  'MP4',  mov:  'MOV',  avi:  'AVI',
        zip:  'ZIP',  rar:  'RAR',  '7z': '7Z',
        json: 'JSON', xml:  'XML',  html: 'HTML', py: 'PY',
        js:   'JS',   ts:   'TS',   css:  'CSS',
    };

    /* ─── расширение → группа цвета ─────────────────────────── */
    const EXT_COLOR = {
        pdf:  'red',   doc: 'blue', docx: 'blue',
        xls:  'green', xlsx: 'green', csv: 'green',
        ppt:  'orange', pptx: 'orange',
        png:  'purple', jpg: 'purple', jpeg: 'purple',
        gif:  'purple', webp: 'purple', svg: 'purple', bmp: 'purple', avif: 'purple',
        mp4:  'purple', mov: 'purple', avi: 'purple', webm: 'purple', mkv: 'purple', m4v: 'purple', ogv: 'purple',
        zip:  'gray',  rar: 'gray', '7z': 'gray',
        json: 'yellow', xml: 'yellow',
        py:   'teal',  js: 'yellow', ts: 'blue', css: 'purple',
    };

    const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'avif', 'ico']);
    const VIDEO_EXTS = new Set(['mp4', 'webm', 'mov', 'm4v', 'ogv', 'avi', 'mkv']);

    function _getExt(filename) {
        return (filename.split('.').pop() || '').toLowerCase();
    }

    function _isImageFile(file) {
        const ext = _getExt(file?.name || '');
        const mime = (file?.type || '').toLowerCase();
        return mime.startsWith('image/') || IMAGE_EXTS.has(ext);
    }

    function _isVideoFile(file) {
        if (_isImageFile(file)) return false;
        const ext = _getExt(file?.name || '');
        const mime = (file?.type || '').toLowerCase();
        return mime.startsWith('video/') || VIDEO_EXTS.has(ext);
    }

    function _getPreviewUrl(file) {
        if (!file) return '';
        if (!file._previewUrl) {
            try { file._previewUrl = URL.createObjectURL(file); } catch (_) { file._previewUrl = ''; }
        }
        return file._previewUrl;
    }

    function _getLabel(filename) {
        const ext = _getExt(filename);
        return EXT_LABEL[ext] || ext.toUpperCase().slice(0, 4) || 'FILE';
    }

    function _getColor(fileOrName) {
        if (fileOrName && typeof fileOrName === 'object') {
            if (_isImageFile(fileOrName) || _isVideoFile(fileOrName)) return 'purple';
            const ext = _getExt(fileOrName.name || '');
            return EXT_COLOR[ext] || 'gray';
        }
        const ext = _getExt(String(fileOrName || ''));
        return EXT_COLOR[ext] || 'gray';
    }

    function getFilesMeta() {
        return _files.map(f => ({
            name: f.name,
            ext:  _getExt(f.name),
        }));
    }

    /* ─── создать скрытый input[file] ───────────────────────── */
    function _createHiddenInput() {
        const inp = document.createElement('input');
        inp.type     = 'file';
        inp.multiple = true;
        inp.accept   = '*/*';
        inp.style.cssText = 'position:absolute;width:1px;height:1px;opacity:0;pointer-events:none;';
        inp.setAttribute('aria-hidden', 'true');
        document.body.appendChild(inp);

        inp.addEventListener('change', () => {
            if (!inp.files?.length) return;
            _addFiles(Array.from(inp.files));
            inp.value = ''; // сброс чтобы можно было выбрать тот же файл снова
        });

        return inp;
    }

    /* ─── toast-уведомление ──────────────────────────────────── */
    const MAX_FILE_SIZE = 20 * 1024 * 1024; // 20 МБ
    const MAX_FILES = 10; // максимум файлов на одно сообщение

    function _showToast(message) {
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            document.body.appendChild(container);
        }

        const toast = document.createElement('div');
        toast.className = 'toast toast--error';
        toast.textContent = message;
        container.appendChild(toast);

        // Показываем
        requestAnimationFrame(() => requestAnimationFrame(() => toast.classList.add('toast--visible')));

        // Убираем через 4 секунды
        setTimeout(() => {
            toast.classList.remove('toast--visible');
            toast.addEventListener('transitionend', () => toast.remove(), { once: true });
        }, 4000);
    }

    /* ─── добавить файлы ─────────────────────────────────────── */
    function _addFiles(newFiles) {
        // Лимит на общее число файлов в одном сообщении (с учётом уже прикреплённых).
        let _slots = MAX_FILES - _files.length;
        if (_slots <= 0) {
            _showToast(`Можно прикрепить не более ${MAX_FILES} файлов.`);
            return;
        }
        if (newFiles.length > _slots) {
            _showToast(`Можно прикрепить не более ${MAX_FILES} файлов — лишние пропущены.`);
            newFiles = newFiles.slice(0, _slots);
        }
        newFiles.forEach(f => {
            // Проверка размера — не более 20 МБ
            if (f.size > MAX_FILE_SIZE) {
                _showToast(`Файл «${f.name}» превышает 20 МБ и не будет прикреплён.`);
                return;
            }
            // дедупликация по имени + размеру
            const dup = _files.find(x => x.name === f.name && x.size === f.size);
            if (!dup) _files.push(f);
        });
        _renderPreview();
        _updateAttachBtn();
    }

    /* ─── удалить файл (по индексу или ссылке) ────────────────── */
    function _removeFile(fileOrIdx) {
        const file = typeof fileOrIdx === 'number' ? _files[fileOrIdx] : fileOrIdx;
        if (!file || file._faRemoving) return;
        const bar = _previewBar;
        const el = bar ? Array.from(_barTrack(bar).children).find(c => c._faFile === file) : null;
        file._faRemoving = true;
        const finish = () => {
            const i = _files.indexOf(file);
            if (i !== -1) _files.splice(i, 1);
            if (el) {
                window.FileTiles?.stopAudioFor(el);
                el.remove();
            }
            if (file._previewUrl) {
                try { URL.revokeObjectURL(file._previewUrl); } catch (_) {}
                file._previewUrl = '';
            }
            file._faRemoving = false;
            _renderPreview();
            _updateAttachBtn();
        };
        const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
        if (el && !reduce) {
            window.FileTiles?.stopAudioFor(el);
            el.classList.remove('fa-tile--enter');
            el.classList.add('fa-tile--leave');
            _syncBarVisible(bar);
            setTimeout(finish, 200);
        } else {
            finish();
        }
    }

    /* ─── обновить подсветку кнопки прикрепить ──────────────── */
    function _updateAttachBtn() {
        if (_attachBtn) {
            _attachBtn.classList.toggle('has-files', _files.length > 0);
        }
        try { window.__catchpmApp?._syncSendButtonState?.(); } catch (_) {}
        try { window.GroupChat?._syncInputBtn?.(); } catch (_) {}
    }

    /* ─── создать/найти preview-bar внутри inputBox ─────────── */
    function _ensurePreviewBar() {
        if (!_inputBox) return null;
        let bar = _inputBox.querySelector('.fa-preview-bar');
        if (!bar) {
            bar = document.createElement('div');
            bar.className = 'fa-preview-bar';
            // вставляем ПЕРЕД контентедитабл-полем (первым дочерним)
            _inputBox.insertBefore(bar, _inputBox.firstChild);
        }
        if (window.FileTiles) window.FileTiles.enhanceScroller(bar, { wheel: true });
        _previewBar = bar;
        return bar;
    }

    function _barTrack(bar) {
        return window.FileTiles ? window.FileTiles.trackOf(bar) : bar;
    }

    function _syncBarVisible(bar) {
        if (!bar) return;
        const has = bar.querySelector('.fa-chip:not(.fa-tile--leave)') !== null;
        // reflow перед показом, чтобы transition сработал и у только что созданной полосы
        if (has && !bar.classList.contains('fa-preview-bar--visible')) void bar.offsetHeight;
        bar.classList.toggle('fa-preview-bar--visible', has);
    }

    /* ─── квадратная плитка файла (FileTiles) ─────────────────── */
    function _isAudioFile(file) {
        if (_isImageFile(file) || _isVideoFile(file)) return false;
        if (window.FileTiles) return window.FileTiles.kindOf(file?.name, file?.type) === 'audio';
        return (file?.type || '').toLowerCase().startsWith('audio/');
    }

    function _makeFileIcon(file, idx) {
        const isMedia = _isImageFile(file) || _isVideoFile(file) || _isAudioFile(file);
        const blobUrl = _getPreviewUrl(file);
        const holder = document.createElement('div');
        if (window.FileTiles) {
            holder.innerHTML = window.FileTiles.tileHtml(
                { name: file.name, size: file.size, type: file.type, url: isMedia ? blobUrl : '', dlUrl: blobUrl },
                { removable: true, idx, enter: !file._faShown },
            );
        } else {
            holder.innerHTML = `<div class="fa-chip fa-chip--${_getColor(file)}" title="${_escHtml(file.name)}">`
                + `<span class="fa-chip__name">${_escHtml(file.name)}</span>`
                + `<button class="fa-chip__remove" type="button" aria-label="Удалить файл">×</button></div>`;
        }
        const el = holder.firstElementChild;
        file._faShown = true;
        el._faFile = file;
        el.querySelector('.fa-chip__remove')?.addEventListener('click', (e) => {
            e.stopPropagation();
            _removeFile(file);
        });
        return el;
    }

    /* ─── отрисовать превью ──────────────────────────────────── */
    // Существующие плитки переиспользуются: превью не перезагружается и не
    // анимируется повторно, въезжают только новые файлы.
    function _renderPreview() {
        const bar = _ensurePreviewBar();
        if (!bar) return;
        const track = _barTrack(bar);
        const keep = new Set(_files);
        track.querySelectorAll('.fa-chip:not(.input-chip)').forEach(el => {
            if (!el._faFile || !keep.has(el._faFile)) el.remove();
        });
        const firstInput = track.querySelector('.input-chip');
        let created = false;
        _files.forEach((f, idx) => {
            let el = Array.from(track.children).find(c => c._faFile === f);
            if (!el) {
                el = _makeFileIcon(f, idx);
                track.insertBefore(el, firstInput);
                created = true;
            }
            el.dataset.idx = String(idx);
        });
        _syncBarVisible(bar);
        if (created && track !== bar) {
            requestAnimationFrame(() => track.scrollTo({ left: track.scrollWidth, behavior: 'smooth' }));
        }
    }

    /* ─── именованные хендлеры (для снятия слушателей) ───────── */
    // Храним ссылки на привязанные обработчики и контейнер/кнопку,
    // чтобы при повторном init() (смена контекста: агент <-> общий чат)
    // снять старые слушатели и не плодить утечку на прошлых боксах.
    let _boundBox      = null;   // элемент, на который сейчас навешаны dnd/paste
    let _boundBtn      = null;   // кнопка, на которую навешан click
    let _hDragEnter    = null;
    let _hDragOver     = null;
    let _hDragLeave    = null;
    let _hDropClear    = null;
    let _hDrop         = null;
    let _hPaste        = null;
    let _hBtnClick     = null;

    /* ─── drag & drop на inputBox ────────────────────────────── */
    function _bindDragDrop(box) {
        _hDragEnter = e => { e.preventDefault(); box.classList.add('fa-dragover'); };
        _hDragOver  = e => { e.preventDefault(); box.classList.add('fa-dragover'); };
        _hDragLeave = () => box.classList.remove('fa-dragover');
        _hDropClear = () => box.classList.remove('fa-dragover');
        _hDrop      = e => {
            e.preventDefault();
            const dt = e.dataTransfer;
            if (dt?.files?.length) _addFiles(Array.from(dt.files));
        };
        box.addEventListener('dragenter', _hDragEnter, false);
        box.addEventListener('dragover',  _hDragOver,  false);
        box.addEventListener('dragleave', _hDragLeave, false);
        box.addEventListener('drop',      _hDropClear, false);
        box.addEventListener('drop',      _hDrop,      false);
    }

    /* ─── paste из буфера ────────────────────────────────────── */
    function _bindPaste(box) {
        _hPaste = e => {
            const items = e.clipboardData?.items;
            if (!items) return;
            const fileItems = Array.from(items).filter(i => i.kind === 'file');
            if (!fileItems.length) return;
            // Скриншоты из буфера почти всегда приходят с одинаковым именем
            // (image.png) и близким размером -> дедуп по name+size отбрасывал
            // повторы, и в стек попадал только последний. Переименовываем
            // вставленные файлы, чтобы каждый Ctrl+V добавлял НОВУЮ картинку.
            const pastedFiles = fileItems.map(i => {
                const f = i.getAsFile();
                if (!f) return null;
                const ext = (f.type && f.type.split('/')[1]) || 'png';
                const stamp = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
                const base = (f.name && f.name !== 'image.png') ? f.name.replace(/\.[^.]+$/, '') : 'pasted';
                try {
                    return new File([f], `${base}-${stamp}.${ext}`, { type: f.type });
                } catch (err) {
                    // старые браузеры без конструктора File — отдаём как есть
                    return f;
                }
            }).filter(Boolean);
            if (pastedFiles.length) _addFiles(pastedFiles);
        };
        box.addEventListener('paste', _hPaste);
    }

    /* ─── снять ВСЕ ранее навешанные слушатели ───────────────── */
    // Вызывается в начале init() перед перепривязкой к новому контексту.
    // Лечит баг: файл, брошенный/вставленный в чат агента, оседал
    // в контексте общего чата (или наоборот), т.к. синглтон FileAttach
    // держал слушатели на прошлом _inputBox, а гард renderMessageInput
    // не переинициализировал модуль при возврате.
    function _unbindPrevious() {
        if (_boundBox) {
            if (_hDragEnter) _boundBox.removeEventListener('dragenter', _hDragEnter, false);
            if (_hDragOver)  _boundBox.removeEventListener('dragover',  _hDragOver,  false);
            if (_hDragLeave) _boundBox.removeEventListener('dragleave', _hDragLeave, false);
            if (_hDropClear) _boundBox.removeEventListener('drop',      _hDropClear, false);
            if (_hDrop)      _boundBox.removeEventListener('drop',      _hDrop,      false);
            if (_hPaste)     _boundBox.removeEventListener('paste',     _hPaste);
            _boundBox.classList.remove('fa-dragover');
        }
        if (_boundBtn && _hBtnClick) {
            _boundBtn.removeEventListener('click', _hBtnClick);
        }
        _boundBox = _boundBtn = null;
        _hDragEnter = _hDragOver = _hDragLeave = _hDropClear = _hDrop = _hPaste = _hBtnClick = null;
    }

    /* ─── вспомогательные ────────────────────────────────────── */
    function _escHtml(str) {
        return str
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    /* ══════════════════════════════════════════════════════════
       PUBLIC API
    ══════════════════════════════════════════════════════════ */

    /**
     * Инициализировать FileAttach для конкретного инпута.
     * Вызывать каждый раз после renderMessageInput().
     *
     * @param {HTMLElement|null} attachBtn  — кнопка прикрепить (или null — найдём сами)
     * @param {HTMLElement|null} inputContainer — .message-input-container (или null — весь document)
     */
    function init(attachBtn, inputContainer) {
        const root = inputContainer || document;

        // Снимаем слушатели с ПРЕДЫДУЩЕГО контекста, иначе синглтон
        // накапливает dnd/paste на всех боксах, куда когда-либо инитили,
        // и файл оседает не в том чате (агент <-> общий чат).
        _unbindPrevious();

        // Кнопка: явная | #attach-btn | #start-attach-btn
        _attachBtn = attachBtn
            || root.querySelector('#attach-btn')
            || root.querySelector('#start-attach-btn');

        // Input-box: тот в котором живёт поле ввода
        _inputBox = root.querySelector('.chat-input-box')
                 || root.querySelector('.start-input-box')
                 || root.querySelector('.gc-input-wrapper');

        if (!_attachBtn || !_inputBox) return;

        // Скрытый input создаём один раз
        if (!_hiddenInput) {
            _hiddenInput = _createHiddenInput();
        }

        // Сбрасываем старое превью при смене контекста (новый чат / переключение)
        // НЕ трогаем _files — файлы живут до отправки
        _previewBar = null;

        // Кнопка открывает проводник (именованный хендлер — снимается в _unbindPrevious)
        _hBtnClick = () => { _hiddenInput.click(); };
        _attachBtn.addEventListener('click', _hBtnClick);

        // Drag & drop + paste
        _bindDragDrop(_inputBox);
        _bindPaste(_inputBox);

        // Запоминаем текущий контекст для последующего снятия
        _boundBox = _inputBox;
        _boundBtn = _attachBtn;

        // Если файлы уже есть — показать превью сразу
        if (_files.length > 0) {
            _renderPreview();
            _updateAttachBtn();
        }
    }

    /**
     * Вернуть массив текущих файлов.
     */
    function getFiles() {
        return [..._files];
    }

    /**
     * Загрузить файлы на сервер.
     * POST /api/chats/upload_files  multipart/form-data
     * Поля: chat_id, message_id, files[]
     *
     * @param {number|string} chatId
     * @param {string}        msgId
     * @returns {Promise<Response|null>}
     */
    async function upload(chatId, msgId) {
        if (!_files.length) return null;

        const fd = new FormData();
        fd.append('chat_id',    String(chatId));
        fd.append('message_id', String(msgId));
        _files.forEach(f => fd.append('files', f));

        try {
            const res = await fetch('/api/chats/upload_files', {
                method:      'POST',
                credentials: 'include',
                body:        fd,
            });
            if (!res.ok) {
                console.warn('FileAttach upload: server error', res.status);
            }
            return res;
        } catch (err) {
            console.error('FileAttach upload error:', err);
            return null;
        }
    }

    /**
     * Сбросить состояние (файлы + превью).
     */
    function clear() {
        _files = [];
        if (_previewBar) {
            window.FileTiles?.stopAudioFor(_previewBar);
            _previewBar.querySelectorAll('.fa-chip:not(.input-chip)').forEach(el => el.remove());
            _syncBarVisible(_previewBar);
        }
        _updateAttachBtn();
    }

    /**
     * Метаданные + локальные blob-URL для оптимистичного рендера отправленного
     * сообщения. На сервер не уходит (для тела запроса — getFilesMeta).
     */
    function getPreviewItems() {
        return _files.map(f => {
            const url = _getPreviewUrl(f);
            return { name: f.name, ext: _getExt(f.name), size: f.size, type: f.type, url, dlUrl: url };
        });
    }

    return { init, getFiles, upload, clear, getFilesMeta, getPreviewItems };

})();

window.FileAttach = FileAttach;
