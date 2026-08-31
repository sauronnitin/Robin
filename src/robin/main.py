import os
import sys
from dotenv import load_dotenv

load_dotenv()

from robin import events_bus
from robin import profile as robin_profile
from robin.crew import RobinCrew
from robin.graph_crew import crew_from_env_or_default


def run():
    resume_path = robin_profile.profile_resume_path()
    if resume_path is None:
        raise FileNotFoundError(
            "No resume found. Place resume.tex or resume.pdf in user/ "
            "(see README), or use the shipped example via ROBIN_PROFILE=product-designer."
        )
    resume_latex = resume_path.read_text(encoding="utf-8")
    sheet_id = os.environ.get("MASTER_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("MASTER_SHEET_ID is not set in the environment")

    run_id = events_bus.begin_run()
    events_bus.register_crewai_listeners()
    events_bus.write_state(pid=os.getpid(), status="running")
    print(f"[jobhunter] run_id={run_id} starting kickoff (DRY_RUN={os.environ.get('DRY_RUN', 'True')})")

    # The search describes the candidate, not a list in a config file. These
    # come from the roles on their own resume (see role_profile).
    from robin import location_fit
    from robin import role_profile

    user_profile = robin_profile.load_profile()
    role = role_profile.ensure(user_profile)
    home = location_fit.home_country(user_profile)
    titles = robin_profile.search_titles()
    niche = str((user_profile.get("search") or {}).get("niche") or "").strip()
    print(
        f"[jobhunter] searching as {role.get('primary_title') or 'unknown role'}"
        f" ({role.get('seniority')}) based in {home}:"
        f" {', '.join(titles) or 'no titles derived'}"
    )

    inputs = {
        "resume_text": resume_latex,
        "resume_latex": resume_latex,
        "spreadsheet_id": sheet_id,
        "search_titles": ", ".join(f'"{t}"' for t in titles),
        "primary_role": role.get("primary_title") or "",
        "seniority": role.get("seniority") or "senior",
        "home_country": home,
        "niche": niche or str(role.get("primary_title") or "the candidate's field"),
    }
    try:
        graph_crew = crew_from_env_or_default()
        crew = graph_crew if graph_crew is not None else RobinCrew().crew()
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
