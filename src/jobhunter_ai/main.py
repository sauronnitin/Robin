import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from jobhunter_ai import events_bus
from jobhunter_ai.crew import JobhunterAiCrew
from jobhunter_ai.graph_crew import crew_from_env_or_default


def run():
    resume_path = Path("resume/base_resume.tex")
    if not resume_path.exists():
        raise FileNotFoundError(f"Missing resume at {resume_path}")
    resume_latex = resume_path.read_text(encoding="utf-8")
    sheet_id = os.environ.get("MASTER_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("MASTER_SHEET_ID is not set in the environment")

    run_id = events_bus.begin_run()
    events_bus.register_crewai_listeners()
    events_bus.write_state(pid=os.getpid(), status="running")
    print(f"[jobhunter] run_id={run_id} starting kickoff (DRY_RUN={os.environ.get('DRY_RUN', 'True')})")

    inputs = {
        "resume_text": resume_latex,
        "resume_latex": resume_latex,
        "spreadsheet_id": sheet_id,
    }
    try:
        graph_crew = crew_from_env_or_default()
        crew = graph_crew if graph_crew is not None else JobhunterAiCrew().crew()
        crew.kickoff(inputs=inputs)
        events_bus.end_run("done", detail={"message": "crew.kickoff() finished"})
        print("[jobhunter] run complete")
        return 0
    except Exception as exc:
        events_bus.emit(
            "error",
            status="failed",
            detail={"error": str(exc)[:1200]},
        )
        # If we are not already waiting on user confirm inside GroqLLM, pause once.
        state = events_bus.read_state()
        if state.get("status") != "aborted":
            decision = events_bus.await_user_decision(
                error=str(exc)[:400],
                suggestion="Review the failure, fix config/quota if needed, then confirm retry or abort.",
            )
            if decision == "retry":
                print("[jobhunter] user confirmed retry; re-raising for outer supervisor to restart")
                events_bus.end_run("failed", detail={"error": str(exc)[:800], "retry_requested": True})
                return 75  # special: server may restart
            events_bus.end_run("aborted", detail={"error": str(exc)[:800]})
            return 1
        events_bus.end_run("aborted", detail={"error": str(exc)[:800]})
        return 1


if __name__ == "__main__":
    sys.exit(run() or 0)
