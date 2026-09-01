const api = window.factqApi;
const modal = document.querySelector('#modal');
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const formatDate = value => value ? new Intl.DateTimeFormat('ko-KR', {dateStyle:'medium', timeStyle:'short'}).format(new Date(value)) : '—';

function highlightSearchTerm(value, query) {
  const source = String(value ?? '');
  const term = query.trim();
  if (!term) return escapeHtml(source);
  const lowerSource = source.toLocaleLowerCase('ko-KR');
  const lowerTerm = term.toLocaleLowerCase('ko-KR');
  let cursor = 0;
  let result = '';
  let index = lowerSource.indexOf(lowerTerm, cursor);
  while (index >= 0) {
    result += escapeHtml(source.slice(cursor, index));
    result += `<mark class="archive-search-highlight">${escapeHtml(source.slice(index, index + term.length))}</mark>`;
    cursor = index + term.length;
    index = lowerSource.indexOf(lowerTerm, cursor);
  }
  return result + escapeHtml(source.slice(cursor));
}

function renderExtractedArticle(content) {
  return escapeHtml(String(content || '')).split(/\n+/).map(paragraph => paragraph.trim()).filter(Boolean).map(paragraph => `<p>${paragraph}</p>`).join('');
}

function closeModal() { if (modal) modal.hidden = true; }
function showModal({ title, message, actions, type = 'error' }) {
  document.querySelector('#modal-title').textContent = title;
  document.querySelector('#modal-message').textContent = message;
  document.querySelector('#modal-icon').textContent = type === 'existing' ? '↻' : '!';
  const container = document.querySelector('#modal-actions');
  container.replaceChildren();
  actions.forEach(action => {
    const element = action.href ? document.createElement('a') : document.createElement('button');
    element.textContent = action.label;
    if (action.href) element.href = action.href;
    else element.addEventListener('click', action.onClick || closeModal);
    if (action.primary) element.className = 'confirm';
    container.append(element);
  });
  modal.hidden = false;
}

modal?.addEventListener('click', event => { if (event.target === modal) closeModal(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeModal(); });

const form = document.querySelector('#url-form');
const workspace = document.querySelector('.verification-workspace');
const verificationInput = document.querySelector('#news-url');
const verificationSubmit = document.querySelector('#verification-submit');
const workspaceFormLabel = document.querySelector('#workspace-form-label');
const workspaceStatusBadge = document.querySelector('#workspace-status-badge');
const workspaceStatusLine = document.querySelector('#workspace-status-line');
const workspaceCompletedResult = document.querySelector('#workspace-completed-result');
const workspaceProcessingResult = document.querySelector('#workspace-processing-result');
const completedArticleTitle = document.querySelector('#completed-article-title');
const completedResultSummary = document.querySelector('#completed-result-summary');
const completedResultLink = document.querySelector('#completed-result-link');
const processingArticleTitle = document.querySelector('#processing-article-title');
const processingResultLink = document.querySelector('#processing-result-link');
let currentHomeVerification = null;
let displayedCompletedArticleId = null;
let hiddenCompletedArticleId = null;

function processingStageMarkup(stage) {
  const stages = [
    {keys:['FETCHING_ARTICLE','ARTICLE_FETCHED'], label:'기사 확인'},
    {keys:['EXTRACTING_CLAIMS','CLAIMS_EXTRACTED'], label:'Claim 추출'},
    {keys:['SEARCHING_KOSIS','KOSIS_RESOLVED'], label:'KOSIS 확인'},
    {keys:['VERIFYING'], label:'판정'}
  ];
  const activeIndex = stages.findIndex(item => item.keys.includes(stage));
  if (activeIndex < 0) return '<span class="done">검증 요청 접수 ✓</span>';
  return stages.map((item,index) => `<span class="${index < activeIndex ? 'done' : index === activeIndex ? 'active' : 'waiting'}">${item.label} ${index < activeIndex ? '✓' : index === activeIndex ? '●' : '○'}</span>`).join('<i>·</i>');
}

function setWorkspaceState(state, article = null) {
  if (!workspace || !verificationInput || !verificationSubmit) return;
  currentHomeVerification = article;
  workspace.classList.remove('state-processing','state-completed','state-failed');
  workspaceStatusBadge.hidden = state === 'IDLE';
  workspaceStatusLine.hidden = state === 'IDLE';
  if (workspaceCompletedResult) workspaceCompletedResult.hidden = true;
  if (workspaceProcessingResult) workspaceProcessingResult.hidden = true;
  if (workspaceFormLabel) workspaceFormLabel.textContent = '기사 검색 및 검증';
  verificationInput.disabled = state === 'PROCESSING';
  verificationSubmit.disabled = state === 'PROCESSING';
  verificationSubmit.classList.toggle('text-button', state === 'PROCESSING' || state === 'FAILED');
  verificationSubmit.querySelector('svg').hidden = state === 'PROCESSING' || state === 'FAILED';
  verificationSubmit.querySelector('span').textContent = '';

  if (state === 'IDLE') return;
  if (state === 'PROCESSING') {
    displayedCompletedArticleId = null;
    hiddenCompletedArticleId = null;
    verificationInput.value = '';
    workspace.classList.add('state-processing');
    if (workspaceProcessingResult) workspaceProcessingResult.hidden = false;
    if (processingArticleTitle) processingArticleTitle.textContent = article?.title || article?.request_input || article?.url || '요청한 뉴스 기사';
    if (processingResultLink && article?.article_id) processingResultLink.href = `/result/${encodeURIComponent(article.article_id)}`;
    workspaceStatusBadge.innerHTML = '<i></i>검증 중';
    workspaceStatusLine.innerHTML = processingStageMarkup(article?.stage);
    verificationSubmit.querySelector('span').textContent = '검증 중 ···';
    return;
  }
  if (state === 'COMPLETED') {
    const showCompletedResult = hiddenCompletedArticleId !== article?.article_id;
    if (showCompletedResult) workspace.classList.add('state-completed');
    workspaceStatusBadge.hidden = true;
    workspaceStatusLine.hidden = true;
    const summary = article?.summary;
    const summaryText = summary?.total_claims
      ? `Claim ${summary.total_claims}건 · 일치 ${summary.matched} · 불일치 ${summary.mismatched} · 판단 불가 ${summary.unverified}${summary.not_eligible ? ` · 검증 대상 아님 ${summary.not_eligible}` : ''}${summary.errors ? ` · 오류 ${summary.errors}` : ''}`
      : '검증 처리가 완료되었습니다.';
    if (workspaceCompletedResult) workspaceCompletedResult.hidden = !showCompletedResult;
    if (completedArticleTitle) completedArticleTitle.textContent = article?.title || '검증한 기사';
    if (completedResultSummary) completedResultSummary.textContent = summaryText;
    if (completedResultLink) completedResultLink.href = `/result/${encodeURIComponent(article.article_id)}`;
    if (workspaceFormLabel) workspaceFormLabel.textContent = showCompletedResult ? '다른 기사도 검증해보세요.' : '기사 검색 및 검증';
    if (displayedCompletedArticleId !== article?.article_id) {
      verificationInput.value = '';
      displayedCompletedArticleId = article?.article_id || null;
    }
    return;
  }
  verificationInput.value = article?.request_input || article?.url || article?.title || verificationInput.value;
  workspace.classList.add('state-failed');
  workspaceStatusBadge.textContent = '! 검증 실패';
  workspaceStatusLine.textContent = '검증 처리에 실패했습니다. 입력 내용을 확인한 뒤 다시 시도해주세요.';
  verificationSubmit.querySelector('span').textContent = '다시 시도';
}

verificationInput?.addEventListener('input', () => {
  if (currentHomeVerification?.status !== 'COMPLETED') return;
  hiddenCompletedArticleId = currentHomeVerification.article_id;
  setWorkspaceState('IDLE');
});

form?.addEventListener('submit', async event => {
  event.preventDefault();
  const input = document.querySelector('#news-url');
  const query = input.value.trim();
  let parsed;
  try { parsed = new URL(query); } catch { parsed = null; }
  const looksLikeUrl = /^(https?:\/\/|www\.)/i.test(query);
  if (!query || (looksLikeUrl && (!parsed || !['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname.includes('.')))) {
    showModal({title:'입력 내용을 확인해주세요.', message:'올바른 뉴스 URL 또는 기사 제목을 입력해주세요.', actions:[{label:'확인', primary:true}]});
    return;
  }
  if (!looksLikeUrl && query.length < 2) {
    showModal({title:'기사 제목이 너무 짧습니다.', message:'검색할 기사 제목을 두 글자 이상 입력해주세요.', actions:[{label:'확인', primary:true}]});
    return;
  }
  try {
    const duplicate = await api.checkDuplicateUrl(input.value);
    if (duplicate.exists) {
      showModal({
        title:'이미 검증된 기사입니다.',
        message:'저장된 기사 분석 리포트가 있습니다. 열람실에서 바로 확인하시겠습니까?',
        type:'existing',
        actions:[
          {label:'취소', onClick:closeModal},
          {label:'열람실로 이동', href:duplicate.result_url || `/result/${encodeURIComponent(duplicate.article_id)}`, primary:true}
        ]
      });
      return;
    }
    setWorkspaceState('PROCESSING', {status:'PROCESSING',stage:'REQUESTED',request_input:query,title:query});
    const started = await api.startVerification(query);
    if (started.status === 'COMPLETED' && started.article_id) {
      // 백엔드에 적재된 기사는 즉시 완료 응답이 오므로 검증 중 상태가
      // 전혀 보이지 않는 문제를 막고 동일한 상태 전환을 제공한다.
      await new Promise(resolve => setTimeout(resolve, 700));
      location.href = started.result_url || `/result/${encodeURIComponent(started.article_id)}`;
      return;
    }
    displayedCompletedArticleId = null;
    hiddenCompletedArticleId = null;
    setWorkspaceState('PROCESSING', {article_id:started.article_id,status:'PROCESSING',stage:started.stage || 'REQUESTED',request_input:query,title:query,summary:{total_claims:0,matched:0,mismatched:0,unverified:0}});
    await renderHomeLatest();
  } catch (error) {
    setWorkspaceState('IDLE');
    showModal({title:'데이터 오류', message:error.message, actions:[{label:'확인', primary:true}]});
  }
});

const archiveRows = document.querySelector('#archive-rows');
const homeLatest = document.querySelector('#home-latest');
async function renderHomeLatest() {
  if (!homeLatest) return;
  try {
    const items = await api.getArticleList();
    const current = [...items].filter(item => item.created_at).sort((a,b) => new Date(b.created_at) - new Date(a.created_at))[0] || null;
    if (workspace && current && (current.status === 'PROCESSING' || current.status === 'PENDING')) {
      setWorkspaceState('PROCESSING', current);
    } else if (workspace && current?.status === 'FAILED') {
      setWorkspaceState('FAILED', current);
    } else if (workspace) {
      setWorkspaceState('IDLE');
    }
    const latest = [...items].sort((a,b) =>
      new Date(b.created_at || b.verified_at || b.published_at || 0) -
      new Date(a.created_at || a.verified_at || a.published_at || 0)
    );
    if (!latest.length) {
      homeLatest.innerHTML = '<div class="latest-empty"><strong>아직 저장된 기사가 없습니다.</strong><span>뉴스 URL을 입력하면 이곳에 표시됩니다.</span></div>';
      return;
    }
    homeLatest.innerHTML = latest.slice(0, 5).map((item,index) => `
      <a class="latest-item ${item.article_id === current?.article_id && (item.status === 'PROCESSING' || item.status === 'PENDING') ? 'current-processing' : ''}" href="/result/${encodeURIComponent(item.article_id)}">
        <span class="latest-index">${String(index + 1).padStart(2,'0')}</span>
        <span class="latest-copy"><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.publisher)} · ${escapeHtml(formatDate(item.published_at || item.verified_at))}</small></span>
        ${item.status === 'PROCESSING' || item.status === 'PENDING' ? '<span class="processing-badge"><i></i>검증 중</span>' : item.status === 'FAILED' ? '<span class="failed-badge">실패</span>' : '<span class="complete-badge">✓ 완료</span>'}
        <span class="latest-arrow">›</span>
      </a>`).join('');
  } catch {
    homeLatest.innerHTML = '<p class="latest-empty">기사를 불러오지 못했습니다.</p>';
  }
}
if (homeLatest) { renderHomeLatest(); setInterval(renderHomeLatest, 3000); }
async function renderArchive(query = '') {
  if (!archiveRows) return;
  try {
    const items = await api.getArticleList({query});
    archiveRows.innerHTML = items.map(item => `
      <a class="archive-row" href="/result/${encodeURIComponent(item.article_id)}">
        <span class="article-cell"><b>${highlightSearchTerm(item.title, query)}</b><small>${highlightSearchTerm(item.url, query)}</small></span>
        <time>${escapeHtml(formatDate(item.verified_at))}</time>
        ${item.status === 'PROCESSING' ? '<span class="processing-badge"><i></i>검증중</span>' : item.summary.total_claims ? `<span class="mini-stats"><i class="match">${item.summary.matched}</i><i class="mismatch">${item.summary.mismatched}</i><i class="unknown">${item.summary.unverified}</i>${item.summary.not_eligible ? `<i class="not-eligible">${item.summary.not_eligible}</i>` : ''}${item.summary.errors ? `<i class="error">${item.summary.errors}</i>` : ''}</span>` : '<span class="complete-badge">완료</span>'}
        <span class="row-arrow">→</span>
      </a>`).join('');
    document.querySelector('#empty-state').hidden = items.length > 0 || Boolean(query);
    document.querySelector('#search-empty').hidden = items.length > 0 || !query;
  } catch (error) {
    showModal({title:'열람실 오류', message:error.message, actions:[{label:'확인', primary:true}]});
  }
}
if (archiveRows) renderArchive();
let searchTimer;
document.querySelector('#archive-search')?.addEventListener('input', event => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => renderArchive(event.target.value), 180);
});

const resultRoot = document.querySelector('#result-root');
if (resultRoot) {
  const waitScreen = document.querySelector('#verification-wait-screen');
  api.getVerificationResult(resultRoot.dataset.articleId).then(({article, summary, navigation}) => {
    if (article.status === 'COMPLETED' && !article.claims.length) {
      if (waitScreen) waitScreen.hidden = true;
      resultRoot.hidden = false;
      resultRoot.classList.add('article-preview-mode');
      document.querySelector('#result-title').textContent = article.title;
      document.querySelector('#result-url').href = article.url;
      document.querySelector('.overview-main .eyebrow').innerHTML = '<span></span>NO VERDICT FIXTURE';
      document.querySelector('.result-nav>span').textContent = '실제 판정 결과 없음';
      document.querySelector('.full-article-panel>header small').textContent = '백엔드 판정 결과가 없어 Claim을 표시하지 않습니다.';
      const byline = document.querySelector('#article-byline');
      byline.innerHTML = `<span>${escapeHtml(article.publisher)}</span><span>입력 ${escapeHtml(formatDate(article.published_at))}</span>`;
      document.querySelector('#article-copy').innerHTML = renderExtractedArticle(article.content);
      document.querySelector('#previous-result').addEventListener('click', () => navigation.previous_article_id ? location.href = `/result/${navigation.previous_article_id}` : history.back());
      document.querySelector('#next-result').addEventListener('click', () => navigation.next_article_id ? location.href = `/result/${navigation.next_article_id}` : history.forward());
      return;
    }
    if (article.status !== 'COMPLETED') {
      resultRoot.hidden = true;
      if (waitScreen) waitScreen.hidden = false;
      const pollUntilCompleted = async () => {
        try {
          const next = await api.getVerificationResult(resultRoot.dataset.articleId);
          if (next.article.status === 'COMPLETED') {
            location.reload();
            return;
          }
        } catch {}
        setTimeout(pollUntilCompleted, 1000);
      };
      setTimeout(pollUntilCompleted, 1000);
      return;
    }
    if (waitScreen) waitScreen.hidden = true;
    resultRoot.hidden = false;
    document.querySelector('#result-title').textContent = article.title;
    document.querySelector('#result-url').href = article.url;
    const byline = document.querySelector('#article-byline');
    byline.innerHTML = `<span>${escapeHtml(article.publisher)}</span><span>${escapeHtml(article.author || '')}</span><span>입력 ${escapeHtml(formatDate(article.published_at))}</span>${article.updated_at ? `<span>업데이트 ${escapeHtml(formatDate(article.updated_at))}</span>` : ''}`;
    document.querySelector('#previous-result').addEventListener('click', () => {
      if (navigation.previous_article_id) location.href = `/result/${navigation.previous_article_id}`;
      else history.back();
    });
    document.querySelector('#next-result').addEventListener('click', () => {
      if (navigation.next_article_id) location.href = `/result/${navigation.next_article_id}`;
      else history.forward();
    });
    document.querySelector('#count-total').textContent = summary.total_claims;
    document.querySelector('#count-match').textContent = summary.matched;
    document.querySelector('#count-mismatch').textContent = summary.mismatched;
    document.querySelector('#count-unknown').textContent = summary.unverified;
    document.querySelector('#count-error').textContent = summary.errors;
    const content = article.content || '';
    let cursor = 0;
    const claimCss = group => ({verified:'match',mismatch:'mismatch',unverified:'unknown',error:'error',raw:'raw'}[group] || 'unknown');
    const highlightableGroups = new Set(['verified', 'mismatch', 'unverified', 'raw', 'pending']);
    const visibleClaims = article.claims.filter(claim => highlightableGroups.has(claim.verdictGroup));
    const claimsByPosition = visibleClaims
      .map((claim,index) => ({claim,index}))
      .filter(({claim}) => Number.isInteger(claim.match_start)
        && Number.isInteger(claim.match_end))
      .sort((a,b) => a.claim.match_start - b.claim.match_start);
    const highlighted = claimsByPosition.map(({claim,index}) => {
      if (claim.match_start < cursor) return '';
      const before = escapeHtml(content.slice(cursor, claim.match_start));
      const text = escapeHtml(content.slice(claim.match_start, claim.match_end));
      cursor = claim.match_end;
      return `${before}<button class="article-claim ${claimCss(claim.verdictGroup)}" data-claim-id="${escapeHtml(claim.claim_id)}" data-claim-index="${index}" type="button"><span>${claim.verdictSymbol} Claim ${index + 1} · ${escapeHtml(claim.verdictLabel)}</span>${text}</button>`;
    }).join('') + escapeHtml(content.slice(cursor));
    document.querySelector('#article-copy').innerHTML = highlighted
      .split(/\n+/)
      .map(paragraph => paragraph.trim())
      .filter(Boolean)
      .map(paragraph => `<p>${paragraph}</p>`)
      .join('');

    if (claimsByPosition.length === 0) {
      document.querySelector('.full-article-panel>header small').textContent = '표시할 Claim이 없습니다.';
    }

    if (visibleClaims.length === 0) {
      document.querySelector('#claim-panel-content').innerHTML = `
        <div class="claim-empty-state">
          <strong>표시할 Claim이 없습니다.</strong>
          <p>기사에서 추출된 Claim이 없습니다.</p>
        </div>`;
      return;
    }

    let selectedClaimIndex = 0;
    const displayValue = value => value == null
      ? '확인된 값 없음'
      : new Intl.NumberFormat('ko-KR', {maximumFractionDigits:10}).format(value);
    const retrievalLabel = value => ({RESOLVED:'KOSIS 근거 확정',NOT_FOUND:'관련 통계표 없음',UNRESOLVED:'조회 조건 미확정'}[value] || value || '데이터 없음');
    const hedgeLabel = value => ({exact:'정확한 수치 표현',approx:'근사 표현',approx_range:'범위형 근사 표현',at_least:'이상·초과 표현',at_most:'이하·미만 표현'}[value] || value);
    const renderClaimPanel = (index, shouldScroll = false) => {
      selectedClaimIndex = index;
      const claim = visibleClaims[index];
      const css = claimCss(claim.verdictGroup);
      const comparable = ['verified','mismatch','unverified','raw'].includes(claim.verdictGroup);
      const operator = claim.verdictGroup === 'verified' ? '=' : claim.verdictGroup === 'mismatch' ? '≠' : '?';
      const valueComparison = comparable ? `<div class="value-comparison ${css}">
          <div><span>기사 주장값</span><strong class="${claim.claimedValue == null ? 'missing-value' : ''}">${escapeHtml(displayValue(claim.claimedValue))}</strong></div><b>${operator}</b>
          <div><span>KOSIS 조회값</span><strong class="${claim.actualValue == null ? 'missing-value' : ''}">${escapeHtml(displayValue(claim.actualValue))}</strong></div>
        </div>` : `<div class="claim-status-notice ${css}">판정 처리 중 오류가 발생했습니다.</div>`;
      const evidenceDetails = claim.evidence ? `<dl class="claim-conditions">
          <div><dt>통계표</dt><dd>${escapeHtml(claim.evidence.table_nm || '데이터 없음')}</dd></div>
          <div><dt>통계표 ID</dt><dd>${escapeHtml(claim.evidence.table_tbl_id || '데이터 없음')}</dd></div>
          <div><dt>기관 코드</dt><dd>${escapeHtml(claim.evidence.table_org_id || '데이터 없음')}</dd></div>
          <div><dt>조회 상태</dt><dd>${escapeHtml(retrievalLabel(claim.evidence.retrieval_status))}</dd></div>
        </dl>` : '';
      const technicalDetails = claim.isMinimal ? '' : [
        claim.hedge_type ? `<span>수치 표현: ${escapeHtml(hedgeLabel(claim.hedge_type))}</span>` : '',
        claim.mode ? `<span>판정 방식: ${escapeHtml(claim.mode)}</span>` : '',
        `<span>AI 재해석: ${claim.ai_used ? '사용' : '미사용'}</span>`,
        claim.ai_note ? `<span>${escapeHtml(claim.ai_note)}</span>` : ''
      ].filter(Boolean).join('');
      let sourceAction = '';
      if (['verified','mismatch'].includes(claim.verdictGroup) && claim.source_url) sourceAction = `<a class="evidence-detail-button ${claim.verdictGroup === 'mismatch' ? 'mismatch-action' : ''}" href="${escapeHtml(claim.source_url)}" target="_blank" rel="noopener">KOSIS 공식 통계표에서 확인 <span>↗</span></a>`;
      const unmatchedNotice = claim.match_status === 'UNMATCHED' ? '<div class="claim-location-warning">개발 정보: 원문 위치를 찾지 못해 하이라이트하지 않았습니다.</div>' : '';
      document.querySelector('#claim-panel-content').innerHTML = `
        <div class="claim-counter">Claim ${index + 1} / ${visibleClaims.length} · ${escapeHtml(claim.claim_id)}</div>
        <div class="panel-verdict ${css}">${claim.verdictSymbol} ${escapeHtml(claim.verdictLabel)}</div>
        <blockquote>${escapeHtml(claim.claim_text)}</blockquote>
        ${unmatchedNotice}
        ${valueComparison}
        ${evidenceDetails}
        <div class="panel-reason"><span>판정 근거</span><p>${escapeHtml(claim.explanation)}</p></div>
        <div class="claim-technical-details">${technicalDetails}</div>
        ${sourceAction}
        <nav class="claim-pagination"><button type="button" id="previous-claim" ${index === 0 ? 'disabled' : ''}>← 이전 Claim</button><strong>${index + 1} / ${visibleClaims.length}</strong><button type="button" id="next-claim" ${index === visibleClaims.length - 1 ? 'disabled' : ''}>다음 Claim →</button></nav>`;
      document.querySelectorAll('.article-claim').forEach(element => element.classList.toggle('selected', element.dataset.claimId === claim.claim_id));
      document.querySelector('#previous-claim').addEventListener('click', () => renderClaimPanel(index - 1, true));
      document.querySelector('#next-claim').addEventListener('click', () => renderClaimPanel(index + 1, true));
      if (shouldScroll) document.querySelectorAll('.article-claim').forEach(element => {
        if (element.dataset.claimId === claim.claim_id) element.scrollIntoView({behavior:'smooth',block:'center'});
      });
    };

    document.querySelectorAll('.article-claim').forEach(element => element.addEventListener('click', () => renderClaimPanel(Number(element.dataset.claimIndex))));
    renderClaimPanel(selectedClaimIndex);
  }).catch(error => {
    if (waitScreen) waitScreen.hidden = true;
    showModal({title:'결과 조회 오류', message:error.message, actions:[{label:'열람실', href:'/archive', primary:true}]});
  });
}
