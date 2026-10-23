# qiskit-resume

**어떤 Qiskit Primitives 워크플로든 중간에 끊겨도 이어서 실행 — 이미 쓴 QPU 시간을 다시 쓰지 않습니다.**

Estimator / Sampler를 `resume.wrap`으로 감싸고 `resume_run(...)` 안에서 실행하세요. 할당량 소진,
PC 종료, 네트워크, Ctrl+C로 멈추면 **같은 명령을 다시 실행**하면 됩니다.

- 끝난 호출은 로컬 `.qresume` 파일에서 **즉시 재생**
- IBM에서 아직 돌고 있던 job은 다시 제출하지 않고 **재연결**
- 새 작업만 QPU 사용

알고리즘 상태가 아니라 **호출**을 기록하기 때문에 VQE, QAOA, 샘플링, 직접 짠 루프 등 무엇이든 됩니다.
재실행하면 파이썬 코드가 처음부터 돌면서 기록된 답을 받아 자기 상태(optimizer 기억 포함)를 다시 만들고,
기록이 끝나는 지점부터 실제로 실행합니다. IBM Quantum Open Plan(28일 rolling 10분)을 1순위로 설계했습니다.

```python
import qiskit_resume as resume

estimator = resume.wrap(EstimatorV2(mode=backend))        # 로컬이면 StatevectorEstimator()

with resume.resume_run("vqe.qresume", qpu_budget_s=120):
    theta0 = resume.rng().uniform(-3.14, 3.14, n)          # 재생되는 랜덤
    res = minimize(lambda x: float(estimator.run([(isa, H, x)]).result()[0].data.evs), theta0, method="COBYLA")
```

## 설치

```bash
pip install qiskit-resume            # Qiskit 1.4 이상 (1.4·2.6에서 검증), Python 3.10 이상
pip install "qiskit-resume[ibm]"     # IBM job 재연결·QPU 사용량
```

## 무엇을 감싸나

| `resume.wrap(...)` | 기록 | 재생 시 |
|---|---|---|
| `EstimatorV2` / `SamplerV2` (로컬·IBM) | 질문 지문, job id(결과 대기 전), 결과, QPU 초 | 파일에서 결과 / 돌던 IBM job 재연결 |
| pass manager — `resume.wrap(pm, backend=backend)` | (원본 회로, 장비)별 변환 회로 | 캘리브레이션이 바뀌어도 같은 회로 |
| 느리고 부수효과 없는 함수 — `@resume.wrap` | 인자별 반환값 | 파일에서 값 |

## 요령

- **랜덤:** `resume.rng()` / `resume.seed` 사용. 전역 `random`·`numpy`·`algorithm_globals`는 자동 고정.
- **코드 수정:** 재생 중 질문이 달라지면 경고 후 그 지점부터 실제 실행 (`on_divergence="stop"`이면 중단). 이전 기록은 보관.
- **스냅샷(선택):** `run.snapshot({...})` / `run.load_snapshot()`. 루프에 필요한 변수를 **전부** 넣으세요. 빠지면 감지해서 알려줍니다.
- **예산:** `qpu_budget_s`에 닿기 전 job 제출 직전에 깔끔히 멈춤.
- **장비 변경:** 감지·기록·경고 (`on_backend_change="stop"`이면 중단).

## QPU 쓰기 전에

```bash
qresume selftest my_script.py -- my.qresume                 # 가짜 결과로 여러 지점 강제 종료·재개 검증 (QPU 0)
qresume check my_script.py --file my.qresume -- my.qresume  # 이 파일로 이어갈 수 있는지 (IBM 전송 없음)
qresume status my.qresume
```

그 밖에 `copy`(실행 중 안전 복사), `export`, `quota`, `fork`(스냅샷에서 갈래), `pack`/`unpack`, `compact`.

## 한계

- 회로 하나의 실행 도중은 멈출 수 없음 (양자 상태 저장 불가) → 끊김 단위는 primitive 호출 1회.
- 시간·외부 데이터처럼 seed로 고정할 수 없는 동작은 재생이 어긋날 수 있음 (어디서인지 알려줌).
- Primitives를 거치지 않는 계산(`backend.run`, `Statevector` 직접 계산)은 기록 안 됨.

문서: `docs/DECISIONS.md`(왜) · `docs/ARCHITECTURE.md`(구조·흐름) · `docs/CONTRACTS.md`(모듈 입출력). 라이선스 MIT.
