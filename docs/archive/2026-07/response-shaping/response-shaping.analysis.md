# response-shaping — Gap Analysis

> **Design Doc**: [response-shaping.design.md](response-shaping.design.md)
> **Analyzed**: 2026-07-06 (gap-detector) · 라이브 검증: 422 힌트+서버 detail, 배열 셰이핑, v1→v2 승격

## Match Rate: 94.4% ✅ → post-fix ~100%

`(40 full + 5 partial × 0.5) / 45 = 0.944`. FR 6/6 구현(2건 partial), 누락 0.

## 발견 및 조치 (동일 세션)

| # | 발견 | 심각도 | 조치 |
|---|---|:---:|---|
| 1 | `_meta.truncated {shown,total}` 구조 누락 (hint 문자열에만 존재) | Med | shape가 truncations 반환, render가 `_meta.truncated` 부착 + 테스트 |
| 2 | `confirm_and_run`이 response_format을 pop하지 않음 (실경로는 run_chat pop으로 방어되나 직접 호출 시 백엔드 유출 가능) | Med | 방어 pop 추가 + Spy adapter 테스트 |
| 3 | detailed 모드도 긴 문자열 마커 절단 (설계 문구와 불일치) | Low | 설계를 코드 기준으로 정정 (필드 보존이 본질, 문자열 마커는 양 모드 공통) |
| 4 | unknown_tool이 transport 힌트를 받음 (오유도) | Low | 전용 힌트("pick from the tool list / search_tools") + 기타 로컬 코드는 무힌트, transport 판별 유지 |
| 5 | pop 테스트가 시뮬레이션 | Low | confirm 경로는 실테스트로 대체(#2), run_chat pop은 코드 경로 검증됨 |
| 역방향 | ours-set 잡음 억제·renamed carry-forward·_fit·에러 본문 셰이핑 | 긍정 | 설계 §2.1/§2.3/§4에 문서화 |

**Post-fix: 90/90 tests.** 라이브 재확인 항목: 실제 FastAPI 422 → 힌트+서버 detail 동봉,
40개 배열 → 10개 + `_meta`, v1 아티팩트 v2 승격 시 renamed=0·잡음 0.

## 잔여 (정직 보고)
- 데모 API가 스텁이라 404 형제 제안은 단위 테스트로만 검증 (라이브 404 불가)
- 페이지네이션 커서·필드 프로젝션은 계획대로 후속
