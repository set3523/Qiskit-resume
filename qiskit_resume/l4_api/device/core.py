"""resume.backend(): the device of an experiment, chosen once and recorded (D-007, D-020).

See docs/CONTRACTS.md section 5.8.
"""

from __future__ import annotations

from typing import Any

from qiskit_resume.l4_api.run import get_active_run


def backend(service: Any = None, name: str | None = None, **least_busy_filters: Any) -> Any:
    """Return the experiment's device, so a rerun never silently switches hardware.

    First run: ``name`` if given, else ``service.least_busy(operational=True, simulator=False,
    **least_busy_filters)``; the choice is recorded in the experiment file.
    Reruns: the recorded device is returned again (``name`` may be omitted). Passing a different
    ``name`` switches on purpose; that is reported like any device change (on_backend_change).
    Must be called inside ``with resume_run(...)``. ``service`` defaults to QiskitRuntimeService().
    """
    run = get_active_run()
    if service is None:
        from qiskit_ibm_runtime import QiskitRuntimeService

        service = QiskitRuntimeService()
    recorded = run.db.get_meta("backend")
    chosen = name or recorded
    if chosen is None:
        filters = {"operational": True, "simulator": False, **least_busy_filters}
        b = service.least_busy(**filters)
    else:
        try:
            b = service.backend(chosen)
        except Exception as exc:  # noqa: BLE001
            if chosen == recorded and not name:
                raise RuntimeError(
                    f"this experiment ran on {recorded!r}, which is not available now ({exc}). Run again "
                    f"later, or pass name='...' to continue on another device (earlier results stay from "
                    f"{recorded!r}).") from exc
            raise
    run._check_backend(b.name, b)  # records it on the first run; warns or stops if it changed
    run._say("info", f"device: {b.name}" + (" (recorded; reruns use it automatically)" if recorded is None
                                           else " (from the experiment file)" if not name else ""))
    return b
