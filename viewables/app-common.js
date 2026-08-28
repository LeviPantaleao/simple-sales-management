/* app-common.js — shared desktop-bridge, popup and money helpers.
 * Loaded once by base.html on every page. Single source of truth for the
 * functions that used to be copy-pasted across the old per-page templates
 * (clients / sales / sale form / search bar), now all merged into
 * management.html.
 */
(function(){
  'use strict';

  function __desktopApi(){
    try{
      return (window.pywebview && window.pywebview.api)
          || (window.ssm && window.ssm.api)
          || (window.electronAPI && window.electronAPI.api)
          || null;
    }catch(_){ return null; }
  }

  function __download(url, filename){
    const a=document.createElement('a');
    a.href=url;
    if(filename) a.download=filename;
    a.rel='noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function __saveFromUrl(url, filename){
    const api = __desktopApi();
    const suggested = filename || 'export.csv';
    const ext = (suggested.split('.').pop() || '').toLowerCase();

    // Electron (ssm/electronAPI bridge): "Salvar como..." + download
    if(api && typeof api.save_as_from_url === 'function'){
      const rr = await api.save_as_from_url(url, suggested);
      if(rr === true) return true;
      if(rr && rr.ok){
        if(ext === 'csv' && typeof api.open_file === 'function' && rr.path){
          try{ await api.open_file(rr.path); }catch(_){}
        }
        return true;
      }
      return false;
    }

    // PyWebView legado
    if(api && typeof api.choose_save_path === 'function' && typeof api.download_pdf_to === 'function'){
      const p = await api.choose_save_path(suggested);
      if(!p) return false;
      const rr = await api.download_pdf_to(url, p);
      if(ext === 'csv' && typeof api.open_file === 'function'){
        try{ await api.open_file(p); }catch(_){}
      }
      return !!rr || true;
    }

    // Browser fallback
    __download(url, suggested);
    return true;
  }

  function getContentCenterX(){
    const app = document.getElementById('appScroller') || document.querySelector('.app');
    if(!app) return Math.round(window.innerWidth/2);
    const container = app.querySelector('section.container') || app.querySelector('.container') || app;
    const r = container.getBoundingClientRect();
    if(!r || !isFinite(r.left) || !isFinite(r.width) || r.width < 10){
      const ar = app.getBoundingClientRect();
      return Math.round(ar.left + ar.width/2);
    }
    return Math.round(r.left + r.width/2);
  }

  // Campos opcionais (descrição/contato/endereço) deixados em branco no cadastro.
  // window.__VAGO_TEXT é definido por base.html (string traduzida "(Blank)").
  function orVago(s){
    s = (s || '').trim();
    return s || (window.__VAGO_TEXT || '(Blank)');
  }

  // Contador de itens selecionados (hints/painel de seleção): trava em "+999"
  // acima disso, em vez de deixar o número crescer e estourar a largura.
  function formatSelCount(n){
    n = Number(n) || 0;
    return n > 999 ? '+999' : String(n);
  }

  function toNumber(v){
    const s = String(v ?? '').replace(/[^\d.,-]/g,'');
    const n = Number(s.replace(/\.(?=\d{3,})/g,'').replace(',','.'));
    return isFinite(n)?n:0;
  }
  function toMoneyRaw(v){
    const n = Number(v);
    if (!isFinite(n)) return '0.00';
    // segue o idioma renderizado pelo servidor (<html lang>) em vez do
    // locale do Chromium — assim trocar de idioma não exige recriar a janela.
    let loc;
    try{ loc = document.documentElement.getAttribute('lang') || undefined; }catch(e){ loc = undefined; }
    return new Intl.NumberFormat(loc, {
      style:'decimal',
      minimumFractionDigits:2,
      maximumFractionDigits:2
    }).format(n);
  }
  function toMoneyDisplay(v){ return toMoneyRaw(v); }

  /* ===================== Máscara de dinheiro "estilo caixa eletrônico" =====================
   * Os dígitos digitados entram sempre pela casa dos centavos (a mais à direita),
   * empurrando o que já foi digitado para a esquerda -- ex.: digitar 2,0,0,9,1 mostra
   * 0,02 -> 0,20 -> 2,00 -> 20,09 -> 200,91. É o mesmo padrão usado por apps bancários
   * e caixas eletrônicos (ATM-style currency input). O valor "de verdade" fica guardado
   * em centavos inteiros em data-cents; o texto exibido é só formatação.
   */
  const MONEY_MASK_MAX_CENTS = 999999999999; // teto generoso (até 9.999.999.999,99)

  function moneyMaskCents(el){ return Number(el && el.dataset ? el.dataset.cents : 0) || 0; }
  function moneyMaskValue(el){ return moneyMaskCents(el) / 100; }

  function renderMoneyMask(el, cents){
    cents = Math.max(0, Math.min(MONEY_MASK_MAX_CENTS, Math.round(cents) || 0));
    el.dataset.cents = String(cents);
    el.value = cents > 0 ? toMoneyRaw(cents / 100) : '';
    try{ el.setSelectionRange(el.value.length, el.value.length); }catch(_){}
  }

  function setMoneyMaskValue(el, numberValue){
    if(!el) return;
    renderMoneyMask(el, Math.round((Math.max(0, Number(numberValue) || 0)) * 100));
  }

  function enforceMoneyMask(el){
    if(!el) return;
    el.setAttribute('inputmode','numeric');
    renderMoneyMask(el, moneyMaskCents(el));

    function flashInvalidKeepValue(){
      el.setAttribute('aria-invalid','true');
      el.classList.remove('invalid');
      try{ void el.offsetWidth; }catch(_){}
      el.classList.add('invalid');
      if(el.__invTimer) clearTimeout(el.__invTimer);
      el.__invTimer = setTimeout(()=>{
        try{ el.classList.remove('invalid'); el.removeAttribute('aria-invalid'); }catch(_){}
        el.__invTimer = null;
      }, 420);
    }

    function pushDigits(digitsStr){
      let cur = moneyMaskCents(el);
      for(const ch of digitsStr) cur = Math.min(MONEY_MASK_MAX_CENTS, cur*10 + Number(ch));
      renderMoneyMask(el, cur);
      el.dispatchEvent(new Event('input', { bubbles:true }));
    }
    function popDigit(){
      renderMoneyMask(el, Math.trunc(moneyMaskCents(el) / 10));
      el.dispatchEvent(new Event('input', { bubbles:true }));
    }

    el.addEventListener('beforeinput', (e)=>{
      if(el.hasAttribute('readonly')) return;
      const t = e.inputType || '';

      if(t.startsWith('delete')){
        e.preventDefault();
        popDigit();
        return;
      }

      const data = e.data;
      if(data == null){ e.preventDefault(); return; }

      const digits = data.replace(/[^\d]/g,'');
      e.preventDefault();
      if(!digits){ flashInvalidKeepValue(); return; }
      pushDigits(digits);
    });

    el.addEventListener('keydown', (e)=>{
      if(e.ctrlKey || e.metaKey || e.altKey) return;
      if(el.hasAttribute('readonly')) return;

      const k = e.key;
      if(k === 'Backspace' || k === 'Delete'){
        e.preventDefault();
        popDigit();
        return;
      }
      if(k === 'Tab' || k === 'Enter' || k.startsWith('Arrow') || k === 'Home' || k === 'End') return;
      if(k.length === 1 && !/\d/.test(k)){
        e.preventDefault();
        flashInvalidKeepValue();
      }
    });

    el.addEventListener('paste', (e)=>{
      if(el.hasAttribute('readonly')) return;
      e.preventDefault();
      const txt = (e.clipboardData || window.clipboardData)?.getData('text') || '';
      const digits = txt.replace(/[^\d]/g,'');
      if(!digits){ flashInvalidKeepValue(); return; }
      pushDigits(digits);
    });

    // clique/foco: mantém o cursor sempre no fim (é uma máscara, não um texto editável no meio)
    el.addEventListener('click', ()=>{ try{ el.setSelectionRange(el.value.length, el.value.length); }catch(_){} });
    el.addEventListener('focus', ()=>{ try{ el.setSelectionRange(el.value.length, el.value.length); }catch(_){} });
  }

  // Feedback claro de campo inválido (contorno vermelho + chacoalhada via .invalid,
  // definido em base.html). Usado pelo formulário de Nova Venda e pela
  // ferramenta de desconto do Editar de Venda em management.html.
  function flashInvalid(el){
    if(!el) return;
    el.setAttribute('aria-invalid','true');
    el.classList.remove('invalid');
    try{ void el.offsetWidth; }catch(_){}
    el.classList.add('invalid');
    if(el.__invTimer) clearTimeout(el.__invTimer);
    el.__invTimer = setTimeout(()=>{
      try{
        el.classList.remove('invalid');
        el.removeAttribute('aria-invalid');
      }catch(_){}
      el.__invTimer = null;
    }, 420);
  }

  // "Slime" — animação leve de entrada/saída (scale+opacity) para botões que
  // trocam de estado (ex.: Discount <-> Alterar/Remover). Usada pelo formulário
  // de Nova Venda e pela ferramenta de desconto do Editar de Venda em management.html.
  function animateInSlime(el, opts){
    opts = opts || {duration:300};
    if(!el) return;
    if(isReduceMotion()){ el.hidden = false; return; }
    try{ if(el.__ani) el.__ani.cancel(); }catch(e){}
    el.hidden = false;
    el.style.willChange = 'transform,opacity';
    el.__ani = el.animate([
      { transform: 'scale(0.96)', opacity: 0 },
      { transform: 'scale(1.03)', opacity: 1 },
      { transform: 'scale(1)', opacity: 1 }
    ], { duration: opts.duration, easing: 'cubic-bezier(.20,.70,.20,1)', fill: 'forwards' });
    el.__ani.onfinish = ()=>{ el.style.willChange=''; el.__ani = null; };
  }
  function animateOutSlime(el, opts){
    opts = opts || {duration:260};
    if(!el) return;
    if(isReduceMotion()){ el.hidden = true; return; }
    try{ if(el.__ani) el.__ani.cancel(); }catch(e){}
    el.style.willChange = 'transform,opacity';
    el.__ani = el.animate([
      { transform: 'scale(1)', opacity: 1 },
      { transform: 'scale(0.96)', opacity: 0 }
    ], { duration: opts.duration, easing: 'cubic-bezier(.22,.7,.18,1)', fill: 'forwards' });
    el.__ani.onfinish = ()=>{ el.hidden = true; el.style.willChange=''; el.__ani = null; };
  }

  // ✅ remove um item de lista (card) com fade + colapso suave em vez de
  // sumir/pular no corte — usado ao excluir vendas/clientes.
  function animateRemove(el, opts){
    opts = opts || {duration:220};
    if(!el || !el.parentNode) return;
    if(isReduceMotion()){ el.remove(); return; }
    var rect = el.getBoundingClientRect();
    var cs = getComputedStyle(el);
    el.style.overflow = 'hidden';
    el.style.boxSizing = 'border-box';
    el.style.height = rect.height + 'px';
    el.style.marginTop = cs.marginTop;
    el.style.marginBottom = cs.marginBottom;
    // força um reflow antes de trocar os valores-alvo, senão o navegador
    // agrupa tudo numa transição só (sem estado inicial pra animar a partir)
    void el.offsetHeight;
    el.style.transition = 'opacity '+opts.duration+'ms ease, transform '+opts.duration+'ms ease, '+
      'height '+opts.duration+'ms ease, margin '+opts.duration+'ms ease, padding '+opts.duration+'ms ease';
    el.style.opacity = '0';
    el.style.transform = 'scale(.97)';
    el.style.height = '0px';
    el.style.marginTop = '0px';
    el.style.marginBottom = '0px';
    el.style.paddingTop = '0px';
    el.style.paddingBottom = '0px';
    setTimeout(function(){ try{ el.remove(); }catch(e){} }, opts.duration + 30);
  }

  let __lockY=0, __sbw=0;
  function lockViewport(){
    // ✅ nas páginas com sidebar (Vendas/Clientes/Nova Venda) quem rola é a
    // #appScroller interna, não a window — a window já não rola nada
    // (body.main-chrome tem overflow:hidden) e o backdrop do popup, cobrindo
    // a tela inteira, já bloqueia o scroll da lista por baixo dele. Travar a
    // window nesse caso é inútil e foi a causa da "balançada": o
    // position:fixed/inset:0 aplicado ao body força um reflow da árvore
    // inteira (sidebar + lista) que podia fazer o scrollTop da lista ser
    // recalculado/clampado — visível só quando ela já estava rolada.
    if(document.getElementById('appScroller')) return;
    __lockY = window.scrollY||document.documentElement.scrollTop||0;
    __sbw = window.innerWidth - document.documentElement.clientWidth;
    document.documentElement.style.setProperty('--lock-top', `${-__lockY}px`);
    document.documentElement.style.setProperty('--sbw', (__sbw||0)+'px');
    document.body.classList.add('lock-scroll');
  }
  function unlockViewport(){
    if(document.getElementById('appScroller')) return;
    document.body.classList.remove('lock-scroll');
    document.documentElement.style.removeProperty('--lock-top');
    document.documentElement.style.removeProperty('--sbw');
    window.scrollTo(0, __lockY|0);
  }

  function isReduceMotion(){
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }

  function recenterSheet(sheetEl){
    try{
      const cx=getContentCenterX();
      sheetEl.style.left = cx + 'px';
    }catch(e){}
  }
  function recenterSheetSoon(sheetEl){
    recenterSheet(sheetEl);
    requestAnimationFrame(()=>recenterSheet(sheetEl));
    setTimeout(()=>recenterSheet(sheetEl),180);
  }

  function openPicker(backdropEl, sheetEl, focusEl){
    if(!backdropEl || !sheetEl) return;
    lockViewport();
    recenterSheetSoon(sheetEl);
    backdropEl.classList.remove('leaving');
    backdropEl.classList.add('open');
    backdropEl.setAttribute('aria-hidden','false');

    autoGrowAll(sheetEl);
    requestAnimationFrame(()=>autoGrowAll(sheetEl));
    setTimeout(()=>autoGrowAll(sheetEl), 180);

    if(focusEl){
      setTimeout(()=>{ try{ focusEl.focus({preventScroll:true}); }catch(e){} },0);
    }else{
      sheetEl.setAttribute('tabindex','-1');
      setTimeout(()=>{ try{ sheetEl.focus({preventScroll:true}); }catch(e){} },0);
    }
  }
  function closePicker(backdropEl){
    if(!backdropEl) return;
    if(!backdropEl.classList.contains('open')) return;
    if(isReduceMotion()){
      backdropEl.classList.remove('open','leaving');
      backdropEl.setAttribute('aria-hidden','true');
      unlockViewport();
      return;
    }
    backdropEl.classList.add('leaving');
    backdropEl.classList.remove('open');
    backdropEl.setAttribute('aria-hidden','true');
    setTimeout(()=>{ try{ backdropEl.classList.remove('leaving'); }catch(e){} unlockViewport(); }, 220);
  }
  function pickerIsOpen(backdropEl){ return !!(backdropEl && backdropEl.classList.contains('open')); }

  // Auto-grow de <textarea>: cresce/encolhe com o conteúdo, sem alça de
  // redimensionar manual (resize:none no CSS). `field-sizing:content` já
  // resolve isso nativamente onde suportado; este JS é o fallback para
  // navegadores/Chromium mais antigos e cobre o caso de valor setado via
  // script (popups de edição), que não dispara o evento "input".
  function autoGrowTextarea(el){
    if(!el || el.tagName !== 'TEXTAREA') return;
    try{
      el.style.height = 'auto';
      const max = Math.round(window.innerHeight * 0.6);
      el.style.height = Math.max(el.scrollHeight, 0) > max ? max + 'px' : el.scrollHeight + 'px';
    }catch(e){}
  }
  function autoGrowAll(root){
    try{
      (root || document).querySelectorAll('textarea').forEach(autoGrowTextarea);
    }catch(e){}
  }
  document.addEventListener('input', function(e){
    if(e.target && e.target.tagName === 'TEXTAREA') autoGrowTextarea(e.target);
  });
  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', function(){ autoGrowAll(document); }, {once:true});
  }else{
    autoGrowAll(document);
  }

  window.__desktopApi = __desktopApi;
  window.__download = __download;
  window.__saveFromUrl = __saveFromUrl;
  window.getContentCenterX = getContentCenterX;
  window.orVago = orVago;
  window.flashInvalid = flashInvalid;
  window.formatSelCount = formatSelCount;
  window.toNumber = toNumber;
  window.toMoneyRaw = toMoneyRaw;
  window.toMoneyDisplay = toMoneyDisplay;
  window.moneyMaskCents = moneyMaskCents;
  window.moneyMaskValue = moneyMaskValue;
  window.renderMoneyMask = renderMoneyMask;
  window.setMoneyMaskValue = setMoneyMaskValue;
  window.enforceMoneyMask = enforceMoneyMask;
  window.lockViewport = lockViewport;
  window.unlockViewport = unlockViewport;
  window.isReduceMotion = isReduceMotion;
  window.animateInSlime = animateInSlime;
  window.animateOutSlime = animateOutSlime;
  window.animateRemove = animateRemove;
  window.recenterSheet = recenterSheet;
  window.recenterSheetSoon = recenterSheetSoon;
  window.openPicker = openPicker;
  window.closePicker = closePicker;
  window.pickerIsOpen = pickerIsOpen;
  window.autoGrowTextarea = autoGrowTextarea;
  window.autoGrowAll = autoGrowAll;

  // ✅ Nenhum elemento do app é feito pra ser arrastado (não há
  // draggable="true" em lugar nenhum) — o único motivo de um "dragstart"
  // disparar é o comportamento nativo do Chromium em links/imagens, que
  // mostra pro usuário a URL real do backend local (http://127.0.0.1:<porta>/…).
  // Bloqueado globalmente aqui; a regra -webkit-user-drag em base.html já
  // cobre a/img, isto é só reforço pra qualquer outro elemento.
  document.addEventListener('dragstart', function(e){ e.preventDefault(); });
})();
