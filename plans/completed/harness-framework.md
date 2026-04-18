# Plan: harness-framework

## 목표
Claude Code를 위한 자율 실행 하네스 프레임워크 구축

## 범위
- execute.py: step 자동 실행, 자가교정, circuit breaker, 규칙 진화
- show_logs.py: 터미널 로그 뷰어
- test_execute.py: 87개 단위 테스트
- check.sh, settings.json, harness.md, CLAUDE.md

## 접근 방식
Python StepExecutor 클래스 + Claude -p 서브세션 워커/리뷰어 분리

## 예상 Step 수
완료
