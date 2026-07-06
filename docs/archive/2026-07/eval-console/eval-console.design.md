# eval-console Design Document

> **Summary**: 읽기 전용 /evals API + 신뢰 배지 + 단일 파일 대시보드 상세 설계
>
> **Project**: any2agent
> **Author**: jhchoi
> **Date**: 2026-07-06
> **Status**: Draft
> **Planning Doc**: [eval-console.plan.md](eval-console.plan.md)

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | eval 결과가 CLI/파일에만 있어 웹 사용자는 검증 상태를 볼 수 없음 |
| **WHO** | 채팅 UI 사용자, 비개발 이해관계자 |
| **RISK** | stale 데이터 — /evals는 요청 시마다 파일 재읽기 |
| **SUCCESS** | 배지→대시보드로 rate·추세·fix·lessons 확인, 쓰기 경로 0 |
| **SCOPE** | GET /evals, 배지, /evals/ui — 실행 트리거·인증 없음 |

## 1. Architecture (선택: Minimal Changes)

3안 비교 결과 **Option A — Minimal Changes** 채택: 데이터 계층(history/lessons)이 이미
존재하므로 서버에 GET 2개 + HTML 1개 + 배지 fetch만 추가. Clean(별도 evals 서비스
레이어)은 이 규모에서 과설계, 균형안과의 차이도 없음.

```
GET /evals  ──▶ history.load(state_dir, 20)   ← 요청 시마다 재읽기 (stale 방지)
            ──▶ lessons.load(lessons_path)
            ──▶ evals.json 존재 시 태스크 수
GET /evals/ui ──▶ server/web/evals.html (단일 파일, /evals fetch)
chat.html 배지 ──▶ fetch /evals → 헤더에 ✅ 0.88 · 2 runs (클릭: /evals/ui 새 탭)
```

## 2. API Contract — `GET /evals`

```json
{
  "evaluated": true,
  "project": "notes-api",
  "latest": {"ts": 1751772000, "rate": 0.5, "rated": 2, "passed": false,
             "failed": ["fb-read-2"],
             "fixes": [{"task_id": "fb-read-2", "class": "wrong_tool",
                        "guidance": "For requests like ..., use get__health ..."}]},
  "trend": "rate 0.50 (prev 1.00 ▼0.50, 2 runs)",
  "history": [{"ts": ..., "rate": ..., "rated": ..., "passed": ..., "failed": [...]}],
  "lessons": [{"task_id": "...", "class": "...", "guidance": "..."}],
  "tasks_total": 2
}
```

- 데이터 없음/파손: `{"evaluated": false, "project": "..."}` — 항상 200, 500 금지.
- `fixes`는 history 엔트리에 저장(FR-02): `history.append(state_dir, rep, fixes=built)`
  — `fixes` kwarg 기본값 `None`이라 기존 호출 하위 호환이지만, cli/connect 두 호출부
  모두 lessons build 결과를 전달하도록 갱신.

## 3. UI Specification

### 3.1 배지 (chat.html 헤더)

- 위치: 모델 선택기 왼쪽. 마크업: `<a id="evalBadge" href="/evals/ui" target="_blank">`
- 상태: `✅ 0.88 · 3 runs`(passed) / `❌ 0.50 · 2 runs`(failed) / `— not evaluated`
- 로직: 기존 `/info` fetch와 별개로 `fetch('/evals')` 1회, 실패 시 배지 숨김(콘솔 에러 금지)

### 3.2 `/evals/ui` — evals.html (단일 파일, chat.html 스타일 변수 재사용)

섹션 순서 (사용자 확인 우선순위 그대로):
1. **헤더**: 프로젝트명 + 최신 상태 큰 배지 + trend 문자열
2. **What to fix** (실패 있을 때만): 최신 실행의 fixes — task_id, class 칩, guidance 1줄
3. **History**: 최근 20회 표 (시각, rate, rated, PASS/FAIL, failed 목록) + 인라인 SVG 스파크라인
4. **Active lessons**: guidance 목록 + "이 지침은 매 대화에 주입됩니다" 설명
5. 데이터 없으면: "Not evaluated yet — run `any2agent eval --project {p}`" 안내만

## 4. Module Changes

| File | Change | LOC |
|------|--------|----|
| `evals/history.py` | `append(..., fixes=None)` — 엔트리에 `fixes` 포함(빈 리스트 생략) | +4 |
| `cli.py` | built(lessons) 계산을 append보다 먼저로 이동, `fixes=` 전달 | ~5 |
| `connect.py` | `_eval_gate` 동일 갱신 | ~3 |
| `server/app.py` | `GET /evals`(요청 시 재읽기), `GET /evals/ui` | +40 |
| `server/web/evals.html` | 신규 대시보드 | ~180 |
| `server/web/chat.html` | 헤더 배지 + fetch | +15 |
| `pyproject.toml` | package-data는 `web/*.html` glob이라 무변경 확인 | 0 |

## 5. Error Handling

| 상황 | 동작 |
|------|------|
| history/lessons/evals.json 없음 | `evaluated:false` 200 |
| 파일 파손 | 기존 모듈의 skip/빈 목록 동작 그대로 → 부분 데이터로 응답 |
| /evals fetch 실패(UI) | 배지 숨김, 대시보드는 "failed to load" 1줄 |

## 6. Test Plan

- `GET /evals`: FastAPI TestClient — 데이터 있음/없음/파손 3경로, 요청 간 파일 갱신 반영(stale 방지)
- `history.append(fixes=)` 하위 호환: fixes 없이 호출 시 기존 스키마 유지
- cli/connect 호출부가 fixes를 전달하는지 (기존 --fix 테스트 확장)
- 브라우저: Playwright로 배지 표시 + /evals/ui 렌더 (수동 검증 단계)

## 7. Implementation Order

1. [ ] history.append fixes + cli/connect 갱신 + 테스트
2. [ ] GET /evals + 테스트 (TestClient)
3. [ ] evals.html + /evals/ui 라우트
4. [ ] chat.html 배지
5. [ ] Playwright 실동작 검증
