# qiskit-resume v1 — 모듈 입출력 계약 (정밀 정의)

> 구조·흐름은 [`ARCHITECTURE.md`](ARCHITECTURE.md), 설계 근거는 [`DECISIONS.md`](DECISIONS.md).
> 이 문서는 **각 모듈이 밖으로 내놓는 함수·클래스의 시그니처, 입력·출력 타입, 오류, 부수효과, 불변식**의 정본입니다.
> 구현은 이 계약을 따르고, 계약을 바꿀 때는 이 문서를 먼저 고칩니다. ✅ = 구현됨.
> 검증: Qiskit 1.4.6 · 2.6.0 에서 전체 테스트 통과 (2026-10-22).

표기
- 타입은 Python 표기. `JSON` = `None | bool | int | float | str | list[JSON] | dict[str, JSON]`.
- `Codable` = `l0_disk.codec`가 저장할 수 있는 값 (JSON + `bytes` + `tuple` + `complex` + numpy 배열/스칼라).
- **부수효과** = 디스크에 쓰는 테이블. **불변식** = 항상 지켜야 하는 성질.
- 모든 시각은 UTC ISO-8601 문자열, 밀리초, `Z` 접미사 (`l0_disk.db.utc_now()`).

---

## 0. 공통 Payload 타입 (계층 사이를 오가는 데이터)

정의 위치 = 그 타입을 **만들어 내놓는** 모듈. 다른 모듈은 그 모듈의 `__init__`에서 import.

```python
BlobRef      = str                                   # l0_disk.blobs — SHA-256 hex, 64자
QuestionParts = Mapping[str, bytes]                  # l3_qiskit.circuits → l1_record.hashing
CallKind     = Literal["estimator", "sampler", "transpile", "function"]     # l1_record.journal
CallStatus   = Literal["submitted", "done", "failed", "invalid", "compacted"]  # l1_record.journal
RunStatus    = Literal["running", "finished", "crashed", "aborted"]         # l1_record.runs
EventLevel   = Literal["info", "warning", "error"]                          # l1_record.runs
Action       = Literal["replay", "reattach", "execute"]                     # l2_replay.replay
JobState     = Literal["queued", "running", "done", "failed", "cancelled", "unknown"]  # l3_qiskit.ibm
ErrorKind    = Literal["quota", "device", "circuit", "ttl", "unknown"]      # l3_qiskit.ibm

@dataclass(frozen=True)
class InputKey:                      # l1_record.hashing
    key_hash: str                    # 전체 질문 지문 (SHA-256 hex)
    parts: dict[str, str]            # 부분 이름 → 부분 지문

@dataclass(frozen=True)
class CallRecord:                    # l1_record.journal
    id: int
    run_id: int                      # 처음 기록한 run
    seq: int                         # 그 run 안에서의 호출 순번 (0부터, 사용자 코드 호출 순서)
    kind: CallKind
    key_hash: str
    occurrence: int                  # 같은 (kind, key_hash)가 몇 번째로 나왔는지 (0부터)
    parts: dict[str, str]
    status: CallStatus
    job_id: str | None
    backend: str | None
    inputs_ref: BlobRef | None       # 질문 원본 (선택 저장)
    result_ref: BlobRef | None       # 중립 형식 결과 (status == "done"일 때만 not None)
    usage_s: float | None            # QPU 사용 초 (모르면 None)
    duration_s: float | None         # 벽시계 소요 (함수 wrap의 느린 함수 추천용)
    created_at: str
    finished_at: str | None
    error: str | None

@dataclass(frozen=True)
class SnapshotRecord:                # l1_record.snapshots
    id: int
    run_id: int
    position: int                    # 스냅샷 시점까지 사용자 코드가 한 호출 수
    state_ref: BlobRef
    rng_ref: BlobRef | None
    created_at: str
    valid: bool

@dataclass(frozen=True)
class RunRecord:                     # l1_record.runs
    id: int
    started_at: str
    ended_at: str | None
    status: RunStatus
    host: str
    pid: int
    code_fingerprint: str | None     # 사용자 스크립트 SHA-256
    settings: dict[str, JSON]        # resume_run 인자 (D-010)
    env: dict[str, JSON]             # python/qiskit/qiskit-ibm-runtime/numpy 버전, 플랫폼

@dataclass(frozen=True)
class EventRecord:                   # l1_record.runs
    id: int
    run_id: int | None
    at: str
    level: EventLevel
    kind: str                        # 아래 §2.4 이벤트 종류
    detail: JSON | None

@dataclass(frozen=True)
class Divergence:                    # l2_replay.replay
    position: int                    # 어긋난 호출 순번
    kind: CallKind
    expected: CallRecord             # 기록상 이 순번에 있던 호출
    got: InputKey                    # 이번 실행의 질문
    changed_parts: list[str]         # 달라진 부분 이름 (정렬)

@dataclass(frozen=True)
class Decision:                      # l2_replay.replay
    action: Action
    position: int
    occurrence: int
    record: CallRecord | None        # replay/reattach면 not None; execute면 실패 기록 또는 None
    divergence: Divergence | None

@dataclass(frozen=True)
class Verdict:                       # l2_replay.divergence
    level: EventLevel
    message: str                     # 사용자에게 보여줄 문장
    action: Literal["continue", "stop"]
    invalidate_from: int | None      # continue일 때, 이 순번 이후 옛 기록을 무효 보관

@dataclass(frozen=True)
class UsageSummary:                  # l2_replay.budget
    window_days: int                 # 28
    used_window_s: float             # 창 안에서 이 도구로 쓴 QPU 초
    used_run_s: float                # 이번 run에서 쓴 QPU 초
    limit_s: float | None            # qpu_budget_s
    remaining_s: float | None        # limit_s - used_run_s (한도 없으면 None)
    next_recovery_at: str | None     # 창에서 가장 오래된 사용분이 빠지는 시각
    unknown_usage_calls: int         # usage를 아직 모르는 완료 호출 수

NeutralResult = dict[str, Codable]   # l3_qiskit.results — §4.2 형식
```

---

## 1. l0_disk — 디스크

### 1.1 `l0_disk.db` ✅

```python
FORMAT_VERSION: int = 1
def utc_now() -> str

class ExperimentDB:
    path: Path; conn: sqlite3.Connection; read_only: bool; journal_mode: str   # 항상 "delete"

    @classmethod
    def open_for_run(cls, path: str | os.PathLike) -> ExperimentDB
    @classmethod
    def open_read_only(cls, path: str | os.PathLike) -> ExperimentDB
    def tx(self) -> ContextManager[sqlite3.Connection]
    def ensure_schema(self, ddl: str) -> None
    def get_meta(self, key: str, default: JSON = None) -> JSON
    def set_meta(self, key: str, value: JSON) -> None
    def integrity_check(self) -> list[str]           # [] = 정상
    def close(self) -> None                          # context manager 지원

def backup_copy(src: str | os.PathLike, dst: str | os.PathLike) -> Path
def verify_file(path: str | os.PathLike) -> None
def running_info(path: str | os.PathLike) -> dict | None   # 실행 중이면 {host, pid, started_at}, 아니면 None
# 저널 모드는 어디서나 rollback journal(DELETE) 하나 — 로컬·네트워크·USB 구분 없이 같은 동작 (WAL은 네트워크에서 안전하지 않음)
# open_read_only는 URI(file:...?mode=ro)가 아니라 일반 경로 + PRAGMA query_only (URI는 UNC 경로를 거부)
```

| 함수 | 입력 | 출력 | 오류 | 부수효과 |
|---|---|---|---|---|
| `open_for_run` | 경로 (없으면 생성, 상위 폴더 생성) | 쓰기 가능 DB | `AlreadyRunningError`(다른 프로세스 실행 중, 메시지에 host·pid·시작 시각) · `FormatError`(더 새 형식) | `<path>.lock` 생성·잠금, `meta` 초기화 |
| `open_read_only` | 기존 파일 경로 | 읽기 전용 DB | `FileNotFoundError` · `FormatError` · `IntegrityError` | 없음 (실행 중 파일도 열림) |
| `tx` | — | 연결 (with 블록) | 블록 안 예외는 롤백 후 재전파 · 읽기 전용이면 `RuntimeError` | 블록 전체가 한 번에 커밋 |
| `ensure_schema` | `CREATE ... IF NOT EXISTS` 문 | — | — | 상위 모듈의 테이블 생성 (트랜잭션 밖에서 호출) |
| `backup_copy` | 원본, 대상(없어야 함) | 대상 경로 | `FileExistsError` | 일관된 사본 생성 (원본 실행 중이어도 안전) |
| `verify_file` | 경로 | — | `IntegrityError` (메시지에 `qresume copy` 안내) | 없음 |

**불변식**: 한 파일에 쓰는 프로세스는 최대 1개 · 커밋된 기록은 강제 종료·전원 차단에도 유지(synchronous=FULL) · 실행 중 삭제·정리 없음 · 잠금은 프로세스 종료 시 OS가 해제.
**소유 테이블**: `meta(key TEXT PK, value TEXT JSON)`.
**meta 키**: `format_version`, `created_at`, `package` (l0) · `seed`, `experiment_id`, `hash_version` (l1 runs/hashing이 기록).

### 1.2 `l0_disk.blobs` ✅

```python
def content_hash(data: bytes) -> BlobRef
class BlobStore:
    def __init__(self, db: ExperimentDB) -> None          # 테이블 생성 → 트랜잭션 밖에서 생성
    def put(self, data: bytes, *, kind: str = "raw") -> BlobRef   # 호출자의 tx 안에서
    def get(self, ref: BlobRef) -> bytes                  # 없으면 KeyError
    def has(self, ref: BlobRef) -> bool
```
**kind 값**: `question`, `result`, `circuit_qpy`, `state`, `rng`, `value`, `raw`.
**불변식**: 같은 bytes → 같은 BlobRef, 한 번만 저장. 저장된 bytes는 바뀌지 않음.
**소유 테이블**: `blobs(hash PK, kind, size, data)`.

### 1.3 `l0_disk.codec` ✅

```python
def encode(value: Codable, *, allow_pickle: bool = False) -> bytes
def decode(data: bytes) -> Codable
class EncodeError(TypeError): path: str               # 예: "value.opt.state[1]"
def canonical_floats(values: ArrayLike, *, sig_digits: int = 12) -> bytes   # 해시 입력용 (l1 hashing·l3 circuits 공용)
def canonical_scalar(x: float, *, sig_digits: int = 12) -> str
```
`canonical_floats`: 유효숫자 12자리 십진 텍스트 + shape. `-0.0`→`0`, NaN·inf 고정 표기. 같은 값 → 같은 bytes (PC·OS 무관).
| 지원 타입 | 복원 결과 |
|---|---|
| `None` `bool` `int` `float`(NaN·inf 포함) `str` `complex` `bytes` | 같은 타입·값 |
| `list` / `tuple` / `dict[str, …]` | 같은 컨테이너 (tuple 유지) |
| `np.ndarray` (object dtype 제외) | dtype·shape·비트 동일 |
| numpy 스칼라 | 0차원 배열 (dtype 유지). 단 `np.float64`는 Python float로 저장 |
| 그 외 | `EncodeError` (allow_pickle=True면 pickle) |

**오류**: 저장 불가 값 → `EncodeError`(위치 포함) · `decode`에 다른 데이터 → `ValueError`.
**불변식**: `decode(encode(x)) == x` (위 표 기준) · 같은 값 → 같은 bytes (dict 순서 포함 입력 순서 유지).

---

## 2. l1_record — 기록 모델 (저장·조회만, 판단 없음)

### 2.1 `l1_record.hashing` ✅

```python
HASH_VERSION: int = 1
def digest_parts(parts: QuestionParts) -> InputKey
def normalize_floats(values: ArrayLike, *, sig_digits: int = 12) -> bytes
def diff_parts(a: InputKey, b: InputKey) -> list[str]
```
| 함수 | 입력 | 출력 | 규칙 |
|---|---|---|---|
| `digest_parts` | 부분 이름 → bytes (이름은 비어 있지 않은 str) | `InputKey` | 부분 지문 = SHA-256(bytes). 전체 지문 = SHA-256(이름 정렬 순서로 `len(name)·name·part_hash` 연결 + `HASH_VERSION`) |
| `normalize_floats` | 실수/복소 배열 (임의 shape) | bytes | `l0_disk.codec.canonical_floats`와 동일 (유효숫자 12자리 십진 텍스트 + shape) |
| `diff_parts` | 두 InputKey | 다른 부분 이름 목록 (정렬, 한쪽에만 있는 이름 포함) | — |

**불변식**: 같은 입력 → 같은 지문 (프로세스·PC·OS 무관) · 부분 하나만 달라도 전체 지문 다름 · `HASH_VERSION`이 바뀌면 옛 기록과 매칭 안 됨(새 파일 권장).

### 2.2 `l1_record.journal` ✅

```python
class Journal:
    def __init__(self, db: ExperimentDB, blobs: BlobStore) -> None
    def record_submitted(self, *, run_id: int, seq: int, kind: CallKind, key: InputKey,
                         occurrence: int, job_id: str | None = None, backend: str | None = None,
                         inputs_ref: BlobRef | None = None) -> CallRecord
    def set_job(self, call_id: int, job_id: str, backend: str | None = None) -> CallRecord   # 재시도 시 새 job
    def record_done(self, call_id: int, *, result_ref: BlobRef, usage_s: float | None = None,
                    duration_s: float | None = None) -> CallRecord
    def record_failed(self, call_id: int, *, error: str) -> CallRecord
    def set_usage(self, call_id: int, usage_s: float) -> CallRecord                         # 나중에 보충
    def find(self, kind: CallKind, key_hash: str, occurrence: int) -> CallRecord | None     # invalid 제외
    def at_position(self, seq: int) -> CallRecord | None     # 가장 최근 유효 기록 중 해당 순번
    def get(self, call_id: int) -> CallRecord                # 없으면 KeyError
    def list_calls(self, *, include_invalid: bool = False) -> list[CallRecord]   # seq, id 순
    def invalidate(self, call_ids: Iterable[int]) -> list[int]                   # 지정 기록 무효 처리 (재시도 이력 보관)
    def invalidate_from(self, seq: int, *, reason: str = "") -> list[int]        # 무효 처리한 id들
    def max_seq(self) -> int | None
```
**상태 전이**: (없음) → `submitted` → `done` | `failed`; 어느 상태든 → `invalid`. `done`은 다시 바뀌지 않음(무효 처리만 가능). `qresume compact --before-snapshot`은 오프라인에서 `done` → `compacted`(결과 삭제)로 바꿈.
실패·미제출 기록을 다시 실행할 때는 그 기록을 `invalidate`하고 새 기록을 만든다 (이력 보존).
**불변식**: 유효 기록 중 `(kind, key_hash, occurrence)`는 유일 · `done`이면 `result_ref`가 존재하는 blob · 무효 처리는 삭제가 아님(D-001 보관).
**소유 테이블**: `calls` (§ARCHITECTURE 6). 무효 처리 이유는 `runs.log_event`가 아니라 호출자(l2)가 남김.

### 2.3 `l1_record.snapshots` ✅

```python
class SnapshotStore:
    def __init__(self, db: ExperimentDB, blobs: BlobStore) -> None
    def save(self, *, run_id: int, position: int, state: bytes, rng: bytes | None) -> SnapshotRecord
    def latest_valid(self, *, before_position: int | None = None) -> SnapshotRecord | None
    def load(self, snap: SnapshotRecord) -> tuple[bytes, bytes | None]          # (state, rng)
    def invalidate(self, snap_id: int) -> None
    def list_snapshots(self, *, include_invalid: bool = False) -> list[SnapshotRecord]
```
`latest_valid(before_position=p)` = position < p 인 유효 스냅샷 중 최신 (뒤에 검증할 기록이 있는 것만 쓰기 위해, D-012).
**소유 테이블**: `snapshots`.

### 2.4 `l1_record.runs` ✅

```python
class RunLog:
    def __init__(self, db: ExperimentDB) -> None
    def start(self, *, code_fingerprint: str | None, settings: dict[str, JSON],
              env: dict[str, JSON]) -> RunRecord          # 이전 "running" 행은 "crashed"로
    def finish(self, run_id: int, status: RunStatus) -> None
    def get(self, run_id: int) -> RunRecord
    def previous(self, run_id: int) -> RunRecord | None
    def list_runs(self) -> list[RunRecord]
    def fix_immutable(self, key: str, value: JSON) -> JSON     # 처음이면 저장, 같으면 그대로, 다르면 ImmutableSettingError
    def log_event(self, run_id: int | None, level: EventLevel, kind: str, detail: JSON = None) -> None
    def events(self, *, run_id: int | None = None, kind: str | None = None) -> list[EventRecord]
class ImmutableSettingError(ValueError): key: str; stored: JSON; given: JSON
```
**이벤트 종류 (`kind`)**: `new_experiment`, `settings_changed`, `code_changed`, `divergence`, `force_branch`, `calls_invalidated`, `snapshot_invalid`, `backend_changed`, `job_reattached`, `job_resubmitted`, `job_retry`, `budget_stop`, `quota_exhausted`, `run_crashed_detected`.
**소유 테이블**: `runs`, `events`. 불변 설정은 `meta`(`seed`, `experiment_id`).

---

## 3. l2_replay — 재생 엔진 (규칙·판단, Qiskit 모름)

### 3.1 `l2_replay.replay` ✅

```python
class ReplayEngine:
    def __init__(self, journal: Journal, *, run_id: int, start_position: int = 0) -> None
    position: int                                         # 다음 호출의 순번
    def next(self, kind: CallKind, key: InputKey) -> Decision
    def jump_to(self, position: int) -> None              # 스냅샷에서 시작: 그 앞 기록으로 occurrence 재계산
    def has_recorded_after(self, position: int) -> bool   # 스냅샷 검증 가능 여부
```
`next()` 판정 규칙 (호출할 때마다 `position` 1 증가, `(kind, key_hash)`별 occurrence 1 증가):

| 조건 | action | record | divergence |
|---|---|---|---|
| `find()` 결과가 `done` | `replay` | 그 기록 | None |
| `find()` 결과가 `submitted`이고 `job_id` 있음 | `reattach` | 그 기록 | None |
| `find()` 결과가 `failed`, 또는 `submitted`인데 `job_id` 없음 | `execute` | 그 기록 (재사용) | None |
| `find()` 없음, `at_position(position)`도 없음 (기록 끝) | `execute` | None | None |
| `find()` 없음, `at_position(position)`은 있음 (질문이 바뀜) | `execute` | None | `Divergence(...)` |

`compacted` 기록을 만나면 `LookupError` (스냅샷에서 시작해야 함).
**불변식**: 판단만 하고 기록하지 않음 (기록은 l4가 journal로) · 같은 기록·같은 질문 순서 → 항상 같은 판정.

### 3.2 `l2_replay.divergence` ✅

```python
def decide(div: Divergence, *, code_changed: bool, started_from_snapshot: bool,
           force: bool, on_divergence: Literal["warn", "stop"]) -> Verdict
def apply(verdict: Verdict, div: Divergence, *, journal: Journal, runs: RunLog, run_id: int) -> list[int]  # 무효 처리 id
```
| 조건 (위에서부터 먼저 맞는 것) | level | action | invalidate_from |
|---|---|---|---|
| `force=True` | info | continue | `div.position` |
| `code_changed` | warning | `on_divergence == "stop"`이면 stop, 아니면 continue | continue면 `div.position` |
| `started_from_snapshot` (스냅샷 이후 **기록된 구간 전체**에서 어긋남) | error | stop (메시지: 스냅샷 상태 누락 · `force=True` 안내) · l4가 그 스냅샷 무효 처리 | None |
| 그 외 (코드 같음, 처음부터 재생) | warning (seed 없는 RNG 안내) | `on_divergence`에 따름 | continue면 `div.position` |

`apply`: 이벤트 기록(`divergence`/`force_branch`) + continue면 `journal.invalidate_from()` + `calls_invalidated` 이벤트.

### 3.3 `l2_replay.budget` ✅

```python
class Budget:
    def __init__(self, journal: Journal, *, run_id: int, limit_s: float | None, window_days: int = 28) -> None
    def summary(self, *, now: str | None = None) -> UsageSummary
    def estimate(self, kind: CallKind, key: InputKey) -> float | None    # 같은 회로 부분 지문의 과거 usage 평균 ×1.2
    def can_submit(self, estimate_s: float | None) -> bool               # 한도 없으면 항상 True
```

---

## 4. l3_qiskit — Qiskit 경계 (변환만)

### 4.1 `l3_qiskit.circuits` ✅

```python
def question_parts(kind: Literal["estimator", "sampler"], pubs: Sequence[Any], *,
                   precision: float | None = None, shots: int | None = None,
                   options: Mapping[str, Any] | None = None) -> QuestionParts
def circuit_to_qpy(circuit: QuantumCircuit) -> bytes
def circuit_from_qpy(data: bytes) -> QuantumCircuit
def circuit_canonical_bytes(circuit: QuantumCircuit) -> bytes  # 구조 정규형 (아래)
def circuit_structure_hash(circuit: QuantumCircuit) -> str     # SHA-256(circuit_canonical_bytes)
def canonical_array(values) -> bytes                            # = l0 canonical_floats
def pub_shapes(kind, pubs, *, precision=None, shots=None) -> list[dict]   # selftest 가짜 결과용 shape·shots·creg
def backend_name(obj: Any) -> str | None                        # primitive / backend / batch에서. 로컬이면 None
```
`question_parts` 출력 부분 이름: `kind` · `pub{i}.circuit` (구조 정규형) · `pub{i}.params` (회로 파라미터 순서대로 canonical_array) · `pub{i}.observables` (estimator) · `pub{i}.precision` / `pub{i}.shots` · `options` (primitive의 default_precision/default_shots/options 등, 정렬된 JSON). pubs는 Qiskit `EstimatorPub.coerce` / `SamplerPub.coerce`로 정규화 후 분해.
**회로 정규형**: QPY bytes가 아니라 직접 만든 텍스트 — QPY에는 실행마다 바뀌는 Parameter UUID가 들어가기 때문. 큐비트·클래식 비트 수, 레지스터, global phase, 파라미터 이름, 명령마다 (이름, 파라미터(12자리), 큐비트·비트 인덱스, 조건, 하위 블록 지문), layout.
**불변식**: 같은 질문 → 같은 bytes (회로 객체가 새로 만들어져도) · 1e-12 미만 θ 차이는 같은 bytes.

### 4.2 `l3_qiskit.results` ✅

```python
NEUTRAL_VERSION: int = 1
def to_neutral(result: PrimitiveResult, *, kind: Literal["estimator", "sampler"],
               sampler_storage: Literal["raw", "counts"] = "raw") -> NeutralResult
def from_neutral(n: NeutralResult) -> PrimitiveResult
class UnsupportedResultError(ValueError)
```
NeutralResult 형식:
```python
{"v": 1, "kind": "estimator" | "sampler", "qiskit_version": str,
 "metadata": {...Codable...},
 "pubs": [  # 각 pub
   {"shape": [int...], "sampler": bool, "metadata": {...},
    "data": {name: {"type": "array", "value": ndarray}                       # estimator: evs, stds, …
                  | {"type": "bitarray", "bits": ndarray[uint8], "num_bits": int}   # sampler raw
                  | {"type": "counts", "counts": {str: int}, "num_bits": int}}}]}    # sampler counts
```
counts 모드는 shape `()` pub만 counts로 저장 (그 외는 raw로 저장).
**불변식**: `from_neutral(to_neutral(r))`의 `evs`·`stds`·측정 비트가 원본과 비트 동일 (raw 모드) · 형식 버전이 더 새로우면 `UnsupportedResultError`.

### 4.3 `l3_qiskit.ibm` ✅

```python
def available() -> bool                                     # qiskit-ibm-runtime 설치 여부
def is_runtime_job(job: Any) -> bool
def job_state(job: Any) -> JobState                         # INITIALIZING → "queued"
def classify_error(job: Any) -> ErrorKind                   # job.error_message() 기반
def job_usage_s(job: Any) -> float | None                   # usage()가 0이면 None (아직 모름)
def fetch_job(service: Any, job_id: str) -> Any | None      # 못 찾으면 None
def service_of(primitive: Any) -> Any | None
def job_backend_name(job: Any) -> str | None
def calibration_time(backend: Any) -> str | None           # backend.properties().last_update_date
```
`classify_error`(job, message=None): 메시지 키워드로 분류 — quota(quota, insufficient, usage limit…) · ttl(ttl, expired…) · circuit(isa, not supported, invalid, instruction…) · device(backend, maintenance, internal, calibration, unavailable…) · 그 외 unknown(재시도 안 함).

---

## 5. l4_api — 사용자 API

### 5.1 `l4_api.run` ✅

```python
def resume_run(path: str | os.PathLike, *,
               seed: int | None = None,                      # 불변. None이면 새 실험 때 무작위 생성
               force: bool = False,
               on_divergence: Literal["warn", "stop"] = "warn",
               on_backend_change: Literal["warn", "stop"] = "warn",
               qpu_budget_s: float | None = None,
               job_wait_timeout_s: float | None = None,
               job_max_retries: int = 5,
               sampler_storage: Literal["raw", "counts"] = "raw",
               use_snapshots: bool = True,
               slow_report_s: float = 10.0,                   # 이 이상 걸린 고전 구간을 종료 시 알림 (0 = 끔)
               quiet: bool = False,
               log_calls: bool = False) -> ResumeRun          # True: 호출마다 콘솔에 한 줄 (재생 구간은 100개마다 요약). 기록에는 저장 안 함

class ResumeRun:                                    # context manager
    path: Path; run_id: int; is_new: bool; seed: int; settings: dict[str, JSON]
    def snapshot(self, state: dict[str, Codable]) -> int          # 스냅샷 id
    def load_snapshot(self) -> dict[str, Codable] | None
    def usage(self) -> UsageSummary
    def __enter__(self) -> ResumeRun; def __exit__(...) -> None  # 정상 종료 finished, 예외 aborted

def get_active_run() -> ResumeRun                    # 없으면 NoActiveRunError
```
| 오류 | 언제 |
|---|---|
| `AlreadyRunningError` | 같은 파일을 다른 프로세스가 실행 중 |
| `ImmutableSettingError` | seed 등 불변 설정이 기록과 다름 |
| `DivergenceStop` | Verdict.action == stop (메시지에 force 안내) |
| `BudgetExceeded` | qpu_budget_s 도달 — 다음 제출 전에 깔끔히 중단 |
| `BackendChangedStop` | 장비 변경 + on_backend_change="stop" |
| `NoActiveRunError` | `resume_run` 블록 밖에서 wrap 객체·`resume.seed`·`resume.rng()` 사용 (정의: l4 rng) |
| `QuotaExhausted` · `JobWaitTimeout` | IBM 할당량 소진 / 큐 대기 시간 초과 — job은 보관, 다음 실행에서 재연결 (정의: l4 primitive) |

출력(stderr, `[qiskit-resume]` 접두, `quiet=True`면 info 생략): 새 실험 안내 · 설정 변경 · 경고 · 종료 요약(재생·재연결·실행 수, QPU 초) · 느린 고전 구간 위치(`slow_report_s`, 해당 위치를 함수로 묶어 wrap 권장).
코드 지문 = SHA-256(스크립트 파일 + 명령줄 인자). 인자가 바뀌어도 '의도적 변경'으로 봄.
CLI 연동 환경변수(사용자 API 아님): `QRESUME_MODE`(check|selftest), `QRESUME_FILE`, `QRESUME_REPORT`, `QRESUME_KILL_AT`.

### 5.2 `l4_api.rng` ✅

```python
def seed() -> int                                    # 활성 run의 seed (resume.seed)
def rng(name: str | None = None, *, seed: int | None = None) -> np.random.Generator   # resume.rng
def seed_for(name: str) -> int                       # 이름별 고정 정수 seed (resume.seed_for), uint32 범위
def apply_global_seed(seed: int) -> None             # random, np.random, algorithm_globals(있으면)
def capture_state() -> bytes                         # 전역 RNG + rng() 스트림 상태 (codec bytes)
def restore_state(data: bytes) -> None
def activate(seed: int) -> None; def deactivate() -> None   # l4 run이 호출 (run을 import하지 않기 위해)
class NoActiveRunError(RuntimeError)
```
- `rng()` (이름 없음): n번째 호출 = `np.random.default_rng([seed, n])` → 호출 순서에 의존 (중간에 호출을 끼우면 뒤가 밀림).
- `rng("name")`: 이름당 **하나의 Generator 객체** — 첫 호출에 `default_rng([seed, 1, H(name)])`(H = SHA-256 앞 8바이트)로 만들고, 이후 같은 이름은 같은 객체 반환. 그래서 반복문 안에서 `rng("jitter").random()`을 불러도 매번 다음 값(같은 값 반복 아님). 이름 사이 순서와 무관. 스냅샷에 이름별 상태 포함. 여러 난수가 필요하면 이 방식 권장.
- `rng(seed=k)`: `default_rng(k)`와 같음 (명시적 고정, 스냅샷에 포함 안 됨 — 사용자 소유 generator와 동일 취급).
- `seed_for("name")`: `SeedSequence([seed, 2, H(name)])`의 첫 uint32 → int seed를 받는 API용 (`seed_transpiler=` 등). 태그 1/2 덕분에 세 방식의 스트림은 겹치지 않음.
- `rng()`, `rng("name")`, `seed_for()`는 활성 run 안에서만 (없으면 `NoActiveRunError`). `rng(seed=k)`는 run 없이도 동작.
```python
```

### 5.3 `l4_api.wrap` ✅

```python
def wrap(obj: Any = None, *, name: str | None = None, allow_pickle: bool = False, backend: Any = None) -> Any
# obj 없이 호출하면 데코레이터 팩토리: @resume.wrap(allow_pickle=True). backend는 pass manager용 (이름 또는 backend 객체)
```
분기: `BaseEstimatorV2` → primitive(estimator) · `BaseSamplerV2` → primitive(sampler) · `PassManager`/`StagedPassManager` → passmanager · 그 외 callable → function (데코레이터로도 사용) · 나머지 → `TypeError`.
같은 객체를 두 번 감싸면 그대로 반환.

### 5.4 `l4_api.primitive` ✅

```python
class WrappedPrimitive:                              # 원래 primitive의 다른 속성은 그대로 통과
    def run(self, pubs: Sequence[Any], **kwargs: Any) -> WrappedJob
class WrappedJob:
    def result(self, timeout: float | None = None) -> PrimitiveResult
    def job_id(self) -> str | None
    def status(self) -> str
```
`WrappedEstimator(WrappedPrimitive, BaseEstimatorV2)` / `WrappedSampler(WrappedPrimitive, BaseSamplerV2)`: 라이브러리의 `isinstance(x, BaseSamplerV2)` 검사를 통과하도록 실제 하위 클래스로 만든다 (예: VQD의 ComputeUncompute).
스레드 안전: 판정·저장은 `run.db.lock`(RLock) 안에서 수행 — 라이브러리가 worker 스레드에서 primitive를 호출해도 됨 (D-018).

`run()`은 판정만 하고 즉시 반환, 실제 대기는 `result()`. 실행 시 job_id를 **결과 대기 전에** 기록 (D-008). 재생 시 `job_id()`는 기록된 값.

### 5.5 `l4_api.passmanager` ✅

```python
class WrappedPassManager:
    def run(self, circuits: QuantumCircuit | Sequence[QuantumCircuit], **kwargs) -> 같은 모양
```
캐시 키 = (원본 회로 정규형, `wrap(pm, backend=…)`의 장비 이름, pass manager 클래스, run 인자). 적중 시 기록된 QPY 반환 + Parameter를 이름으로 사용자 회로의 것과 다시 연결. backend를 안 주면 1회 경고. seed는 pass manager를 만들 때 `seed_transpiler=resume.seed`로 지정 권장 (첫 변환만 영향, 이후는 캐시).

```python
def pass_manager(backend: Backend | None = None, optimization_level: int | None = None,
                 **preset_kwargs) -> WrappedPassManager                         # resume.pass_manager
```
`resume.wrap(generate_preset_pass_manager(optimization_level, backend=backend, seed_transpiler=seed_for("transpile"), **preset_kwargs), backend=backend)`와 같음. `seed_transpiler`를 주면 그 값 사용. 내부 pass manager는 **처음 쓸 때** 생성(그때 활성 run의 seed 필요) → `resume_run` 밖에서 만들어 둬도 됨. backend가 없으면 캐시 키의 장비 이름은 `"local"`.

### 5.6 `l4_api.function` ✅

```python
class WrappedFunction:
    def __call__(self, *args: Codable, **kwargs: Codable) -> Codable
```
키 = (모듈.이름 + 소스 코드 SHA-256 앞 16자, encode된 인자). 인자·반환값이 Codable 아니면 `EncodeError`. 실행 시간을 `duration_s`로 기록. 메서드에도 사용 가능.

반환값·인자에 `SparsePauliOp`(파라미터 없는 것)과 `QuantumCircuit`도 가능 (D-020): 저장은 태그 dict(`qiskit_resume.type`) — 연산자는 라벨+계수, 회로는 QPY; 기록 키용 인자 회로는 구조 정규형(QPY의 무작위 id 회피). 그 밖의 객체는 `allow_pickle=True`.

### 5.7 `l4_api.snapshot` ✅

```python
def save(run: ResumeRun, state: dict[str, Codable]) -> int          # ResumeRun.snapshot 구현
def load(run: ResumeRun) -> dict[str, Codable] | None               # ResumeRun.load_snapshot 구현
```
저장 시 RNG 상태 자동 포함 · 불러올 때 뒤에 검증할 기록이 있는 최신 유효 스냅샷만 사용 · 불러온 뒤 첫 호출이 어긋나면 `divergence`(started_from_snapshot=True).

---

### 5.8 `l4_api.device` ✅

```python
def backend(service: QiskitRuntimeService | None = None, name: str | None = None,
            **least_busy_filters) -> Backend                                    # resume.backend
```
- 활성 run 필요. `service` 기본값 `QiskitRuntimeService()`.
- 기록된 장비(meta `backend`)가 없으면: `name` 또는 `service.least_busy(operational=True, simulator=False, **filters)` → 기록.
- 기록이 있으면: `name`을 안 주면 기록된 장비 반환(가장 한가한 장비가 바뀌어도). `name`이 기록과 다르면 의도적 변경 → `on_backend_change`에 따라 경고 또는 `BackendChangedStop`.
- 기록된 장비를 지금 쓸 수 없으면 `RuntimeError` (나중에 다시 실행하거나 `name=`으로 바꾸라는 안내).

## 6. tools

### 6.1 `tools.cli` ✅ — `qresume <명령> [옵션] [-- 스크립트 인자]` (종료 코드 0 = 정상, 1 = 문제 발견·거부, 2 = 사용법 오류)

`python -m qiskit_resume`로도 실행. check는 파일의 일관된 사본으로 작업하므로 원본(실행 중이어도)을 건드리지 않음. pack/unpack은 Python 3.14+면 zstd(`.zst`), 아니면 gzip(`.gz`).

| 명령 | 입력 | 출력 |
|---|---|---|
| `status FILE [--json]` | 실험 파일 | 장비, run 수, 호출 수(상태별), 스냅샷, QPU 사용, 최근 경고 |
| `check SCRIPT --file FILE` | 스크립트 + 파일 | 무결성, 재생 일치 수, 첫 불일치 위치·부분, 스냅샷 검증, 코드 지문, 다음 실제 실행 지점 (IBM 전송 없음) |
| `selftest SCRIPT [--points N]` | 스크립트 | 중단 지점별 일치/불일치 |
| `copy SRC DST` | 실행 중 파일 가능 | 일관된 사본 |
| `export FILE [--format json\|csv] [--group-by backend\|calibration\|run]` | 파일 | 호출·결과 내보내기 |
| `pack FILE` / `unpack FILE.qresume.zst` | 파일 | 압축본 / 복원본 (실행 중이면 거부) |
| `quota FILE` | 파일 | UsageSummary |
| `fork SRC --at-snapshot N --to DST` | 파일 | 갈래 파일 |
| `compact FILE [--keep-snapshots 5] [--drop-invalid] [--before-snapshot ID]` | 파일 (실행 중이면 거부) | 정리 결과. `--before-snapshot`은 그 이전 결과를 지우고 기록을 `compacted`로 → 이후 스냅샷에서만 재개 가능 |

### 6.2 `tools.selftest` ✅

```python
def run_selftest(script: str | os.PathLike, *, args: Sequence[str] = (), points: int = 3, timeout_s: float = 600) -> SelftestReport
def report_text(r: SelftestReport) -> str; def report_json(r: SelftestReport) -> str
@dataclass
class SelftestReport: ok: bool; reference_calls: int; points: list[SelftestPoint]; error: str | None
@dataclass
class SelftestPoint: kill_at_call: int; ok: bool; first_mismatch: int | None; changed_parts: list[str]; hint: str | None
```

동작: 양자 결과는 **가짜(질문 해시로 정해지는 결정적 값, 올바른 shape)** 로 대체 → 시뮬레이터·QPU 불필요, 큐비트 수 무관. 변환·wrap 함수는 실제 실행.
기준 실행 → 지점 k마다 (같은 seed의 새 파일) k번째 호출에서 강제 종료(exit 77) → 재실행 → 질문 순서가 기준과 같고 divergence 이벤트가 없어야 통과.
