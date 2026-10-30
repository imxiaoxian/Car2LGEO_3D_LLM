"""Identify live job owners without trusting persisted 'processing' labels."""
import os
import json
from pathlib import Path
import sys
from uuid import uuid4

from .common import digest, write_json


class AssemblyControlError(RuntimeError):
    pass


class AssemblyCancelled(AssemblyControlError):
    pass


def process_identity(pid):
    if type(pid) is not int or pid <= 0:
        return None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            code = wintypes.DWORD()
            times = [wintypes.FILETIME() for _ in range(4)]
            if (not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259
                    or not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times))):
                return None
            return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError):
        return None


def alive(owner):
    return bool(owner and owner.get("identity") and process_identity(owner.get("pid")) == owner["identity"])


def current_owner():
    return dict(pid=os.getpid(), identity=process_identity(os.getpid()))


def executing(record):
    return record.get("status") == "running" and (alive(record.get("owner")) or alive(record.get("child")))


def owns_assembly_revision(directory, artifacts, runtime, *, owner=None):
    """Recognize the host, its child, and the Windows venv launcher child."""
    directory, artifacts = Path(directory).resolve(), Path(artifacts).resolve()
    if artifacts == directory or not artifacts.is_relative_to(directory):
        return False
    owner = dict(owner or current_owner())
    child = runtime.get("child", {})
    launcher_child = (owner == current_owner() and child.get("pid") == os.getppid() and alive(child))
    return (alive(owner) and (owner in (runtime.get("owner"), runtime.get("child")) or launcher_child)
        and _read(directory / "desktop.json").get("artifacts") == artifacts.relative_to(directory).as_posix())


def save_runtime(path, session, *, status, stage=None, child=None):
    from datetime import datetime, timezone
    record = dict(schema="vehicle-job-runtime.v1", owner=current_owner(), session=session,
        status=status, stage=stage, observed_at=datetime.now(timezone.utc).isoformat())
    if child:
        record["child"] = dict(pid=child.pid, identity=process_identity(child.pid))
    write_json(path, record)
    return record


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def register_assembly(request, output, artifacts, *, owner=None):
    """Attach a standalone assembly worker without discarding earlier job evidence."""
    request, output, artifacts = (Path(p).resolve() for p in (request, output, artifacts))
    directory = request.parent
    if (request.name != "request.json" or len(directory.name) != 32
            or any(c not in "0123456789abcdef" for c in directory.name)
            or not request.is_file() or not artifacts.is_relative_to(directory)
            or artifacts == directory or output != artifacts / "03-lego/agent"):
        raise ValueError("Assembly runtime must use the job's request and artifact directory")
    owner = dict(owner or current_owner())
    if not alive(owner):
        raise ValueError("Assembly owner is no longer alive")
    previous = _read(directory / "runtime.json")
    metadata = _read(directory / "desktop.json")
    relative = artifacts.relative_to(directory).as_posix()
    if executing(previous):
        if owns_assembly_revision(directory, artifacts, previous, owner=owner):
            return None  # The desktop host already owns this child.
        raise RuntimeError("A live worker already owns this job")
    session = uuid4().hex
    result_path = output / "agent.json"
    history = directory / "execution-history" / (session + ".json")
    write_json(history, dict(schema="vehicle-execution-history.v1", runtime=previous,
        desktop=metadata, status=_read(directory / "status.json")))
    record = dict(schema="vehicle-job-runtime.v1", kind="standalone_assembly", owner=owner,
        session=session, status="running", stage="lego", artifacts=relative,
        controls="assembly-checkpoint.v1",
        request_sha256=digest(request), previous_execution=history.relative_to(directory).as_posix(),
        result_before=digest(result_path) if result_path.is_file() else None)
    from datetime import datetime, timezone
    record["observed_at"] = datetime.now(timezone.utc).isoformat()
    write_json(directory / "runtime.json", record)
    write_json(directory / "desktop.json", {**metadata, "artifacts": relative,
        "workflow": "assembly_agent_v1", "execution_session": session})
    write_json(directory / "status.json", dict(status="processing", stage="lego", progress=68, error=None))
    return record


def bound_assembly(directory, runtime):
    """Validate a standalone worker's exact job, input and selected revision."""
    directory = Path(directory).resolve()
    if runtime.get("kind") != "standalone_assembly":
        return None
    request = directory / "request.json"
    name = runtime.get("artifacts")
    if not isinstance(name, str) or not request.is_file():
        return None
    artifacts = (directory / name).resolve()
    metadata = _read(directory / "desktop.json")
    if (artifacts == directory or not artifacts.is_relative_to(directory)
            or metadata.get("artifacts") != name
            or metadata.get("execution_session") != runtime.get("session")
            or digest(request) != runtime.get("request_sha256")):
        return None
    return artifacts


def controllable_assembly(directory, runtime):
    return (runtime.get("controls") == "assembly-checkpoint.v1" and executing(runtime)
        and bound_assembly(directory, runtime) is not None)


def assembly_checkpoint(request, registration):
    """Cooperative control only for the registered standalone worker session."""
    import time
    directory = Path(request).resolve().parent
    while True:
        runtime = _read(directory / "runtime.json")
        if (runtime.get("session") != registration["session"] or
                runtime.get("owner") != registration["owner"] or
                not controllable_assembly(directory, runtime)):
            raise AssemblyControlError("Assembly job ownership or bound inputs changed")
        status = _read(directory / "status.json").get("status")
        if status == "cancelled":
            raise AssemblyCancelled("装配任务已取消，模块检查点已保留。")
        if status != "paused":
            return
        time.sleep(0.25)


def desktop_assembly_checkpoint(request):
    """Keep a desktop-owned assembly child responsive to cooperative controls."""
    import time
    directory = Path(request).resolve().parent
    original = _read(directory / "runtime.json")
    request_hash = digest(request)

    def checkpoint():
        while True:
            runtime = _read(directory / "runtime.json")
            if (not original.get("session") or runtime.get("session") != original["session"]
                    or runtime.get("owner") != original.get("owner") or not executing(runtime)
                    or digest(request) != request_hash):
                raise AssemblyControlError("Desktop assembly ownership or bound inputs changed")
            status = _read(directory / "status.json").get("status")
            if status == "cancelled":
                raise AssemblyCancelled("装配任务已取消，模块检查点已保留。")
            if status != "paused":
                return
            time.sleep(0.25)

    return checkpoint


def assembly_state(directory, runtime):
    """Project an attached worker's live or final state; never certify its output."""
    directory = Path(directory).resolve()
    artifacts = bound_assembly(directory, runtime)
    if artifacts is None:
        return None
    requested = _read(directory / "status.json").get("status")
    if runtime.get("controls") == "assembly-checkpoint.v1" and requested == "cancelled":
        return dict(status="cancelled", stage="cancelled", progress=68, error=None)
    if executing(runtime):
        return dict(status="paused" if requested == "paused" else "processing",
            stage="lego", progress=68, error=None)
    if runtime.get("error"):
        return dict(status="failed", stage="lego", progress=68, error=runtime["error"])
    result_path = artifacts / "03-lego/agent/agent.json"
    result = _read(result_path)
    fresh = result_path.is_file() and digest(result_path) != runtime.get("result_before")
    if (fresh and result.get("schema") == "assembly-scheduled-agent.v1"
            and result.get("status") == "draft" and result.get("export", {}).get("ok") is True):
        return dict(status="draft", stage="draft", progress=100, error=None)
    return dict(status="failed", stage="lego", progress=68,
        error="装配进程已退出，尚无本次完整导出报告。已保留模块检查点，可恢复任务。")


def finish_assembly(request, registration, error=None):
    if not registration:
        return
    directory = Path(request).resolve().parent
    runtime = _read(directory / "runtime.json")
    if runtime.get("session") != registration["session"]:
        return
    runtime["status"] = "stopped"
    if error and not isinstance(error, AssemblyCancelled):
        runtime["error"] = str(error)
    write_json(directory / "runtime.json", runtime)
    state = assembly_state(directory, runtime)
    if state:
        write_json(directory / "status.json", state)
