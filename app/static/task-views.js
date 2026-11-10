(() => {
  'use strict';
  const activeStatuses = new Set(['queued', 'discovering', 'capturing', 'downloading', 'postprocessing', 'cancelling']);
  const canonicalPlatform = value => value === 'xhs' ? 'xiaohongshu' : value === 'bili' ? 'bilibili' : value;
  const isActive = job => activeStatuses.has(job.status);
  const needsAction = job => job.status !== 'completed' && job.status !== 'paused' && !isActive(job);
  const items = job => {
    const selected = Array.isArray(job.selected_item_ids) ? new Set(job.selected_item_ids) : null;
    return (job.items || []).filter(item => !selected || selected.has(item.id));
  };
  const mediaType = job => {
    const types = new Set(items(job).map(item => item.media_type).filter(Boolean));
    if (!types.size) return job.wechat || canonicalPlatform(job.platform) === 'wechat' ? 'video' : 'other';
    if ([...types].every(type => ['image', 'live_photo'].includes(type))) return 'image';
    if ([...types].every(type => type === 'audio')) return 'audio';
    if (types.has('video')) return 'video';
    return 'other';
  };
  const jobTitle = job => job.title || items(job).find(item => item.title && item.title !== 'Untitled')?.title || '';
  const completionTime = job => job.finished_at || job.completed_at || job.updated_at || job.created_at || '';
  function files(job) {
    if (job.wechat) return (job.output_files || (job.path ? [{index: 0, path: job.path, exists: null, can_open: false}] : [])).map(file => ({...file, item_id: null}));
    return items(job).flatMap(item => (item.metadata?.output_files || (item.output_paths || []).map((path, index) => ({index, path, exists: null, can_open: false}))).map(file => ({...file, item_id: item.id})));
  }
  function fileAvailability(job) {
    const output = files(job);
    const missing = output.filter(file => file.exists === false).length;
    return missing ? missing === output.length ? 'missing' : 'partial' : output.length && output.every(file => file.exists === true) ? 'available' : 'unknown';
  }
  function counts(entries) {
    const jobs = entries.map(([, job]) => job);
    return {
      unfinished: jobs.filter(job => job.status !== 'completed').length,
      active: jobs.filter(isActive).length,
      paused: jobs.filter(job => job.status === 'paused').length,
      failed: jobs.filter(needsAction).length,
      completed: jobs.filter(job => job.status === 'completed').length
    };
  }
  function select(entries, options = {}) {
    const { view = 'home', filter = 'all', type = 'all', platform = 'all', sort = 'newest', query = '', platformNames = {} } = options;
    const needle = query.trim().toLocaleLowerCase();
    return entries.filter(([, job]) => {
      if (view === 'tasks') {
        if (job.status === 'completed') return false;
        if (filter === 'active' && !isActive(job)) return false;
        if (filter === 'paused' && job.status !== 'paused') return false;
        if (filter === 'failed' && !needsAction(job)) return false;
      }
      if (view === 'completed') {
        if (job.status !== 'completed') return false;
        if (type !== 'all' && mediaType(job) !== type) return false;
        if (platform === 'other' ? Object.hasOwn(platformNames, canonicalPlatform(job.platform)) : platform !== 'all' && canonicalPlatform(job.platform) !== platform) return false;
      }
      return !needle || `${jobTitle(job)} ${job.author || ''} ${platformNames[canonicalPlatform(job.platform)] || ''}`.toLocaleLowerCase().includes(needle);
    }).sort((a, b) => {
      if (view === 'completed' && sort === 'title') return jobTitle(a[1]).localeCompare(jobTitle(b[1]), 'zh-CN');
      const dateA = view === 'completed' ? completionTime(a[1]) : a[1].created_at || '';
      const dateB = view === 'completed' ? completionTime(b[1]) : b[1].created_at || '';
      const order = (Date.parse(dateB) || 0) - (Date.parse(dateA) || 0) || String(a[0]).localeCompare(String(b[0]));
      return view === 'completed' && sort === 'oldest' ? -order : order;
    });
  }
  const views = { canonicalPlatform, isActive, needsAction, items, mediaType, completionTime, files, fileAvailability, counts, select };
  if (typeof module !== 'undefined' && module.exports) module.exports = views;
  else window.ShiyingTaskViews = views;
})();
