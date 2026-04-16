"""
测试 pyvis 图形在 Streamlit iframe 中的渲染问题
运行: streamlit run test_graph_render.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
from pyvis.network import Network
import re
import os
import pyvis as _pyvis

st.set_page_config(page_title="Graph Test", layout="wide")

st.title("Pyvis Graph Test")

st.markdown("""
### Testing:
1. Physics disable (should fix gray screen / stuck at 0%)
2. Fullscreen toggle (should work without iframe disappearing)
3. window.network capture (should enable zoom controls)
""")

# 创建一个简单的网络
net = Network(height="520px", width="100%", directed=True, bgcolor="#0d1117", font_color="white")
net.add_node("a", label="Root Node", color="#e74c3c", size=35)
net.add_node("b", label="Node B", color="#27ae60", size=20)
net.add_node("c", label="Node C", color="#2980b9", size=20)
net.add_edge("a", "b")
net.add_edge("a", "c")
net.add_edge("b", "c")

html = net.generate_html()

st.divider()
st.markdown("### Raw HTML Analysis")

col1, col2, col3 = st.columns(3)

with col1:
    physics_pattern = re.search(r'"physics"[^}]*\}', html)
    if physics_pattern:
        st.code(physics_pattern.group(0), language="json")
    else:
        st.caption("No physics config found")

with col2:
    network_pattern = re.search(r'new vis\.Network\([^)]+\)', html)
    if network_pattern:
        st.code(network_pattern.group(0), language="javascript")
    else:
        st.caption("No vis.Network call found")

with col3:
    drawgraph_calls = re.findall(r'drawGraph\([^)]*\)', html)
    st.code("\n".join(drawgraph_calls), language="javascript")

st.divider()

# 应用修复（使用physics块替换方案）
def inject_controls_fixed(html: str) -> tuple:
    """注入控制栏和physics禁用（修复版 - 直接替换physics配置块）"""
    errors = []

    # 修复1: 内联 utils.js
    _utils_path = os.path.join(os.path.dirname(_pyvis.__file__), 'lib', 'bindings', 'utils.js')
    if os.path.exists(_utils_path):
        _utils_raw = open(_utils_path, encoding='utf-8').read()
        _utils_safe = _utils_raw.replace('</script>', '<\\/script>')
        patterns = [
            r'<script\s+src="lib/bindings/utils\.js"></script>',
            r'<script\s+[^>]*?src="[^"]*?utils\.js"[^>]*></script>',
        ]
        for pat in patterns:
            replaced = re.sub(pat, '<script>' + _utils_safe + '</script>', html, count=1)
            if replaced != html:
                html = replaced
                st.success("[OK] utils.js inlined")
                break

    # 修复2: 删除所有无效的 node_modules 引用
    html = re.sub(r'<script[^>]*node_modules[^>]*>[\s]*</script>', '', html)
    html = re.sub(r'<link[^>]*node_modules[^>]*/?\s*>', '', html)
    st.success("[OK] Removed node_modules refs")

    # 修复3: 删除无效的 vis-network CSS CDN
    html = re.sub(r'<link[^>]*vis-network[^>]*css[^>]*/?\s*>', '', html)
    st.success("[OK] Removed invalid CSS CDN")

    # 修复4: 删除 bootstrap 引用
    html = re.sub(r'<script[^>]*bootstrap[^>]*>[\s]*</script>', '', html)
    html = re.sub(r'<link[^>]*bootstrap[^>]*/?\s*>', '', html)
    st.success("[OK] Removed bootstrap refs")

    # 修复5: 直接替换 physics 配置块（禁用stabilization）
    physics_block_pattern = re.compile(
        r'"physics"\s*:\s*\{[^}]*"stabilization"[^}]*\{[^}]*\}[^}]*\}'
    )
    match = physics_block_pattern.search(html)
    if match:
        orig_block = match.group(0)
        new_block = '"physics": {"enabled": false, "stabilization": {"enabled": false}}'
        html = html.replace(orig_block, new_block, 1)
        st.success("[OK] Physics block replaced - stabilization disabled")
    else:
        errors.append("Physics block pattern not matched")
        st.error("[FAIL] Physics block NOT found")
        # Fallback: 注入方式
        network_pattern = re.compile(r'network\s*=\s*new\s+vis\.Network')
        match = network_pattern.search(html)
        if match:
            html = html.replace(match.group(0), 'options.physics={enabled:false}; ' + match.group(0), 1)
            st.warning("[FALLBACK] Used injection method")

    # 暴露 network
    _drawgraph_pattern = re.compile(r'drawGraph\s*\(\s*\)\s*;')
    _drawgraph_match = _drawgraph_pattern.search(html)
    if _drawgraph_match:
        orig_call = _drawgraph_match.group(0)
        new_call = "window.network = drawGraph();"
        html = html.replace(orig_call, new_call, 1)
        st.success("[OK] window.network exposed")
    else:
        errors.append("drawGraph pattern not found")
        st.error("[FAIL] drawGraph not matched")

    # 控制栏（简化版）
    controls = """
    <div id="__graph_ctrl" style="position:sticky;top:0;z-index:999;background:#161b22;padding:8px;border-bottom:1px solid #30363d;font-family:system-ui;">
      <span style="color:#8b949e;font-size:12px;">Zoom:</span>
      <button onclick="if(window.network)window.network.moveTo({scale:window.network.getScale()+0.2})" style="background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:4px;padding:2px 8px;">+</button>
      <button onclick="if(window.network)window.network.moveTo({scale:window.network.getScale()-0.2})" style="background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:4px;padding:2px 8px;">-</button>
      <button onclick="if(window.network)window.network.fit()" style="background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:4px;padding:2px 8px;">Fit</button>
      <button onclick="window.__toggleFs()" style="margin-left:10px;background:#1158c7;color:#fff;border:none;border-radius:4px;padding:2px 8px;">Fullscreen</button>
      <span id="__status" style="margin-left:8px;color:#8b949e;font-size:11px;">Loading...</span>
    </div>
    """

    # 全屏脚本（修复版：同步更新容器尺寸）
    fullscreen_script = """
    <script>
    (function() {
      var _isFs = false;
      var _origWidth = null;
      var _origHeight = null;

      function _setStatus(msg) {
        var st = document.getElementById('__status');
        if (st) st.textContent = msg;
      }

      function _getIframe() {
        if (window.frameElement) return window.frameElement;
        try {
          var iframes = parent.document.querySelectorAll('iframe');
          for (var i = 0; i < iframes.length; i++) {
            if (iframes[i].contentWindow === window) return iframes[i];
          }
        } catch(e) {}
        return null;
      }

      function _resizeContainer(fullscreen) {
        var container = document.getElementById('mynetwork');
        if (!container) return;
        if (fullscreen) {
          container.style.width = '100vw';
          container.style.height = '100vh';
        } else {
          container.style.width = '100%';
          container.style.height = '520px';
        }
      }

      window.__toggleFs = function() {
        var iframe = _getIframe();
        if (!iframe) { _setStatus('Fullscreen unavailable'); return; }

        if (!_isFs) {
          _origWidth = iframe.style.width || '100%';
          _origHeight = iframe.style.height || '520px';
          iframe.style.position = 'fixed';
          iframe.style.top = '0';
          iframe.style.left = '0';
          iframe.style.width = '100vw';
          iframe.style.height = '100vh';
          iframe.style.zIndex = '999999';
          iframe.style.background = '#0d1117';

          _resizeContainer(true);
          _isFs = true;
          _setStatus('Fullscreen');

          setTimeout(function() {
            if (window.network) window.network.fit();
          }, 100);
        } else {
          iframe.style.position = '';
          iframe.style.width = _origWidth;
          iframe.style.height = _origHeight;
          iframe.style.zIndex = '';
          iframe.style.background = '';

          _resizeContainer(false);
          _isFs = false;
          _setStatus('Connected');

          setTimeout(function() {
            if (window.network) window.network.fit();
          }, 100);
        }
      };

      var _pollCount = 0;
      function _poll() {
        _pollCount++;
        if (window.network) {
          _setStatus('Connected');
          return;
        }
        if (_pollCount > 100) { _setStatus('Timeout'); return; }
        setTimeout(_poll, 50);
      }
      setTimeout(_poll, 50);
    })();
    </script>
    """

    html = html.replace('<body>', '<body>' + controls, 1)
    html = html.replace('</body>', fullscreen_script + '</body>', 1)

    return html, errors

html_fixed, errors = inject_controls_fixed(html)

st.divider()
st.markdown("### Fixed HTML Analysis")

col1, col2 = st.columns(2)
with col1:
    if "options.physics" in html_fixed:
        st.code("options.physics={enabled:false} found", language="javascript")
    else:
        st.error("Physics disable not found")

with col2:
    if "window.network" in html_fixed:
        st.code("window.network = drawGraph() found", language="javascript")
    else:
        st.error("Network expose not found")

st.divider()
st.markdown("### Graph Rendering Test (should show nodes immediately)")

st.components.v1.html(html_fixed, height=560, scrolling=True)

st.caption("If still gray, check browser console (F12) for errors")

# frameElement 可用性测试
st.divider()
st.markdown("### iframe Environment Test")

test_html = """
<script>
(function() {
  var results = [];
  results.push('window.frameElement: ' + (window.frameElement ? 'AVAILABLE' : 'NULL'));
  results.push('window === window.parent: ' + (window === window.parent));
  try {
    var iframes = parent.document.querySelectorAll('iframe');
    results.push('parent iframes count: ' + iframes.length);
    for (var i = 0; i < iframes.length; i++) {
      if (iframes[i].contentWindow === window) {
        results.push('Found self in parent iframes[' + i + ']');
      }
    }
  } catch(e) {
    results.push('parent access error: ' + e.message);
  }
  document.body.innerHTML = '<pre style="color:#3fb950;background:#0d1117;padding:10px;">' + results.join('\\n') + '</pre>';
})();
</script>
"""
st.components.v1.html(test_html, height=120)