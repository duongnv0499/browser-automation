/* Runs synchronously once per frame. No page-supplied selectors or scripts. */
(options) => {
  const key = '__browserAutomationSnapshot_v1';
  let state = globalThis[key];
  if (!state || state.document !== document) {
    state = {document, token: Array.from(crypto.getRandomValues(new Uint8Array(16)),n=>n.toString(16).padStart(2,'0')).join(''), nodes: new Map(), ids: new WeakMap(), next: 1};
    Object.defineProperty(globalThis, key, {value: state, configurable: true});
    state.secretValues = new WeakMap();
  }
  const sensitive = el => el.matches('input[type=password],input[autocomplete*=password],input[autocomplete^="cc-"],input[autocomplete="one-time-code"]');
  const viewportClip = options.clip || {left:0,top:0,right:innerWidth,bottom:innerHeight};
  const maxTextChars = options.max_text_chars ?? 1000000;
  const maxElements = options.max_elements ?? 2000;
  const maxNodes = options.max_nodes ?? 20000;
  const visible = el => {
    const style = getComputedStyle(el), rect = el.getBoundingClientRect();
    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0 || rect.width <= 0 || rect.height <= 0) return false;
    let left=Math.max(viewportClip.left,rect.left), top=Math.max(viewportClip.top,rect.top), right=Math.min(viewportClip.right,rect.right), bottom=Math.min(viewportClip.bottom,rect.bottom);
    for(let parent=el.parentElement||el.getRootNode().host;parent;parent=parent.parentElement||parent.getRootNode().host){
      const s=getComputedStyle(parent),r=parent.getBoundingClientRect();
      if(s.display==='none'||Number(s.opacity)===0) return false;
      if(/hidden|clip|auto|scroll/.test(s.overflowX)){left=Math.max(left,r.left+parent.clientLeft);right=Math.min(right,r.left+parent.clientLeft+parent.clientWidth);}
      if(/hidden|clip|auto|scroll/.test(s.overflowY)){top=Math.max(top,r.top+parent.clientTop);bottom=Math.min(bottom,r.top+parent.clientTop+parent.clientHeight);}
    }
    return right>left&&bottom>top;
  };
  const label = el => {
    const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => (el.getRootNode().getElementById?.(id)||document.getElementById(id))?.textContent || '').join(' ').trim();
    return (el.getAttribute('aria-label') || labelled || Array.from(el.labels || []).map(x => x.textContent).join(' ') || el.getAttribute('alt') || el.getAttribute('title') || el.getAttribute('placeholder') || el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 300);
  };
  const fieldValue = el => {
    const value=String(el.value||'');
    if(!sensitive(el)) return value;
    const old=state.secretValues.get(el);
    const current=old&&old.value===value?old:{value,version:(old?.version||0)+1};
    state.secretValues.set(el,current);
    return '[REDACTED:'+current.version+']';
  };
  state.fingerprint = el => JSON.stringify([el.tagName,el.type||el.getAttribute('type'),el.getAttribute('role'),label(el),fieldValue(el),el.href||'',el.form?.action||'',el.form?.method||'',el.getAttribute('formaction'),el.getAttribute('formmethod'),el.disabled,el.readOnly,el.multiple]);
  const elements = [], text = [], live = new Map(), fields = [];
  let visitedNodes = 0, textChars = 0, sourceTruncated = false, omittedElements = 0;
  const walk = root => {
    if (visitedNodes >= maxNodes) { sourceTruncated = true; return; }
    for (const el of root.children || []) {
      if (++visitedNodes > maxNodes) { sourceTruncated = true; break; }
      if (['SCRIPT','STYLE','NOSCRIPT','TEMPLATE'].includes(el.tagName)) continue;
      if(el.matches('input,select,textarea,[contenteditable="true"]')) fields.push(state.fingerprint(el));
      if (visible(el)) {
        const tag = el.tagName.toLowerCase(), type = el.getAttribute('type') || '', role = el.getAttribute('role') || ({a:'link',button:'button',select:'combobox',textarea:'textbox',input:type==='checkbox'?'checkbox':type==='radio'?'radio':'textbox',canvas:'canvas'}[tag] || tag);
        const ops = [];
        if (!el.disabled && !el.closest('[inert]')) {
          if (['a','button','input','select','textarea','summary'].includes(tag) || el.hasAttribute('onclick') || el.tabIndex >= 0 || ['button','link','checkbox','radio','menuitem','tab','switch','option'].includes(role)) ops.push('click','hover','press');
          if (tag === 'textarea' || (tag === 'input' && !['button','submit','reset','checkbox','radio','file','hidden','range','color','image'].includes(type)) || el.isContentEditable) {if (!el.readOnly) ops.push('fill');}
          if (tag === 'select') ops.push('select');
          if (tag === 'input' && type === 'file') ops.push('upload');
          if (el.draggable) ops.push('drag');
        }
        if (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1) ops.push('scroll');
        if (tag === 'canvas') ops.push('click','hover','drag');
        if (ops.length) {
          let id = state.ids.get(el); if (!id) {id = String(state.next++); state.ids.set(el,id);}
          live.set(id,el);
          const r = el.getBoundingClientRect();
          if (elements.length < maxElements) {
            const isSub = !!el.form && (['submit','image'].includes(type) || (tag === 'button' && (el.type === 'submit' || !el.getAttribute('type'))));
            elements.push({id,signature:state.fingerprint(el),role,name:label(el),value:sensitive(el)?'[REDACTED]':String(el.value || '').slice(0,1000),operations:[...new Set(ops)],bounds:{x:r.x,y:r.y,width:r.width,height:r.height},sensitive:sensitive(el),input_type:tag==='button'?(el.type||'submit'):(el.type||type),is_submit:isSub,tag,href:el.href||null,form_action:el.hasAttribute('formaction')?el.formAction:el.form?.action||null,form_method:el.hasAttribute('formmethod')?el.formMethod:el.form?.method||null,multiple:tag==='select'?!!el.multiple:(tag==='input'&&type==='file')?!!el.multiple:false,options:tag==='select'?Array.from(el.options).map(o=>({value:o.value,label:o.label,selected:!!o.selected,disabled:o.disabled||(o.parentElement.tagName==='OPTGROUP'&&o.parentElement.disabled)})):[],selected_values:tag==='select'?Array.from(el.selectedOptions||[]).map(o=>o.value):undefined});
          } else {
            omittedElements++;
            sourceTruncated = true;
          }
        }
        for (const node of el.childNodes) if (node.nodeType === Node.TEXT_NODE && node.textContent.trim()) {
          const range=document.createRange();range.selectNodeContents(node);const r=range.getBoundingClientRect();
          if(r.width>0&&r.height>0&&r.right>viewportClip.left&&r.bottom>viewportClip.top&&r.left<viewportClip.right&&r.top<viewportClip.bottom) {
            const str = node.textContent.trim();
            if (textChars + str.length <= maxTextChars) {
              text.push(str);
              textChars += str.length;
            } else if (textChars < maxTextChars) {
              text.push(str.slice(0, maxTextChars - textChars));
              textChars = maxTextChars;
              sourceTruncated = true;
            } else {
              sourceTruncated = true;
            }
          }
        }
      }
      if (el.shadowRoot) {
        walk(el.shadowRoot);
      }
      walk(el);
    }
  };
  walk(document);
  state.nodes = live;
  return {document:state.token,elements,omitted_elements:omittedElements,fields,text:text.join('\n'),source_truncated:sourceTruncated,viewport:{width:innerWidth,height:innerHeight},url:location.href,title:document.title};
}
