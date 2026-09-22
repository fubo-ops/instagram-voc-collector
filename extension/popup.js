'use strict';
const statusBox = document.getElementById('status');

async function activeInstagramTab() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab || !/^https:\/\/(www\.)?instagram\.com\/(p|reel)\//.test(tab.url || '')) throw new Error('请先打开 Instagram 帖子或 Reel 页面');
  return tab;
}
async function downloadCapture(capture) {
  if (!capture || capture.schema_version !== 'instagram_visible_capture_v1') throw new Error('未获得有效的可见评论数据');
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const filename = `instagram-voc/${capture.media.media_id}-${stamp}.json`;
  const data = 'data:application/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(capture, null, 2));
  await chrome.downloads.download({url: data, filename, saveAs: false});
}
document.getElementById('capture').addEventListener('click', async () => {
  statusBox.textContent = '正在读取当前标签页…';
  try {
    const tab = await activeInstagramTab();
    const injected = await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['capture_core.js', 'capture.js']});
    const capture = injected[0] && injected[0].result;
    await downloadCapture(capture);
    statusBox.textContent = `已下载 ${capture.comments.length} 条当前可见评论。`;
  } catch (error) { statusBox.textContent = `采集停止：${error.message}`; }
});
document.getElementById('startContinuous').addEventListener('click', async () => {
  statusBox.textContent = '正在启动…';
  try {
    const tab = await activeInstagramTab();
    const target = Math.max(0, Number(document.getElementById('targetComments').value) || 0);
    const response = await chrome.runtime.sendMessage({action: 'start_continuous', tabId: tab.id, url: tab.url, options: {target_comments: target}});
    if (!response || !response.ok) throw new Error(response && response.error || '启动失败');
    statusBox.textContent = '持续采集已启动；可关闭此窗口。完成后会自动下载 JSON。';
  } catch (error) { statusBox.textContent = `启动停止：${error.message}`; }
});
document.getElementById('stopContinuous').addEventListener('click', async () => {
  try {
    const tab = await activeInstagramTab();
    const response = await chrome.runtime.sendMessage({action: 'stop_continuous', tabId: tab.id});
    if (!response || !response.ok) throw new Error(response && response.error || '停止失败');
    statusBox.textContent = '已请求停止；当前已采集内容将自动保存。';
  } catch (error) { statusBox.textContent = `停止失败：${error.message}`; }
});
(async () => {
  try {
    const tab = await activeInstagramTab();
    const response = await chrome.runtime.sendMessage({action: 'get_status', tabId: tab.id});
    if (response && response.state && response.state.phase !== 'idle') {
      const s = response.state;
      statusBox.textContent = `${s.phase}：${s.comment_count || 0} 条，轮次 ${s.round_count || 0}${s.stop_reason ? `，原因 ${s.stop_reason}` : ''}`;
    }
  } catch (_) {}
})();
