"""Direct code generation run — dispatches elements to Redis, then consumes via OpenCode."""
import asyncio, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "plugins" / "cycle" / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from src.storage.filesystem import FilesystemStorage
from src.storage.models import ElementStatus
from spec_editor_cycle.engine import WorkflowEngine
from spec_editor_cycle.providers import OpenCodeProvider
from src.agents.task_queue import AbstractTaskQueue, get_queue_url, TaskResult


async def main():
    project = sys.argv[1] if len(sys.argv) > 1 else "/Users/dmitry/Documents/Droid/gen-panel/prompt3"
    storage = FilesystemStorage(Path(project))

    # Clean dispatched tags
    for s in storage.list_all():
        el = storage.read_element(s.id)
        tags = getattr(el, "tags", []) or []
        if "dispatched" in tags:
            el.tags = [t for t in tags if t != "dispatched"]
            storage.write_element(el)
    print("Tags cleaned")

    # Dispatch
    engine = WorkflowEngine(storage=storage, project_path=project, provider="opencode")
    result = await engine._dispatch_to_redis(filter_all_reviewed=True)
    print(f"Dispatched: {result['dispatched']} to Redis")

    if result["dispatched"] == 0:
        print("Nothing to do.")
        return

    # Consume
    queue_url = get_queue_url(project)
    queue = AbstractTaskQueue.connect(queue_url)
    await queue.connect()

    opencode = OpenCodeProvider(project)
    generated = 0
    failed = 0
    max_tasks = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    async for task in queue.subscribe("coding", consumer_id="direct-run"):
        if generated + failed >= max_tasks:
            break

        payload = task.payload
        eid = payload.get("element_id", "?")
        action = payload.get("action", "generate")
        desc = (
            f"Generate code for {eid}: {payload.get('title', '')}"
            if action == "generate"
            else f"Fix {eid}: {payload.get('title', '')}"
        )
        print(f"[{generated + failed + 1}/{max_tasks}] Coding: {action} {eid}...", flush=True)

        try:
            r = await opencode.run(
                storage=storage,
                task=desc,
                model=payload.get("model", "deepseek/deepseek-reasoner"),
            )
            status = "ok" if r.get("status") == "ok" else "failed"
        except Exception as exc:
            r = {"status": "error", "error": str(exc)}
            status = "failed"

        if status == "ok":
            try:
                el = storage.read_element(eid)
                el.status = ElementStatus.CONFIRMED
                storage.write_element(el)
            except Exception:
                pass
            generated += 1
            print(f"  ✓ {eid} CONFIRMED", flush=True)
        else:
            failed += 1
            print(f"  ✗ {eid} failed: {r.get('error', 'unknown')[:100]}", flush=True)

        await queue.ack(task, TaskResult(task.task_id, "coding", status, {"error": r.get("error", "")}))

    await queue.close()
    print(f"\nDone: {generated} generated, {failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
