(function () {
  'use strict';

  var COLORS = {
    kakao: '#e0a400', kbank: '#1a9c8c', kpay: '#3b8fd9', sm: '#c0469a', kgames: '#8a8f3a',
    naver: '#17a34a', krafton: '#e5572f', nc: '#7a5bd6', netmarble: '#c98a1a', nhn: '#4a6fa5',
    hybe: '#b23a48', pearl: '#2f8fa0', KOSPI: '#6b7280', KOSDAQ: '#9ca3af', KOSPI_EXSEMI: '#8b5cf6'
  };
  var DEFAULTS = ['kakao', 'kbank', 'kpay', 'sm', 'naver', 'krafton', 'KOSPI', 'KOSPI_EXSEMI', 'KOSDAQ'];
  var EVENTS = [
    ['08-21', '분할 발표'], ['08-24', '목표가 하향'], ['08-26', '노조 반발'],
    ['09-16', '주주 설명회'], ['09-22', '합병 1차 통과']
  ];
  var GROUPS = { index: '지수', group: '카카오 그룹', peer: '피어' };
  var root = null, data = null, series = [], selected = null, observer = null;
  var mode = 'cum', benchmark = 'KOSPI', sortKey = 'cum', sortDirection = -1;
  var chart = null, resizeBound = false;

  function escape(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function numeric(value) { return typeof value === 'number' && isFinite(value); }
  function pct(value, unit) {
    if (!numeric(value)) return '—';
    var rounded = Math.round(value * 100) / 100;
    return (rounded > 0 ? '+' : '') + rounded.toFixed(2) + (unit || '%');
  }
  function number(value) {
    return numeric(value) ? value.toLocaleString('ko-KR', { maximumFractionDigits: 2 }) : '—';
  }
  function tone(value) { return numeric(value) ? (value > 0 ? 'pt-up' : value < 0 ? 'pt-down' : '') : ''; }
  function color(item) { return COLORS[item.id] || '#6b7280'; }
  function find(id) { return series.filter(function (item) { return item.id === id; })[0]; }
  function enabled(item) { return selected.indexOf(item.id) !== -1; }
  function cumulative(item, close) {
    if (!item || !numeric(item.base) || item.base <= 0 || !numeric(close)) return null;
    var result = (close / item.base - 1) * 100;
    return numeric(result) ? result : null;
  }
  function at(item, index) { return cumulative(item, item && item.closes && item.closes[index]); }
  function total(item) { return cumulative(item, item && item.last); }
  function excess(value, other) { return numeric(value) && numeric(other) ? value - other : null; }
  // 기준지수 id(KOSPI_EXSEMI 등)를 화면에 그대로 쓰면 안 되니 시리즈 이름으로 바꾼다.
  function benchName() { var item = find(benchmark); return item && item.name ? item.name : benchmark; }
  function chartValue(item, index) {
    var value = at(item, index);
    return mode === 'cum' ? value : excess(value, at(find(benchmark), index));
  }
  function dateLabel(date) {
    var parts = String(date || '').split('-');
    return parts.length === 3 ? Number(parts[1]) + '/' + Number(parts[2]) : String(date || '');
  }
  function saveSelection() {
    try { window.localStorage.setItem('kmw-price-v1', JSON.stringify(selected)); } catch (ignore) { /* Storage may be blocked. */ }
  }
  function loadSelection() {
    if (selected !== null) return;
    selected = DEFAULTS.slice();
    try {
      var stored = JSON.parse(window.localStorage.getItem('kmw-price-v1'));
      if (Array.isArray(stored) && stored.every(function (id) { return typeof id === 'string'; })) selected = stored;
    } catch (ignore) { /* Defaults remain usable without storage. */ }
  }

  function injectStyle() {
    if (document.getElementById('pt-price-style')) return;
    var style = document.createElement('style');
    style.id = 'pt-price-style';
    style.textContent = [
      '.pt-root{--pt-bg:var(--bg,#f7f8fa);--pt-card:var(--card,#fff);--pt-line:var(--line,#e5e7eb);--pt-text:var(--text,#111827);--pt-muted:var(--muted,#6b7280);color:var(--pt-text);font:inherit;font-variant-numeric:tabular-nums}',
      '.pt-root *{box-sizing:border-box}.pt-root button{font:inherit;cursor:pointer}.pt-root button:focus-visible{outline:2px solid var(--blue,#2563eb);outline-offset:3px}',
      '.pt-title{margin:0 0 6px;font-size:20px}.pt-subtitle,.pt-note{color:var(--pt-muted);font-size:12px;line-height:1.6}.pt-subtitle{margin:0 0 16px}',
      '.pt-kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:16px}.pt-tile,.pt-section{border:1px solid var(--pt-line);border-radius:var(--radius,10px);background:var(--pt-card);padding:14px}',
      '.pt-tile-label{font-size:12px;font-weight:600;line-height:1.5}.pt-tile-value{font-size:25px;font-weight:700;margin:7px 0}.pt-tile .pt-note{overflow-wrap:anywhere}',
      '.pt-up{color:var(--red,#dc2626)}.pt-down{color:var(--blue,#2563eb)}.pt-section{margin-top:14px}.pt-heading{font-size:15px;margin:0 0 12px}',
      '.pt-modes,.pt-chip-row{display:flex;flex-wrap:wrap;align-items:center;gap:7px}.pt-modes{margin-bottom:13px}.pt-chip-row{margin:9px 0}.pt-group-label{min-width:83px;font-size:12px;font-weight:600}',
      '.pt-button{border:1px solid var(--pt-line);border-radius:6px;background:var(--pt-card);color:var(--pt-muted);padding:6px 10px;font-size:12px!important}.pt-button[aria-pressed="true"]{background:var(--pt-text);border-color:var(--pt-text);color:var(--pt-card)}',
      '.pt-chip{border:1px solid currentColor;border-radius:20px;background:var(--pt-card);padding:4px 9px;font-size:12px!important}.pt-chip[aria-pressed="false"]{opacity:.35}.pt-link{border:0;background:none;padding:4px;color:var(--pt-muted);font-size:11px!important;text-decoration:underline}',
      '.pt-chart{position:relative;min-width:0;height:320px}.pt-svg{display:block;width:100%;height:320px;overflow:hidden;touch-action:pan-y}.pt-svg text{font-family:inherit;font-size:11px;fill:var(--pt-muted)}',
      '.pt-tooltip{display:none;position:absolute;z-index:5;pointer-events:none;background:var(--pt-card);border:1px solid var(--pt-line);border-radius:8px;padding:9px 11px;box-shadow:0 3px 15px #0002;font-size:11px;max-width:calc(100% - 8px)}',
      '.pt-tooltip-title{font-weight:700;margin-bottom:5px}.pt-tooltip-row{display:flex;align-items:center;gap:6px;justify-content:space-between;line-height:1.5}.pt-swatch{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}',
      '.pt-events{display:flex;flex-wrap:wrap;gap:6px 15px;list-style:none;padding:0;margin:8px 0 0;font-size:11px;color:var(--pt-muted)}.pt-event-number{display:inline-block;font-weight:700;color:var(--pt-text);margin-right:4px}',
      '.pt-empty{padding:25px;text-align:center;color:var(--pt-muted);font-size:13px}.pt-rank-row{display:grid;grid-template-columns:110px minmax(50px,1fr) 80px;gap:10px;align-items:center;min-height:28px;font-size:12px}.pt-rank-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.pt-rank-track{position:relative;height:18px;background:var(--pt-bg);border-radius:3px}.pt-rank-track:after{content:"";position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--muted-2,#9ca3af)}.pt-rank-bar{position:absolute;top:3px;height:12px;border-radius:2px}.pt-rank-value{text-align:right}',
      '.pt-table-wrap{overflow-x:auto}.pt-table{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}.pt-table th,.pt-table td{padding:10px 9px;border-bottom:1px solid var(--pt-line);text-align:right}.pt-table th:first-child,.pt-table td:first-child,.pt-table th:nth-child(2),.pt-table td:nth-child(2){text-align:left}',
      '.pt-table th{background:var(--pt-bg);font-weight:600}.pt-sort{border:0;background:none;color:inherit;padding:0;font-size:inherit!important}.pt-disabled{opacity:.38}.pt-error{display:block;max-width:220px;white-space:normal;font-size:10px;color:var(--pt-muted)}',
      '@media(max-width:850px){.pt-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.pt-tile:first-child{grid-column:1/-1}}@media(max-width:450px){.pt-section{padding:10px}.pt-group-label{width:100%}.pt-rank-row{grid-template-columns:85px minmax(30px,1fr) 68px;gap:6px}.pt-tile-value{font-size:22px}}'
    ].join('\n');
    (document.head || document.documentElement).appendChild(style);
  }

  function tile(label, value, note) {
    return '<div class="pt-tile"><div class="pt-tile-label">' + escape(label) + '</div><div class="pt-tile-value ' + tone(value) + '">' + pct(value) + '</div><div class="pt-note">' + escape(note) + '</div></div>';
  }
  function averageTile(label, items) {
    var valid = items.filter(function (item) { return numeric(total(item)); });
    var mean = valid.length ? valid.reduce(function (sum, item) { return sum + total(item); }, 0) / valid.length : null;
    var note = !items.length ? '선택한 종목 없음' : valid.length + '/' + items.length + '개 유효 종목 / 동일 비중 평균';
    return tile(label, mean, note);
  }
  function individualTile(id, label) {
    var item = find(id);
    var note = item ? number(item.base) + ' → ' + number(item.last) + (item.grp === 'index' ? 'pt' : '원') + ', 발표일 ' + pct(item.annPct) : '데이터 없음';
    return tile(label, total(item), note);
  }
  function kpis() {
    return '<div class="pt-kpis">' + individualTile('kakao', '카카오') +
      averageTile('카카오X 편입 계열사 평균', series.filter(function (item) { return enabled(item) && ['kbank', 'kpay', 'sm'].indexOf(item.id) !== -1; })) +
      averageTile('선택 피어 평균', series.filter(function (item) { return enabled(item) && item.grp === 'peer'; })) +
      individualTile('KOSPI', 'KOSPI') + individualTile('KOSDAQ', 'KOSDAQ') + '</div>';
  }
  function button(action, value, text, active) {
    return '<button type="button" class="pt-button" data-pt-action="' + action + '" data-pt-value="' + escape(value) + '" aria-pressed="' + active + '">' + escape(text) + '</button>';
  }
  function controls() {
    var html = '<div class="pt-modes">' + button('mode', 'cum', '누적 수익률', mode === 'cum') + button('mode', 'excess', '지수 대비 초과', mode === 'excess');
    if (mode === 'excess') html += button('benchmark', 'KOSPI', 'vs KOSPI', benchmark === 'KOSPI') + button('benchmark', 'KOSPI_EXSEMI', 'vs 코스피(삼전/하이닉스 제외)', benchmark === 'KOSPI_EXSEMI') + button('benchmark', 'KOSDAQ', 'vs KOSDAQ', benchmark === 'KOSDAQ');
    html += '</div>';
    ['index', 'group', 'peer'].forEach(function (group) {
      html += '<div class="pt-chip-row"><span class="pt-group-label">' + GROUPS[group] + '</span>';
      series.filter(function (item) { return item.grp === group; }).forEach(function (item) {
        html += '<button type="button" class="pt-chip" style="color:' + color(item) + '" data-pt-action="toggle" data-pt-value="' + escape(item.id) + '" aria-pressed="' + enabled(item) + '">' + escape(item.name) + '</button>';
      });
      if (group !== 'index') html += '<button type="button" class="pt-link" data-pt-action="all" data-pt-value="' + group + '">전체 선택</button><button type="button" class="pt-link" data-pt-action="none" data-pt-value="' + group + '">해제</button>';
      html += '</div>';
    });
    return html;
  }
  function ranking() {
    var items = series.filter(enabled).slice().sort(function (a, b) { return compare(total(a), total(b), -1); });
    var max = items.reduce(function (value, item) { return Math.max(value, Math.abs(total(item) || 0)); }, 0) || 1;
    var html = '<section class="pt-section"><h3 class="pt-heading">누적 수익률 순위</h3>';
    if (!items.length) html += '<div class="pt-empty">표시할 종목을 선택하세요</div>';
    items.forEach(function (item) {
      var value = total(item), width = numeric(value) ? Math.abs(value) / max * 50 : 0;
      var left = value < 0 ? 50 - width : 50;
      html += '<div class="pt-rank-row"><span class="pt-rank-name" title="' + escape(item.name) + '">' + escape(item.name) + '</span><div class="pt-rank-track"><span class="pt-rank-bar" style="left:' + left + '%;width:' + width + '%;background:' + (value < 0 ? 'var(--blue,#2563eb)' : 'var(--red,#dc2626)') + '"></span></div><span class="pt-rank-value ' + tone(value) + '">' + pct(value) + '</span></div>';
    });
    return html + '</section>';
  }
  function field(item, key) {
    if (key === 'cum') return total(item);
    if (key === 'KOSPI' || key === 'KOSDAQ') return excess(total(item), total(find(key)));
    if (key === 'grp') return GROUPS[item.grp] || item.grp;
    return item[key];
  }
  function compare(a, b, direction) {
    var missingA = a == null || (typeof a === 'number' && !numeric(a));
    var missingB = b == null || (typeof b === 'number' && !numeric(b));
    if (missingA || missingB) return missingA === missingB ? 0 : missingA ? 1 : -1;
    return (typeof a === 'string' ? a.localeCompare(String(b), 'ko') : a - b) * direction;
  }
  function table() {
    var columns = [['name', '종목'], ['grp', '구분'], ['base', dateLabel(data.baseDate).replace('/', '-') + ' 종가'], ['annPct', '발표일 등락'], ['last', '최근 종가'], ['cum', '누적'], ['KOSPI', 'vs KOSPI'], ['KOSDAQ', 'vs KOSDAQ']];
    var html = '<section class="pt-section"><h3 class="pt-heading">종목별 상세</h3><div class="pt-note">종가: 주식 원 / 지수 pt / 지수 대비 초과: 누적 수익률 차이(%p)</div><div class="pt-table-wrap"><table class="pt-table"><thead><tr>';
    columns.forEach(function (column) {
      var sorted = sortKey === column[0];
      html += '<th scope="col" aria-sort="' + (sorted ? sortDirection === 1 ? 'ascending' : 'descending' : 'none') + '"><button type="button" class="pt-sort" data-pt-action="sort" data-pt-value="' + column[0] + '">' + escape(column[1]) + (sorted ? sortDirection === 1 ? ' ▲' : ' ▼' : ' ↕') + '</button></th>';
    });
    html += '</tr></thead><tbody>';
    series.slice().sort(function (a, b) { return compare(field(a, sortKey), field(b, sortKey), sortDirection); }).forEach(function (item) {
      html += '<tr' + (enabled(item) ? '' : ' class="pt-disabled"') + '>';
      columns.forEach(function (column) {
        var key = column[0], value = field(item, key), text;
        if (key === 'name' || key === 'grp') text = escape(value);
        else if (key === 'base' || key === 'last') text = number(value);
        else text = pct(value, key === 'KOSPI' || key === 'KOSDAQ' ? '%p' : '%');
        if (key === 'name' && item.error) text += '<span class="pt-error">' + escape(item.error) + '</span>';
        html += '<td class="' + (['annPct', 'cum', 'KOSPI', 'KOSDAQ'].indexOf(key) !== -1 ? tone(value) : '') + '">' + text + '</td>';
      });
      html += '</tr>';
    });
    return html + '</tbody></table></div></section>';
  }

  function drawChart() {
    if (!root || !document.documentElement.contains(root)) return;
    var host = root.querySelector('.pt-chart');
    if (!host) return;
    var dates = Array.isArray(data.dates) ? data.dates : [];
    var width = Math.max(host.clientWidth || root.clientWidth || 320, 140);
    var left = 58, right = width - 18, top = 30, bottom = 283;
    var values = [], items = series.filter(enabled);
    items.forEach(function (item) { dates.forEach(function (_, i) { var value = chartValue(item, i); if (numeric(value)) values.push(value); }); });
    var min = 0, max = 0;
    values.forEach(function (value) { min = Math.min(min, value); max = Math.max(max, value); });
    var span = max - min || 1;
    min -= span * 0.1; max += span * 0.1;
    var x = function (i) { return left + (dates.length > 1 ? i / (dates.length - 1) : 0.5) * (right - left); };
    var y = function (value) { return bottom - (value - min) / (max - min) * (bottom - top); };
    chart = { dates: dates, left: left, right: right, width: width, x: x };
    var unit = mode === 'cum' ? '%' : '%p';
    var html = '<svg class="pt-svg" width="100%" height="320" role="img" aria-label="' + (mode === 'cum' ? '누적 수익률' : benchName() + ' 대비 초과 수익률') + ' 추이"><text x="4" y="14">' + unit + '</text>';
    for (var tick = 0; tick <= 4; tick++) {
      var value = min + (max - min) * tick / 4, py = y(value);
      if (Math.abs(py - y(0)) < 13) continue;
      html += '<line x1="' + left + '" x2="' + right + '" y1="' + py + '" y2="' + py + '" stroke="var(--line,#e5e7eb)"/><text x="' + (left - 8) + '" y="' + (py + 4) + '" text-anchor="end">' + value.toFixed(1) + '</text>';
    }
    html += '<line x1="' + left + '" x2="' + right + '" y1="' + y(0) + '" y2="' + y(0) + '" stroke="var(--muted,#6b7280)" stroke-width="1.4"/><text x="' + (left - 8) + '" y="' + (y(0) + 4) + '" text-anchor="end">0</text>';
    EVENTS.forEach(function (event, i) {
      var index = dates.indexOf(String(data.annDate || data.baseDate || '').slice(0, 4) + '-' + event[0]);
      if (index < 0) return;
      html += '<line x1="' + x(index) + '" x2="' + x(index) + '" y1="24" y2="' + bottom + '" stroke="var(--muted-2,#9ca3af)" stroke-dasharray="3 4"/><text x="' + x(index) + '" y="17" text-anchor="middle">' + (i + 1) + '</text>';
    });
    items.slice().sort(function (a, b) { return (a.id === 'kakao' ? 1 : 0) - (b.id === 'kakao' ? 1 : 0); }).forEach(function (item) {
      var points = [];
      dates.forEach(function (_, i) { var value = chartValue(item, i); if (numeric(value)) points.push([x(i), y(value)]); });
      if (points.length === 1) html += '<circle cx="' + points[0][0] + '" cy="' + points[0][1] + '" r="2.5" fill="' + color(item) + '"/>';
      if (points.length > 1) html += '<path d="' + points.map(function (point, i) { return (i ? 'L' : 'M') + point[0].toFixed(2) + ',' + point[1].toFixed(2); }).join(' ') + '" fill="none" stroke="' + color(item) + '" stroke-width="' + (item.id === 'kakao' ? '2.5' : '1.6') + '"' + (item.grp === 'index' ? ' stroke-dasharray="5 4"' : '') + '/>';
    });
    var count = Math.min(dates.length, Math.max(2, Math.floor((right - left) / 75)));
    for (var label = 0; label < count; label++) {
      var index = count === 1 ? 0 : Math.round(label * (dates.length - 1) / (count - 1));
      html += '<text x="' + x(index) + '" y="308" text-anchor="' + (label === 0 ? 'start' : label === count - 1 ? 'end' : 'middle') + '">' + escape(dateLabel(dates[index])) + '</text>';
    }
    if (!values.length) html += '<text x="' + ((left + right) / 2) + '" y="155" text-anchor="middle">' + (!items.length ? '표시할 종목을 선택하세요' : '표시할 수 있는 데이터가 없습니다') + '</text>';
    html += '<line class="pt-crosshair" x1="0" x2="0" y1="24" y2="' + bottom + '" stroke="var(--muted,#6b7280)" stroke-dasharray="2 3" visibility="hidden"/></svg><div class="pt-tooltip" role="status"></div>';
    host.innerHTML = html;
  }
  function render(focusAction, focusValue) {
    root.innerHTML = '<div class="pt-root"><h2 class="pt-title">주가 흐름</h2><p class="pt-subtitle">' + escape(dateLabel(data.baseDate)) + ' 종가 = 0% 기준 / 갱신 ' + escape(data.generatedAt || '정보 없음') + '</p>' + kpis() + '<section class="pt-section">' + controls() + '<div class="pt-note">' + (mode === 'cum' ? '누적 수익률 (%)' : escape(benchName()) + ' 대비 누적 수익률 차이 (%p)') + '</div><div class="pt-chart"></div><ol class="pt-events">' + EVENTS.map(function (event, i) { return '<li><span class="pt-event-number">' + (i + 1) + '</span>' + event[0] + ' ' + event[1] + '</li>'; }).join('') + '</ol></section>' + ranking() + table() + '</div>';
    drawChart();
    if (focusAction) Array.prototype.some.call(root.querySelectorAll('[data-pt-action]'), function (element) {
      if (element.getAttribute('data-pt-action') === focusAction && element.getAttribute('data-pt-value') === focusValue) { element.focus(); return true; }
      return false;
    });
  }
  function click(event) {
    var target = event.target;
    while (target && target !== root && !(target.getAttribute && target.getAttribute('data-pt-action'))) target = target.parentNode;
    if (!target || target === root) return;
    var action = target.getAttribute('data-pt-action'), value = target.getAttribute('data-pt-value');
    if (action === 'mode') mode = value;
    else if (action === 'benchmark') benchmark = value;
    else if (action === 'sort') {
      sortDirection = sortKey === value ? -sortDirection : (value === 'name' || value === 'grp' ? 1 : -1);
      sortKey = value;
    } else if (action === 'toggle') {
      var index = selected.indexOf(value);
      if (index === -1) selected.push(value); else selected.splice(index, 1);
      saveSelection();
    } else if (action === 'all' || action === 'none') {
      series.filter(function (item) { return item.grp === value; }).forEach(function (item) {
        var index = selected.indexOf(item.id);
        if (action === 'all' && index === -1) selected.push(item.id);
        if (action === 'none' && index !== -1) selected.splice(index, 1);
      });
      saveSelection();
    } else return;
    render(action, value);
  }
  function hideTooltip() {
    if (!root) return;
    var tooltip = root.querySelector('.pt-tooltip'), crosshair = root.querySelector('.pt-crosshair');
    if (tooltip) tooltip.style.display = 'none';
    if (crosshair) crosshair.setAttribute('visibility', 'hidden');
  }
  function move(event) {
    if (!root || !chart || !chart.dates.length) return;
    var svg = root.querySelector('.pt-svg'), tooltip = root.querySelector('.pt-tooltip');
    if (!svg || !tooltip || !svg.contains(event.target)) { hideTooltip(); return; }
    var rect = svg.getBoundingClientRect(), px = event.clientX - rect.left;
    if (px < chart.left || px > chart.right || event.clientY - rect.top > 283) { hideTooltip(); return; }
    var index = chart.dates.length === 1 ? 0 : Math.round((px - chart.left) / (chart.right - chart.left) * (chart.dates.length - 1));
    var html = '<div class="pt-tooltip-title">' + escape(chart.dates[index]) + (mode === 'excess' ? ' / vs ' + escape(benchName()) + ' (%p)' : ' / 누적 (%)') + '</div>';
    series.filter(enabled).forEach(function (item) {
      var value = chartValue(item, index);
      html += '<div class="pt-tooltip-row"><span><span class="pt-swatch" style="background:' + color(item) + '"></span>' + escape(item.name) + '</span><strong class="' + tone(value) + '">' + pct(value, mode === 'cum' ? '%' : '%p') + '</strong></div>';
    });
    tooltip.innerHTML = html;
    tooltip.style.display = 'block';
    tooltip.style.left = Math.max(4, Math.min(px + 12, rect.width - tooltip.offsetWidth - 4)) + 'px';
    tooltip.style.top = Math.max(0, Math.min(event.clientY - rect.top + 12, 320 - tooltip.offsetHeight)) + 'px';
    var crosshair = root.querySelector('.pt-crosshair');
    crosshair.setAttribute('x1', chart.x(index)); crosshair.setAttribute('x2', chart.x(index)); crosshair.setAttribute('visibility', 'visible');
  }
  function mount() {
    var next = document.getElementById('priceTracker');
    if (!next) return;
    if (root) {
      root.removeEventListener('click', click);
      root.removeEventListener('mousemove', move);
      root.removeEventListener('mouseleave', hideTooltip);
    }
    if (observer) { observer.disconnect(); observer = null; }
    root = next; chart = null; data = window.KMW_PRICES;
    injectStyle();
    series = data && Array.isArray(data.series) ? data.series.filter(function (item) { return item && typeof item.id === 'string'; }) : [];
    if (!series.length) { root.innerHTML = '<div class="pt-root pt-empty">주가 데이터가 없습니다</div>'; return; }
    loadSelection();
    render();
    root.addEventListener('click', click);
    root.addEventListener('mousemove', move);
    root.addEventListener('mouseleave', hideTooltip);
    if (typeof window.ResizeObserver === 'function') {
      var previousWidth = root.clientWidth;
      observer = new window.ResizeObserver(function () {
        if (root.clientWidth !== previousWidth) { previousWidth = root.clientWidth; drawChart(); }
      });
      observer.observe(root);
    } else if (!resizeBound) {
      window.addEventListener('resize', drawChart);
      resizeBound = true;
    }
  }

  window.KMW_PriceTracker = { mount: mount };
}());
