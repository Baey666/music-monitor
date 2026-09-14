/* music-monitor 前端：零构建单页应用 */
'use strict';

const App = (() => {
  const state = {
    platforms: [],
    qualityLevels: [],
    chartGroups: [],
    chartIndex: {},
    chartVerify: {},
    activeChartPlatform: '',
    resolvedChartEntry: null,
    resolvedChartCount: 0,
    favorites: [],
    favPlatforms: [],
    editId: null,
    sourcesSel: new Set(),
    chartSel: new Set(),
    favSel: new Set(),
  };

  /* ------------------------------------------------------------ 基础工具 */
  async function api(path, options) {
    const res = await fetch('/api' + path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    if (!res.ok) {
      const msg = (data && (data.detail || data.message || data.error)) || ('HTTP ' + res.status);
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    return data;
  }

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const $ = (id) => document.getElementById(id);

  let toastTimer = null;
  function toast(message) {
    const el = $('toast');
    el.textContent = message;
    el.style.display = 'inline-flex';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.style.display = 'none'; }, 3200);
  }

  function fmtTime(iso) {
    if (!iso) return '—';
    return iso.replace('T', ' ').slice(0, 19);
  }

  function fmtDuration(sec) {
    if (!sec) return '';
    const m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + String(s).padStart(2, '0');
  }

  function parseLinkLines(text) {
    return String(text || '').split('\n').map((line) => line.trim()).filter(Boolean).map((line) => {
      const idx = line.indexOf('|');
      if (idx > 0) return { name: line.slice(0, idx).trim(), link: line.slice(idx + 1).trim() };
      return { name: '', link: line };
    }).filter((x) => x.link);
  }

  const sourceLabel = (key) => {
    const found = state.platforms.find((p) => p.key === key);
    return found ? found.label : key;
  };

  /* ------------------------------------------------------------ 标签页 */
  function initTabs() {
    document.querySelectorAll('nav.tabs button').forEach((btn) => {
      btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
  }

  function switchTab(name) {
    document.querySelectorAll('nav.tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
    document.querySelectorAll('.page').forEach((p) => p.classList.toggle('active', p.id === 'page-' + name));
    if (name === 'records') loadRuns();
    if (name === 'settings') loadSettings();
  }

  /* ------------------------------------------------------------ 顶部状态 */
  async function refreshAll() {
    await Promise.all([loadBase(), checkEngine()]);
    await Promise.all([loadMonitors(), loadRuns()]);
    if (document.querySelector('#page-charts.active')) renderCharts();
  }

  async function loadBase() {
    try {
      const data = await api('/platforms');
      state.platforms = data.items || [];
      state.qualityLevels = data.quality_levels || [];
      renderPlatformChips('m-sources', state.sourcesSel);
      renderPlatformChips('fav-platforms', new Set(state.favPlatforms), (key, on) => {
        state.favPlatforms = on
          ? [...state.favPlatforms, key]
          : state.favPlatforms.filter((k) => k !== key);
      });
      fillQualitySelects();
      renderQualityHelp();
    } catch (err) {
      toast('读取平台清单失败：' + err.message);
    }
  }

  async function checkEngine() {
    try {
      const data = await api('/health');
      const ok = data.engine && data.engine.ok;
      if ($('app-version')) $('app-version').textContent = '版本 v' + (data.version || '未知');
      $('engine-pill').className = 'pill ' + (ok ? 'ok' : 'bad');
      $('engine-text').textContent = ok ? '引擎正常' : '引擎不可用';
      const s = data.scheduler || {};
      $('sched-pill').className = 'pill';
      $('sched-text').textContent = '调度 ' + (s.last_tick_ago == null ? '启动中' : s.last_tick_ago + 's 前') +
        ' · 运行中 ' + (s.running_monitors || []).length;
      $('engine-info').innerHTML = [
        ['引擎地址', data.engine.url],
        ['连接状态', ok ? '正常' : ('失败 — ' + (data.engine.detail || ''))],
        ['调度心跳', (s.tick_seconds || '—') + ' 秒'],
        ['并行监控上限', s.parallel_limit],
        ['单监控并发下载', s.download_concurrency],
        ['曲目记录', JSON.stringify(data.tracks || {})],
      ].map(([k, v]) => `<span>${esc(k)}：<b>${esc(v)}</b></span>`).join('');
      return ok;
    } catch (err) {
      $('engine-pill').className = 'pill bad';
      $('engine-text').textContent = '后端异常';
      return false;
    }
  }

  function fillQualitySelects() {
    const opts = state.qualityLevels.map((q) => `<option value="${esc(q.key)}">${esc(q.label)}</option>`).join('');
    ['m-quality', 's-quality'].forEach((id) => {
      const el = $(id);
      if (!el) return;
      const cur = el.value;
      el.innerHTML = opts;
      if (cur) el.value = cur;
    });
  }

  function renderQualityHelp() {
    const rows = state.qualityLevels.map((q) => `<div><b>${esc(q.key)}</b><p class="muted small">${esc(q.label)}</p></div>`);
    $('quality-help').innerHTML = rows.join('');
  }

  function renderPlatformChips(containerId, sel, onChange) {
    const box = $(containerId);
    if (!box) return;
    box.innerHTML = state.platforms.map((p) => {
      const on = sel.has(p.key);
      const tags = [
        p.playlist ? '歌单' : '',
        p.user_playlist ? '个人歌单' : '',
      ].filter(Boolean).map((t) => `<span class="tag">${t}</span>`).join('');
      return `<label class="chip ${on ? 'on' : ''}" data-key="${esc(p.key)}" title="${esc(p.desc || '')}">
        ${esc(p.label)}${tags}</label>`;
    }).join('');
    box.querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', (ev) => {
        ev.preventDefault();
        const key = chip.dataset.key;
        const on = !sel.has(key);
        if (on) sel.add(key); else sel.delete(key);
        chip.classList.toggle('on', on);
        if (onChange) onChange(key, on);
      });
    });
  }

  /* ------------------------------------------------------------ 监控列表 */
  async function loadMonitors() {
    let data;
    try { data = await api('/monitors'); } catch (err) { toast(err.message); return; }
    const items = data.items || [];
    const running = new Set(data.running || []);
    $('monitor-summary').textContent = `共 ${items.length} 个监控任务`;
    $('monitor-empty').style.display = items.length ? 'none' : 'block';

    $('monitor-list').innerHTML = items.map((m) => {
      const kindLabel = { chart: '热门榜单', playlist: '指定歌单', favorites: '个人收藏夹', artist: '歌手关注' }[m.kind] || m.kind;
      const isRunning = running.has(m.id);
      const srcs = (m.sources || []).map((s) => `<span class="tag">${esc(sourceLabel(s))}</span>`).join('');
      let targetDesc = '';
      if (m.kind === 'chart') {
        const names = (m.target.charts || []).map((c) => c.name || c.key || c.link || c.id);
        targetDesc = names.length ? names.join('、') : '未选择榜单';
      } else if (m.kind === 'playlist') {
        const names = (m.target.playlists || []).map((c) => c.name || c.link);
        targetDesc = names.length ? names.join('、') : '未配置歌单';
      } else if (m.kind === 'artist') {
        const names = (m.target.artists || []).map((c) => c.name || c.id).filter(Boolean);
        targetDesc = names.length ? '关注：' + names.join('、') : '未配置歌手';
      } else {
        const ids = m.target.playlist_ids || [];
        targetDesc = ids.length ? '已选 ' + ids.length + ' 个收藏夹' : '该平台全部收藏夹';
      }
      return `<div class="card monitor-card">
        <div class="monitor-head">
          <div style="flex:1">
            <div class="title">${esc(m.name)} ${isRunning ? '<span class="status running">执行中</span>' : ''}
              ${m.enabled ? '' : '<span class="status skipped">已停用</span>'}</div>
            <div class="sub">${esc(kindLabel)} · ${esc(targetDesc)}</div>
          </div>
        </div>
        <div class="kv">
          <span>音质：<b>${esc(m.quality)}</b></span>
          <span>策略：<b>${m.fallback === 'skip' ? '严格' : '取最高可用'}</b></span>
          <span>间隔：<b>${m.interval_minutes} 分钟</b></span>
          <span>上次：<b>${esc(fmtTime(m.last_run_at))}</b></span>
          <span>下次：<b>${esc(fmtTime(m.next_run_at))}</b></span>
          <span>自动下载：<b>${m.auto_download ? '开' : '关'}</b></span>
        </div>
        <div class="chips">${srcs || '<span class="muted small">未指定平台</span>'}</div>
        <div class="row tight">
          <button class="btn small primary" onclick="App.runMonitor(${m.id})">立即执行</button>
          <button class="btn small" onclick="App.previewMonitor(${m.id})">干跑预览</button>
          <button class="btn small" onclick="App.toggleMonitor(${m.id}, ${m.enabled ? 'false' : 'true'})">${m.enabled ? '停用' : '启用'}</button>
          <button class="btn small" onclick="App.editMonitor(${m.id})">编辑</button>
          <button class="btn small" onclick="App.showRuns(${m.id})">历史</button>
          <button class="btn small danger" onclick="App.deleteMonitor(${m.id})">删除</button>
        </div>
        <div id="monitor-preview-${m.id}" class="small muted"></div>
      </div>`;
    }).join('');
  }

  async function runMonitor(id) {
    try {
      const r = await api(`/monitors/${id}/run`, { method: 'POST' });
      toast(r.message || '已触发');
      setTimeout(loadMonitors, 1500);
    } catch (err) { toast('触发失败：' + err.message); }
  }

  async function previewMonitor(id) {
    const box = $('monitor-preview-' + id);
    box.innerHTML = '正在干跑预览…';
    try {
      const r = await api(`/monitors/${id}/preview`, { method: 'POST' });
      box.innerHTML = `可解析曲目 <b>${r.total}</b> 首，过滤后 <b>${r.filtered}</b> 首`
        + (r.warnings && r.warnings.length ? `<br><span style="color:var(--warn)">${r.warnings.map(esc).join('<br>')}</span>` : '');
    } catch (err) { box.innerHTML = '<span style="color:var(--err)">' + esc(err.message) + '</span>'; }
  }

  async function toggleMonitor(id, enabled) {
    try {
      await api(`/monitors/${id}`, { method: 'PATCH', body: JSON.stringify({ enabled: !!enabled }) });
      loadMonitors();
    } catch (err) { toast(err.message); }
  }

  async function deleteMonitor(id) {
    if (!confirm('删除该监控？关联的曲目记录也会一起删除（已下载的音乐文件不受影响）。')) return;
    try { await api(`/monitors/${id}`, { method: 'DELETE' }); loadMonitors(); }
    catch (err) { toast(err.message); }
  }

  async function showRuns(id) {
    switchTab('records');
    $('track-monitor').value = String(id);
    await loadRuns(id);
    await loadTracks();
  }

  /* ------------------------------------------------------------ 榜单 */
  async function renderCharts() {
    if (!state.chartGroups.length) {
      const data = await api('/charts');
      state.chartGroups = data.groups || [];
      state.chartIndex = data.index || {};
      state.activeChartPlatform = state.chartGroups[0]?.platform || '';
    }
    const platforms = state.chartGroups.map((g) => g.platform);
    if (!platforms.includes(state.activeChartPlatform)) state.activeChartPlatform = platforms[0] || '';

    $('chart-platforms').innerHTML = state.chartGroups.map((g) => {
      const count = g.charts.filter((c) => state.chartVerify[c.key]?.ok).length;
      const badge = count ? `<span class="tag">${count} 可用</span>` : '';
      return `<label class="chip ${g.platform === state.activeChartPlatform ? 'on' : ''}" data-key="${esc(g.platform)}">
        ${esc(g.label)}<span class="tag">${esc(g.region)}</span>${badge}</label>`;
    }).join('');
    $('chart-platforms').querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', () => { state.activeChartPlatform = chip.dataset.key; renderCharts(); });
    });

    const group = state.chartGroups.find((g) => g.platform === state.activeChartPlatform);
    $('chart-list').innerHTML = (group?.charts || []).map((c) => {
      const v = state.chartVerify[c.key];
      const badge = v ? (v.ok
        ? `<span class="status downloaded">${v.count} 首</span>`
        : `<span class="status failed">不可用</span>`) : '';
      return `<div class="card">
        <div class="row tight"><b>${esc(c.name)}</b>${badge}</div>
        <div class="muted small mono" style="word-break:break-all;margin:6px 0">${esc(c.rank ? c.platform + ' 榜单 ' + c.rank : (c.id || c.link || ''))}</div>
        ${c.note ? `<div class="muted small">${esc(c.note)}</div>` : ''}
        ${v && !v.ok && v.message ? `<div class="small" style="color:var(--err)">${esc(v.message)}</div>` : ''}
        <div class="row tight" style="margin-top:8px">
          <button class="btn small" onclick="App.verifyChart('${esc(c.key)}')">校验</button>
          <button class="btn small" onclick="App.previewChart('${esc(c.key)}')">预览曲目</button>
          <button class="btn small primary" onclick="App.monitorFromChart('${esc(c.key)}')">创建监控</button>
        </div>
      </div>`;
    }).join('') || '<div class="empty">该平台暂无内置榜单，可用下方「自定义榜单」添加。</div>';
  }

  function currentChartKeys() {
    const group = state.chartGroups.find((g) => g.platform === state.activeChartPlatform);
    return (group?.charts || []).map((c) => c.key);
  }

  async function verifyChart(key) { await verifyCharts([key], true); }

  async function verifyCharts(keys, quiet) {
    const status = $('chart-verify-status');
    status.textContent = '校验中…';
    try {
      const r = await api('/charts/verify', { method: 'POST', body: JSON.stringify({ keys: keys || [] }) });
      (r.items || []).forEach((item) => {
        if (item.key) state.chartVerify[item.key] = item;
      });
      const ok = (r.items || []).filter((i) => i.ok).length;
      status.textContent = `校验完成：${ok}/${(r.items || []).length} 可用`;
      renderCharts();
      if (!quiet) toast(status.textContent);
    } catch (err) { status.textContent = '校验失败：' + err.message; }
  }

  async function previewChart(key) {
    const c = state.chartIndex[key];
    if (!c) return;
    showChartPreview(`正在解析「${c.name}」…`, '', []);
    try {
      const r = await api('/charts/resolve?key=' + encodeURIComponent(key));
      state.resolvedChartEntry = { platform: c.platform, id: c.id || '', link: c.link || '', name: c.name, key };
      state.resolvedChartCount = r.count;
      showChartPreview(`${c.name}（${r.count} 首）`, r.ok ? '' : r.message,
        r.songs.map((s, i) => `<tr><td>${i + 1}</td><td>${esc(s.name)}</td><td>${esc(s.artist)}</td>
          <td>${esc(s.album)}</td><td>${esc(sourceLabel(s.source))}</td></tr>`));
    } catch (err) { showChartPreview(c.name, err.message, []); }
  }

  function showChartPreview(title, message, rows) {
    $('chart-preview-card').style.display = 'block';
    $('chart-preview-title').textContent = title;
    $('chart-preview-meta').innerHTML = message ? `<span style="color:var(--warn)">${esc(message)}</span>` : '';
    $('chart-preview-body').innerHTML = rows.join('') || '<tr><td colspan="5" class="empty">没有曲目</td></tr>';
  }

  function monitorFromChart(key) {
    const c = state.chartIndex[key];
    if (!c) return;
    state.chartSel = new Set([key]);
    openMonitorModal(null, { kind: 'chart', charts: [key] });
  }

  async function resolveCustomChart() {
    const name = $('custom-chart-name').value.trim();
    const link = $('custom-chart-link').value.trim();
    const box = $('custom-chart-result');
    if (!link) { box.textContent = '请填写歌单链接'; return; }
    box.textContent = '解析中…';
    try {
      const r = await api('/charts/resolve?link=' + encodeURIComponent(link) + '&limit=30');
      if (!r.ok) { box.innerHTML = `<span style="color:var(--err)">解析失败：${esc(r.message || '未知原因')}</span>`; return; }
      box.innerHTML = `解析成功，共 <b>${r.count}</b> 首。` +
        `<button class="btn small" style="margin-left:8px" onclick="App.monitorFromCustomChart()">创建监控</button>`;
      state.resolvedChartEntry = { platform: (r.resolved && r.resolved.source) || '', id: (r.resolved && r.resolved.id) || '', link, name: name || (r.resolved && r.resolved.name) || '自定义榜单' };
      state.resolvedChartCount = r.count;
      showChartPreview(state.resolvedChartEntry.name + `（${r.count} 首）`, '',
        r.songs.map((s, i) => `<tr><td>${i + 1}</td><td>${esc(s.name)}</td><td>${esc(s.artist)}</td>
          <td>${esc(s.album)}</td><td>${esc(sourceLabel(s.source))}</td></tr>`));
    } catch (err) { box.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
  }

  function monitorFromCustomChart() {
    if (!state.resolvedChartEntry) return;
    openMonitorModal(null, { kind: 'chart', custom: [state.resolvedChartEntry] });
  }

  /* ------------------------------------------------------------ 歌单 / 收藏夹 */
  async function resolvePlaylist() {
    const link = $('playlist-link').value.trim();
    const box = $('playlist-result');
    if (!link) { box.textContent = '请填写歌单链接'; return; }
    box.textContent = '解析中…';
    try {
      const r = await api('/playlists/resolve?link=' + encodeURIComponent(link) + '&limit=40');
      if (!r.ok) { box.innerHTML = `<span style="color:var(--err)">${esc(r.message || '解析失败')}</span>`; return; }
      const picked = r.picked || {};
      box.innerHTML = `识别为「${esc(picked.name || '未命名歌单')}」（${esc(sourceLabel(picked.source))}），共 <b>${r.count}</b> 首。` +
        `<button class="btn small" style="margin-left:8px" onclick="App.monitorFromPlaylist()">创建监控</button>`;
      state.resolvedPlaylist = { id: picked.id, source: picked.source, link: picked.link || link, name: picked.name || '歌单' };
      showChartPreview((picked.name || '歌单') + `（${r.count} 首）`, '',
        r.songs.map((s, i) => `<tr><td>${i + 1}</td><td>${esc(s.name)}</td><td>${esc(s.artist)}</td>
          <td>${esc(s.album)}</td><td>${esc(sourceLabel(s.source))}</td></tr>`));
    } catch (err) { box.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
  }

  function monitorFromPlaylist() {
    if (!state.resolvedPlaylist) return;
    openMonitorModal(null, { kind: 'playlist', playlists: [state.resolvedPlaylist] });
  }

  async function loadFavorites() {
    const box = $('fav-list');
    const status = $('fav-status');
    const sources = state.favPlatforms;
    status.textContent = '读取中…';
    box.innerHTML = '<div class="empty"><span class="spin">◐</span> 正在向各平台请求…</div>';
    try {
      const r = await api('/favorites/list', { method: 'POST', body: JSON.stringify({ sources }) });
      state.favorites = r.items || [];
      status.textContent = r.ok ? `读到 ${state.favorites.length} 个歌单/收藏夹` : (r.message || '没有数据');
      box.innerHTML = state.favorites.map((p) => `
        <label class="chip ${state.favSel.has(p.key) ? 'on' : ''}" data-key="${esc(p.key)}" style="margin:4px 6px 0 0">
          ${esc(p.name)}<span class="tag">${esc(sourceLabel(p.source))} · ${esc(p.track_count)} 首</span>
        </label>`).join('') || `<div class="empty">${esc(r.message || '没有读到个人歌单')}</div>`;
      box.querySelectorAll('.chip').forEach((chip) => {
        chip.addEventListener('click', (ev) => {
          ev.preventDefault();
          const key = chip.dataset.key;
          const on = !state.favSel.has(key);
          if (on) state.favSel.add(key); else state.favSel.delete(key);
          chip.classList.toggle('on', on);
        });
      });
    } catch (err) {
      status.textContent = '失败';
      box.innerHTML = `<div class="empty" style="color:var(--err)">${esc(err.message)}</div>`;
    }
  }

  function createFavoritesMonitor() {
    const ids = [...state.favSel];
    const sources = [...new Set(ids.map((k) => k.split(':')[0]))];
    openMonitorModal(null, { kind: 'favorites', playlist_ids: ids, sources: sources.length ? sources : state.favPlatforms });
  }

  /* ------------------------------------------------------------ 记录 */
  async function loadRuns(monitorId) {
    let data;
    try { data = await api('/runs?limit=50'); } catch (_) { return; }
    const nameOf = {};
    try {
      const m = await api('/monitors');
      (m.items || []).forEach((x) => { nameOf[x.id] = x.name; });
      const sel = $('track-monitor');
      const cur = sel.value;
      sel.innerHTML = '<option value="">全部监控</option>' +
        (m.items || []).map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join('');
      sel.value = cur || '';
    } catch (_) { /* ignore */ }

    $('run-body').innerHTML = (data.items || []).map((r) => `
      <tr>
        <td class="mono">${esc(fmtTime(r.started_at))}</td>
        <td>${esc(nameOf[r.monitor_id] || ('#' + r.monitor_id))}</td>
        <td><span class="status ${esc(r.status)}">${esc(r.status)}</span></td>
        <td>${r.found}</td><td>${r.new_items}</td><td>${r.downloaded}</td><td>${r.skipped}</td><td>${r.failed}</td>
        <td class="small muted">${esc((r.message || '').slice(0, 80))}</td>
        <td><button class="btn small" onclick="App.showRunLog(${r.id})">日志</button></td>
      </tr>`).join('') || '<tr><td colspan="10" class="empty">暂无执行记录</td></tr>';
  }

  async function showRunLog(runId) {
    try {
      const r = await api('/runs?limit=200');
      const run = (r.items || []).find((x) => x.id === runId);
      if (!run) return;
      $('run-log-card').style.display = 'block';
      $('run-log-title').textContent = `运行日志 #${run.id}（${fmtTime(run.started_at)}）`;
      $('run-log').textContent = run.log || '（无日志）';
      $('run-log-card').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (err) { toast(err.message); }
  }

  async function loadTracks() {
    const mid = $('track-monitor').value;
    const status = $('track-status').value;
    const qs = [`limit=200`];
    if (mid) qs.push('monitor_id=' + mid);
    if (status) qs.push('status=' + status);
    let data;
    try { data = await api('/tracks?' + qs.join('&')); } catch (err) { toast(err.message); return; }
    $('track-summary').textContent = '统计：' + Object.entries(data.stats || {}).map(([k, v]) => `${k}=${v}`).join('  ');
    $('track-body').innerHTML = (data.items || []).map((t) => `
      <tr>
        <td>${esc(t.name)}</td>
        <td>${esc(t.artist)}</td>
        <td>${esc(sourceLabel(t.source))}</td>
        <td><span class="status ${esc(t.status)}">${esc(t.status)}</span></td>
        <td class="small">${esc(t.quality_actual || t.bitrate || '')}</td>
        <td class="mono small" title="${esc(t.file_path)}">${esc((t.file_path || '').split(/[\\/]/).pop())}</td>
        <td class="small muted" title="${esc(t.error)}">${esc((t.error || '').slice(0, 60))}</td>
        <td><button class="btn small" onclick="App.retryTracks([${t.id}])">重试</button></td>
      </tr>`).join('') || '<tr><td colspan="8" class="empty">暂无曲目记录</td></tr>';
  }

  async function retryTracks(ids) {
    if (!ids.length) return;
    toast('正在重试 ' + ids.length + ' 首…');
    try {
      const r = await api('/tracks/retry', { method: 'POST', body: JSON.stringify({ track_ids: ids }) });
      const ok = (r.items || []).filter((x) => x.ok).length;
      toast(`重试完成：成功 ${ok} / ${ids.length}`);
      loadTracks();
    } catch (err) { toast('重试失败：' + err.message); }
  }

  async function retryFailed() {
    const mid = $('track-monitor').value;
    const qs = ['status=failed', 'limit=20'];
    if (mid) qs.push('monitor_id=' + mid);
    const data = await api('/tracks?' + qs.join('&'));
    const ids = (data.items || []).map((t) => t.id);
    if (!ids.length) { toast('没有失败记录'); return; }
    retryTracks(ids);
  }

  /* ------------------------------------------------------------ 设置 */
  async function loadSettings() {
    try {
      const r = await api('/settings');
      const s = r.settings || {};
      $('s-quality').value = s.default_quality || 'lossless';
      $('s-interval').value = s.default_interval_minutes || 360;
      $('s-max').value = s.default_max_downloads || 30;
      $('s-embed').value = String(s.default_embed != null ? s.default_embed : 1);
      $('engine-username').value = s.engine_username || '';
      if (Object.keys(s).length) { /* noop */ }
    } catch (err) { toast(err.message); }

    try {
      const r = await api('/engine/settings');
      const s = r.settings || {};
      const rows = Object.entries(s).map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td class="mono">${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</td></tr>`);
      $('engine-settings-body').innerHTML = rows.join('') || '<tr><td colspan="2" class="empty">读不到引擎设置</td></tr>';
    } catch (err) {
      $('engine-settings-body').innerHTML = `<tr><td colspan="2" class="empty">${esc(err.message)}</td></tr>`;
    }
    await checkEngine();
  }

  async function saveSettings() {
    try {
      await api('/settings', {
        method: 'POST',
        body: JSON.stringify({
          default_quality: $('s-quality').value,
          default_interval_minutes: Number($('s-interval').value) || 360,
          default_max_downloads: Number($('s-max').value) || 30,
          default_embed: Number($('s-embed').value),
        }),
      });
      toast('已保存默认参数');
    } catch (err) { toast(err.message); }
  }

  async function engineLogin() {
    const box = $('engine-login-result');
    box.textContent = '登录中…';
    try {
      const r = await api('/engine/login', {
        method: 'POST',
        body: JSON.stringify({ username: $('engine-username').value, password: $('engine-password').value }),
      });
      box.innerHTML = r.ok ? '<span style="color:var(--ok)">登录成功</span>' : `<span style="color:var(--err)">${esc(r.detail)}</span>`;
    } catch (err) { box.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
  }

  async function engineCookies() {
    const box = $('engine-login-result');
    try {
      const r = await api('/engine/cookies');
      box.innerHTML = r.ok
        ? '引擎已配置 Cookie 的平台：<b>' + esc(r.configured.join('、')) + '</b>'
        : esc(r.message);
    } catch (err) { box.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
  }

  async function pushCookies() {
    let mapping;
    try { mapping = JSON.parse($('cookie-json').value || '{}'); }
    catch (_) { toast('JSON 格式不正确'); return; }
    if (!Object.keys(mapping).length) { toast('没有可写入的内容'); return; }
    try {
      await api('/engine/cookies', { method: 'POST', body: JSON.stringify({ cookies: mapping }) });
      toast('已写入引擎，之后下载的音质会按新 Cookie 生效');
      engineCookies();
    } catch (err) { toast('写入失败：' + err.message); }
  }

  /* ------------------------------------------------------------ 监控弹窗 */
  function openMonitorModal(monitor, preset) {
    state.editId = monitor ? monitor.id : null;
    $('monitor-modal-title').textContent = monitor ? '编辑监控' : '新建监控';
    $('monitor-modal').classList.add('show');

    const kind = monitor ? monitor.kind : (preset?.kind || 'chart');
    $('m-kind').value = kind;
    $('m-name').value = monitor ? monitor.name : '';
    $('m-quality').value = monitor ? monitor.quality : ($('s-quality').value || 'lossless');
    $('m-fallback').value = monitor ? monitor.fallback : 'best_effort';
    $('m-interval').value = monitor ? monitor.interval_minutes : ($('s-interval').value || 360);
    $('m-max').value = monitor ? monitor.max_downloads : ($('s-max').value || 30);
    $('m-include').value = monitor ? monitor.include_kw : '';
    $('m-exclude').value = monitor ? monitor.exclude_kw : '';
    $('m-enabled').checked = monitor ? monitor.enabled : true;
    $('m-auto').checked = monitor ? monitor.auto_download : true;
    $('m-embed').checked = monitor ? monitor.embed : true;
    $('m-preview').innerHTML = '';

    // 榜单选择
    state.chartSel = new Set();
    const custom = [];
    if (monitor && monitor.kind === 'chart') {
      (monitor.target.charts || []).forEach((c) => {
        if (c.key && state.chartIndex[c.key]) state.chartSel.add(c.key);
        else custom.push(c);
      });
    } else if (preset?.charts) {
      preset.charts.forEach((k) => state.chartSel.add(k));
    } else if (preset?.custom) {
      custom.push(...preset.custom);
    }
    renderChartPicker();
    $('m-chart-links').value = custom.map((c) => (c.name ? c.name + '|' : '') + (c.link || (c.platform + ':' + c.id))).join('\n');

    // 歌单
    const playlistLinks = monitor && monitor.kind === 'playlist' ? (monitor.target.playlists || []) :
      (preset?.playlists || []);
    $('m-playlist-links').value = playlistLinks.map((p) => (p.name ? p.name + '|' : '') + (p.link || (p.source + ':' + p.id))).join('\n');

    // 收藏夹
    state.favSel = new Set(monitor && monitor.kind === 'favorites' ? (monitor.target.playlist_ids || []) : (preset?.playlist_ids || []));
    renderFavPicker();

    // 歌手关注
    const artists = monitor && monitor.kind === 'artist' ? (monitor.target.artists || []) :
      (preset?.artists || []);
    $('m-artist-names').value = artists
      .map((a) => (a.name || '') + (a.sources && a.sources.length ? '|' + a.sources.join(',') : ''))
      .join('\n');

    // 平台
    const sources = monitor ? monitor.sources : (preset?.sources || state.platforms.map((p) => p.key));
    state.sourcesSel = new Set(sources && sources.length ? sources : state.platforms.map((p) => p.key));
    renderPlatformChips('m-sources', state.sourcesSel);

    onKindChange();
  }

  function renderChartPicker() {
    const box = $('m-chart-picker');
    box.innerHTML = state.chartGroups.map((g) => `
      <div style="margin-bottom:8px">
        <div class="muted small" style="margin-bottom:4px">${esc(g.label)}</div>
        <div class="chips">${g.charts.map((c) => `
          <label class="chip ${state.chartSel.has(c.key) ? 'on' : ''}" data-key="${esc(c.key)}">${esc(c.name)}</label>
        `).join('')}</div>
      </div>`).join('');
    box.querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', (ev) => {
        ev.preventDefault();
        const key = chip.dataset.key;
        const on = !state.chartSel.has(key);
        if (on) state.chartSel.add(key); else state.chartSel.delete(key);
        chip.classList.toggle('on', on);
      });
    });
  }

  function renderFavPicker() {
    const box = $('m-fav-picker');
    if (!state.favorites.length) {
      box.className = 'muted small';
      box.textContent = '先到「歌单 / 收藏夹」页面点「读取我的收藏」，这里会出现可勾选项。';
      return;
    }
    box.className = 'chips';
    box.innerHTML = state.favorites.map((p) => `
      <label class="chip ${state.favSel.has(p.key) ? 'on' : ''}" data-key="${esc(p.key)}">
        ${esc(p.name)}<span class="tag">${esc(sourceLabel(p.source))}</span></label>`).join('');
    box.querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', (ev) => {
        ev.preventDefault();
        const key = chip.dataset.key;
        const on = !state.favSel.has(key);
        if (on) state.favSel.add(key); else state.favSel.delete(key);
        chip.classList.toggle('on', on);
      });
    });
  }

  function onKindChange() {
    const kind = $('m-kind').value;
    $('m-block-chart').style.display = kind === 'chart' ? 'block' : 'none';
    $('m-block-playlist').style.display = kind === 'playlist' ? 'block' : 'none';
    $('m-block-favorites').style.display = kind === 'favorites' ? 'block' : 'none';
    $('m-block-artist').style.display = kind === 'artist' ? 'block' : 'none';
    syncChip('m-enabled-chip', 'm-enabled');
    syncChip('m-auto-chip', 'm-auto');
    syncChip('m-embed-chip', 'm-embed');
  }

  function syncChip(chipId, inputId) {
    const chip = $(chipId);
    const input = $(inputId);
    if (!chip || !input) return;
    chip.classList.toggle('on', input.checked);
    chip.onclick = (ev) => {
      ev.preventDefault();
      input.checked = !input.checked;
      chip.classList.toggle('on', input.checked);
    };
  }

  function buildDraft() {
    const kind = $('m-kind').value;
    const sources = [...state.sourcesSel];
    const target = {};
    if (kind === 'chart') {
      const charts = [...state.chartSel].map((key) => {
        const c = state.chartIndex[key];
        return { key, name: c.name, platform: c.platform, id: c.id || '', link: c.link || '', rank: c.rank || '' };
      });
      parseLinkLines($('m-chart-links').value).forEach((x) => charts.push({ name: x.name, link: x.link }));
      target.charts = charts;
    } else if (kind === 'playlist') {
      target.playlists = parseLinkLines($('m-playlist-links').value).map((x) => {
        if (/^(https?:)?\/\//.test(x.link)) return x;
        const [source, id] = x.link.split(':');
        return { name: x.name, source, id, link: '' };
      });
    } else if (kind === 'artist') {
      // 每行「歌手名」或「歌手名|平台1,平台2」；不指定平台就用监控级的平台列表。
      target.artists = parseLinkLines($('m-artist-names').value).map((x) => {
        const name = (x.name || x.link || '').trim();
        const srcs = x.name ? (x.link || '').split(',').map((s) => s.trim()).filter(Boolean) : [];
        return { name, sources: srcs };
      }).filter((a) => a.name);
    } else {
      target.playlist_ids = [...state.favSel];
    }
    return {
      name: $('m-name').value.trim(),
      kind,
      enabled: $('m-enabled').checked,
      sources,
      target,
      quality: $('m-quality').value,
      fallback: $('m-fallback').value,
      auto_download: $('m-auto').checked,
      embed: $('m-embed').checked,
      interval_minutes: Number($('m-interval').value) || 360,
      max_downloads: Number($('m-max').value) || 30,
      include_kw: $('m-include').value,
      exclude_kw: $('m-exclude').value,
    };
  }

  async function previewDraft() {
    const draft = buildDraft();
    const box = $('m-preview');
    box.innerHTML = '正在干跑…';
    try {
      const r = await api('/preview', {
        method: 'POST',
        body: JSON.stringify({
          kind: draft.kind, sources: draft.sources, target: draft.target,
          include_kw: draft.include_kw, exclude_kw: draft.exclude_kw,
        }),
      });
      box.innerHTML = `共解析 <b>${r.total}</b> 首，过滤后 <b>${r.filtered}</b> 首。` +
        (r.warnings?.length ? `<br><span style="color:var(--warn)">${r.warnings.map(esc).join('<br>')}</span>` : '') +
        (r.songs?.length ? `<br><span class="muted">前几首：${r.songs.slice(0, 8).map((s) => esc(s.name)).join('、')}</span>` : '');
    } catch (err) { box.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`; }
  }

  async function saveMonitor() {
    const draft = buildDraft();
    if (!draft.name) { toast('请填写监控名称'); return; }
    try {
      if (state.editId) {
        await api(`/monitors/${state.editId}`, { method: 'PATCH', body: JSON.stringify(draft) });
        toast('已更新');
      } else {
        const r = await api('/monitors', { method: 'POST', body: JSON.stringify(draft) });
        toast('已创建，将在下一轮调度时执行');
        if (draft.enabled && r.id) setTimeout(() => runMonitor(r.id), 400);
      }
      closeModal();
      loadMonitors();
    } catch (err) { toast('保存失败：' + err.message); }
  }

  function closeModal() { $('monitor-modal').classList.remove('show'); state.editId = null; }

  async function editMonitor(id) {
    try {
      const r = await api('/monitors/' + id);
      openMonitorModal(r.monitor);
    } catch (err) { toast(err.message); }
  }

  /* ------------------------------------------------------------ 启动 */
  function init() {
    initTabs();
    document.querySelectorAll('nav.tabs button').forEach((b) => {
      if (b.dataset.tab === 'charts') b.addEventListener('click', renderCharts);
      if (b.dataset.tab === 'playlists') b.addEventListener('click', () => {
        renderPlatformChips('fav-platforms', new Set(state.favPlatforms), (key, on) => {
          state.favPlatforms = on ? [...state.favPlatforms, key] : state.favPlatforms.filter((k) => k !== key);
        });
      });
    });
    $('monitor-modal').addEventListener('click', (ev) => {
      if (ev.target.id === 'monitor-modal') closeModal();
    });
    $('chart-preview-create')?.addEventListener('click', () => {
      if (state.resolvedChartEntry) {
        openMonitorModal(null, { kind: 'chart', custom: [state.resolvedChartEntry] });
      }
    });
    loadBase().then(() => { checkEngine(); loadMonitors(); });
  }

  document.addEventListener('DOMContentLoaded', init);

  return {
    refreshAll, loadMonitors, runMonitor, previewMonitor, toggleMonitor, deleteMonitor, showRuns,
    verifyCharts, verifyChart, previewChart, monitorFromChart, resolveCustomChart, monitorFromCustomChart,
    resolvePlaylist, monitorFromPlaylist, loadFavorites, createFavoritesMonitor,
    loadRuns, loadTracks, showRunLog, retryTracks, retryFailed,
    loadSettings, saveSettings, engineLogin, engineCookies, pushCookies, checkEngine,
    openMonitorModal, editMonitor, closeModal, saveMonitor, previewDraft, onKindChange,
    currentChartKeys,
  };
})();
