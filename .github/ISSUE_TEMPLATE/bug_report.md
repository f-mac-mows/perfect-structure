---
name: "\U0001F41B Bug Report (시스템 오류)"
about: bug report template
title: "[Bug] 오류 요약 (예: 특정 뉴스 포맷 크롤링 시 JSON 파싱 에러 발생)"
labels: bug
assignees: f-mac-mows

---

---
name: Bug Report (시스템 오류)
description: AI 추론 오류, 파이프라인 병목, 웹 에러 등 발생한 문제를 리포트합니다.
title: "[Bug] 오류 요약"
labels: ["bug"]
assignees: ""
---

## 🚨 문제 상황 (Context)
> 버그가 발생한 상황과 에러 현상(AI 오작동, 웹 크래시 등)을 설명해주세요.

- 예시: AI의 팩트체크 결과가 지정된 JSON 포맷을 벗어나 문자열로 반환되면서 백엔드 파싱 코드가 터집니다.

## 🔄 재현 경로 (Steps to Reproduce)
1. 프론트엔드 검증창에 특정 뉴스 URL 입력
2. AI 팩트체크 모듈에 요청 전송
3. AI가 응답 본문에 마크다운 기호를 섞어서 반환
4. 백엔드에서 JSON parsing exception 발생

## 💥 예상 결과 vs 실제 결과
- **예상 결과**: AI가 순수 JSON 포맷 데이터만 반환하여 정상적으로 파싱되어야 함.
- **실제 결과**: AI 출력에 자연어가 섞여 들어와 500 에러 발생.

## 📋 에러 로그 / AI Raw Response
> 터미널 로그 또는 AI가 뱉은 원본 데이터를 첨부해주세요.

```text
// 에러 로그를 여기에 붙여넣으세요.
