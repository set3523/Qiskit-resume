# qiskit-resume v1 재작성 계획

> 근거: `docs/DECISIONS.md` (D-001 ~ D-015). 기존 코드(`seq_*.json` 체크포인트 방식)는 구조가 달라 **새로 작성**.

## 패키지 구조 (계층 · 모듈 · 입출력 계약 → `docs/ARCHITECTURE.md` 가 정본)

```
qiskit_resume/
├── l0_disk/     db · blobs · codec                         디스크 (Qiskit 무관)
├── l1_record/   hashing · journal · snapshots · runs       기록 모델 (저장/조회만)
├── l2_replay/   replay · divergence · budget               재생 엔진 (규칙/판단)
├── l3_qiskit/   circuits · results · ibm                   Qiskit 경계 (변환만)
├── l4_api/      run · rng · wrap · primitive · passmanager · function · snapshot   사용자 API
└── tools/       cli · selftest
tests/<계층>/test_<모듈>.py      모듈 단독 테스트
tests/test_layers.py             계층 순서·모듈 의존 표 자동 검사
```

- 모듈 폴더 = `__init__.py`(IN/OUT 계약 docstring + 공개 이름) + `core.py`(구현).
- 아래 계층만, 의존 표에 적힌 모듈만 import. Qiskit은 l3·l4·tools에서만.

## 단계 (순서대로, 각 단계 끝에 테스트)

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **P0 준비** ✅ | pyproject 갱신(MIT, 의존성 D-015), LICENSE, 기존 코드 ver1/ 보관, 계층 구조·계약 문서·의존 검사 테스트 | 설치·import 성공 |
| **P1 저장** ✅ | l0_disk: db · blobs · codec | 쓰기 중 강제 종료해도 파일 정상, 동시 실행 차단, 실행 중 읽기 가능 |
| **P2 해시·기록** ✅ | hashing · journal | 같은 입력 = 같은 해시, 순서 바뀌어도 매칭 |
| **P3 어댑터** ✅ | results · circuits | 로컬 Estimator/Sampler 결과 → 저장 → 복원 결과 동일 |
| **P4 핵심 런타임** ✅ | run · wrap(primitive) · rng | **🎯 마일스톤 1: 로컬 VQE를 중간에 죽이고 재실행 → 끊김 없는 실행과 결과 동일** |
| **P5 고전·변환** ✅ | passmanager · function · 느린 함수 추천 | 변환 캐시 재사용, 함수 결과 재생 |
| **P6 스냅샷·불일치** ✅ | snapshot · replay 규칙 · force | 누락 스냅샷 검출, 코드 수정 시 warning, force 갈래 |
| **P7 IBM** ✅ (마일스톤 2 대기) | job_id 선기록·재연결·재시도·usage·예산 | 가짜 IBM job으로 모든 상태(DONE/QUEUED/ERROR…) 테스트 → **마일스톤 2: 실제 IBM 소규모 실행(사용자 계정)** |
| **P8 CLI** ✅ | 10개 명령 | 각 명령 동작 테스트 |
| **P9 selftest** ✅ | 로컬에서 여러 지점 강제 중단·재개 비교 | 일부러 넣은 seed 버그·스냅샷 누락을 잡아냄 |
| **P10 마무리** ✅ | README(한/영) 재작성, examples 재작성, 여러 Qiskit 버전 자동 테스트 | PyPI 배포 준비 완료 |

## 작업 방식

- 코드는 클라우드 작업공간에서 작성·테스트(qiskit 설치) 후 폴더에 반영.
- 단계마다 결과 보고 → 확인 후 다음 단계.
- git 커밋은 사용자 PC에서 직접 (현재 세션은 PC 쪽 셸 사용 불가).
- 실제 IBM 실행(P7 마일스톤 2)은 사용자 계정·토큰이 필요하므로 사용자가 실행.

## 폴더 배치

```
qiskit-resume/          (git 저장소 루트)
├── ver1/               v0 체크포인트 방식 코드 보관 (README_VER1.md)
└── ver2/               ← v1 작업 폴더 (이 문서가 있는 곳). pip install -e ver2[dev]
```

## 정리 대기 (이 세션에서 PC 쪽 삭제 불가 — 직접 삭제 필요)

루트에 남아 있는 아래 항목은 ver1/ 또는 ver2/에 사본이 있으므로 지워도 됩니다:
- `legacy/` → `ver1/`로 대체
- `qiskit_resume/`, `tests/`, `examples/`, `chk/`, `pyproject.toml`, `LICENSE` → v0 코드는 `ver1/`, 새 코드는 `ver2/`
- `docs/` (DECISIONS·ARCHITECTURE·CONTRACTS·REWRITE_PLAN → `ver2/docs/`, WORKFLOW_AND_GAPS·workflow-gaps.html → `ver1/docs/`)
- `README.md`, `README_ko.md`, `temp.md` → `ver1/`에 사본 (새 README는 P10에서 `ver2/`에 작성)
- `qiskit_resume.egg-info/`, `__pycache__/` (빌드 부산물)
