(function (global) {
    'use strict';

    var ALLOWED_TAGS = [
        'p', 'br', 'hr', 'strong', 'em', 'del', 'code', 'pre', 'blockquote',
        'ul', 'ol', 'li', 'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'table', 'thead', 'tbody', 'tr', 'th', 'td', 'img'
    ];
    var ALLOWED_ATTR = ['href', 'src', 'alt', 'title', 'class', 'start'];

    var LEGACY_FILE_LINK = /^<a href='([^'<>\n]+)' target='_blank'>([^<>\n]+)<\/a>$/;
    var LEGACY_FILE_LINK_DOUBLE = /^<a href="([^"<>\n]+)" target="_blank">([^<>\n]+)<\/a>$/;
    var LEGACY_IMAGE_DOUBLE = /^<img src="([^"<>\n]+)" alt="uploaded image" \/>$/;
    var UPLOAD_PATH = /^(?:chat_uploads|documents)\/[^\s"'<>\\]+$/;

    var configured = false;
    var hooksInstalled = false;

    function plainText(container, text) {
        container.textContent = text == null ? '' : String(text);
    }

    function escapeHtml(text) {
        return String(text == null ? '' : text).replace(/[&<>"']/g, function (char) {
            return {
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
            }[char];
        });
    }

    function escapeMarkdown(text) {
        return String(text).replace(/([\\`*_{}\[\]()#+\-.!|~<>&])/g, '\\$1');
    }

    function mediaBase() {
        var base = global.KAZI_MEDIA_URL;
        if (typeof base !== 'string' || !base) return '';
        if (base.charAt(0) === '/') return base.replace(/\/+$/, '') + '/';
        try {
            return new URL(base).href.replace(/\/+$/, '') + '/';
        } catch (error) {
            return '';
        }
    }

    function safeUploadDestination(url) {
        if (typeof url !== 'string' || !url || /[\s"'<>\\]/.test(url)) return null;
        var base = mediaBase();
        if (!base || url.indexOf(base) !== 0) return null;
        var path = url.slice(base.length);
        if (!UPLOAD_PATH.test(path) || path.indexOf('..') !== -1) return null;
        return url.replace(/</g, '\\<').replace(/>/g, '\\>');
    }

    function legacyUploadMarkdown(text) {
        var match = LEGACY_FILE_LINK.exec(text) || LEGACY_FILE_LINK_DOUBLE.exec(text);
        if (match) {
            var destination = safeUploadDestination(match[1]);
            if (!destination) return null;
            return '[' + escapeMarkdown(match[2]) + '](<' + destination + '>)';
        }
        var image = LEGACY_IMAGE_DOUBLE.exec(text);
        if (image) {
            var imageDestination = safeUploadDestination(image[1]);
            if (!imageDestination) return null;
            return '![uploaded image](<' + imageDestination + '>)';
        }
        return null;
    }

    function configureParser() {
        if (configured) return true;
        var marked = global.marked;
        if (!marked || typeof marked.use !== 'function') return false;
        try {
            marked.use({
                breaks: true,
                gfm: true,
                renderer: {
                    checkbox: function (token) {
                        return token && token.checked ? '\u2611' : '\u2610';
                    },
                    html: function (token) {
                        return escapeHtml(token && token.text);
                    },
                    text: function (token) {
                        if (token && token.tokens) {
                            return this.parser.parseInline(token.tokens);
                        }
                        var text = token && token.text != null ? String(token.text) : '';
                        text = text.replace(/\t/g, '\u00a0\u00a0\u00a0\u00a0');
                        text = text.replace(/ {2,}/g, function (run) {
                            return new Array(run.length + 1).join('\u00a0');
                        });
                        return escapeHtml(text);
                    },
                    image: function (token) {
                        var href = token && token.href != null ? String(token.href) : '';
                        var alt = token && token.text != null ? String(token.text) : '';
                        var html = '<img src="' + escapeHtml(href) + '" alt="' + escapeHtml(alt) + '"';
                        if (token && token.title) {
                            html += ' title="' + escapeHtml(token.title) + '"';
                        }
                        return html + '>';
                    }
                },
                tokenizer: {
                    html: function () {
                        return undefined;
                    },
                    tag: function () {
                        return undefined;
                    }
                }
            });
        } catch (error) {
            return false;
        }
        configured = true;
        return true;
    }

    function sameOrigin(src) {
        try {
            var url = new URL(src, global.location.href);
            return url.origin === global.location.origin;
        } catch (error) {
            return false;
        }
    }

    function mediaOrigin() {
        var base = mediaBase();
        if (!base) return '';
        try {
            return new URL(base, global.location.href).origin;
        } catch (error) {
            return '';
        }
    }

    function imageAllowed(src) {
        if (!src) return false;
        if (/^data:image\//i.test(src)) return true;
        if (sameOrigin(src)) return true;
        var origin = mediaOrigin();
        if (!origin) return false;
        try {
            return new URL(src, global.location.href).origin === origin;
        } catch (error) {
            return false;
        }
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
        var original = text == null ? '' : String(text);
        var raw = original;
        var legacyUpload = legacyUploadMarkdown(raw);
        if (legacyUpload !== null) raw = legacyUpload;
        var marked = global.marked;
        var purify = global.DOMPurify;
        if (!marked || typeof marked.parse !== 'function' ||
            !purify || typeof purify.sanitize !== 'function') {
            plainText(container, original);
            return;
        }
        try {
            if (!configureParser()) {
                plainText(container, original);
                return;
            }
            installHooks(purify);
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
            plainText(container, original);
        }
    }

    global.KaziRender = { renderInto: renderInto };
})(window);
