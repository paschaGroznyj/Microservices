/* Fixed, body-portaled popups. No listeners or animation frames survive close.
 * attach(popup, anchor, { gap=4, margin=4, x=0, y=0,
 *                         ariaExpanded=true, onClose(reason) })
 * x/y are CSS-pixel offsets added to the normal right-aligned/below (or above)
 * position, before viewport clamping. They move with the anchor, not the page.
 * Returns { update(), close() }. External .remove() is also supported.
 * Close reasons: closed, removed, anchor-detached, anchor-hidden, replaced.
 * Dismissal (outside click, Escape, action selection) belongs to the caller.
 */
(function () {
    'use strict';

    const popups = new WeakMap();
    const anchors = new WeakMap();
    const finite = (value, fallback) => Number.isFinite(value) ? value : fallback;
    const clamp = (value, min, max) => Math.max(min, Math.min(value, max));
    const clips = /^(auto|scroll|hidden|clip|overlay)$/;

    function visibleRect(anchor) {
        const rect = anchor.getBoundingClientRect();
        let left = Math.max(0, rect.left);
        let top = Math.max(0, rect.top);
        let right = Math.min(document.documentElement.clientWidth, rect.right);
        let bottom = Math.min(window.innerHeight, rect.bottom);
        if (!anchor.getClientRects().length || rect.width <= 0 || rect.height <= 0) return null;

        for (let node = anchor; node; node = node.parentElement) {
            const style = getComputedStyle(node);
            if (style.display === 'none' || style.visibility === 'hidden' ||
                style.visibility === 'collapse' || Number(style.opacity) === 0 ||
                style.contentVisibility === 'hidden') return null;

            // Root overflow clips to the viewport, already intersected above.
            if (node === anchor || node === document.body || node === document.documentElement) continue;
            if (clips.test(style.overflowX) || clips.test(style.overflowY)) {
                const box = node.getBoundingClientRect();
                const sx = node.offsetWidth ? box.width / node.offsetWidth : 1;
                const sy = node.offsetHeight ? box.height / node.offsetHeight : 1;
                const x = box.left + node.clientLeft * sx;
                const y = box.top + node.clientTop * sy;
                if (clips.test(style.overflowX)) {
                    left = Math.max(left, x);
                    right = Math.min(right, x + node.clientWidth * sx);
                }
                if (clips.test(style.overflowY)) {
                    top = Math.max(top, y);
                    bottom = Math.min(bottom, y + node.clientHeight * sy);
                }
            }
        }
        return right > left && bottom > top ? rect : null;
    }

    function attach(popup, anchor, opts = {}) {
        if (!(popup instanceof HTMLElement) || !(anchor instanceof HTMLElement) ||
            popup === anchor || popup.contains(anchor)) {
            throw new TypeError('AnchoredPopup.attach requires a popup and a separate anchor HTMLElement');
        }
        popups.get(popup)?.close('replaced');
        anchors.get(anchor)?.close('replaced');

        const gap = Math.max(0, finite(opts.gap, 4));
        const margin = Math.max(0, finite(opts.margin, 4));
        const x = finite(opts.x, 0);
        const y = finite(opts.y, 0);
        let closed = false;
        let frame = 0;
        const observer = new MutationObserver(() => {
            if (!popup.isConnected) close('removed');
            else if (!anchor.isConnected) close('anchor-detached');
        });

        function close(reason = 'closed') {
            if (closed) return;
            closed = true;
            cancelAnimationFrame(frame);
            frame = 0;
            observer.disconnect();
            popups.delete(popup);
            anchors.delete(anchor);
            if (opts.ariaExpanded !== false) anchor.setAttribute('aria-expanded', 'false');
            popup.remove();
            opts.onClose?.(reason);
        }

        function setStyle(name, value) {
            if (popup.style[name] !== value) popup.style[name] = value;
        }

        function update() {
            if (closed) return;
            if (!popup.isConnected) return close('removed');
            if (!anchor.isConnected) return close('anchor-detached');
            const rect = visibleRect(anchor);
            if (!rect) return close('anchor-hidden');
            if (popup.parentElement !== document.body) document.body.appendChild(popup);

            const width = document.documentElement.clientWidth;
            const height = window.innerHeight;
            const mx = Math.min(margin, width / 2);
            const my = Math.min(margin, height / 2);
            setStyle('maxWidth', `${Math.max(0, width - mx * 2)}px`);
            setStyle('maxHeight', `${Math.max(0, height - my * 2)}px`);
            // offset sizes ignore the popup's entrance transform/animation.
            const w = popup.offsetWidth;
            const h = popup.offsetHeight;
            const below = rect.bottom + gap + y;
            const above = rect.top - gap - h + y;
            const spaceBelow = height - my - below;
            const spaceAbove = rect.top - gap + y - my;
            const top = below + h > height - my && spaceAbove > spaceBelow ? above : below;
            setStyle('left', `${clamp(rect.right - w + x, mx, width - mx - w)}px`);
            setStyle('top', `${clamp(top, my, height - my - h)}px`);
        }

        function tick() {
            frame = 0;
            update();
            if (!closed) frame = requestAnimationFrame(tick);
        }

        const handle = { update, close };
        popups.set(popup, handle);
        anchors.set(anchor, handle);
        Object.assign(popup.style, {
            position: 'fixed', right: 'auto', bottom: 'auto', margin: '0',
            boxSizing: 'border-box', minHeight: '0', overflow: 'auto'
        });
        document.body.appendChild(popup);
        if (opts.ariaExpanded !== false) anchor.setAttribute('aria-expanded', 'true');
        observer.observe(document.documentElement, { childList: true, subtree: true });
        // One loop only while open catches nested scroll (including non-bubbling
        // scroll), resize, CSS transforms, and layout motion without global listeners.
        update();
        if (!closed) frame = requestAnimationFrame(tick);
        return handle;
    }

    window.AnchoredPopup = { attach };
})();
