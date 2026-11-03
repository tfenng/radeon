/* AMD Chat 前端：连续多轮对话 + SSE 流式输出 + 实时滚动 */
(() => {
  'use strict';

  const $ = (s) => document.querySelector(s);
  const messagesEl = $('#messages');
  const inputEl = $('#input');
  const sendBtn = $('#send');
  const stopBtn = $('#stop');
  const appEl = document.querySelector('.app');
  const sideList = $('#sideList');
  const sideFoot = $('#sideFoot');
  const newChatBtn = $('#newchat');
  const openSideBtn = $('#openSide');
  const closeSideBtn = $('#closeSide');
  const modelSel = $('#model');
  const effortSel = $('#effort');
  const statusEl = $('#status');
  const connEl = $('#conn');
  const welcome = $('#welcome');

  /** 后端对话上下文：[{role, content}] */
  let history = [];
  let sessionId = null;        // 当前会话 id（null = 还没落盘）
  let sessions = [];           // 侧边栏列表
  let controller = null;      // 正在进行的 fetch（用于停止）
  let streaming = false;

  /* ---------------------------------------------------------- 滚动 */
  // 用户手动往上翻时不要把他拽回底部。
  // 注意：内容变长时 scrollHeight 变大，“距底距离”也随之变大，
  // 所以不能用“当前距底距离”判断用户是否在底部，要用 sticky 标记。
  const BOTTOM_THRESHOLD = 80;
  let stickToBottom = true;

  function nearBottom(threshold = BOTTOM_THRESHOLD) {
    return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < threshold;
  }
  messagesEl.addEventListener('scroll', () => {
    stickToBottom = nearBottom();
  });
  function scrollToBottom(force = false) {
    if (force || stickToBottom) {
      // 流式输出用瞬时滚动，避免 smooth 动画被打断而追不上
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
  }

  /* ---------------------------------------------------------- 渲染 */
  function addMessage(role, content) {
    if (welcome && welcome.parentNode) welcome.remove();
    const wrap = document.createElement('div');
    wrap.className = `msg ${role}`;
    wrap.innerHTML = `
      <div class="avatar">${role === 'user' ? '我' : 'AI'}</div>
      <div class="body">
        <div class="content"></div>
        <div class="meta"></div>
      </div>`;
    wrap.querySelector('.content').textContent = content;
    messagesEl.appendChild(wrap);
    scrollToBottom(true);
    return wrap;
  }

  /** 把存好的消息渲染成气泡（非流式） */
  function renderStored(messages) {
    messagesEl.innerHTML = '';
    history = [];
    messages.forEach(m => {
      const el = addMessage(m.role, '');
      const contentEl = el.querySelector('.content');
      if (m.role === 'assistant') {
        contentEl.innerHTML = md(m.content || '');
      } else {
        contentEl.textContent = m.content || '';
      }
      history.push({ role: m.role, content: m.content || '' });
    });
    if (!messages.length && welcome) messagesEl.appendChild(welcome);
    scrollToBottom(true);
  }

  function setMeta(el, html) {
    el.querySelector('.meta').innerHTML = html;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  }

  // Markdown 渲染（web/markdown.js，GFM 子集：表格/列表/标题/引用/代码块…）
  const md = (window.MD && window.MD.render)
    ? window.MD.render
    : (text) => escapeHtml(text).replace(/\n/g, '<br>');

  /* ---------------------------------------------------------- 模型列表 */
  async function loadModels() {
    try {
      const r = await fetch('/api/models');
      const d = await r.json();
      modelSel.innerHTML = '';
      (d.models || []).forEach(m => {
        const o = document.createElement('option');
        const s = m.stats || {};
        let tag = '';
        if (s.requests > 0) {
          tag = `  中位 ${s.ttft_median?.toFixed(2)}s / 慢${s.slow_gt5s}/${s.requests}`;
        }
        o.value = m.id;
        o.textContent = m.id + (m.vision ? ' [vision]' : '') + tag;
        if (m.id === d.default) o.selected = true;
        modelSel.appendChild(o);
      });
      if (!modelSel.options.length) {
        const o = document.createElement('option');
        o.value = d.default; o.textContent = d.default;
        modelSel.appendChild(o);
      }
      if ((d.other || []).length) {
        console.info('已过滤的非 chat 模型:', d.other.join(', '));
      }
      connEl.className = 'conn ok';
    } catch (e) {
      connEl.className = 'conn err';
      console.error(e);
    }
  }

  /* ---------------------------------------------------------- 会话 */
  async function loadSessions(selectId) {
    try {
      const r = await fetch('/api/sessions');
      const d = await r.json();
      sessions = d.sessions || [];
      renderSessions();
      sideFoot.textContent = `${sessions.length} 个会话`;
      if (selectId) {
        const s = sessions.find(x => x.id === selectId);
        if (s) await openSession(selectId);
      }
    } catch (e) {
      console.error(e);
    }
  }

  function renderSessions() {
    sideList.innerHTML = '';
    sessions.forEach(s => {
      const div = document.createElement('div');
      div.className = 'side-item' + (s.id === sessionId ? ' active' : '');
      div.innerHTML = `<div class="t"></div><div class="x" title="删除">×</div>`;
      div.querySelector('.t').textContent = s.title;
      div.querySelector('.t').title = `${s.title}\n${s.updated_at || ''} · ${s.messages_count} 条`;
      div.addEventListener('click', (e) => {
        if (e.target.classList.contains('x')) return;
        openSession(s.id);
      });
      div.querySelector('.x').addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`删除会话「${s.title}」？`)) return;
        await fetch('/api/sessions/' + s.id, { method: 'DELETE' });
        if (sessionId === s.id) newSession();
        loadSessions();
      });
      sideList.appendChild(div);
    });
  }

  async function openSession(sid) {
    const r = await fetch('/api/sessions/' + sid);
    if (!r.ok) return;
    const d = await r.json();
    sessionId = d.session.id;
    renderStored(d.session.messages || []);
    if (d.session.model) modelSel.value = d.session.model;
    renderSessions();
    statusEl.textContent = `已载入会话「${d.session.title}」`;
  }

  async function newSession() {
    const r = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: modelSel.value }),
    });
    const d = await r.json();
    sessionId = d.session.id;
    renderStored([]);
    renderSessions();
    statusEl.textContent = '新会话';
    inputEl.focus();
  }

  newChatBtn.addEventListener('click', newSession);
  openSideBtn.addEventListener('click', () => appEl.classList.toggle('side-hidden'));
  closeSideBtn.addEventListener('click', () => appEl.classList.add('side-hidden'));

  /* ---------------------------------------------------------- 发送 */
  async function send() {
    const text = inputEl.value.trim();
    if (!text || streaming) return;

    inputEl.value = '';
    autoGrow();
    addMessage('user', text);
    history.push({ role: 'user', content: text });

    const el = addMessage('assistant', '');
    const contentEl = el.querySelector('.content');
    const caret = document.createElement('span');
    caret.className = 'caret';
    contentEl.appendChild(caret);

    streaming = true;
    controller = new AbortController();
    sendBtn.classList.add('hidden');
    stopBtn.classList.remove('hidden');
    connEl.className = 'conn busy';
    statusEl.textContent = '生成中…';
    const t0 = performance.now();
    let firstChunkAt = null;
    let raw = '';

    // 按帧合并渲染：一帧内到多个 delta 只渲染一次
    let rafId = 0;
    const scheduleRender = () => {
      if (rafId) return;
      rafId = requestAnimationFrame(() => {
        rafId = 0;
        contentEl.innerHTML = md(raw);
        contentEl.appendChild(caret);
        scrollToBottom();                   // 实时向下滚动
      });
    };

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: history,
          model: modelSel.value,
          effort: effortSel.value,
          session_id: sessionId,
        }),
        signal: controller.signal,
      });

      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });

        const events = buf.split('\n\n');
        buf = events.pop();
        for (const ev of events) {
          const line = ev.split('\n').find(l => l.startsWith('data:'));
          if (!line) continue;
          let p;
          try { p = JSON.parse(line.slice(5).trim()); } catch { continue; }

          if (p.type === 'delta') {
            if (firstChunkAt === null) firstChunkAt = performance.now();
            raw += p.text;
            scheduleRender();               // 按帧合并渲染，避免每个 token 全量重解析
          } else if (p.type === 'done') {
            const meta = p.meta || {};
            const ttft = meta.ttft;
            const slow = ttft > 5;
            setMeta(el, `
              <span>${meta.model || ''}</span>
              <span>TTFT ${ttft?.toFixed(3) ?? '-'}s</span>
              <span>总耗时 ${meta.total?.toFixed(3)}s</span>
              ${meta.usage ? `<span>prompt ${meta.usage.prompt} / completion ${meta.usage.completion}</span>` : ''}
              ${slow ? '<span class="slow">服务端长尾</span>' : ''}
              <button class="copy">复制</button>`);
            el.querySelector('.copy')?.addEventListener('click', () => {
              navigator.clipboard?.writeText(raw);
            });
            history.push({ role: 'assistant', content: raw });
            if (p.meta && p.meta.session_id) {
              sessionId = p.meta.session_id;
              renderSessions();
              loadSessions();
            }
          } else if (p.type === 'error') {
            contentEl.innerHTML = '';
            setMeta(el, `<span class="err">${escapeHtml(p.message || '出错')}</span>`);
          }
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        setMeta(el, '<span class="slow">已停止</span>');
      } else {
        setMeta(el, `<span class="err">${escapeHtml(e.message || String(e))}</span>`);
      }
    } finally {
      streaming = false;
      controller = null;
      if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
      contentEl.innerHTML = md(raw);
      caret.remove();
      sendBtn.classList.remove('hidden');
      stopBtn.classList.add('hidden');
      connEl.className = 'conn ok';
      const wall = ((performance.now() - t0) / 1000).toFixed(3);
      statusEl.textContent = `本次墙钟 ${wall}s` +
        (firstChunkAt ? ` · 首包 ${((firstChunkAt - t0) / 1000).toFixed(3)}s` : '');
      scrollToBottom();
      inputEl.focus();
    }
  }

  /* ---------------------------------------------------------- 交互 */
  const INPUT_MAX_H = 220;
  function autoGrow() {
    inputEl.style.height = 'auto';
    inputEl.style.overflowY = 'hidden';
    const sh = inputEl.scrollHeight;              // 内容自然高度
    inputEl.style.height = Math.min(sh + 2, INPUT_MAX_H) + 'px';
    // 只有真的超过上限才出滚动条
    inputEl.style.overflowY = sh > INPUT_MAX_H ? 'auto' : 'hidden';
  }
  inputEl.addEventListener('input', autoGrow);
  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send();
    }
  });
  sendBtn.addEventListener('click', send);
  stopBtn.addEventListener('click', () => controller?.abort());
  // 启动：恢复最近一次会话
  (async () => {
    await loadModels();
    await loadSessions();
    if (sessions.length) {
      await openSession(sessions[0].id);
    } else {
      await newSession();
    }
    autoGrow();
    inputEl.focus();
  })();

})();
