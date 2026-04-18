# 프로젝트: {프로젝트명}

## 기술 스택
- {프레임워크 (예: Next.js 15)}
- {언어 (예: TypeScript strict mode)}
- {스타일링 (예: Tailwind CSS)}

## CRITICAL 규칙
- CRITICAL: {절대 지켜야 할 규칙 1 (예: 모든 API 로직은 app/api/ 라우트 핸들러에서만 처리)}
- CRITICAL: {절대 지켜야 할 규칙 2 (예: 클라이언트 컴포넌트에서 직접 외부 API를 호출하지 말 것)}
- CRITICAL: 새 기능 구현 시 반드시 테스트를 먼저 작성하고, 테스트가 통과하는 구현을 작성할 것 (TDD)
- CRITICAL: 커밋 메시지는 conventional commits 형식을 따를 것 (feat:, fix:, docs:, refactor:)

## 문서 인덱스

작업 전 아래 문서를 참조하라. 필요한 문서만 골라 읽는다.

| 문서 | 내용 | 언제 읽는가 |
|------|------|------------|
| [docs/PRD.md](docs/PRD.md) | 무엇을 만드는가 — 목표, 핵심 기능, MVP 범위 | 기능 추가 전 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 어떻게 만드는가 — 디렉토리 구조, 레이어, 데이터 흐름 | 파일 생성/수정 전 |
| [docs/ADR.md](docs/ADR.md) | 왜 이렇게 만드는가 — 기술 결정 이유, 트레이드오프 | 기술 스택 선택 시 |
| [docs/UI_GUIDE.md](docs/UI_GUIDE.md) | UI 규칙 — 색상, 컴포넌트, 디자인 원칙 | UI 작업 시 |

## 명령어
{개발 서버 커맨드}   # 예: npm run dev / ./gradlew bootRun / flutter run
{빌드 커맨드}       # 예: npm run build / ./gradlew build / flutter build
{린트 커맨드}       # 예: npm run lint / ./gradlew checkstyleMain / flutter analyze
{테스트 커맨드}     # 예: npm test / ./gradlew test / flutter test

## 하네스 커맨드
python3 scripts/execute.py {task-name}          # phase 순차 실행
python3 scripts/execute.py {task-name} --push   # 실행 후 브랜치 push
python3 scripts/show_logs.py                    # 전체 실행 이력 요약
python3 scripts/show_logs.py {task-name}        # 특정 phase 상세 로그
