/* 轻量 Markdown 渲染器（GFM 子集）：标题/列表/表格/引用/代码/分割线 + 行内强调
   零依赖，供 app.js 使用：window.MD.render(text) -> html */
(function (global) {
  'use strict';

  var CODE_MARK = '';   // 行内代码占位符（私有区字符）

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, '&#39;');
  }
  function leadingSpaces(s) {
    var m = s.match(/^[ \t]*/);
    return m ? m[0].replace(/\t/g, '    ').length : 0;
  }

  /* ---------------------------------------------------------- 行内 */
  function inline(src) {
    var codes = [];
    // 1. 先摘出行内代码，避免里面的符号被当成语法
    var s = String(src).replace(/`([^`]+)`/g, function (_, c) {
      codes.push(c);
      return CODE_MARK + (codes.length - 1) + CODE_MARK;
    });
    s = escapeHtml(s);

    // 2. 图片 / 链接
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]*)(?:\s+&quot;([^&]*)&quot;)?\)/g,
      function (_, alt, url, title) {
        return '<img src="' + escapeAttr(url) + '" alt="' + escapeAttr(alt) + '"' +
          (title ? ' title="' + escapeAttr(title) + '"' : '') + '>';
      });
    s = s.replace(/\[([^\]]*)\]\(([^)\s]*)(?:\s+&quot;([^&]*)&quot;)?\)/g,
      function (_, text, url, title) {
        return '<a href="' + escapeAttr(url) + '" target="_blank" rel="noopener noreferrer"' +
          (title ? ' title="' + escapeAttr(title) + '"' : '') + '>' + text + '</a>';
      });
    // 自动链接 <https://...>
    s = s.replace(/&lt;(https?:\/\/[^\s&]+)&gt;/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');

    // 3. 强调
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
    // 斜体：左右边界不能是单词字符/星号（中文/中文标点也算边界，允许）
    s = s.replace(/(^|[^A-Za-z0-9_*])\*([^*\n]+)\*(?![A-Za-z0-9_*])/g, '$1<em>$2</em>');
    s = s.replace(/(^|[^A-Za-z0-9_])_([^_\n]+)_(?![A-Za-z0-9_])/g, '$1<em>$2</em>');

    // 4. 反斜杠转义
    s = s.replace(/\\([\\`*_{}\[\]()#+\-.!>|~])/g, '$1');

    // 5. 还原行内代码
    s = s.replace(new RegExp(CODE_MARK + '(\\d+)' + CODE_MARK, 'g'), function (_, i) {
      return '<code>' + escapeHtml(codes[+i]) + '</code>';
    });
    return s;
  }

  /* ---------------------------------------------------------- 表格 */
  function splitRow(line) {
    var s = line.trim();
    if (s.charAt(0) === '|') s = s.slice(1);
    if (s.charAt(s.length - 1) === '|' && s.charAt(s.length - 2) !== '\\') s = s.slice(0, -1);
    var cells = [], cur = '';
    for (var i = 0; i < s.length; i++) {
      if (s.charAt(i) === '\\' && s.charAt(i + 1) === '|') { cur += '|'; i++; continue; }
      if (s.charAt(i) === '|') { cells.push(cur.trim()); cur = ''; continue; }
      cur += s.charAt(i);
    }
    cells.push(cur.trim());
    return cells;
  }

  function isDelimRow(line) {
    if (!line || line.indexOf('|') === -1) return false;
    var cells = splitRow(line);
    if (!cells.length) return false;
    for (var i = 0; i < cells.length; i++) {
      if (!/^:?-{1,}:?$/.test(cells[i].replace(/\s/g, ''))) return false;
    }
    return true;
  }

  function parseTable(lines, start) {
    var header = splitRow(lines[start]);
    var aligns = splitRow(lines[start + 1]).map(function (c) {
      var t = c.replace(/\s/g, '');
      var l = t.charAt(0) === ':', r = t.charAt(t.length - 1) === ':';
      return l && r ? 'center' : r ? 'right' : l ? 'left' : '';
    });
    var i = start + 2;
    var html = '<div class="table-wrap"><table><thead><tr>';
    header.forEach(function (h, k) {
      html += '<th' + (aligns[k] ? ' style="text-align:' + aligns[k] + '"' : '') + '>' + inline(h) + '</th>';
    });
    html += '</tr></thead><tbody>';
    while (i < lines.length && lines[i].trim() && lines[i].indexOf('|') !== -1) {
      var cells = splitRow(lines[i]);
      html += '<tr>';
      for (var k = 0; k < header.length; k++) {
        html += '<td' + (aligns[k] ? ' style="text-align:' + aligns[k] + '"' : '') + '>' +
          inline(cells[k] || '') + '</td>';
      }
      html += '</tr>';
      i++;
    }
    html += '</tbody></table></div>';
    return { html: html, next: i };
  }

  /* ---------------------------------------------------------- 列表 */
  function parseList(lines, start) {
    var first = lines[start].match(/^(\s*)([-*+]|\d+[.)])\s+/);
    var baseIndent = first[1].length;
    var ordered = /\d/.test(first[2]);
    var tag = ordered ? 'ol' : 'ul';
    var html = '<' + tag + '>';
    var i = start;
    while (i < lines.length) {
      var line = lines[i];
      var m = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
      if (!m) {
        if (!line.trim()) {
          var nxt = lines[i + 1];
          if (nxt && /^\s*([-*+]|\d+[.)])\s+/.test(nxt)) { i++; continue; }
        }
        break;
      }
      var indent = m[1].length;
      if (indent < baseIndent || indent > baseIndent) break;
      var content = [m[3]];
      i++;
      while (i < lines.length) {
        var l = lines[i];
        if (!l.trim()) {
          var nx = lines[i + 1];
          if (nx && /^\s*([-*+]|\d+[.)])\s+/.test(nx)) { content.push(''); i++; continue; }
          break;
        }
        var im = l.match(/^(\s*)([-*+]|\d+[.)])\s+/);
        var curIndent = leadingSpaces(l);
        if (im && curIndent <= baseIndent) break;      // 同级或更高级的新项
        content.push(l.slice(Math.min(curIndent, baseIndent + 2)));
        i++;
      }
      var body = parseBlocks(content);
      // 单段内容去掉 <p> 包裹，列表更紧凑
      if (/^<p>[\s\S]*<\/p>$/.test(body)) body = body.replace(/^<p>/, '').replace(/<\/p>$/, '');
      html += '<li>' + body + '</li>';
    }
    html += '</' + tag + '>';
    return { html: html, next: i };
  }

  /* ---------------------------------------------------------- 块级 */
  function parseBlocks(lines) {
    var html = '';
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (!line.trim()) { i++; continue; }

      // 围栏代码块
      var fence = line.match(/^\s{0,3}(`{3,}|~{3,})\s*([^\s`]*)\s*$/);
      if (fence) {
        var marker = fence[1], lang = fence[2] || '';
        var re = new RegExp('^\\s{0,3}' + marker.charAt(0) + '{' + marker.length + ',}\\s*$');
        var body = [];
        i++;
        while (i < lines.length && !re.test(lines[i])) { body.push(lines[i]); i++; }
        i++;  // 跳过收尾围栏
        html += '<pre><code' + (lang ? ' class="lang-' + escapeAttr(lang) + '"' : '') + '>' +
          escapeHtml(body.join('\n')) + '</code></pre>';
        continue;
      }

      // 分割线
      if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) { html += '<hr>'; i++; continue; }

      // 标题
      var h = line.match(/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/);
      if (h) {
        var lv = h[1].length;
        html += '<h' + lv + '>' + inline(h[2]) + '</h' + lv + '>';
        i++;
        continue;
      }

      // 表格
      if (line.indexOf('|') !== -1 && i + 1 < lines.length && isDelimRow(lines[i + 1])) {
        var t = parseTable(lines, i);
        html += t.html; i = t.next; continue;
      }

      // 引用
      if (/^\s{0,3}>/.test(line)) {
        var q = [];
        while (i < lines.length && (/^\s{0,3}>/.test(lines[i]) || (q.length && lines[i].trim()))) {
          q.push(lines[i].replace(/^\s{0,3}>\s?/, ''));
          i++;
        }
        html += '<blockquote>' + parseBlocks(q) + '</blockquote>';
        continue;
      }

      // 列表
      if (/^\s{0,3}([-*+]|\d+[.)])\s+/.test(line)) {
        var l = parseList(lines, i);
        html += l.html; i = l.next; continue;
      }

      // 段落
      var buf = [];
      while (i < lines.length && lines[i].trim() &&
        !/^\s{0,3}(#{1,6}\s|>|(`{3,}|~{3,}))/.test(lines[i]) &&
        !/^\s{0,3}([-*+]|\d+[.)])\s+/.test(lines[i]) &&
        !(lines[i].indexOf('|') !== -1 && i + 1 < lines.length && isDelimRow(lines[i + 1]))) {
        buf.push(lines[i]); i++;
      }
      if (!buf.length) { buf.push(lines[i]); i++; }
      html += '<p>' + inline(buf.join('\n')).replace(/\n/g, '<br>') + '</p>';
    }
    return html;
  }

  function render(text) {
    if (!text) return '';
    try {
      return parseBlocks(String(text).replace(/\r\n?/g, '\n').split('\n'));
    } catch (e) {
      console.error('markdown render failed', e);
      return escapeHtml(text);
    }
  }

  global.MD = { render: render, inline: inline };
})(window);
