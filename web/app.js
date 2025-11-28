async function fetchJSON(url, opts={}) {
  const r = await fetch(url, opts)
  return await r.json()
}

async function loadServers() {
  const data = await fetchJSON('/api/servers')
  const list = document.getElementById('serverList')
  list.innerHTML = ''
  const arr = Array.isArray(data.servers) ? data.servers : []
  arr.forEach(s => {
    const card = document.createElement('div')
    card.className = 'card server-card'
    const head = document.createElement('div')
    head.className = 'row'
    head.innerHTML = `<strong>${s.name}</strong><span class="badge">${s.type||'http'}</span>`
    const actions = document.createElement('div')
    actions.className = 'row'
    const toggle = document.createElement('input')
    toggle.type = 'checkbox'
    toggle.checked = !!s.enabled
    toggle.className = 'toggle'
    toggle.style.marginLeft = 'auto'
    toggle.onchange = async () => {
      await fetchJSON('/api/server/toggle', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:s.name, enabled: toggle.checked})})
    }
    const del = document.createElement('button')
    del.className = 'icon-btn delete-btn'
    del.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>`
    del.onclick = async () => {
      await fetchJSON(`/api/server/${encodeURIComponent(s.name)}`, {method:'DELETE'})
      await loadServers()
      document.getElementById('toolsPanel').style.display = 'none'
    }
    const detail = document.createElement('button')
    detail.className = 'icon-btn settings-btn'
    detail.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"></line><line x1="4" y1="10" x2="4" y2="3"></line><line x1="12" y1="21" x2="12" y2="12"></line><line x1="12" y1="8" x2="12" y2="3"></line><line x1="20" y1="21" x2="20" y2="16"></line><line x1="20" y1="12" x2="20" y2="3"></line><line x1="1" y1="14" x2="7" y2="14"></line><line x1="9" y1="8" x2="15" y2="8"></line><line x1="17" y1="16" x2="23" y2="16"></line></svg>`
    detail.onclick = () => { window.location.href = `/settings.html?name=${encodeURIComponent(s.name)}` }
    head.appendChild(toggle)
    head.appendChild(del)
    head.appendChild(detail)
    card.appendChild(head)
    const desc = document.createElement('div')
    desc.className = 'muted'
    desc.textContent = s.description || ''
    card.appendChild(desc)
    
    card.addEventListener('click', (e) => {
      if (e.target.closest('.icon-btn') || e.target.closest('.toggle')) return
      window.location.href = `/settings.html?name=${encodeURIComponent(s.name)}`
    })
    list.appendChild(card)
  })
}

async function openTools(serverName) {
  const panel = document.getElementById('toolsPanel')
  panel.style.display = 'block'
  const res = await fetchJSON(`/api/server/${encodeURIComponent(serverName)}/tools`)
  const tools = res.tools || []
  panel.innerHTML = `<div class="card"><strong>${serverName}</strong> 的可用工具(${tools.length})</div>`
  tools.forEach(t => {
    const card = document.createElement('div')
    card.className = 'card tool'
    const title = document.createElement('div')
    title.className = 'row'
    title.innerHTML = `<strong>${t.name}</strong> <span class="muted">${t.description||''}</span>`
    const btn = document.createElement('button')
    btn.className = 'btn'
    btn.textContent = '参数'
    btn.onclick = async () => {
      const schema = await fetchJSON(`/api/server/${encodeURIComponent(serverName)}/tool-schema?name=${encodeURIComponent(t.name)}`)
      const props = (schema.inputSchema && schema.inputSchema.properties) || {}
      const req = (schema.inputSchema && schema.inputSchema.required) || []
      const box = document.createElement('div')
      box.className = 'row'
      box.style.flexDirection = 'column'
      box.style.alignItems = 'flex-start'
      box.innerHTML = Object.keys(props).map(k => {
        const typ = (props[k] && props[k].type) || ''
        const desc = (props[k] && props[k].description) || ''
        const star = req.includes(k) ? ' *' : ''
        return `<div><code>${k}</code>${star} <span class='muted'>(${typ})</span> — ${desc}</div>`
      }).join('') || '<div class="muted">无参数</div>'
      card.appendChild(box)
    }
    card.appendChild(title)
    card.appendChild(btn)
    panel.appendChild(card)
  })
}

async function addServer() {
  const name = document.getElementById('newName').value.trim()
  const url = document.getElementById('newUrl').value.trim()
  if (!name || !url) return
  await fetchJSON('/api/server/add', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name, url})})
  document.getElementById('newName').value = ''
  document.getElementById('newUrl').value = ''
  await loadServers()
}

// removed: unified DOMContentLoaded handler below

async function openConfigEditor() {
  const modal = document.getElementById('configModal')
  const errEl = document.getElementById('configError')
  errEl.textContent = ''
  try {
    const res = await fetchJSON('/api/config')
    const text = res.text || ''
    const root = document.getElementById('codeEditor')
    if (root && window.ace) {
      if (!window.aceEditor) {
        window.aceEditor = ace.edit(root)
        window.aceEditor.setTheme('ace/theme/chrome')
        window.aceEditor.session.setMode('ace/mode/json')
        window.aceEditor.session.setUseSoftTabs(true)
        window.aceEditor.session.setTabSize(2)
        window.aceEditor.setOptions({ wrap: true, showPrintMargin: false })
      }
      window.aceEditor.setValue(text, -1)
    }
  } catch (e) {
    errEl.textContent = '加载配置失败'
  }
  modal.style.display = 'flex'
}

function closeConfigEditor() {
  const modal = document.getElementById('configModal')
  modal.style.display = 'none'
}

async function saveConfig() {
  const errEl = document.getElementById('configError')
  errEl.textContent = ''
  let text = ''
  if (window.aceEditor) text = window.aceEditor.getValue()
  try {
    JSON.parse(text)
  } catch (e) {
    errEl.textContent = 'JSON格式错误，请检查后再保存'
    return
  }
  const r = await fetch('/api/config', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text}) })
  try {
    const data = await r.json()
    if (!r.ok || !data.ok) throw new Error('保存失败')
  } catch (e) {
    errEl.textContent = '保存失败'
    return
  }
  closeConfigEditor()
  await loadServers()
}

async function openSettings(serverName) {
  const detail = document.getElementById('detailPanel')
  const toolsPanel = document.getElementById('toolsPanel')
  if (toolsPanel) toolsPanel.style.display = 'none'
  detail.style.display = 'block'
  detail.innerHTML = ''
  const wrap = document.createElement('div')
  wrap.className = 'card'
  const tabs = document.createElement('div')
  tabs.className = 'tabs'
  const tabNames = ['通用','工具','提示','资源']
  const tabElems = tabNames.map((t,i)=>{
    const el = document.createElement('button')
    el.className = 'tab' + (i===0?' active':'')
    el.innerHTML = `${t} <span class="tab-badge"></span>`
    return el
  })
  tabElems.forEach(el=>tabs.appendChild(el))
  const content = document.createElement('div')
  content.style.marginTop = '12px'
  wrap.appendChild(tabs)
  wrap.appendChild(content)
  detail.appendChild(wrap)

  const setActive = (idx) => {
    tabElems.forEach((el,i)=>{ if (i===idx) el.classList.add('active'); else el.classList.remove('active') })
  }
  const setTabCount = (idx, count) => {
    const b = tabElems[idx].querySelector('.tab-badge')
    if (b) b.textContent = count>0 ? `(${count})` : ''
  }

  const renderKV = (obj) => {
    const box = document.createElement('div')
    box.className = 'row'
    box.style.flexDirection = 'column'
    box.style.alignItems = 'stretch'
    Object.keys(obj||{}).forEach(k=>{
      const line = document.createElement('div')
      line.style.display = 'flex'
      line.style.alignItems = 'center'
      line.style.gap = '8px'
      const key = document.createElement('code')
      key.textContent = k
      key.style.minWidth = '140px'
      const val = document.createElement('div')
      val.className = 'muted'
      val.textContent = typeof obj[k] === 'object' ? JSON.stringify(obj[k]) : String(obj[k])
      line.appendChild(key)
      line.appendChild(val)
      box.appendChild(line)
    })
    return box
  }

  const loadGeneral = async () => {
    const res = await fetchJSON(`/api/server/${encodeURIComponent(serverName)}/config`)
    const entry = res.entry || {}
    content.innerHTML = ''
    const form = document.createElement('div')
    form.className = 'card'

    const buildRow = (label, el) => {
      const wrap = document.createElement('div')
      wrap.className = 'form-row'
      const lab = document.createElement('div')
      lab.className = 'muted'
      lab.textContent = label
      wrap.appendChild(lab)
      wrap.appendChild(el)
      return wrap
    }

    const inName = document.createElement('input')
    inName.type = 'text'
    inName.value = res.name || serverName
    const inDesc = document.createElement('textarea')
    inDesc.value = entry.description || ''
    inDesc.style.minHeight = '80px'
    const inType = document.createElement('input')
    inType.type = 'text'
    inType.value = entry.type || 'http'
    inType.disabled = true
    const isStdio = String(entry.type||'').toLowerCase() === 'stdio'
    let inUrl = null
    let inCommand = null
    let inArgs = null
    let inCwd = null
    let inEnv = null
    if (isStdio) {
      inCommand = document.createElement('input')
      inCommand.type = 'text'
      inCommand.value = entry.command || entry.cmd || ''
      inArgs = document.createElement('textarea')
      inArgs.value = JSON.stringify(entry.args || [], null, 2)
      inArgs.style.minHeight = '80px'
      inCwd = document.createElement('input')
      inCwd.type = 'text'
      inCwd.value = entry.cwd || ''
      inEnv = document.createElement('textarea')
      inEnv.value = JSON.stringify(entry.env || {}, null, 2)
      inEnv.style.minHeight = '80px'
    } else {
      inUrl = document.createElement('input')
      inUrl.type = 'text'
      inUrl.value = entry.url || ''
    }

    const msg = document.createElement('div')
    msg.className = 'muted'
    msg.style.marginTop = '6px'

    const actions = document.createElement('div')
    actions.className = 'row'
    const saveBtn = document.createElement('button')
    saveBtn.className = 'btn primary'
    saveBtn.textContent = '保存'
    actions.appendChild(saveBtn)

    form.appendChild(buildRow('名称', inName))
    form.appendChild(buildRow('描述', inDesc))
    form.appendChild(buildRow('类型', inType))
    if (isStdio) {
      form.appendChild(buildRow('command', inCommand))
      form.appendChild(buildRow('args (JSON 数组)', inArgs))
      form.appendChild(buildRow('cwd', inCwd))
      form.appendChild(buildRow('env (JSON 对象)', inEnv))
    } else {
      form.appendChild(buildRow('URL', inUrl))
    }
    form.appendChild(actions)
    form.appendChild(msg)
    content.appendChild(form)

    saveBtn.onclick = async () => {
      msg.textContent = ''
      const newName = (inName.value || '').trim()
      const newDesc = inDesc.value || ''
      const patchEntry = { description: newDesc }
      if (isStdio) {
        const cmd = (inCommand.value || '').trim()
        let argsArr = []
        let envObj = {}
        const cwdVal = (inCwd.value || '').trim()
        try { argsArr = JSON.parse(inArgs.value || '[]') } catch (_) { argsArr = [] }
        try { envObj = JSON.parse(inEnv.value || '{}') } catch (_) { envObj = {} }
        if (!newName || !cmd) {
          msg.textContent = '名称与command不能为空'
          msg.style.color = '#dc2626'
          return
        }
        patchEntry.command = cmd
        patchEntry.args = Array.isArray(argsArr) ? argsArr : []
        if (cwdVal) patchEntry.cwd = cwdVal
        patchEntry.env = envObj
      } else {
        const newUrl = (inUrl.value || '').trim()
        if (!newName || !newUrl) {
          msg.textContent = '名称与URL不能为空'
          msg.style.color = '#dc2626'
          return
        }
        patchEntry.url = newUrl
      }
      // save config
      const r = await fetch(`/api/server/${encodeURIComponent(serverName)}/config`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ name: newName, entry: patchEntry }) })
      let savedName = serverName
      try {
        const data = await r.json()
        if (!r.ok || !data.ok) throw new Error('保存失败')
        savedName = data.name || newName
        showToast('保存成功')
        msg.textContent = '保存成功，正在连接...'
        msg.style.color = '#10b981'
      } catch (e) {
        msg.textContent = '保存失败'
        msg.style.color = '#dc2626'
        return
      }
      // validate connection
      const v = await fetch(`/api/server/${encodeURIComponent(savedName)}/validate`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({}) })
      try {
        const data = await v.json()
        if (!v.ok || !data.ok || !(typeof data.tools_count === 'number' && data.tools_count > 0)) {
          throw new Error(data.error || '未能列出工具')
        }
        msg.textContent = '已保存并成功连接'
        msg.style.color = '#16a34a'
        showToast('连接成功')
        // if name changed, refresh page to new name
        if (savedName !== serverName) {
          const url = new URL(window.location.href)
          url.searchParams.set('name', savedName)
          window.history.replaceState({}, '', url.toString())
          openSettings(savedName)
        } else {
          await loadGeneral()
        }
      } catch (e) {
        msg.textContent = `连接失败：${e.message || e}`
        msg.style.color = '#dc2626'
        showToast('连接失败', 'error')
      }
    }
  }

  const loadTools = async () => {
    content.innerHTML = `<div class="loading"><span class="spinner"></span> 正在加载工具...</div>`
    let res
    try {
      res = await fetchJSON(`/api/server/${encodeURIComponent(serverName)}/tools`)
    } catch (e) {
      content.innerHTML = `<div class="muted">加载失败：${escapeHTML(e.message||String(e))}</div>`
      return
    }
    if (res && res.error === 'Server disabled') {
      setTabCount(1, 0)
      content.innerHTML = `<div class="muted">该服务器未启用，请先启用！</div>`
      return
    }
    const tools = res.tools || []
    setTabCount(1, tools.length)
    content.innerHTML = ''
    const headerRow = document.createElement('div')
    headerRow.className = 'tools-header'
    const hLeft = document.createElement('div')
    hLeft.textContent = ''
    const hRight = document.createElement('div')
    hRight.textContent = '工具启用'
    hRight.style.fontSize = '16px'
    hRight.style.fontWeight = '700'
    hRight.style.textAlign = 'center'
    hRight.style.whiteSpace = 'nowrap'
    hRight.className = 'tools-header-right'
    headerRow.appendChild(hLeft)
    headerRow.appendChild(hRight)
    content.appendChild(headerRow)
    const list = document.createElement('div')
    list.className = 'tools-list'
    if (tools.length === 0) {
      const tip = document.createElement('div')
      tip.className = 'muted'
      tip.textContent = '暂无可用工具。'
      content.appendChild(tip)
      return
    }
    tools.forEach(t=>{
      const card = document.createElement('div')
      card.className = 'card tool collapsed'

      const header = document.createElement('div')
      header.className = 'tool-header'

      const expand = document.createElement('button')
      expand.className = 'icon-btn tool-expand'
      expand.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>`

      const nameWrap = document.createElement('div')
      nameWrap.style.display = 'flex'
      nameWrap.style.flexDirection = 'column'
      nameWrap.style.flex = '1'

      const nameEl = document.createElement('div')
      nameEl.innerHTML = `<strong>${t.name}</strong>`
      const descEl = document.createElement('div')
      descEl.className = 'tool-desc'
      descEl.textContent = t.description || ''
      nameWrap.appendChild(nameEl)
      nameWrap.appendChild(descEl)

      const leftCol = document.createElement('div')
      leftCol.className = 'tool-left'
      leftCol.appendChild(expand)
      leftCol.appendChild(nameWrap)
      header.appendChild(leftCol)

      const toggle = document.createElement('input')
      toggle.type = 'checkbox'
      toggle.className = 'toggle'
      toggle.checked = (t.enabled !== false)
      toggle.title = '启用工具'
      toggle.addEventListener('click', (e)=>{ e.stopPropagation() })
      toggle.onchange = async () => {
        try {
          await fetch(`/api/server/${encodeURIComponent(serverName)}/tools/toggle`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ tool: t.name, enabled: toggle.checked }) })
          showToast(toggle.checked ? '已启用工具' : '已禁用工具')
        } catch (e) {
          showToast('操作失败', 'error')
          toggle.checked = !toggle.checked
        }
      }
      header.appendChild(toggle)


      expand.onclick = async () => {
        const open = card.classList.contains('expanded')
        if (open) {
          expand.classList.remove('open')
          card.classList.remove('expanded')
          card.classList.add('collapsed')
          const oldParams = card.querySelector('.param-list')
          if (oldParams) oldParams.remove()
          const oldNote = card.querySelector('.note-box')
          if (oldNote) oldNote.remove()
          return
        }
        expand.classList.add('open')
        card.classList.remove('collapsed')
        card.classList.add('expanded')
        let details = card.querySelector('.param-list')
        if (!details) {
          details = document.createElement('div')
          details.className = 'param-list'
          const schema = t || {}
          const props = (schema.inputSchema && schema.inputSchema.properties) || {}
          const req = (schema.inputSchema && schema.inputSchema.required) || []
          const rows = Object.keys(props).map(k => {
            const typ = (props[k] && props[k].type) || ''
            const desc = (props[k] && props[k].description) || ''
            const star = req.includes(k) ? `<span class="req-star" title="必填参数">*</span>` : ''
            return `<tr><td class="name-cell"><strong>${k}</strong>${star}</td><td class="type-cell">${typ}</td><td class="desc-cell">${desc}</td></tr>`
          }).join('')
          details.innerHTML = rows ? `<table class="param-table"><tbody>${rows}</tbody></table>` : '<div class="muted">无参数</div>'
          card.appendChild(details)
        }
        let noteBox = card.querySelector('.note-box')
        if (!noteBox) {
          noteBox = document.createElement('div')
          noteBox.className = 'note-box'
          const noteLabel = document.createElement('div')
          noteLabel.className = 'note-label'
          noteLabel.textContent = '使用提示'
          const noteArea = document.createElement('textarea')
          noteArea.className = 'note-textarea'
          noteArea.placeholder = '添加使用提示...'
          noteArea.value = (t.note || '')
          noteArea.addEventListener('click', (e)=>{ e.stopPropagation() })
          const noteActions = document.createElement('div')
          noteActions.className = 'note-actions'
          const noteSave = document.createElement('button')
          noteSave.className = 'btn'
          noteSave.textContent = '保存'
          noteSave.onclick = async () => {
            try { /* 阻止点击导致折叠 */ } finally { /* no-op */ }
            try {
              const r = await fetch(`/api/server/${encodeURIComponent(serverName)}/tools/note`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ tool: t.name, note: noteArea.value }) })
              const data = await r.json()
              if (!r.ok || !data.ok) throw new Error('保存失败')
              showToast('已保存')
              t.note = noteArea.value
            } catch (e) {
              showToast('保存失败', 'error')
            }
          }
          noteActions.appendChild(noteSave)
          noteBox.appendChild(noteLabel)
          noteBox.appendChild(noteArea)
          noteBox.appendChild(noteActions)
          card.appendChild(noteBox)
        }
      }

      card.appendChild(header)
      list.appendChild(card)
    })
    content.appendChild(list)
  }

  const loadPrompts = async () => {
    const res = await fetchJSON(`/api/server/${encodeURIComponent(serverName)}/prompts`)
    const arr = res.prompts || []
    setTabCount(2, arr.length)
    content.innerHTML = `<div class="muted">共 ${arr.length} 个提示</div>`
    arr.forEach(p=>{
      const card = document.createElement('div')
      card.className = 'card tool'
      const title = document.createElement('div')
      title.className = 'row'
      title.innerHTML = `<strong>${p.name||p.id||''}</strong> <span class="muted">${p.description||''}</span>`
      content.appendChild(card)
      card.appendChild(title)
    })
  }

  const loadResources = async () => {
    const res = await fetchJSON(`/api/server/${encodeURIComponent(serverName)}/resources`)
    const arr = res.resources || []
    setTabCount(3, arr.length)
    content.innerHTML = `<div class="muted">共 ${arr.length} 个资源</div>`
    arr.forEach(r=>{
      const card = document.createElement('div')
      card.className = 'card tool'
      const title = document.createElement('div')
      title.className = 'row'
      const uri = r.uri || (r.url||'')
      title.innerHTML = `<strong>${r.name||r.id||''}</strong> <span class="muted">${r.description||''}</span>`
      const meta = document.createElement('div')
      meta.className = 'muted'
      meta.textContent = uri ? `URI: ${uri}` : ''
      card.appendChild(title)
      card.appendChild(meta)
      content.appendChild(card)
    })
  }

  await loadGeneral()
  tabElems[0].onclick = async ()=>{ setActive(0); await loadGeneral() }
  tabElems[1].onclick = async ()=>{ setActive(1); await loadTools() }
  tabElems[2].onclick = async ()=>{ setActive(2); await loadPrompts() }
  tabElems[3].onclick = async ()=>{ setActive(3); await loadResources() }
}

function escapeHTML(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
}

function normalizeJSON(text) {
  try { return JSON.stringify(JSON.parse(text), null, 2) } catch (_) { return text }
}

// highlightJSON removed

function formatJSON() {
  const editor = document.getElementById('configEditor')
  editor.value = normalizeJSON(editor.value || '')
}

window.addEventListener('DOMContentLoaded', () => {
  const p = new URLSearchParams(window.location.search)
  const name = p.get('name')
  if (window.location.pathname === '/settings.html' && name) {
    openSettings(name)
  } else {
    loadServers()
  }
})

function showToast(text, type='success') {
  let el = document.getElementById('toast')
  if (!el) {
    el = document.createElement('div')
    el.id = 'toast'
    el.className = 'toast'
    document.body.appendChild(el)
  }
  el.textContent = text
  el.className = type === 'error' ? 'toast error show' : 'toast show'
  setTimeout(() => { el.className = el.className.replace(' show', '') }, 1800)
}

function openAddServerModal() {
  const modal = document.getElementById('addModal')
  document.getElementById('addName').value = ''
  document.getElementById('addDesc').value = ''
  const typeEl = document.getElementById('addType')
  typeEl.value = 'stdio'
  renderAddTypeFields()
  const msg = document.getElementById('addMsg')
  msg.textContent = ''
  modal.style.display = 'flex'
}

function closeAddServerModal() {
  const modal = document.getElementById('addModal')
  modal.style.display = 'none'
}

function renderAddTypeFields() {
  const type = document.getElementById('addType').value
  const box = document.getElementById('addFields')
  box.innerHTML = ''
  if (type === 'stdio') {
    const command = document.createElement('input')
    command.type = 'text'
    command.id = 'addCommand'
    const args = document.createElement('textarea')
    args.id = 'addArgs'
    args.style.minHeight = '80px'
    args.value = '[]'
    const env = document.createElement('textarea')
    env.id = 'addEnv'
    env.style.minHeight = '80px'
    env.value = '{}'
    const buildRow = (label, el) => {
      const wrap = document.createElement('div')
      wrap.className = 'form-row'
      const lab = document.createElement('div')
      lab.className = 'muted'
      lab.textContent = label
      wrap.appendChild(lab)
      wrap.appendChild(el)
      return wrap
    }
    box.appendChild(buildRow('命令（必填）', command))
    box.appendChild(buildRow('参数（JSON 数组）', args))
    box.appendChild(buildRow('环境变量（JSON 对象）', env))
  } else {
    const url = document.createElement('input')
    url.type = 'text'
    url.id = 'addUrl'
    const headers = document.createElement('textarea')
    headers.id = 'addHeaders'
    headers.style.minHeight = '80px'
    headers.value = '{}'
    const buildRow = (label, el) => {
      const wrap = document.createElement('div')
      wrap.className = 'form-row'
      const lab = document.createElement('div')
      lab.className = 'muted'
      lab.textContent = label
      wrap.appendChild(lab)
      wrap.appendChild(el)
      return wrap
    }
    box.appendChild(buildRow('URL（必填）', url))
    box.appendChild(buildRow('请求头（JSON 对象）', headers))
  }
}

async function connectAddServer() {
  const name = (document.getElementById('addName').value || '').trim()
  const desc = document.getElementById('addDesc').value || ''
  const type = document.getElementById('addType').value
  const msg = document.getElementById('addMsg')
  msg.textContent = ''
  if (!name) {
    msg.textContent = '名称必填'
    msg.style.color = '#dc2626'
    return
  }
  try {
    if (type === 'stdio') {
      const cmd = (document.getElementById('addCommand').value || '').trim()
      let argsArr = []
      let envObj = {}
      try { argsArr = JSON.parse(document.getElementById('addArgs').value || '[]') } catch (_) { argsArr = [] }
      try { envObj = JSON.parse(document.getElementById('addEnv').value || '{}') } catch (_) { envObj = {} }
      if (!cmd) {
        msg.textContent = '命令必填'
        msg.style.color = '#dc2626'
        return
      }
      const entry = { type: 'stdio', command: cmd, args: Array.isArray(argsArr) ? argsArr : [], env: envObj, description: desc, enabled: true }
      const r = await fetch(`/api/server/${encodeURIComponent(name)}/config`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ name, entry }) })
      const d = await r.json()
      if (!r.ok || !d.ok) throw new Error('保存失败')
    } else {
      const url = (document.getElementById('addUrl').value || '').trim()
      let headersObj = {}
      try { headersObj = JSON.parse(document.getElementById('addHeaders').value || '{}') } catch (_) { headersObj = {} }
      if (!url) {
        msg.textContent = 'URL必填'
        msg.style.color = '#dc2626'
        return
      }
      const a = await fetchJSON('/api/server/add', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ name, url }) })
      if (!a || !a.ok) throw new Error('添加失败')
      const patch = { description: desc, headers: headersObj }
      await fetch(`/api/server/${encodeURIComponent(name)}/config`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ name, entry: patch }) })
    }
    const v = await fetch(`/api/server/${encodeURIComponent(name)}/validate`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({}) })
    const vd = await v.json()
    if (!v.ok || !vd.ok || !(typeof vd.tools_count === 'number' && vd.tools_count > 0)) {
      await fetchJSON(`/api/server/${encodeURIComponent(name)}`, { method:'DELETE' })
      throw new Error(vd.error || '连接失败')
    }
    msg.textContent = '连接成功，已写入配置'
    msg.style.color = '#16a34a'
    showToast('连接成功')
    closeAddServerModal()
    await loadServers()
  } catch (e) {
    msg.textContent = `失败：${e.message || e}`
    msg.style.color = '#dc2626'
    showToast('操作失败', 'error')
  }
}
