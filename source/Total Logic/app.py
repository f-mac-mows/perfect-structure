from __future__ import annotations

import os
import hashlib
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from pipeline_bridge import (
    PipelineConfigurationError,
    analyze_frontend_url,
    find_bundled_post,
    find_fixture_article,
    find_frontend_post,
    get_claims_for_verification,
    list_frontend_posts,
    load_frontend_post,
    pipeline_readiness,
    publish_bundled_post,
    run_existing_pipeline,
)
from verification_store import get_record, list_records, save_record


app = FastAPI(title="FactQ", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
SERVER_SESSION_ID = uuid4().hex
# The UI talks to this FastAPI wrapper by default. Set FACTQ_API_BASE to an
# empty value only when explicitly running the JSONL-built frontend Mock mode.
FACTQ_API_BASE = os.getenv("FACTQ_API_BASE", "/api").rstrip("/")


class VerificationRequest(BaseModel):
    input_type: str
    url: Optional[str] = None
    title: Optional[str] = None


def page_context(page: str, **extra):
    return {"page": page, "server_session_id": SERVER_SESSION_ID, "api_base": FACTQ_API_BASE, **extra}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", page_context("home"))


@app.get("/archive", response_class=HTMLResponse)
def archive(request: Request):
    return templates.TemplateResponse(request, "archive.html", page_context("archive"))


@app.get("/result/{article_id}", response_class=HTMLResponse)
def result(request: Request, article_id: str):
    return templates.TemplateResponse(request, "result.html", page_context("result", article_id=article_id))


def _result_verdict(item: dict) -> Optional[str]:
    if item.get("verdict"):
        return item["verdict"]
    return ((item.get("modes") or {}).get("strict") or {}).get("verdict")


def _summary(results: list[dict], article_id: Optional[str] = None) -> dict[str, int]:
    unique_results: dict[str, dict] = {}
    for index, item in enumerate(results):
        item_article_id = item.get("article_id")
        if article_id and item_article_id and item_article_id != article_id:
            continue
        claim_id = item.get("claim_id") or f"__row_{index}"
        unique_results.setdefault(str(claim_id), item)
    scoped_results = list(unique_results.values())
    return {
        "total_claims": len(scoped_results),
        "matched": sum(_result_verdict(item) == "VERIFIED" for item in scoped_results),
        "mismatched": sum(_result_verdict(item) == "MISMATCH" for item in scoped_results),
        "unverified": sum(
            _result_verdict(item) is None
            or str(_result_verdict(item)).startswith("UNVERIFIED_")
            or _result_verdict(item) in {"PENDING", "WAITING", "NOT_VERIFIED", "NOT_ELIGIBLE"}
            for item in scoped_results
        ),
        "not_eligible": 0,
        "errors": sum(str(_result_verdict(item) or "").startswith("ERROR") for item in scoped_results),
    }


def _post_list_item(post: dict) -> dict:
    article = post.get("article") or {}
    summary = post.get("summary") or {}
    generated_at = (post.get("versions") or {}).get("generated_at")
    return {
        "article_id": article.get("article_id"),
        "title": article.get("title"),
        "publisher": article.get("publisher"),
        "url": article.get("url"),
        "status": "COMPLETED",
        "stage": "COMPLETED",
        "request_input": None,
        "created_at": generated_at,
        "published_at": article.get("posted_date"),
        "verified_at": generated_at,
        "summary": {
            "total_claims": summary.get("n_claims"),
            "matched": summary.get("n_verified"),
            "mismatched": summary.get("n_mismatch"),
            "unverified": summary.get("n_unverified"),
            "not_eligible": summary.get("n_not_eligible"),
            "errors": summary.get("n_error"),
        },
    }


def _execute_verification(article_id: str, source_article_id: str) -> None:
    record = get_record(article_id)
    if record is None:
        return
    try:
        claims = get_claims_for_verification(source_article_id)
        results = run_existing_pipeline(claims)
        record.update(
            status="COMPLETED",
            stage="COMPLETED",
            results=results,
            verified_at=datetime.now(timezone.utc).isoformat(),
            error=None,
        )
    except Exception as exc:
        record.update(
            status="FAILED",
            stage="FAILED",
            verified_at=datetime.now(timezone.utc).isoformat(),
            error=str(exc),
        )
    save_record(record)


def _url_article_id(url: str) -> str:
    normalized = url.strip().rstrip("/")
    return "A" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:8]


def _execute_url_verification(article_id: str, url: str) -> None:
    record = get_record(article_id)
    if record is None:
        return
    try:
        post = analyze_frontend_url(url)
        article = post.get("article") or {}
        generated_at = (post.get("versions") or {}).get("generated_at")
        record.update(
            title=article.get("title") or record.get("title"),
            publisher=article.get("publisher") or record.get("publisher"),
            published_at=article.get("posted_date") or record.get("published_at"),
            status="COMPLETED",
            stage="COMPLETED",
            verified_at=generated_at or datetime.now(timezone.utc).isoformat(),
            error=None,
        )
    except Exception as exc:
        record.update(
            status="FAILED",
            stage="FAILED",
            verified_at=datetime.now(timezone.utc).isoformat(),
            error=str(exc),
        )
    save_record(record)


@app.get("/api/pipeline/status")
def pipeline_status():
    return pipeline_readiness()


@app.get("/api/articles/duplicate")
def duplicate_article(url: Optional[str] = None, title: Optional[str] = None):
    post = find_frontend_post(url=url, title=title)
    if post is not None:
        article = post.get("article") or {}
        article_id = article.get("article_id")
        return {
            "exists": True,
            "article_id": article_id,
            "title": article.get("title"),
            "status": "COMPLETED",
            "verified_at": (post.get("versions") or {}).get("generated_at"),
            "result_url": f"/result/{article_id}",
        }
    normalized_url = (url or "").strip().rstrip("/")
    normalized_title = " ".join((title or "").split()).casefold()
    for article in list_records():
        same_url = normalized_url and str(article.get("url") or "").strip().rstrip("/") == normalized_url
        saved_title = " ".join(str(article.get("title") or "").split()).casefold()
        same_title = normalized_title and normalized_title == saved_title
        if same_url or same_title:
            return {
                "exists": True,
                "article_id": article["article_id"],
                "title": article.get("title"),
                "status": article.get("status"),
                "verified_at": article.get("verified_at"),
                "result_url": f"/result/{article['article_id']}",
            }
    return {"exists": False, "article_id": None, "result_url": None}


@app.post("/api/verify", status_code=202)
@app.post("/api/verifications", status_code=202)
def create_verification(payload: VerificationRequest, background_tasks: BackgroundTasks):
    input_type = payload.input_type.strip().upper()
    if input_type not in {"URL", "TITLE"}:
        raise HTTPException(status_code=422, detail="input_type은 URL 또는 TITLE이어야 합니다.")
    request_input = (payload.url if input_type == "URL" else payload.title or "").strip()
    if not request_input:
        raise HTTPException(status_code=422, detail="뉴스 URL 또는 기사 제목을 입력해주세요.")

    if input_type == "URL":
        bundled_post = find_bundled_post(url=request_input)
        if bundled_post is not None:
            publish_bundled_post(bundled_post)
            article = bundled_post.get("article") or {}
            article_id = article.get("article_id")
            return {
                "status": "COMPLETED",
                "stage": "COMPLETED",
                "article_id": article_id,
                "verdict_status": (bundled_post.get("summary") or {}).get("verdict_status"),
                "result_url": f"/result/{article_id}",
            }
        article_id = _url_article_id(request_input)
        created_at = datetime.now(timezone.utc).isoformat()
        publisher = urlparse(request_input).hostname or ""
        publisher = publisher.removeprefix("www.")
        record = {
            "article_id": article_id,
            "source_article_id": None,
            "input_type": input_type,
            "request_input": request_input,
            "url": request_input,
            "title": f"{publisher} 기사 검증 중" if publisher else "뉴스 기사 검증 중",
            "publisher": publisher,
            "published_at": None,
            "content": "",
            "paragraphs": [],
            "category": None,
            "status": "PROCESSING",
            "stage": "REQUESTED",
            "created_at": created_at,
            "verified_at": None,
            "results": [],
            "error": None,
        }
        save_record(record)
        background_tasks.add_task(_execute_url_verification, article_id, request_input)
        return {
            "status": "PROCESSING",
            "stage": "REQUESTED",
            "article_id": article_id,
            "result_url": f"/result/{article_id}",
        }

    fixture = find_fixture_article(
        url=request_input if input_type == "URL" else None,
        title=request_input if input_type == "TITLE" else None,
    )
    if fixture is None:
        raise HTTPException(
            status_code=422,
            detail="현재 Task1이 연결되지 않아 run01 fixture에 포함된 기사만 검증할 수 있습니다.",
        )

    article_id = f"V{uuid4().hex[:12]}"
    created_at = datetime.now(timezone.utc).isoformat()
    record = {
        "article_id": article_id,
        "source_article_id": fixture["article_id"],
        "input_type": input_type,
        "request_input": request_input,
        "url": fixture.get("url") or payload.url or "",
        "title": fixture.get("title") or request_input,
        "publisher": fixture.get("publisher") or "",
        "published_at": f"{fixture['posted_date']}T00:00:00+09:00" if fixture.get("posted_date") else None,
        "content": fixture.get("text") or "",
        "paragraphs": fixture.get("paragraphs") or [],
        "category": fixture.get("category"),
        "status": "PROCESSING",
        "stage": "REQUESTED",
        "created_at": created_at,
        "verified_at": None,
        "results": [],
        "error": None,
    }
    save_record(record)
    background_tasks.add_task(_execute_verification, article_id, fixture["article_id"])
    return {
        "status": "PROCESSING",
        "stage": "REQUESTED",
        "article_id": article_id,
        "result_url": f"/result/{article_id}",
    }


@app.get("/api/articles")
def api_articles(query: str = Query(default="")):
    term = query.strip().casefold()
    post_items = [_post_list_item(post) for post in list_frontend_posts()]
    post_ids = {item.get("article_id") for item in post_items}
    records = [
        item for item in list_records()
        if item.get("article_id") not in post_ids
    ]
    items = post_items + [
            {
                key: item.get(key)
                for key in (
                    "article_id", "title", "publisher", "url", "status", "stage",
                    "request_input", "created_at", "published_at", "verified_at", "error",
                )
            } | {"summary": _summary(item.get("results") or [], item.get("article_id"))}
            for item in records
        ]
    items = [
        item for item in items
        if not term or any(term in str(item.get(field) or "").casefold() for field in ("title", "publisher", "url"))
    ]
    items.sort(key=lambda item: item.get("created_at") or item.get("verified_at") or "", reverse=True)
    return {"items": items}


@app.get("/api/articles/{article_id}")
def api_article_detail(article_id: str):
    post = load_frontend_post(article_id)
    if post is not None:
        return post
    article = get_record(article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="검증 기사를 찾을 수 없습니다.")
    results = article.get("results") or []
    return {
        "status": article.get("status"),
        "article": article,
        "results": results,
        "summary": _summary(results, article_id),
    }
