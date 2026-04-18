# 아키텍처

## 디렉토리 구조
```
예시:
src/
├── app/               # 페이지 + API 라우트
├── components/        # UI 컴포넌트
├── types/             # TypeScript 타입 정의
├── lib/               # 유틸리티 + 헬퍼
└── services/          # 외부 API 래퍼
```

## 패턴
{사용하는 디자인 패턴 (예: Server Components 기본, 인터랙션이 필요한 곳만 Client Component)}

## 데이터 흐름
```
{데이터가 어떻게 흐르는지 (예:
사용자 입력 → Client Component → API Route → 외부 API → 응답 → UI 업데이트
)}
```

## 상태 관리
{상태 관리 방식 (예: 서버 상태는 Server Components, 클라이언트 상태는 useState/useReducer)}

## 에러 처리
{에러 처리 전략 (예: API 에러는 ErrorBoundary로 캐치, 네트워크 에러는 toast로 표시)}

| 에러 유형 | 처리 방식 | 사용자에게 보여주는 것 |
|-----------|-----------|----------------------|
| {예: API 응답 4xx} | {예: 에러 메시지 파싱 후 throw} | {예: toast 알림} |
| {예: 네트워크 오류} | {예: retry 1회 후 실패 처리} | {예: 재시도 버튼} |
| {예: 예상치 못한 예외} | {예: ErrorBoundary catch} | {예: fallback UI} |

## 테스트 전략
{테스트 방침 (예: 비즈니스 로직은 단위 테스트, API 연동은 통합 테스트, UI는 E2E 최소화)}

| 테스트 유형 | 대상 | 도구 |
|-------------|------|------|
| {예: 단위 테스트} | {예: 유틸 함수, 서비스 레이어} | {예: Jest} |
| {예: 통합 테스트} | {예: API 라우트} | {예: Supertest} |
| {예: E2E 테스트} | {예: 핵심 사용자 플로우} | {예: Playwright} |
