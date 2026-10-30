const el = (id) => document.getElementById(id);
const labels = { received: '收到图片', saved: '已归档', rejected: '已筛除', duplicate: '重复图片', error: '处理错误', dropped: '队列跳过' };
const reasons = {
  queue_full: '等待队列已满', unsupported_image_source: '无法取得受支持的图片来源',
  forward_missing_message_id: '转发消息缺少可读取的消息编号', download_failed: 'QQ 图片下载失败',
  processing_failed: '图片处理失败', low_resolution: '尺寸不足', too_small: '尺寸不足',
  low_quality: '低信息量与低清晰度', no_character: '未可靠识别到角色', unknown_character: '未可靠识别到角色',
  uncertain_rating: '内容分级不确定', duplicate: '重复图片', near_duplicate: '近似重复图片',
  saved: '已保存', filtered_tag: '命中筛除标签', animated: '跳过动画', animated_image: '跳过动画',
  aspect_ratio: '图片比例过于狭长', too_large: '图片过大',
  unknown_or_uncertain_character: '未可靠识别到角色', flat_or_strongly_blurred_image: '低信息量且明显模糊',
  extreme_aspect_ratio: '图片比例过于狭长', too_many_pixels: '解码像素过多', file_too_large: '文件过大',
  exact_duplicate: '文件完全重复', accepted_training_candidate: '已作为训练候选图片保存',
  accepted_unknown_character_candidate: '人物明确，具体角色未识别，已作为训练候选图片保存',
  no_reliable_person: '未检测到足够明确的人物特征',
  forward_missing_reference: '合并转发缺少可读取的消息或资源编号',
  forward_unavailable: '合并转发无法取回，可能已过期或服务端暂不可用',
  screenshot_tag: '识别到截图或拼贴标签', unsuitable_image_type: '画面类型不适合插画训练',
  uncertain_artwork_type: '无法可靠确认是完整插画',
  uncertain_nsfw_evidence: '成人分级与明确内容标签不一致，按不确定跳过',
  bangumi: '动画截图', not_painting: '聊天/游戏界面等非绘画内容', comic: '漫画或多格画面',
  illustration: '完整插画', '3d': '3D渲染'
};
function reasonLabel(value) {
  const reason = String(value || '');
  if (reasons[reason]) return reasons[reason];
  const p = reason.indexOf(':');
  return p > 0 && reasons[reason.slice(0, p)] ? reasons[reason.slice(0, p)] + '：' + (reasons[reason.slice(p + 1)] || reason.slice(p + 1)) : reason || '处理完成';
}
function node(tag, cls, content) { const n = document.createElement(tag); if (cls) n.className = cls; n.textContent = content; return n; }
async function refresh() {
  el('refresh').disabled = true;
  try {
    const response = await fetch('../@data/storage.json', { cache: 'no-store' });
    if (!response.ok) throw new Error('尚未读取到插件状态，请确认插件已启用。');
    const { snapshot: s } = await response.json();
    if (!s) throw new Error('尚未生成状态，首次启动后请稍候刷新。');
    el('status').textContent = s.message || s.status;
    el('detail').textContent = `${s.model || 'PixAI Tagger v1.0'}${s.provider ? ' · ' + s.provider : ''} · 等待 ${s.queue || 0} 项${s.processing ? ' · 正在处理' : ''}。${s.receiverMessage || ''}`;
    el('dot').className = 'dot ' + (s.status === 'ready' ? 'ready' : ['error', 'retrying'].includes(s.status) ? 'error' : '');
    el('counts').replaceChildren(...Object.entries(labels).map(([k, title]) => {
      const n = node('div', 'count', ''); n.append(node('b', '', Number(s.counts?.[k] || 0).toLocaleString()), node('span', '', title)); return n;
    }));
    el('folder').textContent = s.outputRoot || '未配置';
    el('scope').textContent = s.scope || 'QQ Agent 接收范围内的全部群聊';
    el('errorBox').hidden = !s.lastError; el('error').textContent = s.lastError || '';
    const recent = Array.isArray(s.recent) ? s.recent : [];
    el('empty').hidden = recent.length > 0;
    el('recent').replaceChildren(...recent.map((r) => {
      const item = node('article', 'item', ''); const head = node('div', 'item-head', '');
      head.append(node('strong', 'badge ' + r.status, labels[r.status] || r.status), node('span', 'muted', new Date(r.at).toLocaleString('zh-CN')), node('span', 'muted', r.groupId ? `群 ${r.groupId}` : ''));
      item.append(head, node('p', 'reason', reasonLabel(r.reason)));
      if (r.typeAssessment?.predicted) {
        const kind = r.typeAssessment.predicted;
        item.append(node('p', '', '画面类型：' + (reasons[kind] || kind) + '（' + Math.round((r.typeAssessment.scores?.[kind] || 0) * 100) + '%）'));
      }
      if (r.characters?.length) item.append(node('p', '', r.characters.join('、')));
      else if (r.characterStatus === 'unidentified') item.append(node('p', '', '归入未识别角色人物目录（_unknown_character）'));
      if (r.error) item.append(node('p', 'reason', r.error));
      if (r.duplicateFile) item.append(node('p', 'file', '与库内图片相似：' + r.duplicateFile));
      if (Object.keys(r.explicitEvidence || {}).length) item.append(node('p', '', '明确内容标签：' + Object.keys(r.explicitEvidence).join('、')));
      for (const f of r.files || []) item.append(node('p', 'file', f));
      return item;
    }));
    el('updated').textContent = '状态更新时间：' + new Date(s.updatedAt).toLocaleString('zh-CN') + '；点击刷新可查看最新结果。';
  } catch (e) { el('status').textContent = e.message; }
  finally { el('refresh').disabled = false; }
}
el('refresh').addEventListener('click', refresh);
refresh();
