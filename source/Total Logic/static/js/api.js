/**
 * FactQ frontend data gateway.
 * Replace only this module's implementation with fetch('/api/...') when FastAPI is ready.
 */
(function () {
  const API_BASE = document.body.dataset.apiBase?.replace(/\/$/, '') || '';
  const USE_REAL_API = Boolean(API_BASE);
  const FRONTEND_MOCK_DB_URL = '/static/data/frontend_mock_db.json?v=20260825-1';
  const SHOW_SEED_ARTICLES_IN_BOARD = false;
  const STORAGE_KEY = 'factq.mock.articles.backend-mapped-v2';
  if (!USE_REAL_API) {
    for (let index = localStorage.length - 1; index >= 0; index -= 1) {
      const key = localStorage.key(index);
      if (!key?.startsWith('factq.mock.articles.') || key === STORAGE_KEY) continue;
      localStorage.removeItem(key);
    }
  }
  let seedPromise;

  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const clone = value => JSON.parse(JSON.stringify(value));

  async function requestJson(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {'Content-Type':'application/json', ...(options.headers || {})}
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || payload.detail || `API 요청 실패 (${response.status})`);
    return payload;
  }

  // Single frontend contract used by both Mock DB records and future FastAPI responses.
  // Article-specific text, values and verdicts must come from the payload, never from UI code.
  function normalizeBackendVerdict(rawVerdict) {
    if (rawVerdict == null) return 'UNVERIFIED';
    const normalized = String(rawVerdict).trim().toUpperCase();
    if (normalized === 'VERIFIED' || normalized === 'MISMATCH') return normalized;
    if (normalized === 'NOT_ELIGIBLE') return 'UNVERIFIED';
    if (normalized.startsWith('ERROR')) return 'ERROR';
    if (normalized.startsWith('UNVERIFIED_') || ['UNVERIFIED', 'PENDING', 'WAITING', 'NOT_VERIFIED'].includes(normalized)) return 'UNVERIFIED';
    return 'UNVERIFIED';
  }

  function mapBackendVerdictToFrontend(rawVerdict) {
    const normalized = normalizeBackendVerdict(rawVerdict);
    if (normalized === 'VERIFIED') return {group:'verified', label:'일치', symbol:'✓'};
    if (normalized === 'MISMATCH') return {group:'mismatch', label:'불일치', symbol:'✕'};
    if (normalized === 'ERROR') return {group:'error', label:'검증 오류', symbol:'!'};
    return {group:'unverified', label:'판단 불가', symbol:'?'};
  }

  function kosisSourceUrl(evidence = {}) {
    if (!evidence.table_org_id || !evidence.table_tbl_id) return null;
    const params = new URLSearchParams({orgId:evidence.table_org_id, tblId:evidence.table_tbl_id});
    return `https://kosis.kr/statHtml/statHtml.do?${params.toString()}`;
  }

  function mapBackendClaim(claim = {}, articleId = '') {
    const verdictPayload = claim.verdict && typeof claim.verdict === 'object' ? claim.verdict : claim;
    const strict = verdictPayload.modes?.strict || null;
    const displayClaim = strict ? {...verdictPayload, ...strict, evidence:verdictPayload.evidence} : verdictPayload;
    const rawVerdict = displayClaim.rawVerdict || displayClaim.raw_verdict || displayClaim.backend_verdict || displayClaim.verdict || null;
    const presentation = mapBackendVerdictToFrontend(rawVerdict);
    const uiVerdict = ({verified:'MATCH',mismatch:'MISMATCH',unverified:'UNVERIFIED',error:'ERROR'}[presentation.group] || 'UNVERIFIED');
    const evidence = verdictPayload.evidence ? {
      table_org_id: verdictPayload.evidence.table_org_id ?? null,
      table_tbl_id: verdictPayload.evidence.table_tbl_id ?? null,
      table_nm: verdictPayload.evidence.table_nm ?? null,
      retrieval_status: verdictPayload.evidence.retrieval_status ?? null,
      kosis_url: verdictPayload.evidence.kosis_url ?? null
    } : null;
    const normalized = {
      claim_id: claim.claim_id || `${articleId}-CLAIM`,
      article_id: claim.article_id || articleId,
      claim: claim.claim || claim.claim_text || '',
      claim_text: claim.claim_text || claim.claim || '',
      start_offset: Number.isInteger(claim.start_offset) ? claim.start_offset : null,
      end_offset: Number.isInteger(claim.end_offset) ? claim.end_offset : null,
      match_start: null,
      match_end: null,
      match_status: 'UNMATCHED',
      status: claim.status || (rawVerdict ? 'COMPLETED' : 'PENDING'),
      rawVerdict,
      raw_verdict: rawVerdict,
      uiVerdict,
      ui_verdict: uiVerdict,
      verdictGroup: presentation.group,
      verdict_group: presentation.group,
      verdictLabel: presentation.label,
      verdict_label: presentation.label,
      verdictSymbol: presentation.symbol,
      explanation: displayClaim.explanation || '',
      isMinimal: rawVerdict === 'NOT_ELIGIBLE' || rawVerdict?.startsWith('ERROR'),
      sent_id: claim.sent_id ?? null,
      metric: claim.metric ?? null,
      metric_normalized: claim.metric_normalized ?? null,
      backendModes: verdictPayload.modes || null,
      raw_backend: claim.raw_backend || claim
    };
    if (!normalized.isMinimal) Object.assign(normalized, {
      claimedValue: displayClaim.claimed_value ?? displayClaim.claimedValue ?? null,
      actualValue: displayClaim.actual_value ?? displayClaim.actualValue ?? null,
      evidence,
      hedge_type: displayClaim.hedge_type ?? null,
      mode: displayClaim.mode || 'strict',
      ai_used: displayClaim.ai_used ?? false,
      ai_note: displayClaim.ai_note ?? null,
      source_url: evidence?.kosis_url || null
    });
    return normalized;
  }

  const normalizeClaim = mapBackendClaim;

  function normalizedTextWithMap(text = '') {
    const source = String(text);
    let normalized = '';
    const map = [];
    let pendingSpace = false;
    for (let index = 0; index < source.length; index += 1) {
      if (/\s/.test(source[index])) {
        pendingSpace = normalized.length > 0;
        continue;
      }
      if (pendingSpace) {
        normalized += ' ';
        map.push(index);
        pendingSpace = false;
      }
      normalized += source[index];
      map.push(index);
    }
    return {normalized:normalized.trim(), map};
  }

  function locateClaim(content, claim, sentences = []) {
    const sentence = sentences.find(item => item.sent_id === claim.sent_id);
    if (!sentence || !Number.isInteger(sentence.start) || !Number.isInteger(sentence.end)) {
      return {start:null, end:null, status:'UNMATCHED'};
    }
    if (sentence.start < 0 || sentence.end <= sentence.start || sentence.end > content.length) {
      return {start:null, end:null, status:'UNMATCHED'};
    }
    return {start:sentence.start, end:sentence.end, status:'SENTENCE'};
  }

  function normalizeArticle(article = {}) {
    const articleId = article.article_id || '';
    const normalized = {
      ...article,
      article_id: articleId,
      input_type: article.input_type || 'URL',
      url: article.url || '',
      title: article.title || '제목을 불러오는 중입니다.',
      publisher: article.publisher || '',
      author: article.author || null,
      published_at: article.published_at || null,
      updated_at: article.updated_at || null,
      content: article.content || '',
      category: article.category || null,
      status: article.status || 'PENDING',
      stage: article.stage || null,
      request_input: article.request_input || null,
      created_at: article.created_at || null,
      verified_at: article.verified_at || null,
      sentences: article.sentences || [],
      claims: (article.claims || article.verdict_results || article.results || []).map(claim => normalizeClaim(claim, articleId))
    };
    normalized.claims = normalized.claims.map(claim => {
      const location = locateClaim(normalized.content, claim, normalized.sentences);
      if (location.status === 'UNMATCHED') console.warn(`[FactQ] 원문 위치를 찾지 못했습니다: ${claim.claim_id}`);
      return {
        ...claim,
        match_start: location.start,
        match_end: location.end,
        match_status: location.status
      };
    });
    return normalized;
  }

  function normalizeDatasetArticle(raw = {}) {
    return normalizeArticle({
      article_id: raw.article_id,
      input_type: 'STORED_ARTICLE',
      url: raw.url,
      title: raw.title,
      subtitle: raw.subtitle || null,
      publisher: raw.publisher || '',
      author: null,
      author_url: null,
      published_at: raw.posted_date ? `${raw.posted_date}T00:00:00+09:00` : null,
      updated_at: null,
      content: raw.text || '',
      paragraphs: raw.paragraphs || [],
      category: null,
      status: 'PENDING',
      created_at: null,
      verified_at: null,
      processing_seconds: null,
      claims: []
    });
  }

  function canonicalUrl(value) {
    const url = new URL(value.trim());
    url.hash = '';
    url.hostname = url.hostname.toLowerCase();
    if ((url.protocol === 'https:' && url.port === '443') || (url.protocol === 'http:' && url.port === '80')) url.port = '';
    if (url.pathname.length > 1) url.pathname = url.pathname.replace(/\/$/, '');
    ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content'].forEach(key => url.searchParams.delete(key));
    url.searchParams.sort();
    return url.toString();
  }

  function isUrlInput(value) {
    try { return ['http:', 'https:'].includes(new URL(value.trim()).protocol); }
    catch { return false; }
  }

  function normalizedTitle(value) {
    return value.trim().replace(/\s+/g, ' ').toLowerCase();
  }

  async function seedArticles() {
    if (!seedPromise) {
      seedPromise = fetch(FRONTEND_MOCK_DB_URL).then(response => {
        if (!response.ok) throw new Error('Mock DB를 불러오지 못했습니다.');
        return response.json();
      }).then(payload => {
        if (!Array.isArray(payload.articles)) throw new Error('Frontend Mock DB에 articles 배열이 없습니다.');
        const diagnostics = payload.diagnostics || {};
        console.info('[FactQ Mock DB]', diagnostics);
        if (diagnostics.unmatched_articles?.length || diagnostics.unmatched_claims?.length) {
          console.warn('[FactQ Mock DB] unmatched data', diagnostics);
        }
        return payload.articles.map(normalizeArticle);
      });
    }
    return clone(await seedPromise);
  }

  function localArticles() {
    try {
      const articles = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]').map(normalizeArticle);
      let changed = false;
      const sanitized = articles.map(article => {
        const isLegacyFabricatedResult = / 기사 통계 검증 결과$/.test(article.title || '') && (article.claims || []).length > 0;
        if (!isLegacyFabricatedResult) return article;
        changed = true;
        const host = (() => { try { return new URL(article.url).hostname.replace(/^www\./, ''); } catch { return article.publisher || '신규 기사'; } })();
        return {
          ...article,
          title: `${host} 기사 검증 요청`,
          publisher: host,
          content: '',
          status: 'PROCESSING',
          verified_at: null,
          verification_due_at: null,
          processing_seconds: null,
          claims: []
        };
      });
      if (changed) saveLocal(sanitized);
      return sanitized;
    }
    catch { return []; }
  }

  function saveLocal(articles) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(articles));
  }

  function refreshProcessingArticles() {
    const articles = localArticles();
    let changed = false;
    const now = Date.now();
    articles.forEach(article => {
      if (article.status !== 'PROCESSING' || !article.verification_due_at || now < new Date(article.verification_due_at).getTime()) return;
      article.status = 'COMPLETED';
      article.stage = 'COMPLETED';
      article.verified_at = new Date().toISOString();
      article.processing_seconds = Math.max(1, Math.round((new Date(article.verified_at) - new Date(article.created_at)) / 1000));
      delete article.verification_due_at;
      changed = true;
    });
    if (changed) saveLocal(articles);
    return articles;
  }

  async function allArticles() {
    const stored = refreshProcessingArticles();
    return SHOW_SEED_ARTICLES_IN_BOARD ? [...stored, ...await seedArticles()] : stored;
  }

  function claimSentenceKey(claim, index) {
    for (const field of ['sent_id', 'sentence_id', 'source_sentence_id']) {
      const value = claim[field];
      if (typeof value === 'string' && value.trim()) return `${field}:${value.trim()}`;
    }
    for (const field of ['original_sentence', 'source_sentence', 'sentence', 'original_claim', 'claim']) {
      const value = claim[field];
      if (typeof value === 'string' && value.trim()) return `${field}:${value.trim().replace(/\s+/g, ' ')}`;
    }
    return `unidentified_claim:${claim.claim_id || index}`;
  }

  function sentenceVerdict(values) {
    const normalized = values.map(normalizeBackendVerdict);
    if (normalized.includes('ERROR')) return 'ERROR';
    if (normalized.includes('MISMATCH')) return 'MISMATCH';
    if (normalized.includes('UNVERIFIED')) return 'UNVERIFIED';
    if (normalized.length && normalized.every(value => value === 'VERIFIED')) return 'VERIFIED';
    return 'UNVERIFIED';
  }

  function summary(article) {
    const scoped = (article.claims || []).filter(claim => !claim.article_id || claim.article_id === article.article_id);
    const claims = [...new Map(scoped.map((claim, index) => [claim.claim_id || `__row_${index}`, claim])).values()];
    const grouped = new Map();
    claims.forEach((claim, index) => {
      const key = claimSentenceKey(claim, index);
      grouped.set(key, [...(grouped.get(key) || []), claim.rawVerdict]);
    });
    const sentenceVerdicts = [...grouped.values()].map(sentenceVerdict);
    return {
      total_claims: claims.length,
      matched: sentenceVerdicts.filter(value => value === 'VERIFIED').length,
      mismatched: sentenceVerdicts.filter(value => value === 'MISMATCH').length,
      unverified: sentenceVerdicts.filter(value => value === 'UNVERIFIED').length,
      not_eligible: sentenceVerdicts.filter(value => value === 'NOT_ELIGIBLE').length,
      errors: sentenceVerdicts.filter(value => value === 'ERROR').length,
      raw: claims.filter(c => c.rawVerdict === 'RAW_ONLY').length,
      processing: 0
    };
  }

  function normalizeDetailPayload(payload = {}) {
    if (Array.isArray(payload.claims) && Array.isArray(payload.sentences) && payload.article) {
      const source = payload.article;
      const article = normalizeArticle({
        ...source,
        input_type:'STORED_ARTICLE',
        published_at:source.posted_date || null,
        content:source.text || '',
        status:'COMPLETED',
        stage:'COMPLETED',
        created_at:payload.versions?.generated_at || null,
        verified_at:payload.versions?.generated_at || null,
        sentences:payload.sentences,
        claims:payload.claims,
        summary:payload.summary,
        versions:payload.versions
      });
      const backendSummary = payload.summary || {};
      return {
        article,
        summary:{
          total_claims:backendSummary.n_claims ?? article.claims.length,
          matched:backendSummary.n_verified ?? 0,
          mismatched:backendSummary.n_mismatch ?? 0,
          unverified:backendSummary.n_unverified ?? 0,
          not_eligible:backendSummary.n_not_eligible ?? 0,
          errors:backendSummary.n_error ?? 0,
          raw:article.claims.filter(claim => claim.rawVerdict === 'RAW_ONLY').length,
          processing:article.claims.filter(claim => !claim.rawVerdict).length
        },
        navigation:{previous_article_id:null, next_article_id:null}
      };
    }
    const articleSource = payload.article || payload;
    const results = payload.verdict_results || payload.results || payload.output || payload.verdicts;
    const article = normalizeArticle(results ? {...articleSource, claims:results} : articleSource);
    return {
      article,
      summary: summary(article),
      navigation: payload.navigation || {previous_article_id:null, next_article_id:null}
    };
  }

  async function checkDuplicateUrl(url) {
    if (USE_REAL_API) {
      const rawInput = url.trim();
      const parameter = isUrlInput(rawInput) ? 'url' : 'title';
      return requestJson(`/articles/duplicate?${parameter}=${encodeURIComponent(rawInput)}`);
    }
    await wait(180);
    const rawInput = url.trim();
    const urlMode = isUrlInput(rawInput);
    const normalized = urlMode ? canonicalUrl(rawInput) : normalizedTitle(rawInput);
    const article = (await allArticles()).find(item => urlMode
      ? item.url && canonicalUrl(item.url) === normalized
      : normalizedTitle(item.title) === normalized);
    return article ? {
      exists: true, article_id: article.article_id, title: article.title,
      status: article.status, verified_at: article.verified_at, result_url: `/result/${article.article_id}`
    } : { exists: false, article_id: null, title: null, status: null, verified_at: null, result_url: null };
  }

  async function startVerification(url) {
    if (USE_REAL_API) {
      const rawInput = url.trim();
      const urlMode = isUrlInput(rawInput);
      return requestJson('/verifications', {
        method:'POST',
        body:JSON.stringify(urlMode
          ? {input_type:'URL', url:rawInput}
          : {input_type:'TITLE', title:rawInput})
      });
    }
    const duplicate = await checkDuplicateUrl(url);
    if (duplicate.exists) return { status: 'EXISTING', ...duplicate };

    const fixtures = await seedArticles();
    const rawInput = url.trim();
    const urlMode = isUrlInput(rawInput);
    const matchedFixture = fixtures.find(item => urlMode
      ? canonicalUrl(item.url) === canonicalUrl(rawInput)
      : normalizedTitle(item.title).includes(normalizedTitle(rawInput)) || normalizedTitle(rawInput).includes(normalizedTitle(item.title)));
    if (!urlMode && !matchedFixture) throw new Error('입력한 제목과 일치하는 기사를 찾을 수 없습니다.');
    const matchedVerdicts = matchedFixture ? clone(matchedFixture.claims || []) : [];
    const normalizedUrl = matchedFixture ? canonicalUrl(matchedFixture.url) : canonicalUrl(rawInput);
    const now = new Date();
    const articleId = `M${now.getTime().toString(36).toUpperCase()}`;
    let host = new URL(normalizedUrl).hostname.replace(/^www\./, '');
    const article = matchedFixture ? normalizeArticle({...clone(matchedFixture), claims:matchedVerdicts}) : {
      article_id: articleId,
      input_type: 'URL',
      url: normalizedUrl,
      title: `${host} 기사 검증 요청`,
      publisher: host,
      author: null,
      published_at: null,
      updated_at: null,
      content: '',
      category: null,
      status: 'PROCESSING',
      claims: []
    };
    article.article_id = articleId;
    article.source_article_id = matchedFixture?.article_id || null;
    article.url = normalizedUrl;
    article.created_at = now.toISOString();
    article.verified_at = null;
    article.verification_due_at = matchedFixture ? new Date(now.getTime() + 2500).toISOString() : null;
    article.status = 'PROCESSING';
    article.stage = 'REQUESTED';
    article.request_input = rawInput;
    article.processing_seconds = null;
    article.input_type = 'URL';
    (article.claims || []).forEach(claim => {
      claim.article_id = articleId;
    });
    const locals = localArticles();
    locals.unshift(article);
    saveLocal(locals);
    return { status: article.status, stage:article.stage, article_id: articleId, result_url: `/result/${articleId}` };
  }

  async function getArticleList({ query = '' } = {}) {
    if (USE_REAL_API) {
      const payload = await requestJson(`/articles?query=${encodeURIComponent(query.trim())}`);
      const items = Array.isArray(payload) ? payload : (payload.items || payload.articles || []);
      return items.map(item => {
        const article = normalizeArticle(item.article || item);
        return {
          article_id:article.article_id, title:article.title, publisher:article.publisher,
          url:article.url, status:article.status, stage:article.stage, request_input:article.request_input,
          created_at:article.created_at, published_at:article.published_at,
          verified_at:article.verified_at, summary:item.summary || summary(article)
        };
      });
    }
    await wait(120);
    const term = query.trim().toLowerCase();
    return (await allArticles())
      .filter(article => !term || [article.title, article.publisher, article.url]
        .some(value => String(value || '').toLowerCase().includes(term)))
      .sort((a, b) =>
        new Date(b.created_at || b.verified_at || b.published_at || 0) -
        new Date(a.created_at || a.verified_at || a.published_at || 0)
      )
      .map(article => ({
        article_id: article.article_id, title: article.title, publisher: article.publisher,
        url: article.url, status: article.status, stage:article.stage, request_input:article.request_input,
        created_at:article.created_at, published_at: article.published_at, verified_at: article.verified_at,
        summary: summary(article)
      }));
  }

  async function getArticleDetail(articleId) {
    if (USE_REAL_API) return normalizeDetailPayload(await requestJson(`/articles/${encodeURIComponent(articleId)}`));
    await wait(120);
    const articles = (await allArticles()).sort((a, b) => new Date(b.verified_at) - new Date(a.verified_at));
    const index = articles.findIndex(article => article.article_id === articleId);
    if (index < 0) throw new Error('검증 결과를 찾을 수 없습니다.');
    const article = normalizeArticle(clone(articles[index]));
    return {
      article,
      summary: summary(article),
      navigation: {
        previous_article_id: index > 0 ? articles[index - 1].article_id : null,
        next_article_id: index + 1 < articles.length ? articles[index + 1].article_id : null
      }
    };
  }

  async function getVerificationResult(articleId) {
    return getArticleDetail(articleId);
  }

  window.factqApi = Object.freeze({
    checkDuplicateUrl,
    startVerification,
    getVerificationResult,
    getArticleList,
    getArticleDetail
  });
})();
