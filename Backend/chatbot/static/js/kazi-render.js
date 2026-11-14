(function (global) {
    'use strict';

    var ALLOWED_TAGS = [
        'p', 'br', 'hr', 'strong', 'em', 'del', 'code', 'pre', 'blockquote',
        'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img'
    ];
    var ALLOWED_ATTR = ['href', 'src', 'alt', 'title', 'class', 'start'];

    var configured = false;
    var hooksInstalled = false;

    function plainText(container, text) {
        container.textContent = text == null ? '' : String(text);
    }

    function configureParser() {
        if (configured) return;
        if (typeof global.marked !== 'undefined' && global.marked &&
            typeof global.marked.use === 'function') {
            global.marked.use({
                breaks: true,
                gfm: true,
                renderer: {
                    checkbox: function (token) {
                        return token && token.checked ? '\u2611' : '\u2610';
                    }
                }
            });
        }
        configured = true;
    }

    function sameOrigin(src) {
        try {
            var url = new URL(src, global.location.href);
            return url.origin === global.location.origin;
        } catch (error) {
            return false;
        }
    }

    function imageAllowed(src) {
        if (!src) return false;
        if (/^data:image\//i.test(src)) return true;
        return sameOrigin(src);
    }

    function imagePlaceholder(src) {
        var host = 'unknown';
        try {
            host = new URL(src, global.location.href).host || host;
        } catch (error) {
            host = 'unknown';
        }
        return global.document.createTextNode('[image from ' + host + ' not loaded]');
    }

    function installHooks(purify) {
        if (hooksInstalled) return;
        purify.addHook('afterSanitizeAttributes', function (node) {
            if (node.nodeType !== 1) return;
            var tag = node.tagName;
            var className = node.getAttribute('class') || '';
            if (tag === 'CODE' && /^language-[\w-]+$/.test(className)) {
                node.setAttribute('class', className);
            } else {
                node.removeAttribute('class');
            }
            if (node.hasAttribute('start') &&
                !(tag === 'OL' && /^\d{1,9}$/.test(node.getAttribute('start')))) {
                node.removeAttribute('start');
            }
            if (tag === 'A') {
                node.setAttribute('rel', 'noopener noreferrer');
                node.setAttribute('target', '_blank');
            }
            if (tag === 'IMG') {
                var src = node.getAttribute('src');
                if (!imageAllowed(src)) {
                    var placeholder = imagePlaceholder(src);
                    if (node.parentNode) {
                        node.parentNode.replaceChild(placeholder, node);
                    }
                }
            }
        });
        hooksInstalled = true;
    }

    function renderInto(container, text) {
        if (!container) return;
        var raw = text == null ? '' : String(text);
        var marked = global.marked;
        var purify = global.DOMPurify;
        if (!marked || typeof marked.parse !== 'function' ||
            !purify || typeof purify.sanitize !== 'function') {
            plainText(container, raw);
            return;
        }
        try {
            installHooks(purify);
            configureParser();
            var parsed = marked.parse(raw);
            var clean = purify.sanitize(parsed, {
                ALLOWED_TAGS: ALLOWED_TAGS,
                ALLOWED_ATTR: ALLOWED_ATTR,
                FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe', 'object', 'embed', 'script'],
                FORBID_ATTR: ['style', 'id'],
                ALLOW_DATA_ATTR: false
            });
            container.innerHTML = clean;
        } catch (error) {
            plainText(container, raw);
        }
    }

    global.KaziRender = { renderInto: renderInto };
})(window);
