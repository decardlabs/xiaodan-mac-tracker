"""
小蛋首次启动引导窗口 — 基于 WKWebView 的多步骤引导流程
"""

import json
import urllib.parse

import objc
from Foundation import NSObject
from AppKit import (
    NSWindow, NSColor, NSApplication,
    NSTitledWindowMask, NSClosableWindowMask,
    NSMiniaturizableWindowMask,
    NSBackingStoreBuffered,
    NSAlert,
)

try:
    from WebKit import WKWebView, WKWebViewConfiguration
except ImportError:
    raise SystemExit("缺少依赖，请运行：pip install pyobjc-framework-WebKit")

from settings import DEFAULT_CATEGORY_PRESETS, load_settings, save_settings


# ── HTML 模板（单文件 + JS 状态机控制步骤切换）──────────────────────────────────
# __PRESETS_JSON__ 在运行时由 _build_onboarding_html() 替换为真实 JSON
_ONBOARDING_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
  color: #1C1C1E;
  background: #fff;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* ── 步骤容器 ── */
.step {
  display: none;
  flex-direction: column;
  flex: 1;
  padding: 36px 44px 24px;
  overflow: hidden;
}
.step.active { display: flex; }

/* ── 进度指示器 ── */
.step-indicator { display: flex; gap: 6px; align-items: center; margin-bottom: 26px; }
.step-dot { width: 8px; height: 8px; border-radius: 50%; background: #E0E0E0; transition: all .2s; flex-shrink: 0; }
.step-dot.done { background: #1C1C1E; }
.step-dot.current { background: #9B72CF; width: 22px; border-radius: 4px; }

/* ── 标题区 ── */
h1 { font-size: 20px; font-weight: 600; margin-bottom: 6px; }
.subtitle { font-size: 13px; color: #8E8E93; margin-bottom: 22px; line-height: 1.6; }
.section-title { font-size: 13px; font-weight: 500; color: #1C1C1E; margin-bottom: 10px; }

/* ── 选项卡（步骤1）── */
.option-card {
  border: 1.5px solid #E8E8E8;
  border-radius: 10px;
  padding: 14px 18px;
  margin-bottom: 10px;
  cursor: pointer;
  transition: border-color .15s, background .15s;
  user-select: none;
}
.option-card:hover { border-color: #C0B0E0; background: #FBFAFF; }
.option-card.selected { border-color: #1C1C1E; background: #F7F7F7; }
.option-title { font-size: 14px; font-weight: 500; margin-bottom: 3px; }
.option-desc { font-size: 12px; color: #8E8E93; }

/* ── 表单（步骤2）── */
.form-label { font-size: 12px; color: #8E8E93; margin-bottom: 6px; display: block; }
input[type=text] {
  width: 100%;
  border: 1px solid #E0E0E0;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 14px;
  font-family: inherit;
  outline: none;
  color: #1C1C1E;
}
input[type=text]:focus { border-color: #9B72CF; }
.error-msg { color: #D85A30; font-size: 12px; margin-top: 7px; display: none; }
.error-msg.show { display: block; }

/* ── 复选框列表（步骤3）── */
.checkbox-list { flex: 1; overflow-y: auto; margin-bottom: 10px; }
.checkbox-list::-webkit-scrollbar { width: 4px; }
.checkbox-list::-webkit-scrollbar-thumb { background: #D0D0D0; border-radius: 2px; }
.check-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 2px;
  border-bottom: .5px solid #F2F2F2;
  cursor: pointer;
  user-select: none;
}
.check-item:last-child { border-bottom: none; }
.xd-cb {
  width: 18px;
  height: 18px;
  border: 1.5px solid #D0D0D0;
  border-radius: 4px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
  transition: all .15s;
}
.xd-cb.checked { border-color: #1C1C1E; background: #1C1C1E; }
.xd-cb.checked::after { content: "✓"; color: #fff; font-size: 11px; font-weight: 700; }
.check-label { font-size: 14px; color: #1C1C1E; }

/* ── 添加自定义行 ── */
.add-row { display: flex; gap: 8px; flex-shrink: 0; }
.add-row input { flex: 1; }
.btn-add {
  padding: 9px 14px;
  border: 1px solid #E0E0E0;
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
  cursor: pointer;
  font-family: inherit;
  white-space: nowrap;
  color: #1C1C1E;
}
.btn-add:hover { border-color: #9B72CF; color: #9B72CF; }

/* ── 页脚按钮区 ── */
.footer {
  display: flex;
  gap: 8px;
  padding-top: 14px;
  border-top: .5px solid #F0F0F0;
  flex-shrink: 0;
  margin-top: auto;
}
.spacer { flex: 1; }
.btn {
  padding: 9px 22px;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  font-family: inherit;
  font-weight: 500;
}
.btn-primary { background: #1C1C1E; color: #fff; }
.btn-primary:hover { background: #333; }
.btn-primary:disabled { background: #888; cursor: not-allowed; opacity: 0.7; }
.btn-secondary { background: #F0F0F0; color: #1C1C1E; }
.btn-secondary:hover { background: #E5E5E5; }

/* ── 子分类区块（步骤4）── */
.sub-scroll { flex: 1; overflow-y: auto; margin-bottom: 10px; }
.sub-scroll::-webkit-scrollbar { width: 4px; }
.sub-scroll::-webkit-scrollbar-thumb { background: #D0D0D0; border-radius: 2px; }
.cat-section { border: .5px solid #E8E8E8; border-radius: 10px; margin-bottom: 10px; overflow: hidden; }
.cat-header {
  font-size: 13px;
  font-weight: 600;
  color: #1C1C1E;
  padding: 10px 14px;
  background: #F7F7F7;
  display: flex;
  align-items: center;
  gap: 8px;
}
.cat-dot { width: 8px; height: 8px; border-radius: 2px; background: #9B72CF; flex-shrink: 0; }
.cat-body { padding: 0 14px 10px; }
.cat-body .add-row { margin-top: 8px; }

/* ── 模型选择（步骤2）── */
.xd-select {
  width: 100%;
  border: 1px solid #E0E0E0;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 14px;
  font-family: inherit;
  color: #1C1C1E;
  background: #fff;
  outline: none;
}
.xd-select:focus { border-color: #9B72CF; }
.btn-link {
  background: none;
  border: none;
  color: #9B72CF;
  font-size: 12px;
  cursor: pointer;
  font-family: inherit;
  padding: 0;
  text-decoration: underline;
}
</style>
</head>
<body>

<!-- ── 步骤1：是否开启 AI ── -->
<div id="step1" class="step active">
  <div class="step-indicator">
    <div class="step-dot current"></div>
    <div class="step-dot"></div>
    <div class="step-dot"></div>
    <div class="step-dot"></div>
  </div>
  <h1>欢迎使用小蛋 🥚</h1>
  <p class="subtitle">用一分钟完成初始设置，小蛋就可以帮你记录和分析每天的电脑使用时间了。</p>

  <div class="section-title">是否开启 AI 智能分类？</div>
  <p class="subtitle" style="margin-top:-6px;">AI 分类调用 Anthropic API 自动识别你在做什么，需要填写 API Key。关闭后仍会记录时间，但需要手动归类。</p>

  <div id="opt-yes" class="option-card" onclick="selectApi(true)">
    <div class="option-title">开启 AI 智能分类</div>
    <div class="option-desc">自动识别应用和网页内容，归入学习、娱乐等分类</div>
  </div>
  <div id="opt-no" class="option-card" onclick="selectApi(false)">
    <div class="option-title">暂不开启，手动分类</div>
    <div class="option-desc">不调用 AI 接口，只记录原始数据</div>
  </div>

  <div class="spacer"></div>
  <div class="footer">
    <div class="spacer"></div>
    <button class="btn btn-primary" onclick="step1Next()">下一步 →</button>
  </div>
</div>

<!-- ── 步骤2：API Key ── -->
<div id="step2" class="step">
  <div class="step-indicator">
    <div class="step-dot done"></div>
    <div class="step-dot current"></div>
    <div class="step-dot"></div>
    <div class="step-dot"></div>
  </div>
  <h1>填写 API Key</h1>
  <p class="subtitle">支持 Anthropic 官方 Key，也可配合下方 Base URL 接入 OpenRouter 等代理服务。</p>

  <label class="form-label" for="api-key-input">API Key</label>
  <input type="text" id="api-key-input" placeholder="sk-ant-api03-..." autocomplete="off" spellcheck="false" oninput="_resetModelSection()">
  <div id="api-key-error" class="error-msg"></div>

  <label class="form-label" for="api-base-url-input" style="margin-top:14px;">Base URL（选填）</label>
  <input type="text" id="api-base-url-input" placeholder="留空使用 Anthropic 官方端点，如 https://openrouter.ai/api/v1" autocomplete="off" spellcheck="false" oninput="_resetModelSection()">

  <!-- 模型区（检测后显示） -->
  <div id="model-section" style="display:none; margin-top:14px;">
    <div id="model-picker" style="display:none;">
      <label class="form-label">检测到多个可用模型，请选择：</label>
      <select id="model-select" class="xd-select"></select>
    </div>
    <div id="model-manual" style="display:none;">
      <label class="form-label">未能自动获取模型列表，请填写模型名称：</label>
      <input type="text" id="model-manual-input" placeholder="例如：deepseek-chat、gpt-4o-mini">
    </div>
  </div>

  <!-- 连续失败后显示的跳过提示 -->
  <div id="skip-api-hint" style="display:none; margin-top:12px; padding-top:12px; border-top:.5px solid #E8E8E8;">
    <p style="font-size:12px; color:#8E8E93; margin-bottom:6px;">没关系，可以先不配置 AI，随时在设置里重新开启。</p>
    <button class="btn-link" onclick="skipApiSetup()">暂时跳过 AI 配置</button>
  </div>

  <div class="spacer"></div>
  <div class="footer">
    <button class="btn btn-secondary" onclick="goBack(2)">← 返回</button>
    <div class="spacer"></div>
    <button class="btn btn-primary" onclick="step2Next()">确认 →</button>
  </div>
</div>

<!-- ── 步骤3：大类选择 ── -->
<div id="step3" class="step">
  <div class="step-indicator">
    <div class="step-dot done"></div>
    <div class="step-dot done"></div>
    <div class="step-dot current"></div>
    <div class="step-dot"></div>
  </div>
  <h1>选择活动大类</h1>
  <p class="subtitle">选择你想追踪的活动大类，默认全选。可以取消不需要的，或添加自定义大类。</p>

  <div class="checkbox-list" id="cat-list"></div>
  <div class="add-row" style="margin-bottom:0">
    <input type="text" id="custom-cat-input" placeholder="添加自定义大类...">
    <button class="btn-add" onclick="addCustomCat()">添加</button>
  </div>

  <div class="spacer"></div>
  <div class="footer">
    <button class="btn btn-secondary" onclick="goBack(3)">← 返回</button>
    <div class="spacer"></div>
    <button class="btn btn-primary" onclick="step3Next()">下一步 →</button>
  </div>
</div>

<!-- ── 步骤4：子分类选择 ── -->
<div id="step4" class="step">
  <div class="step-indicator">
    <div class="step-dot done"></div>
    <div class="step-dot done"></div>
    <div class="step-dot done"></div>
    <div class="step-dot current"></div>
  </div>
  <h1>选择子分类</h1>
  <p class="subtitle">为每个大类选择要追踪的子分类，默认全选。可以添加自定义子分类。</p>

  <div class="sub-scroll" id="sub-list"></div>

  <div class="footer">
    <button class="btn btn-secondary" onclick="goBack(4)">← 返回</button>
    <div class="spacer"></div>
    <button class="btn btn-primary" onclick="complete()">完成设置 ✓</button>
  </div>
</div>

<script>
var PRESETS = __PRESETS_JSON__;

// ── 全局状态（步骤间共享，直到最后一步才写入 Python）──
var state = {
  apiChoice: null,   // true | false
  apiKey: '',
  apiBaseUrl: '',
  apiModel: '',
  catChecked: {},    // { 大类名: boolean }
  subChecked: {}     // { 大类名: { 子类名: boolean } }
};
var _failCount = 0;
var _needsModelConfirm = false;

/* ═══════════════════════════════════════════
   步骤1：API 开关选择
═══════════════════════════════════════════ */
function selectApi(val) {
  state.apiChoice = val;
  document.getElementById('opt-yes').classList.toggle('selected', val === true);
  document.getElementById('opt-no').classList.toggle('selected', val === false);
}

function step1Next() {
  if (state.apiChoice === null) {
    alert('请先选择是否开启 AI 功能');
    return;
  }
  if (state.apiChoice === false) {
    // 选"否" → 直接结束，不进入后续步骤
    xdDone({ api_enabled: false, api_key: '', custom_categories: {} });
    return;
  }
  showStep(2);
}

/* ═══════════════════════════════════════════
   步骤2：API Key 录入
═══════════════════════════════════════════ */
function step2Next() {
  var key = document.getElementById('api-key-input').value.trim();
  if (!key) {
    if (confirm('未填写 API Key，将按未开启 AI 处理，继续吗？')) {
      xdDone({ api_enabled: false, api_key: '', api_base_url: '', custom_categories: {} });
    }
    return;
  }
  // 第二次点击：用户已选/填好模型，进入验证阶段
  if (_needsModelConfirm) {
    var picker = document.getElementById('model-picker');
    var manual = document.getElementById('model-manual');
    var model = '';
    if (picker.style.display !== 'none') {
      model = document.getElementById('model-select').value;
    } else if (manual.style.display !== 'none') {
      model = document.getElementById('model-manual-input').value.trim();
      if (!model) {
        var e = document.getElementById('api-key-error');
        e.textContent = '请填写模型名称';
        e.classList.add('show');
        return;
      }
    }
    if (!model) return;
    _needsModelConfirm = false;
    var btn = document.querySelector('#step2 .btn-primary');
    btn.disabled = true;
    btn.textContent = '正在验证...';
    document.getElementById('api-key-error').classList.remove('show');
    _proceedToValidate(model);
    return;
  }
  // 第一次点击：先检测模型
  var baseUrl = document.getElementById('api-base-url-input').value.trim();
  window._setKeyValidating(true);
  window.location.href = 'xd://onboarding_validate_key?key=' + encodeURIComponent(key)
    + '&base_url=' + encodeURIComponent(baseUrl);
}

/* ═══════════════════════════════════════════
   步骤3：大类选择
═══════════════════════════════════════════ */
function buildStep3() {
  var list = document.getElementById('cat-list');
  list.innerHTML = '';
  // 首次进入时从预设初始化（全选），之后保留用户的勾选状态
  Object.keys(PRESETS).forEach(function(cat) {
    if (!(cat in state.catChecked)) state.catChecked[cat] = true;
  });
  Object.keys(state.catChecked).forEach(function(cat) {
    list.appendChild(makeCatRow(cat));
  });
}

function makeCatRow(cat) {
  var item = document.createElement('div');
  item.className = 'check-item';
  var cb = document.createElement('div');
  cb.className = 'xd-cb' + (state.catChecked[cat] ? ' checked' : '');
  var lbl = document.createElement('span');
  lbl.className = 'check-label';
  lbl.textContent = cat;
  item.appendChild(cb);
  item.appendChild(lbl);
  item.addEventListener('click', function() {
    var now = !state.catChecked[cat];
    state.catChecked[cat] = now;
    cb.classList.toggle('checked', now);
  });
  return item;
}

function addCustomCat() {
  var input = document.getElementById('custom-cat-input');
  var cat = input.value.trim();
  if (!cat || cat in state.catChecked) { input.value = ''; return; }
  state.catChecked[cat] = true;
  document.getElementById('cat-list').appendChild(makeCatRow(cat));
  input.value = '';
}

function step3Next() {
  var any = Object.keys(state.catChecked).some(function(c) { return state.catChecked[c]; });
  if (!any) { alert('请至少选择一个大类'); return; }
  buildStep4();
  showStep(4);
}

/* ═══════════════════════════════════════════
   步骤4：子分类选择（按大类分区块，可滚动）
═══════════════════════════════════════════ */
function buildStep4() {
  var list = document.getElementById('sub-list');
  list.innerHTML = '';

  var selectedCats = Object.keys(state.catChecked).filter(function(c) {
    return state.catChecked[c];
  });

  selectedCats.forEach(function(cat) {
    var presetSubs = PRESETS[cat] || [];

    // 首次进入此大类的子分类时从预设初始化（全选）
    if (!state.subChecked[cat]) state.subChecked[cat] = {};
    presetSubs.forEach(function(sub) {
      if (!(sub in state.subChecked[cat])) state.subChecked[cat][sub] = true;
    });

    // ── 区块容器 ──
    var section = document.createElement('div');
    section.className = 'cat-section';

    var header = document.createElement('div');
    header.className = 'cat-header';
    var dot = document.createElement('div');
    dot.className = 'cat-dot';
    header.appendChild(dot);
    header.appendChild(document.createTextNode(cat));
    section.appendChild(header);

    var body = document.createElement('div');
    body.className = 'cat-body';

    // 合并预设子类与自定义子类（保留原有顺序）
    var allSubs = {};
    presetSubs.forEach(function(s) { allSubs[s] = true; });
    Object.keys(state.subChecked[cat]).forEach(function(s) { allSubs[s] = true; });

    Object.keys(allSubs).forEach(function(sub) {
      body.appendChild(makeSubRow(cat, sub));
    });

    // ── 添加自定义子类 ──
    var addRow = document.createElement('div');
    addRow.className = 'add-row';
    var inp = document.createElement('input');
    inp.type = 'text';
    inp.placeholder = '添加自定义子分类...';
    var addBtn = document.createElement('button');
    addBtn.className = 'btn-add';
    addBtn.textContent = '添加';
    (function(localCat, localBody, localAddRow) {
      addBtn.addEventListener('click', function() {
        var val = inp.value.trim();
        if (!val) return;
        if (!state.subChecked[localCat]) state.subChecked[localCat] = {};
        if (val in state.subChecked[localCat]) { inp.value = ''; return; }
        state.subChecked[localCat][val] = true;
        localBody.insertBefore(makeSubRow(localCat, val), localAddRow);
        inp.value = '';
      });
    })(cat, body, addRow);
    addRow.appendChild(inp);
    addRow.appendChild(addBtn);
    body.appendChild(addRow);

    section.appendChild(body);
    list.appendChild(section);
  });
}

function makeSubRow(cat, sub) {
  var item = document.createElement('div');
  item.className = 'check-item';
  var checked = !!(state.subChecked[cat] && state.subChecked[cat][sub] !== false);
  var cb = document.createElement('div');
  cb.className = 'xd-cb' + (checked ? ' checked' : '');
  var lbl = document.createElement('span');
  lbl.className = 'check-label';
  lbl.textContent = sub;
  item.appendChild(cb);
  item.appendChild(lbl);
  (function(localCat, localSub) {
    item.addEventListener('click', function() {
      if (!state.subChecked[localCat]) state.subChecked[localCat] = {};
      var now = !state.subChecked[localCat][localSub];
      state.subChecked[localCat][localSub] = now;
      cb.classList.toggle('checked', now);
    });
  })(cat, sub);
  return item;
}

function complete() {
  // 汇总最终 custom_categories：只含已选大类 & 其下已选子类
  var result = {};
  Object.keys(state.catChecked).forEach(function(cat) {
    if (!state.catChecked[cat]) return;
    var subs = state.subChecked[cat] || {};
    result[cat] = Object.keys(subs).filter(function(s) { return subs[s]; });
  });
  xdDone({ api_enabled: true, api_key: state.apiKey, api_base_url: state.apiBaseUrl, api_model: state.apiModel, custom_categories: result });
}

/* ═══════════════════════════════════════════
   导航辅助
═══════════════════════════════════════════ */
function showStep(n) {
  document.querySelectorAll('.step').forEach(function(el) {
    el.classList.remove('active');
  });
  document.getElementById('step' + n).classList.add('active');
}

function goBack(fromStep) {
  if (fromStep === 2) {
    showStep(1);
  } else if (fromStep === 3) {
    showStep(2);
  } else if (fromStep === 4) {
    buildStep3();   // 重建步骤3以恢复状态
    showStep(3);
  }
}

/* ═══════════════════════════════════════════
   步骤2：验证状态控制 & Python 回调入口
═══════════════════════════════════════════ */
function _setKeyValidating(loading) {
  var btn = document.querySelector('#step2 .btn-primary');
  var errDiv = document.getElementById('api-key-error');
  if (loading) {
    btn.disabled = true;
    btn.textContent = '正在检测模型...';
    errDiv.textContent = '';
    errDiv.classList.remove('show');
    document.getElementById('model-section').style.display = 'none';
    document.getElementById('skip-api-hint').style.display = 'none';
  } else {
    btn.disabled = false;
    btn.textContent = '确认 →';
  }
}

function _resetModelSection() {
  _needsModelConfirm = false;
  document.getElementById('model-section').style.display = 'none';
  document.getElementById('skip-api-hint').style.display = 'none';
  document.getElementById('api-key-error').classList.remove('show');
}

function _pickModelByHeuristic(models) {
  var keywords = ['flash', 'haiku', 'mini', 'lite'];
  for (var i = 0; i < keywords.length; i++) {
    for (var j = 0; j < models.length; j++) {
      if (models[j].toLowerCase().indexOf(keywords[i]) !== -1) return models[j];
    }
  }
  return null;
}

function _proceedToValidate(model) {
  state.apiModel = model;
  var key = document.getElementById('api-key-input').value.trim();
  var baseUrl = document.getElementById('api-base-url-input').value.trim();
  window.location.href = 'xd://onboarding_validate_with_model'
    + '?key=' + encodeURIComponent(key)
    + '&base_url=' + encodeURIComponent(baseUrl)
    + '&model=' + encodeURIComponent(model);
}

// Python 回调：模型检测完成（Phase 1）
window._onModelsDetected = function(result) {
  _failCount = 0;
  if (result.error) {
    _setKeyValidating(false);
    var e = document.getElementById('api-key-error');
    e.textContent = result.error;
    e.classList.add('show');
    _failCount = 1;
    _maybeShowSkipBtn();
    return;
  }
  var models = result.models || [];
  var section = document.getElementById('model-section');
  document.getElementById('model-picker').style.display = 'none';
  document.getElementById('model-manual').style.display = 'none';

  if (models.length === 0) {
    // 情况 C：手动填写
    section.style.display = '';
    document.getElementById('model-manual').style.display = '';
    _setKeyValidating(false);
    _needsModelConfirm = true;
  } else {
    var chosen = models.length === 1 ? models[0] : _pickModelByHeuristic(models);
    if (chosen) {
      // 情况 A：自动选择，直接进入验证
      var btn = document.querySelector('#step2 .btn-primary');
      btn.textContent = '正在验证...';
      _proceedToValidate(chosen);
    } else {
      // 情况 B：展示下拉选择
      section.style.display = '';
      var sel = document.getElementById('model-select');
      sel.innerHTML = '';
      models.forEach(function(m) {
        var opt = document.createElement('option');
        opt.value = m; opt.textContent = m;
        sel.appendChild(opt);
      });
      document.getElementById('model-picker').style.display = '';
      _setKeyValidating(false);
      _needsModelConfirm = true;
    }
  }
};

// Python 回调：key 验证完成（Phase 2）
window._onKeyValidated = function(success, errorMsg) {
  _setKeyValidating(false);
  if (success) {
    state.apiKey = document.getElementById('api-key-input').value.trim();
    state.apiBaseUrl = document.getElementById('api-base-url-input').value.trim();
    buildStep3();
    showStep(3);
  } else {
    var errDiv = document.getElementById('api-key-error');
    errDiv.textContent = errorMsg;
    errDiv.classList.add('show');
    _failCount++;
    _maybeShowSkipBtn();
  }
};

function _maybeShowSkipBtn() {
  if (_failCount >= 2) document.getElementById('skip-api-hint').style.display = '';
}

function skipApiSetup() {
  xdDone({ api_enabled: false, api_key: '', api_base_url: '', custom_categories: {} });
}

// 一次性将最终结果传回 Python（整个流程唯一的写入时机）
function xdDone(payload) {
  window.location.href = 'xd://onboarding_done?data=' + encodeURIComponent(JSON.stringify(payload));
}
</script>
</body>
</html>"""


def _build_onboarding_html(presets_json: str) -> str:
    return _ONBOARDING_HTML_TEMPLATE.replace("__PRESETS_JSON__", presets_json)


# ── OnboardingWindow ──────────────────────────────────────────────────────────

class OnboardingWindow(NSObject):

    def init(self):
        self = objc.super(OnboardingWindow, self).init()
        if self is not None:
            self._window  = None
            self._webview = None
        return self

    @objc.python_method
    def show(self):
        if self._window is None:
            self._build_window()
        self._window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    @objc.python_method
    def _build_window(self):
        # 保留关闭按钮，但通过 windowShouldClose_ 拦截以给用户确认提示
        mask = NSTitledWindowMask | NSClosableWindowMask | NSMiniaturizableWindowMask
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (540, 560)), mask, NSBackingStoreBuffered, False
        )
        win.setTitle_("小蛋 — 初始设置")
        win.setMinSize_((480, 460))
        win.setBackgroundColor_(NSColor.whiteColor())
        win.setReleasedWhenClosed_(False)
        win.center()
        win.setDelegate_(self)
        self._window = win

        config = WKWebViewConfiguration.alloc().init()
        wv = WKWebView.alloc().initWithFrame_configuration_(
            win.contentView().bounds(), config
        )
        wv.setAutoresizingMask_(18)  # NSViewWidthSizable | NSViewHeightSizable
        wv.setNavigationDelegate_(self)
        wv.setUIDelegate_(self)
        win.contentView().addSubview_(wv)
        self._webview = wv

        presets_json = json.dumps(DEFAULT_CATEGORY_PRESETS, ensure_ascii=False)
        self._webview.loadHTMLString_baseURL_(
            _build_onboarding_html(presets_json), None
        )

    # ── NSWindowDelegate ──────────────────────────────────────────────────────
    # 用户点击红色关闭按钮时拦截，弹出确认提示。
    # 确认跳过 → 视同"选否"保存设置并关闭；取消 → 窗口保持打开。
    def windowShouldClose_(self, win):
        alert = NSAlert.alloc().init()
        alert.setMessageText_("跳过初始设置？")
        alert.setInformativeText_("跳过后 AI 功能将保持关闭，可随时在设置页重新开启。")
        alert.addButtonWithTitle_("取消")           # NSAlertFirstButtonReturn  = 1000
        alert.addButtonWithTitle_("跳过并关闭")    # NSAlertSecondButtonReturn = 1001
        result = alert.runModal()
        if result == 1001:
            self._finish(api_enabled=False, api_key="", api_base_url="", custom_categories={})
            return True   # 允许关闭
        return False       # 阻止关闭

    # ── WKUIDelegate：支持 JS alert() / confirm() ─────────────────────────────
    def webView_runJavaScriptAlertPanelWithMessage_initiatedByFrame_completionHandler_(
            self, webview, message, frame, handler):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.addButtonWithTitle_("确定")
        alert.runModal()
        handler()

    def webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(
            self, webview, message, frame, handler):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.addButtonWithTitle_("确定")
        alert.addButtonWithTitle_("取消")
        result = alert.runModal()
        handler(result == 1000)  # 1000 = 确定

    # ── WKNavigationDelegate：拦截 xd:// scheme ───────────────────────────────
    def webView_decidePolicyForNavigationAction_decisionHandler_(
            self, webview, action, handler):
        url = action.request().URL()
        if url is not None and url.scheme() == "xd":
            handler(0)   # WKNavigationActionPolicyCancel（不真正导航）
            self._handle_action(url)
        else:
            handler(1)   # WKNavigationActionPolicyAllow

    @objc.python_method
    def _handle_action(self, url):
        action = url.host() or ""
        qs = urllib.parse.parse_qs(url.query() or "", keep_blank_values=True)

        def _str(key):
            return urllib.parse.unquote(qs[key][0]) if key in qs else ""

        if action == "onboarding_validate_key":
            key = _str("key")
            base_url = _str("base_url")
            self._webview.evaluateJavaScript_completionHandler_(
                "window._setKeyValidating(true)", None
            )
            import threading
            threading.Thread(
                target=self._bg_validate_key, args=(key, base_url), daemon=True
            ).start()

        elif action == "onboarding_validate_with_model":
            key = _str("key")
            base_url = _str("base_url")
            model = _str("model")
            import threading
            threading.Thread(
                target=self._bg_validate_with_model, args=(key, base_url, model), daemon=True
            ).start()

        elif action == "onboarding_done":
            payload_str = _str("data")
            try:
                payload = json.loads(payload_str)
            except (json.JSONDecodeError, ValueError):
                print(f"[onboarding] JSON 解析失败: {payload_str!r}")
                return
            self._finish(
                api_enabled=bool(payload.get("api_enabled", False)),
                api_key=str(payload.get("api_key", "")),
                api_base_url=str(payload.get("api_base_url", "")),
                api_model=str(payload.get("api_model", "")),
                custom_categories=dict(payload.get("custom_categories", {})),
            )

    @objc.python_method
    def _bg_validate_key(self, key, base_url=""):
        """子线程 Phase 1：查询可用模型列表，结果通过 performSelector 切回主线程。"""
        try:
            import anthropic
        except ImportError:
            result = json.dumps({"models": None, "error": "缺少 anthropic 依赖，请运行：pip install anthropic"})
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "onModelsDetected:", result, False
            )
            return
        try:
            client = anthropic.Anthropic(api_key=key, **({"base_url": base_url} if base_url else {}))
            resp = client.models.list()
            models = [m.id for m in resp.data]
            result = json.dumps({"models": models, "error": ""})
        except anthropic.AuthenticationError:
            error = "该地址下 API Key 认证失败，请检查 Key 与 Base URL 是否匹配" if base_url else "API Key 无效，请检查后重新输入"
            result = json.dumps({"models": None, "error": error})
        except (anthropic.APIConnectionError, anthropic.APITimeoutError):
            error = "无法连接到该地址，请检查 Base URL 是否正确" if base_url else "网络连接失败，请检查网络后重试"
            result = json.dumps({"models": None, "error": error})
        except Exception:
            # 代理不支持 models 接口 → 情况 C，让用户手动填写
            result = json.dumps({"models": [], "error": ""})
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "onModelsDetected:", result, False
        )

    @objc.python_method
    def _bg_validate_with_model(self, key, base_url, model):
        """子线程 Phase 2：用指定模型名验证 key 有效性，结果通过 performSelector 切回主线程。"""
        try:
            import anthropic
        except ImportError:
            result = json.dumps({"success": False, "error": "缺少 anthropic 依赖"})
            self.performSelectorOnMainThread_withObject_waitUntilDone_(
                "onKeyValidated:", result, False
            )
            return
        try:
            client = anthropic.Anthropic(api_key=key, **({"base_url": base_url} if base_url else {}))
            client.messages.create(
                model=model,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}]
            )
            result = json.dumps({"success": True, "error": ""})
        except anthropic.AuthenticationError:
            error = "该地址下 API Key 认证失败，请检查 Key 与 Base URL 是否匹配" if base_url else "API Key 无效，请检查后重新输入"
            result = json.dumps({"success": False, "error": error})
        except anthropic.RateLimitError:
            result = json.dumps({"success": True, "error": ""})
        except (anthropic.APIConnectionError, anthropic.APITimeoutError):
            error = "无法连接到该地址，请检查 Base URL 是否正确" if base_url else "网络连接失败，请检查网络后重试"
            result = json.dumps({"success": False, "error": error})
        except anthropic.APIResponseValidationError:
            result = json.dumps({"success": False, "error": "服务返回格式与 Anthropic SDK 不兼容，请确认服务商是否支持 Anthropic 官方格式"})
        except Exception as _e:
            result = json.dumps({"success": False, "error": f"验证失败：{type(_e).__name__}: {_e}"})
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "onKeyValidated:", result, False
        )

    def onModelsDetected_(self, result_json):
        """主线程 ObjC 方法：接收模型列表检测结果并更新 WKWebView（Phase 1 回调）。"""
        try:
            data = json.loads(str(result_json))
        except Exception:
            data = {"models": [], "error": "解析失败"}
        js = "window._onModelsDetected({})".format(
            json.dumps(data, ensure_ascii=False)
        )
        if self._webview:
            self._webview.evaluateJavaScript_completionHandler_(js, None)

    def onKeyValidated_(self, result_json):
        """主线程 ObjC 方法：接收验证结果并更新 WKWebView（Phase 2 回调）。"""
        try:
            data = json.loads(str(result_json))
            success = data.get("success", False)
            error_msg = data.get("error", "")
        except Exception:
            success = False
            error_msg = "未知错误"
        js = "window._onKeyValidated({}, {})".format(
            "true" if success else "false",
            json.dumps(error_msg, ensure_ascii=False)
        )
        if self._webview:
            self._webview.evaluateJavaScript_completionHandler_(js, None)

    @objc.python_method
    def _finish(self, api_enabled: bool, api_key: str, api_base_url: str = "", api_model: str = "", custom_categories: dict = None):
        """将整个引导流程的最终结果一次性写入 settings。"""
        if custom_categories is None:
            custom_categories = {}
        s = load_settings()
        s["onboarding_completed"] = True
        s["api_enabled"] = api_enabled
        if api_key:
            s["api_key"] = api_key
            s["api_base_url"] = api_base_url  # 空字符串表示使用官方端点
            if api_model:
                s["api_model"] = api_model
        if custom_categories:
            s["custom_categories"] = custom_categories
        save_settings(s)
        # 成功验证了 key → 清除"key 失效"标记（如果之前有的话）
        if api_key:
            try:
                from classifier import clear_api_key_invalid
                clear_api_key_invalid()
            except Exception:
                pass
        if self._window:
            self._window.orderOut_(None)


# ── 对外接口 ──────────────────────────────────────────────────────────────────

_instance = None


def show_onboarding_window():
    global _instance
    if _instance is None:
        _instance = OnboardingWindow.alloc().init()
    _instance.show()
